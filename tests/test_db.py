"""Postgres suite: the metrics job, charts and the daily job on a synthetic
tape. Needs DATABASE_URL_TEST pointing at a disposable database (it is wiped).
"""

import dataclasses
import os
from datetime import UTC, datetime, timedelta

import pytest

from tape import daily, metrics
from tape.db import connect, migrate
from tape.settings import load

DSN = os.environ.get("DATABASE_URL_TEST")
pytestmark = pytest.mark.skipif(not DSN, reason="DATABASE_URL_TEST is unset")

T0 = datetime(2026, 10, 1, 0, tzinfo=UTC)
MARKETS = {
    "xyz:CRWV": ("xyz", "neoclouds", 30.0),
    "xyz:DRAM": ("xyz", "memory_price", 48.0),
    "xyz:NVDA": ("xyz", "silicon", 200.0),
}


@pytest.fixture
def s():
    base = load()
    env = dataclasses.replace(base.env, database_url=DSN, chart_store="db", telegram_token=None)
    tape = {**base.tape, "events": {**base.tape["events"], "warmup_hours": 0}}
    return dataclasses.replace(base, env=env, tape=tape)


@pytest.fixture
def conn(s):
    with connect(DSN) as c:
        c.execute("drop schema public cascade; create schema public;")
        c.commit()
    migrate(DSN)
    with connect(DSN) as c:
        yield c


def seed(conn, hours: int) -> None:
    for k, label, i in (("neoclouds", "Neoclouds", 0), ("memory_price", "Memory price", 1), ("silicon", "Silicon", 2)):
        conn.execute("insert into legs values (%s, %s, %s)", (k, label, i))
    for coin, (dex, leg, _) in MARKETS.items():
        conn.execute("insert into markets (coin, dex, leg) values (%s, %s, %s)", (coin, dex, leg))
    whales = [f"0xw{i:02d}" for i in range(60)]
    smalls = [f"0xs{i:03d}" for i in range(400)]
    for a in whales + smalls:
        conn.execute(
            "insert into addresses (address, first_fill, first_fill_complete) values (%s, %s, true)",
            (a, T0 - timedelta(days=60 if a.startswith("0xw") else 5)),
        )
        conn.execute("insert into address_dexes (address, dex, first_swept_hour) values (%s, 'xyz', %s)", (a, T0))
    for h in range(hours):
        hour = T0 + timedelta(hours=h)
        conn.execute(
            "insert into sweeps (hour, started_at, finished_at) values (%s, %s, %s)",
            (hour, hour, hour + timedelta(minutes=10)),
        )
        for coin, (_, _, mark) in MARKETS.items():
            # Whales drift from long to short CRWV over the window; small wallets take the other side.
            tilt = 1 - 2 * h / hours if coin == "xyz:CRWV" else 0.5
            rows = []
            for i, w in enumerate(whales):
                rows.append((w, 300_000 * tilt * (1 + i / 60)))
            whale_total = sum(r[1] for r in rows)
            for sm in smalls:
                rows.append((sm, -whale_total / len(smalls)))
            longs = sum(x for _, x in rows if x > 0)
            conn.execute(
                "insert into market_hours (coin, hour, oi, mark, oracle, funding) values (%s,%s,%s,%s,%s,%s)",
                (coin, hour, longs, mark, mark * 0.999, 0.00001),
            )
            with conn.cursor() as cur:
                cur.executemany(
                    "insert into snapshots (hour, coin, address, size) values (%s,%s,%s,%s)",
                    [(hour, coin, a, size) for a, size in rows if size],
                )
    conn.commit()


def test_metrics_charts_and_daily(conn, s):
    seed(conn, 31)
    done = metrics.run(conn, s)
    assert len(done) == 31
    cx = conn.execute(
        "select value, coverage from series where subject = 'complex' and metric = 'whale_net' order by hour desc limit 1"
    ).fetchone()
    assert cx["coverage"] == pytest.approx(1.0)
    whales = conn.execute("select count(*) n from address_days where whale_rank is not null").fetchone()["n"]
    assert whales >= 50
    crwv = conn.execute(
        """select value from series where subject = 'xyz:CRWV' and metric = 'whale_net_change'
           and value is not null order by hour desc limit 1"""
    ).fetchone()
    assert crwv["value"] < -0.5
    kinds = {r["kind"] for r in conn.execute("select distinct kind from charts")}
    assert {"home", "legs", "market", "card"} <= kinds
    png = conn.execute("select bytes from charts where kind = 'home' limit 1").fetchone()["bytes"]
    assert bytes(png[:8]) == b"\x89PNG\r\n\x1a\n"
    ev = {r["kind"] for r in conn.execute("select kind from events")}
    assert "whale_net_move" in ev and "whale_flip" in ev
    tops = conn.execute(
        "select count(*) n from top_positions where coin = 'xyz:CRWV' and hour = (select max(hour) from top_positions)"
    ).fetchone()["n"]
    assert tops == 50
    # The run is idempotent: nothing is pending, and recomputing an hour replaces its rows.
    assert metrics.run(conn, s) == []
    n0 = conn.execute("select count(*) n from series").fetchone()["n"]
    metrics.run(conn, s, [T0 + timedelta(hours=30)], render=False)
    assert conn.execute("select count(*) n from series").fetchone()["n"] == n0

    out = daily.run(s, day=datetime(2026, 10, 1).date(), send=False)
    assert out["posted"] and "Complex whale net" in out["caption"]
    assert conn.execute("select count(*) n from charts where kind = 'daily'").fetchone()["n"] == 1
    # 15:00 ET on Oct 1 is 19:00Z, inside the window: daily rows exist.
    assert conn.execute("select count(*) n from daily_series").fetchone()["n"] > 0


def test_daily_gap_when_coverage_low(conn, s):
    seed(conn, 22)
    conn.execute("update market_hours set oi = oi * 2")  # half the OI is now unseen
    conn.commit()
    metrics.run(conn, s, render=False)
    out = daily.run(s, day=datetime(2026, 10, 1).date(), send=False)
    assert out == {"posted": False, "reason": "coverage", "coverage": pytest.approx(0.5)}
    assert conn.execute("select count(*) n from daily_series").fetchone()["n"] == 0


def test_cohorts_recompute_until_coverage_clears(conn, s):
    seed(conn, 22)
    conn.execute("update market_hours set oi = oi * 2 where hour < %s", (T0 + timedelta(hours=20),))
    conn.commit()
    metrics.run(conn, s, render=False)
    meta = {r["key"]: r["meta"] for r in conn.execute("select key, meta from runs where job = 'cohorts'")}
    # T0 is 20:00 ET on Sep 30. Sep 30's set bootstraps at 00Z at 50% coverage and is
    # recomputed every hour through 18Z. Oct 1's set is first computed at 19Z (15:00 ET),
    # still at 50%, recomputed at 20Z when coverage is full, and then fixed.
    assert meta["2026-09-30"]["hour"] == "2026-10-01T18Z"
    assert meta["2026-10-01"] == {"hour": "2026-10-01T20Z", "coverage": pytest.approx(1.0)}


def _churn(conn, start, hours: int, addr="0xw00") -> None:
    """A market maker on CRWV: buy then sell 2 minutes later, 20 times an hour."""
    rows, tid = [], 1
    for h in range(hours):
        for k in range(20):
            t = start + timedelta(hours=h, minutes=3 * k)
            rows.append(("xyz:CRWV", tid, t, 30.0, 1.0, "B", addr, "0xother"))
            rows.append(("xyz:CRWV", tid + 1, t + timedelta(minutes=2), 30.0, 1.0, "A", "0xother", addr))
            tid += 2
    with conn.cursor() as cur:
        cur.executemany(
            "insert into fills (coin, tid, time, px, sz, side, buyer, seller) values (%s,%s,%s,%s,%s,%s,%s,%s)", rows
        )
    conn.commit()


def test_bots_need_a_day_of_fills(conn, s):
    seed(conn, 22)
    _churn(conn, T0, 21)  # 40 fills an hour over 21 hours: under a day observed at every hour
    metrics.run(conn, s, render=False)
    assert conn.execute("select count(*) n from address_days where is_bot").fetchone()["n"] == 0


def test_market_maker_is_a_bot_and_not_a_whale(conn, s):
    seed(conn, 22)
    _churn(conn, T0 - timedelta(days=2), 70)  # 960 fills a day, 2-minute holds, watched for 2+ days
    metrics.run(conn, s, render=False)
    r = conn.execute("select is_bot, whale_rank from address_days where address = '0xw00' order by day desc").fetchone()
    assert r["is_bot"] and r["whale_rank"] is None
