"""Stack Engine against Postgres: versioned quantities, the daily model run
(estimates, gaps and their persistence, scenarios, forecast scoring) and the
SF Compute store. Needs DATABASE_URL_TEST (a disposable database; it is wiped)."""

import dataclasses
import os
from datetime import UTC, date, datetime, timedelta

import pytest
import yaml

from tape import modeljob
from tape.db import connect, migrate
from tape.loaders import sfcompute
from tape.model.core import Quantity, Range
from tape.settings import load

DSN = os.environ.get("DATABASE_URL_TEST")
pytestmark = pytest.mark.skipif(not DSN, reason="DATABASE_URL_TEST is unset")

DAY = date(2026, 11, 3)


@pytest.fixture
def s(monkeypatch):
    monkeypatch.setenv("DATABASE_URL_UNPOOLED", DSN)
    base = load()
    env = dataclasses.replace(base.env, database_url=DSN, telegram_token=None)
    tape = {**base.tape, "stack": {**base.tape["stack"], "gap_persist_days": 2}}
    return dataclasses.replace(base, env=env, tape=tape)


@pytest.fixture
def conn(s):
    with connect(DSN) as c:
        c.execute("drop schema public cascade; create schema public;")
        c.commit()
    migrate(DSN)
    with connect(DSN) as c:
        yield c


def write(d, name, obj):
    (d / name).write_text(yaml.safe_dump(obj, sort_keys=False))


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    """A small config dir: the repo's graph, a stack with one comparison."""
    stack = tmp_path / "stack"
    stack.mkdir()
    (tmp_path / "graph.yaml").write_text((modeljob.CONFIG_DIR / "graph.yaml").read_text())
    write(stack, "entities.yaml", {"entities": {
        "coreweave": {"name": "CoreWeave", "kind": "company", "segment": "cloud", "node": "coreweave"},
        "h100": {"name": "Nvidia H100", "kind": "sku", "segment": "accelerators"},
    }})  # fmt: skip
    write(stack, "sources.yaml", {"sources": {"rel": {"url": "https://example.com/q2", "title": "Q2 release"}}})
    write(stack, "quantities.yaml", {
        "quantities": [{"key": "coreweave.active_power", "unit": "mw", "period": "2026-06-30", "value": 1500,
                        "kind": "company", "source": "rel", "verified_at": "2026-11-01"}],
        "estimates": [
            {"key": "coreweave.net_debt", "unit": "usd", "method": "difference", "inputs": ["coreweave.debt", "coreweave.cash"]},
            {"key": "coreweave.mw_per_gpu", "unit": "mw_per_gpu", "method": "judgment", "period": "2026-06-30",
             "range": [0.0015, 0.002, 0.0025], "basis": "power per GPU", "source": "rel", "verified_at": "2026-11-01"},
            {"key": "coreweave.gpus_deployed", "unit": "gpus", "method": "ratio",
             "inputs": ["coreweave.active_power", "coreweave.mw_per_gpu"]},
        ],
    })  # fmt: skip
    write(stack, "conversions.yaml", {"comparisons": [{
        "node": "coreweave.rent_util", "entity": "coreweave", "label": "CoreWeave rent x utilization",
        "conversion": "neocloud_rent", "market": {"price": "xyz:CRWV"},
        "inputs": {"shares": "coreweave.shares_diluted", "net_debt": "coreweave.net_debt",
                   "gpus": "coreweave.gpus_deployed", "rent": "h100.rent_traded"},
        "assumptions": {"ev_revenue": {"range": [6, 9, 12], "note": "n"}, "utilization": {"range": [0.7, 0.85, 0.95]}},
        "reviewed_at": "2026-11-01",
    }]})  # fmt: skip
    write(stack, "scenarios.yaml", {"links": [], "scenarios": []})
    write(stack, "forecasts.yaml", {"forecasts": [{
        "id": "crwv-debt-q2", "key": "coreweave.debt", "unit": "usd", "period": "2026-06-30",
        "value": 33e9, "range": [30e9, 36e9], "published": "2026-07-15",
    }]})  # fmt: skip
    monkeypatch.setattr(modeljob, "STACK_DIR", stack)
    monkeypatch.setattr(modeljob, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr("tape.graph.CONFIG_DIR", tmp_path)
    return stack


def filed(key, v, end=date(2026, 6, 30), start=None, unit="usd"):
    return Quantity(key, unit, start or end, end, Range.point(v), "filed", "edgar", source="src_edgar",
                    revision_reason="10-Q filed 2026-08-12")  # fmt: skip


def seed_market(conn, day, close, bid, ask):
    conn.execute(
        "insert into candles (coin, day, close) values ('xyz:CRWV', %s, %s) on conflict (coin, day) do update set close = excluded.close",
        (day, close),
    )
    t = datetime(day.year, day.month, day.day, 12, tzinfo=UTC)
    conn.execute(
        """insert into rent_quotes (time, accelerator, sku, start_at, end_at, hours, bid, ask)
           values (%s, 'h100', 'sku_a', %s, %s, 168, %s, %s)""",
        (t, t + timedelta(days=1), t + timedelta(days=8), bid, ask),
    )


def seed_filed(conn):
    modeljob.put_sources(conn, [modeljob.Source("src_edgar", "https://www.sec.gov/x/", "10-Q")])
    modeljob.register(conn, modeljob.load_files())
    modeljob.put_quantities(conn, [
        filed("coreweave.debt", 35e9), filed("coreweave.cash", 5e9),
        filed("coreweave.shares_diluted", 551e6, start=date(2026, 4, 1), unit="shares"),
    ])  # fmt: skip
    conn.commit()


def test_quantities_are_versioned_with_reasons(conn, s, cfg):
    modeljob.register(conn, modeljob.load_files())
    conn.commit()
    base = Quantity("coreweave.active_power", "mw", date(2026, 6, 30), date(2026, 6, 30), Range.point(1500), "company",
                    "config", source=modeljob.load_files().stack.quantities[0].source, verified_at=date(2026, 11, 1))  # fmt: skip
    assert modeljob.put_quantities(conn, [base]) == 1
    assert modeljob.put_quantities(conn, [base]) == 0  # the same figure again writes nothing
    conn.commit()
    with pytest.raises(ValueError, match="revision_reason"):
        modeljob.put_quantities(conn, [dataclasses.replace(base, value=Range.point(1600))])
    conn.rollback()
    new = dataclasses.replace(base, value=Range.point(1600), revision_reason="company restated active power")
    assert modeljob.put_quantities(conn, [new]) == 1
    rows = conn.execute(
        "select version, value, valid_to is null as current, revision_reason from quantities order by version"
    ).fetchall()
    assert [(r["version"], r["value"], r["current"]) for r in rows] == [(1, 1500, False), (2, 1600, True)]
    assert rows[1]["revision_reason"] == "company restated active power"


def test_model_run_writes_estimates_gaps_flags_and_scores(conn, s, cfg):
    seed_filed(conn)
    # Day 1: price 100 -> EV 85.1B, revenue 9.46B at 9x; GPUs 600k-1M -> implied ~1.35 per GPU-hour mid.
    # Traded rent 2.0-2.2 x utilization 0.7-0.95 = 1.4-2.09: the ranges overlap.
    seed_market(conn, DAY, 100.0, 2.0, 2.2)
    conn.commit()
    out = modeljob.run(s, DAY)
    assert out["comparisons_skipped"] == {} and len(out["gaps"]) == 1, out
    g = conn.execute("select * from gaps where day = %s", (DAY,)).fetchone()
    assert g["overlap"] and not g["flagged"] and g["days_apart"] == 0
    assert g["model_lo"] == pytest.approx(2.0 * 0.7) and g["model_hi"] == pytest.approx(2.2 * 0.95)
    names = {i["name"] for i in g["detail"]["inputs"]}
    assert names == {"price", "shares", "net_debt", "gpus", "rent"}
    assert {a["name"] for a in g["detail"]["assumptions"]} == {"ev_revenue", "utilization"}
    assert g["detail"]["model_from"] == ["h100.rent_traded"]
    est = {r["key"]: r for r in conn.execute("select * from quantities where origin = 'model' and valid_to is null")}
    assert est["coreweave.net_debt"]["value"] == pytest.approx(30e9)
    assert est["coreweave.gpus_deployed"]["lo"] == pytest.approx(600_000)
    assert conn.execute("select ok from model_runs").fetchone()["ok"]
    # The forecast is in, and scored against the filed debt figure.
    f = conn.execute("select * from forecasts").fetchone()
    assert f["actual"] == 35e9 and f["within"] and f["error"] == pytest.approx(33 / 35 - 1)

    # Days 2 and 3: the stock doubles, rents do not. Apart two days running -> flagged.
    for i, d in enumerate((DAY + timedelta(days=1), DAY + timedelta(days=2)), start=1):
        seed_market(conn, d, 400.0, 2.0, 2.2)
        conn.commit()
        modeljob.run(s, d)
        g = conn.execute("select * from gaps where day = %s", (d,)).fetchone()
        assert not g["overlap"] and g["days_apart"] == i and g["flagged"] == (i >= 2)
        assert g["distance"] > 0
    # Estimates did not change, so no new versions were written.
    assert conn.execute("select count(*) n from quantities where origin = 'model' and version > 1").fetchone()["n"] == 0
    # A forecast edited after publication is refused.
    write(cfg, "forecasts.yaml", {"forecasts": [{"id": "crwv-debt-q2", "key": "coreweave.debt", "unit": "usd",
          "period": "2026-06-30", "value": 34e9, "range": [30e9, 36e9], "published": "2026-07-15"}]})  # fmt: skip
    with pytest.raises(ValueError, match="changed after publication"):
        modeljob.run(s, DAY + timedelta(days=3))


def test_dry_run_with_drafts_stores_nothing(conn, s, cfg):
    seed_filed(conn)
    seed_market(conn, DAY, 100.0, 2.0, 2.2)
    conn.commit()
    out = modeljob.run(s, DAY, drafts=True)
    assert out["stored"] is False and len(out["gaps"]) == 1
    assert conn.execute("select count(*) n from gaps").fetchone()["n"] == 0
    assert conn.execute("select count(*) n from model_runs").fetchone()["n"] == 0


def test_sfcompute_store_is_idempotent(conn, s):
    window = {"instance_sku": "sku_a", "start_at": 1777507200, "end_at": 1778112000, "duration_hours": 168,
              "best_bid": {"dollars_per_node_hour": "17.38"}, "best_ask": {"dollars_per_node_hour": "17.42"}}  # fmt: skip
    rows = sfcompute.parse_windows(
        "h100", [window, {**window, "instance_sku": "sku_b"}], datetime(2026, 4, 29, tzinfo=UTC), 8
    )
    fills = sfcompute.parse_fills("h100", 1777507200, 1778112000, [
        {"instance_sku": "sku_a", "dollars_per_node_hour": "17.40", "filled_at": 1777424010,
         "allocation_schedule_delta": [{"start_at": 1777507200, "end_at": 1778112000, "node_count": 3}]}], 8)  # fmt: skip
    for _ in range(2):
        sfcompute.store(conn, rows, fills)
    conn.commit()
    assert conn.execute("select count(*) n from rent_quotes").fetchone()["n"] == 2
    f = conn.execute("select * from rent_fills").fetchone()
    assert f["price"] == pytest.approx(17.40 / 8) and f["nodes"] == 3
