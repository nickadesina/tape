"""AI Leverage Map against Postgres: the sweep's account margin, the weekly
stress run, its gates and release, the replay, and the standing series.
Needs DATABASE_URL_TEST (a disposable database; it is wiped)."""

import asyncio
import dataclasses
import json
import os
from datetime import date, timedelta

import pytest

from tape import standing, stressjob
from tape.db import aconnect, connect, migrate
from tape.graph import load_graph_file
from tape.indexer import Indexer
from tape.settings import load
from tape.timeutil import et_hour_on, floor_hour, utcnow

DSN = os.environ.get("DATABASE_URL_TEST")
pytestmark = pytest.mark.skipif(not DSN, reason="DATABASE_URL_TEST is unset")

DAY = date(2026, 11, 1)  # a Sunday
HOUR = et_hour_on(DAY, 15)
MARKETS = {"xyz:ANT": ("labs", 100.0), "xyz:CRWV": ("neoclouds", 50.0), "xyz:MU": ("memory_names", 200.0)}


@pytest.fixture
def s(monkeypatch):
    monkeypatch.setenv("DATABASE_URL_UNPOOLED", DSN)
    base = load()
    env = dataclasses.replace(base.env, database_url=DSN, chart_store="db", telegram_token=None)
    return dataclasses.replace(base, env=env)


@pytest.fixture
def conn(s):
    with connect(DSN) as c:
        c.execute("drop schema public cascade; create schema public;")
        c.commit()
    migrate(DSN)
    with connect(DSN) as c:
        yield c


def ladder(mid: float, step: float, n: int, sz: float):
    return [[mid - step * (i + 0.5), sz] for i in range(n)], [[mid + step * (i + 0.5), sz] for i in range(n)]


def seed(conn, hour=HOUR, coverage=0.99) -> None:
    for i, k in enumerate(["labs", "neoclouds", "memory_names"]):
        conn.execute("insert into legs values (%s, %s, %s) on conflict do nothing", (k, k, i))
    for coin, (leg, mark) in MARKETS.items():
        conn.execute(
            "insert into markets (coin, dex, leg, max_leverage) values (%s, 'xyz', %s, 10) on conflict do nothing",
            (coin, leg),
        )
        conn.execute("insert into market_hours (coin, hour, oi, mark) values (%s, %s, 1000, %s)", (coin, hour, mark))
        views = {"full": ladder(mark, mark * 0.0005, 20, 1.0), "3": ladder(mark, mark * 0.01, 20, 20.0)}
        conn.execute(
            "insert into books (coin, hour, time, mid, views) values (%s, %s, %s, %s, %s)",
            (coin, hour, hour, mark, json.dumps(views)),
        )
    conn.execute(
        "insert into sweeps (hour, started_at, finished_at) values (%s, %s, %s)",
        (hour, hour, hour + timedelta(minutes=10)),
    )
    conn.execute(
        "insert into series (subject, metric, hour, value) values ('complex', 'coverage', %s, %s)", (hour, coverage)
    )
    # Ten isolated ANT longs with liquidation prices 1% apart from 89 down, and
    # one cross account long ANT and CRWV.
    for i in range(10):
        a = f"0xiso{i}"
        conn.execute(
            """insert into snapshots (hour, coin, address, size, liq_px, margin_type, position_value)
               values (%s, 'xyz:ANT', %s, 30, %s, 'isolated', 3000)""",
            (hour, a, 89 - i),
        )
    conn.execute(
        """insert into snapshots (hour, coin, address, size, margin_type, position_value) values
           (%s, 'xyz:ANT', '0xcross', 100, 'cross', 10000), (%s, 'xyz:CRWV', '0xcross', 100, 'cross', 5000)""",
        (hour, hour),
    )
    conn.execute(
        """insert into accounts (hour, address, dex, account_value, cross_value, cross_maintenance, margin_mode)
           values (%s, '0xcross', 'xyz', 2500, 2500, 750, 'cross')""",
        (hour,),
    )
    conn.commit()


def test_weekly_run_stores_scenarios_cards_and_gates(s, conn):
    seed(conn)
    out = stressjob.run_week(s, DAY, send=False)
    assert out["ran"] and out["scenarios"] >= 9  # anthropic, coreweave, micron at three shocks
    run = conn.execute("select * from stress_runs where date = %s", (DAY,)).fetchone()
    assert run["accounts"] == 11 and run["positions"] == 12 and run["released_at"] is None
    assert run["gates"]["coverage_ok"] is True and run["gates"]["model_validated"] is False
    r = conn.execute(
        "select * from stress_results where date = %s and scenario = 'node:anthropic@-20'", (DAY,)
    ).fetchone()
    # -20% puts ANT at 80: all ten isolated longs (liq 89..80) and the cross account go.
    assert r["accounts_hit"] == 11 and r["markets_hit"] == 2
    assert r["forced_lo"] <= r["forced_usd"] <= r["forced_hi"]
    assert r["detail"]["markets"]["xyz:CRWV"]["forced_usd"][0] > 0  # the cross account's other market
    calm = conn.execute(
        "select accounts_hit from stress_results where date = %s and scenario = 'node:micron@-10'", (DAY,)
    ).fetchone()
    assert calm["accounts_hit"] == 0
    kinds = {(c["kind"], c["subject"]) for c in conn.execute("select kind, subject from charts")}
    assert {("stress", "all"), ("stress", "top")} <= kinds
    assert "A 20% drop in" in out["caption"] and "NOT FOR POSTING" in out["caption"]
    with pytest.raises(SystemExit, match="model not validated"):
        stressjob.release(s, DAY)


def test_release_refuses_under_the_coverage_bar(s, conn):
    seed(conn, coverage=0.5)
    stressjob.run_week(s, DAY, send=False)
    with pytest.raises(SystemExit, match="coverage"):
        stressjob.release(s, DAY)


def test_no_run_without_the_sweep(s, conn):
    out = stressjob.run_week(s, DAY, send=False)
    assert out == {"ran": False, "reason": "no sweep"}


def test_replay_compares_predicted_with_recorded_liquidations(s, conn):
    start = floor_hour(utcnow()) - timedelta(hours=30)
    seed(conn, hour=start)
    # ANT falls 15% over six hours; CRWV and MU are flat.
    for h in range(1, 8):
        for coin, (_, mark) in MARKETS.items():
            m = mark * (1 - 0.15 * min(h, 6) / 6) if coin == "xyz:ANT" else mark
            conn.execute(
                "insert into market_hours (coin, hour, oi, mark) values (%s, %s, 1000, %s)",
                (coin, start + timedelta(hours=h), m),
            )
    # At 85 the isolated longs with liq 89..85 go (5 x 30 units); the tape recorded them.
    for i in range(5):
        conn.execute(
            """insert into liquidations (coin, tid, address, time, sz, px, notional)
               values ('xyz:ANT', %s, %s, %s, 30, 86, 2580)""",
            (i, f"0xiso{i}", start + timedelta(hours=3)),
        )
    conn.commit()
    rows = stressjob.replay(conn, s)
    assert len(rows) == 1
    r = rows[0]
    assert r["coin"] == "xyz:ANT" and r["move"] == pytest.approx(-0.15)
    assert r["recorded_usd"] == pytest.approx(5 * 2580)
    # Predicted at the trough price: 5 x 30 x 85 = 12,750 against 12,900 recorded.
    assert r["predicted_usd"] == pytest.approx(5 * 30 * 85) and r["within"]
    gate = stressjob.validation(conn, s)
    assert gate["episodes"] == 0 and not gate["model_validated"]  # under min_recorded_usd


def test_standing_series_run_on_the_utc_close(s, conn):
    import random

    rnd = random.Random(3)
    start = date(2026, 6, 1)
    rows = []
    p = {c: 100.0 for c in ("xyz:ANT", "xyz:CRWV", "xyz:NVDA", "xyz:MU")}
    for i in range(120):
        common = rnd.gauss(0, 0.02)
        for c in p:
            p[c] *= 1 + common + rnd.gauss(0, 0.01)
            rows.append((c, start + timedelta(days=i), p[c]))
    with conn.cursor() as cur:
        cur.executemany("insert into candles (coin, day, close) values (%s, %s, %s)", rows)
    conn.commit()
    hour = floor_hour(utcnow()).replace(hour=0)
    hour = hour.replace(year=2026, month=9, day=29)  # the day after the last candle closes
    g = load_graph_file()
    stats, events = standing.run(conn, s, hour, g)
    subjects = {x.subject for x in stats}
    assert {"regime:labs~neoclouds", "regime:labs~chips", "regime:labs~memory"} <= subjects
    corr = {(x.subject, x.metric): x.value for x in stats}
    assert corr[("regime:labs~neoclouds", "corr")] > 0.5
    assert standing.run(conn, s, hour + timedelta(hours=1), g) == ([], [])


class MarginInfo:
    weight_spent = 0

    async def perp_dexs(self):
        return [None, {"name": "xyz"}]

    async def meta_and_ctxs(self, dex):
        if dex != "xyz":
            return [{"universe": []}, []]
        ctx = {
            "markPx": "30",
            "openInterest": "1000",
            "oraclePx": "30",
            "funding": "0",
            "premium": "0",
            "dayNtlVlm": "1",
        }
        return [{"universe": [{"name": "xyz:CRWV", "maxLeverage": 10, "szDecimals": 2}]}, [ctx]]

    async def clearinghouse_state(self, user, dex, priority=0):
        pos = {
            "coin": "xyz:CRWV",
            "szi": "-2",
            "entryPx": "31",
            "leverage": {"type": "isolated", "value": 5},
            "liquidationPx": "36",
            "marginUsed": "14",
            "positionValue": "60",
        }
        return {
            "assetPositions": [{"position": pos}],
            "marginSummary": {"accountValue": "500", "totalMarginUsed": "14", "totalNtlPos": "60"},
            "crossMarginSummary": {"accountValue": "486"},
            "crossMaintenanceMarginUsed": "0",
            "withdrawable": "470",
        }

    async def close(self):
        pass


def test_sweep_stores_account_margin_with_positions(s, conn):
    now = utcnow()
    conn.execute("insert into addresses (address, last_fill) values ('0xa', %s)", (now,))
    conn.execute("insert into address_dexes (address, dex, last_fill) values ('0xa', 'xyz', %s)", (now,))
    conn.commit()
    hour = floor_hour(now)

    async def go():
        c = await aconnect(DSN)
        ix = Indexer(s, c, c)
        await ix.info.close()
        ix.info = MarginInfo()
        try:
            await ix.load_universe()
            return await ix.sweep_step(hour, deadline=None)
        finally:
            await c.close()

    assert asyncio.run(go()) is True
    snap = conn.execute("select * from snapshots where hour = %s", (hour,)).fetchone()
    assert (snap["margin_type"], snap["margin_used"], snap["position_value"]) == ("isolated", 14, 60)
    acct = conn.execute("select * from accounts where hour = %s", (hour,)).fetchone()
    assert acct["account_value"] == 500 and acct["cross_value"] == 486 and acct["margin_mode"] == "isolated"
