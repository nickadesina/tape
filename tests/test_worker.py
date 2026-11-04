"""The Vercel worker: the resumable sweep, shift start-up state, the task and
HTTP endpoints. Needs DATABASE_URL_TEST (a disposable database; it is wiped)."""

import asyncio
import dataclasses
import json
import os
from datetime import timedelta

import psycopg
import pytest

from tape import indexer as indexer_mod
from tape import web, worker
from tape.db import aconnect, connect, migrate
from tape.indexer import SWEEP_LOCK, Indexer
from tape.settings import load
from tape.timeutil import floor_hour, utcnow

DSN = os.environ.get("DATABASE_URL_TEST")
pytestmark = pytest.mark.skipif(not DSN, reason="DATABASE_URL_TEST is unset")

CTX = {"markPx": "30", "openInterest": "1000", "oraclePx": "30", "funding": "0.0001", "premium": "0", "dayNtlVlm": "1"}


class FakeInfo:
    """Hyperliquid's info endpoint, one market, every address long 1.5."""

    weight_spent = 0

    def __init__(self):
        self.calls = 0

    async def perp_dexs(self):
        return [None, {"name": "xyz"}, {"name": "io"}]

    async def meta_and_ctxs(self, dex):
        if dex != "xyz":
            return [{"universe": []}, []]
        return [{"universe": [{"name": "xyz:CRWV", "maxLeverage": 10, "szDecimals": 2}]}, [CTX]]

    async def clearinghouse_state(self, user, dex, priority=0):
        self.calls += 1
        pos = {"coin": "xyz:CRWV", "szi": "1.5", "entryPx": "30", "leverage": {"value": 5}, "liquidationPx": "20"}
        return {"assetPositions": [{"position": pos}]}

    async def close(self):
        pass


@pytest.fixture
def s(monkeypatch):
    monkeypatch.setenv("DATABASE_URL_UNPOOLED", DSN)
    base = load()
    env = dataclasses.replace(base.env, database_url=DSN, chart_store="db", telegram_token=None)
    return dataclasses.replace(base, env=env)


@pytest.fixture
def db(s):
    with connect(DSN) as c:
        c.execute("drop schema public cascade; create schema public;")
        c.commit()
    migrate(DSN)


def seed_pairs(n: int) -> None:
    now = utcnow()
    with connect(DSN) as c:
        for i in range(n):
            a = f"0xa{i:04d}"
            c.execute("insert into addresses (address, last_fill) values (%s, %s)", (a, now))
            c.execute("insert into address_dexes (address, dex, last_fill) values (%s, 'xyz', %s)", (a, now))
        c.commit()


async def indexer(s) -> tuple[Indexer, object]:
    conn = await aconnect(DSN)
    ix = Indexer(s, conn, conn)
    await ix.info.close()
    ix.info = FakeInfo()
    await ix.load_universe()
    return ix, conn


def test_sweep_resumes_where_it_stopped(s, db, monkeypatch):
    seed_pairs(450)
    hour = floor_hour(utcnow())
    ticks = iter(range(15, 10_000, 15))
    monkeypatch.setattr(indexer_mod, "_clock", lambda: next(ticks))

    async def go():
        ix, conn = await indexer(s)
        ix.bucket.set_rate(1e9)  # the time check then reserves just its 20 s margin
        try:
            # 15 + 20 fits under 40; 30 + 20 does not: exactly one chunk.
            first = await ix.sweep_step(hour, deadline=40)
            with connect(DSN) as c:
                mid = c.execute("select count(*) n from snapshots where hour = %s", (hour,)).fetchone()["n"]
                unfinished = c.execute("select finished_at from sweeps where hour = %s", (hour,)).fetchone()
            second = await ix.sweep_step(hour, deadline=None)
            again = await ix.sweep_step(hour, deadline=None)
            return first, mid, unfinished, second, again, ix.info.calls
        finally:
            await conn.close()

    first, mid, unfinished, second, again, calls = asyncio.run(go())
    assert first is False and mid == indexer_mod.SWEEP_CHUNK and unfinished["finished_at"] is None
    assert second is True and again is True
    assert calls == 450  # nothing swept twice
    with connect(DSN) as c:
        sw = c.execute("select * from sweeps where hour = %s", (hour,)).fetchone()
        n = c.execute("select count(*) n from snapshots where hour = %s", (hour,)).fetchone()["n"]
        left = c.execute(
            "select count(*) n from address_dexes where swept_hour is distinct from %s", (hour,)
        ).fetchone()["n"]
        ctx = c.execute("select count(*) n from market_hours where hour = %s", (hour,)).fetchone()["n"]
    assert sw["finished_at"] is not None and sw["addresses"] == 450 and sw["positions"] == 450 and sw["errors"] == 0
    assert n == 450 and left == 0 and ctx == 1


def test_sweep_yields_to_a_running_sweeper(s, db):
    seed_pairs(3)
    hour = floor_hour(utcnow())
    holder = psycopg.connect(DSN, autocommit=True)
    holder.execute("select pg_advisory_lock(%s)", (SWEEP_LOCK,))

    async def go():
        ix, conn = await indexer(s)
        try:
            return await ix.sweep_step(hour, deadline=None), ix.info.calls
        finally:
            await conn.close()

    try:
        assert asyncio.run(go()) == (None, 0)
    finally:
        holder.close()


def test_new_sweep_closes_an_abandoned_one(s, db):
    seed_pairs(2)
    hour = floor_hour(utcnow())
    with connect(DSN) as c:
        c.execute("insert into sweeps (hour, started_at) values (%s, %s)", (hour - timedelta(hours=1), utcnow()))
        c.commit()

    async def go():
        ix, conn = await indexer(s)
        try:
            return await ix.sweep_step(hour, deadline=None)
        finally:
            await conn.close()

    assert asyncio.run(go()) is True
    with connect(DSN) as c:
        open_ = c.execute("select count(*) n from sweeps where finished_at is null").fetchone()["n"]
    assert open_ == 0


def test_shift_starts_from_sweep_plus_later_fills(s, db):
    seed_pairs(1)
    hour = floor_hour(utcnow()) - timedelta(hours=1)
    fin = hour + timedelta(minutes=5)
    with connect(DSN) as c:
        c.execute("insert into sweeps (hour, started_at, finished_at) values (%s, %s, %s)", (hour, hour, fin))
        c.execute("update address_dexes set first_swept_hour = %s", (hour,))
        c.execute("insert into snapshots (hour, coin, address, size) values (%s, 'xyz:CRWV', '0xa0000', 2.0)", (hour,))
        for tid, (t, buyer, seller) in enumerate(
            [(fin - timedelta(minutes=1), "0xa0000", "0xb"), (fin + timedelta(minutes=1), "0xb", "0xa0000")]
        ):
            c.execute(
                """insert into fills (coin, tid, time, px, sz, buyer, seller, source)
                   values ('xyz:CRWV', %s, %s, 30, 0.5, %s, %s, 'ws')""",
                (tid, t, buyer, seller),
            )
        c.commit()

    async def go():
        ix, conn = await indexer(s)
        try:
            await ix.load_state()
            return ix.positions[("0xa0000", "xyz:CRWV")], "0xa0000" in ix.known["xyz"]
        finally:
            await conn.close()

    # 2.0 at the sweep; the fill before it finished is already in the snapshot,
    # the sale after it is replayed.
    assert asyncio.run(go()) == (1.5, True)


def test_task_runs_a_cli_command(s, db):
    out = worker.task(["status"])
    assert out["exit"] == 0 and "sweeps:" in out["output"]
    assert "error" in worker.task(["index"])


def _call(path: str, method="GET", headers=None, body=b""):
    sent = []

    async def receive():
        return {"type": "http.request", "body": body, "more_body": False}

    async def send(msg):
        sent.append(msg)

    scope = {
        "type": "http",
        "path": path,
        "method": method,
        "query_string": b"",
        "headers": [(k.encode(), v.encode()) for k, v in (headers or {}).items()],
    }
    asyncio.run(web.app(scope, receive, send))
    return sent[0]["status"], json.loads(sent[1]["body"])


def test_http_auth_and_task(s, db, monkeypatch):
    monkeypatch.setenv("CRON_SECRET", "sekrit")
    assert _call("/api/health")[0] == 200
    assert _call("/api/shift")[0] == 401
    assert _call("/api/shift", headers={"authorization": "Bearer wrong"})[0] == 401
    status, out = _call(
        "/api/task", "POST", {"authorization": "Bearer sekrit"}, json.dumps({"args": ["status"]}).encode()
    )
    assert status == 200 and out["exit"] == 0
    assert _call("/api/nope", headers={"authorization": "Bearer sekrit"})[0] == 404


def test_daily_runs_only_at_16_et(s, db):
    out = worker.daily_job(s)
    from tape.timeutil import ET

    if utcnow().astimezone(ET).hour != 16:
        assert "skipped" in out
