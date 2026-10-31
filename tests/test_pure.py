from datetime import UTC, datetime, timedelta

import pytest

from tape.cohorts import (
    BOT,
    MID,
    SMALL,
    WHALE,
    CohortRules,
    Position,
    SideFill,
    classify_bot,
    cohort_of,
    is_fresh,
    median_hold_seconds,
    rank_whales,
)
from tape.compute import COMPLEX, HourInputs, MarketCtx, compute_hour, rows_by, top_positions
from tape.events import EventRules, market_events, position_events
from tape.fills import estimate_first_fill, from_user_fill, from_ws_trade, liquidated_user
from tape.hl import HIGH, LOW, WeightBucket, base_weight, extra_weight
from tape.match import match_coins
from tape.settings import load_universe_config
from tape.timeutil import et_hour_on, hour_key, parse_hour_key
from tape.universe import resolve

H = datetime(2026, 10, 31, 19, tzinfo=UTC)
RULES = CohortRules()


def test_ws_trade_users_are_buyer_then_seller():
    f = from_ws_trade(
        {
            "coin": "xyz:DRAM",
            "side": "B",
            "px": "48.4",
            "sz": "3",
            "time": 1793473256098,
            "hash": "0x1",
            "tid": 7,
            "users": ["0xAAA", "0xBBB"],
        }
    )
    assert (f.buyer, f.seller, f.side, f.source) == ("0xaaa", "0xbbb", "B", "ws")
    assert f.notional == pytest.approx(145.2)


def test_user_fill_side_and_aggressor():
    maker_sell = from_user_fill(
        "0xA", {"coin": "xyz:MU", "px": "1", "sz": "2", "side": "A", "time": 1, "tid": 9, "crossed": False}
    )
    assert maker_sell.seller == "0xa" and maker_sell.buyer is None and maker_sell.side == "B"
    taker_buy = from_user_fill(
        "0xA", {"coin": "xyz:MU", "px": "1", "sz": "2", "side": "B", "time": 1, "tid": 9, "crossed": True}
    )
    assert taker_buy.buyer == "0xa" and taker_buy.side == "B"
    assert liquidated_user({"liquidation": {"liquidatedUser": "0xABC"}}) == "0xabc"
    assert liquidated_user({}) is None


def test_first_fill_complete_only_when_first_page_short():
    page = [{"coin": "BTC", "time": 1}, {"coin": "xyz:MU", "time": 5}, {"coin": "xyz:MU", "time": 3}]
    ff = estimate_first_fill([page], {"xyz:MU"})
    assert ff.first_fill_ms == 3 and ff.complete
    full = [{"coin": "BTC", "time": i} for i in range(2000)]
    ff2 = estimate_first_fill([full, [{"coin": "xyz:MU", "time": 3000}]], {"xyz:MU"})
    assert ff2.first_fill_ms == 3000 and not ff2.complete


def test_fresh_needs_complete_history():
    assert is_fresh(H - timedelta(days=3), True, H, RULES)
    assert not is_fresh(H - timedelta(days=3), False, H, RULES)
    assert not is_fresh(H - timedelta(days=40), True, H, RULES)
    assert not is_fresh(None, True, H, RULES)


def test_median_hold_fifo():
    fills = [SideFill("c", 0, 1), SideFill("c", 60, -1), SideFill("c", 100, 2), SideFill("c", 400, -2)]
    assert median_hold_seconds(fills) == 300  # 1 unit held 60s, 2 units held 300s
    # A close with no open in the window is ignored.
    assert median_hold_seconds([SideFill("c", 0, -5)]) is None
    # Flip: sell 3 against a 1 long closes 1 and opens 2 short.
    assert median_hold_seconds([SideFill("c", 0, 1), SideFill("c", 10, -3), SideFill("c", 30, 2)]) == 20


def test_bot_classification():
    assert classify_bot(2100, 7, 120, RULES) == (300, True)
    assert classify_bot(2100, 7, 3600, RULES)[1] is False
    assert classify_bot(700, 7, 60, RULES)[1] is False
    assert classify_bot(2100, 7, None, RULES)[1] is False


def test_rank_whales_excludes_bots_and_breaks_ties_by_address():
    g = {"0xb": 10.0, "0xa": 10.0, "0xbot": 99.0, "0xc": 1.0}
    assert rank_whales(g, {"0xbot"}, 2) == ["0xa", "0xb"]
    assert cohort_of("0xbot", 99, {"0xa"}, {"0xbot"}, RULES) == BOT
    assert cohort_of("0xa", 1, {"0xa"}, set(), RULES) == WHALE
    assert cohort_of("0xz", 30_000, set(), set(), RULES) == MID
    assert cohort_of("0xz", 3_000, set(), set(), RULES) == SMALL


def _inputs(**kw):
    ctxs = {
        "xyz:CRWV": MarketCtx("xyz:CRWV", "neoclouds", oi=100.0, mark=10.0, oracle=10.0, funding=0.0001),
        "xyz:NVDA": MarketCtx("xyz:NVDA", "silicon", oi=50.0, mark=100.0, oracle=99.0, funding=0.0),
    }
    positions = [
        Position("0xw", "xyz:CRWV", -40.0),  # whale short CRWV, $400
        Position("0xw", "xyz:NVDA", 30.0),  # whale long NVDA, $3000
        Position("0xs", "xyz:CRWV", 58.0),  # small long CRWV
        Position("0xs2", "xyz:CRWV", -18.0),
        Position("0xs3", "xyz:NVDA", 20.0),
        Position("0xs4", "xyz:NVDA", -50.0),
    ]
    base = dict(
        ctxs=ctxs,
        positions=positions,
        whales={"0xw"},
        bots=set(),
        fresh={"0xs"},
        liq_usd={"xyz:CRWV": 50.0},
        whale_net_24h_ago={"xyz:CRWV": -0.1},
        rules=CohortRules(mid_floor_usd=10_000),
        crowding_top_n=1,
    )
    base.update(kw)
    return HourInputs(**base)


def test_compute_market_level():
    r = rows_by(compute_hour(_inputs()))
    assert r[("xyz:CRWV", "whale_net")].value == pytest.approx(-0.4)
    assert r[("xyz:CRWV", "whale_net_change")].value == pytest.approx(-0.3)
    assert r[("xyz:CRWV", "coverage")].value == pytest.approx(0.58)
    assert r[("xyz:CRWV", "whale_net")].coverage == pytest.approx(0.58)
    assert r[("xyz:CRWV", "small_net")].value == pytest.approx(0.4)
    assert r[("xyz:CRWV", "fresh_share")].value == pytest.approx(0.58)
    assert r[("xyz:CRWV", "crowding")].value == pytest.approx(0.58)
    assert r[("xyz:CRWV", "liq_share")].value == pytest.approx(0.05)
    assert r[("xyz:NVDA", "premium_bps")].value == pytest.approx(101.0101, rel=1e-4)
    assert r[("xyz:CRWV", "funding_ann")].value == pytest.approx(0.876)
    # Every cohort's net sums to longs minus shorts.
    total = sum(r[("xyz:CRWV", m)].value for m in ("whale_net", "mid_net", "small_net", "bot_net"))
    assert total == pytest.approx(r[("xyz:CRWV", "imbalance")].value)


def test_compute_complex_is_notional_weighted_spread():
    r = rows_by(compute_hour(_inputs()))
    # OI: CRWV $1,000, NVDA $5,000. Whale: -$400 + $3,000 = $2,600 of $6,000.
    assert r[(COMPLEX, "whale_net")].value == pytest.approx(2600 / 6000)
    assert r[("leg:silicon", "whale_net")].value == pytest.approx(0.6)
    assert r[(COMPLEX, "coverage")].value == pytest.approx((580 + 5000) / 6000)
    assert r[(COMPLEX, "whale_net_change")].value is None  # no complex value 24h ago
    assert r[(COMPLEX, "oi_usd")].value == pytest.approx(6000)


def test_top_positions_rank_by_size():
    tops = [t for t in top_positions(_inputs()) if t.coin == "xyz:CRWV"]
    assert [t.address for t in tops] == ["0xs", "0xw", "0xs2"]
    assert tops[1].cohort == WHALE and tops[0].fresh


def test_position_events():
    rules = EventRules()
    kw = dict(time=H, et_day="2026-10-31", coin="xyz:CRWV", address="0xw", mark=50.0, rules=rules)
    flip = position_events(prev_size=10_000, new_size=-6_000, is_whale=True, **kw)
    assert [e.kind for e in flip] == ["whale_flip"] and flip[0].size_usd == 300_000
    assert position_events(prev_size=10_000, new_size=-4_000, is_whale=True, **kw) == []  # $200k < floor
    assert position_events(prev_size=0, new_size=-6_000, is_whale=True, **kw) == []  # opening is not a flip
    entry = position_events(prev_size=1_000, new_size=11_000, is_whale=False, **kw)
    assert [e.kind for e in entry] == ["whale_entry"]
    assert position_events(prev_size=11_000, new_size=12_000, is_whale=False, **kw) == []  # already over
    assert flip[0].dedupe_key == "whale_flip:xyz:CRWV:0xw:2026-10-31"


def test_market_events():
    rules = EventRules()
    evs = market_events(
        time=H,
        hour_key="2026-10-31T19Z",
        et_day="2026-10-31",
        coin="xyz:DRAM",
        rules=rules,
        values={
            "whale_net_change": -0.06,
            "funding": 0.0006,
            "premium_bps": 10,
            "liq_share": 0.04,
            "oi_usd": 1e6,
            "coverage": 0.99,
        },
    )
    assert sorted(e.kind for e in evs) == ["funding", "liquidations", "whale_net_move"]
    liq = next(e for e in evs if e.kind == "liquidations")
    assert liq.size_usd == pytest.approx(40_000)
    assert (
        market_events(time=H, hour_key="k", et_day="d", coin="c", rules=rules, values={"whale_net_change": None}) == []
    )


async def test_weight_bucket_priorities():
    t = [0.0]
    b = WeightBucket(60, low_reserve=0.5, clock=lambda: t[0])  # 1 token/s, capacity 60
    await b.acquire(40, HIGH)
    assert b.wait_time(1, LOW) == pytest.approx(11)  # needs 20 + 30 floor, has 20
    assert b.wait_time(1, HIGH) == 0
    t[0] = 30
    assert b.wait_time(1, LOW) == 0
    assert base_weight("clearinghouseState") == 2 and base_weight("userFillsByTime") == 20
    assert extra_weight("userFillsByTime", 2000) == 100 and extra_weight("metaAndAssetCtxs", 2000) == 0


def _cfg():
    return load_universe_config(
        {
            "dexes": ["xyz", "io"],
            "legs": {
                "neo": {"label": "Neo", "markets": ["xyz:CRWV", "xyz:GONE"]},
                "gpu": {"label": "GPU", "markets": ["xyz:H100"]},
            },
            "candidates": ["xyz:WDC"],
            "aliases": {"xyz:CRWV": ["CoreWeave"]},
        }
    )


def test_resolve_universe_against_meta():
    metas = {
        "xyz": [
            {
                "universe": [
                    {"name": "xyz:CRWV", "maxLeverage": 10, "szDecimals": 2},
                    {"name": "xyz:H100", "maxLeverage": 10, "szDecimals": 0, "isDelisted": True},
                    {"name": "xyz:WDC", "maxLeverage": 10, "szDecimals": 3},
                    {"name": "xyz:TSLA", "maxLeverage": 20, "szDecimals": 3},
                ]
            },
            [],
        ]
    }
    u = resolve(_cfg(), metas)
    assert u.coins == ["xyz:CRWV"]
    assert u.markets["xyz:CRWV"].leg == "neo" and u.markets["xyz:CRWV"].dex == "xyz"
    assert u.missing == ["xyz:GONE"] and u.delisted == ["xyz:H100"]
    assert u.candidates == ["xyz:WDC"] and u.unclassified == ["xyz:TSLA"]


def test_config_rejects_market_in_two_legs():
    with pytest.raises(ValueError):
        load_universe_config(
            {"dexes": ["xyz"], "legs": {"a": {"label": "A", "markets": ["x"]}, "b": {"label": "B", "markets": ["x"]}}}
        )


def test_match_coins():
    cfg = _cfg()
    assert match_coins(cfg, "CoreWeave is raising again; $CRWV down 8%") == ["xyz:CRWV"]
    assert match_coins(cfg, "crwvx is not a ticker") == []


def test_real_config_loads_and_matches():
    from tape.settings import load_universe_config as real

    cfg = real()
    assert cfg.leg_of("xyz:CRWV") == "neoclouds"
    assert match_coins(cfg, "SK Hynix guides HBM up")[:1] in (["xyz:SKHX"], ["xyz:SKHY"], ["xyz:DRAM"])


def test_hour_keys_and_et():
    assert hour_key(H) == "2026-10-31T19Z" and parse_hour_key("2026-10-31T19Z") == H
    # 15:00 ET is 19:00Z in EDT (Oct 31) and 20:00Z in EST (Nov 2).
    from datetime import date

    assert et_hour_on(date(2026, 10, 31), 15) == H
    assert et_hour_on(date(2026, 11, 2), 15) == datetime(2026, 11, 2, 20, tzinfo=UTC)
