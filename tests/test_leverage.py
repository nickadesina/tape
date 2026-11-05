"""AI Leverage Map: depth, the stress model, the graph and the standing series. Pure."""

from datetime import UTC, date, datetime, timedelta

import pytest

from tape import loop, stress
from tape.depth import BID, bands_usd, profile, size_within, walk
from tape.graph import load_graph, load_graph_file
from tape.indexer import account_row
from tape.settings import load_universe_config

# ---- depth -----------------------------------------------------------------


def book(mid, step, n, sz):
    """A symmetric ladder: n levels a side, `step` apart, `sz` each."""
    bids = [[mid - step * (i + 0.5), sz] for i in range(n)]
    asks = [[mid + step * (i + 0.5), sz] for i in range(n)]
    return bids, asks


def test_profile_stitches_coarser_views_past_the_fine_one():
    fine = book(100, 0.1, 5, 1.0)  # reaches 0.45% from mid
    coarse = book(100, 1.0, 20, 10.0)  # 0.5% to 19.5%
    prof = profile([fine, coarse], 100, BID)
    # Fine levels first, then only the coarse levels beyond the fine reach.
    assert prof[:5] == [(pytest.approx(0.0005 + 0.001 * i), pytest.approx(1.0 + i)) for i in range(5)]
    assert prof[5][0] == pytest.approx(0.005) and prof[5][1] == pytest.approx(15.0)
    assert size_within(prof, 0.02) == pytest.approx(5 + 2 * 10)  # coarse levels at 0.5% and 1.5%


def test_bands_report_usd_and_reach():
    b = bands_usd([book(100, 1.0, 20, 2.0)])
    assert b["mid"] == 100
    assert b["bid2"] == pytest.approx(2 * 2.0 * 100)  # levels at 0.5% and 1.5%
    assert b["ask10"] == pytest.approx(10 * 2.0 * 100)
    assert b["reach_bid"] == pytest.approx(0.195)
    assert bands_usd([([], [])]) is None


def test_walk_reaches_the_level_that_fills_and_reports_what_is_left():
    prof = [(0.01, 5.0), (0.02, 10.0), (0.05, 20.0)]
    assert walk(prof, 4) == (0.01, 0.0)
    assert walk(prof, 12) == (0.05, 0.0)
    assert walk(prof, 30) == (0.05, 10.0)
    assert walk(prof, 0) == (0.0, 0.0)


# ---- the stress model ------------------------------------------------------


def flat_book(per_pct: float) -> stress.Book:
    """per_pct coin units resting at every 1% step out to 50%, each side."""
    prof = [(i / 100, per_pct * i) for i in range(1, 51)]
    return stress.Book(prof, prof)


def iso_long(coin, size, px0, liq):
    return stress.Pos(coin, size, px0, "isolated", liq)


def test_isolated_long_is_liquidated_past_its_liquidation_price_only():
    accts = [
        stress.Account("a", "xyz", (iso_long("X", 10, 100, 85),)),
        stress.Account("b", "xyz", (iso_long("X", 10, 100, 75),)),
    ]
    sh = stress.node_shock("x", "X", ["X"], -0.20)
    r = stress.run(accts, {"X": 100}, {"X": flat_book(1000)}, sh)
    assert r.accounts_hit == 1 and r.rounds == 1
    # 10 units sold into a book of 1000 per 1%: the price barely moves.
    assert r.markets["X"].prices[-1] == pytest.approx(80 * 0.99)
    assert r.forced_usd == pytest.approx(10 * (80 + 80 * 0.99) / 2)
    assert r.amplification == pytest.approx((80 * 0.99 / 100 - 1) / -0.2)


def test_forced_selling_cascades_through_a_thin_book():
    # Liquidation ladder: the first account sits just above the shocked price.
    accts = [stress.Account(f"a{i}", "xyz", (iso_long("X", 100, 100, 91 - 2 * i),)) for i in range(5)]
    sh = stress.node_shock("x", "X", ["X"], -0.10)
    thin = stress.run(accts, {"X": 100}, {"X": flat_book(50)}, sh)
    deep = stress.Book([(0.0005, 1e9)], [(0.0005, 1e9)])
    thick = stress.run(accts, {"X": 100}, {"X": deep}, sh)
    assert thick.accounts_hit == 1 and thick.rounds == 1
    # 100 units per account into 50 a percent: each sale knocks the next one over.
    assert thin.accounts_hit == 5 and thin.rounds == 5
    assert thin.markets["X"].prices[-1] == pytest.approx(90 * 0.90)
    assert thin.amplification == pytest.approx(1.9)


def test_cross_margin_spills_a_shock_into_the_accounts_other_markets():
    # One cross account long X and long Y: a drop in X alone breaches it, so
    # its Y is sold too.
    acct = stress.Account(
        "a",
        "xyz",
        (
            stress.Pos("X", 100, 100, "cross", None, mm_rate=0.05),
            stress.Pos("Y", 50, 10, "cross", None, mm_rate=0.05),
        ),
        cross_value=1200.0,
        cross_mm=525.0,  # 0.05 * (100 * 100 + 50 * 10)
    )
    books = {"X": flat_book(1e6), "Y": flat_book(1e6)}
    small = stress.run([acct], {"X": 100, "Y": 10}, books, stress.node_shock("x", "X", ["X"], -0.05))
    big = stress.run([acct], {"X": 100, "Y": 10}, books, stress.node_shock("x", "X", ["X"], -0.10))
    assert small.accounts_hit == 0
    assert big.accounts_hit == 1 and big.markets_hit == 2
    assert sum(big.markets["Y"].forced_usd) == pytest.approx(50 * 10, rel=0.01)


def test_cross_position_without_a_margin_row_falls_back_to_its_liquidation_price():
    acct = stress.Account("a", "xyz", (stress.Pos("X", 1, 100, "cross", 90),))
    r = stress.run([acct], {"X": 100}, {}, stress.node_shock("x", "X", ["X"], -0.15), walk_books=False)
    assert r.accounts_hit == 1 and r.markets["X"].prices == [85.0]


def test_shorts_are_bought_back_up_the_ask():
    acct = stress.Account("a", "xyz", (stress.Pos("X", -10, 100, "isolated", 105),))
    sh = stress.Shock("up", "X +10%", "node", {"X": 0.10}, ("X",), 0.10)
    r = stress.run([acct], {"X": 100}, {"X": flat_book(5)}, sh)
    assert r.markets["X"].prices[-1] == pytest.approx(110 * 1.02)


def test_unabsorbed_flow_is_reported_when_the_book_runs_out():
    acct = stress.Account("a", "xyz", (iso_long("X", 1000, 100, 95),))
    shallow = stress.Book([(0.01, 10.0)], [(0.01, 10.0)])
    r = stress.run([acct], {"X": 100}, {"X": shallow}, stress.node_shock("x", "X", ["X"], -0.10))
    assert r.unabsorbed_usd == pytest.approx(990 * 90 * 0.99)


def test_range_runs_thick_central_and_thin_books():
    accts = [stress.Account(f"a{i}", "xyz", (iso_long("X", 100, 100, 91 - 2 * i),)) for i in range(5)]
    sh = stress.node_shock("x", "X", ["X"], -0.10)
    thick, mid, thin = stress.run_range(accts, {"X": 100}, {"X": flat_book(100)}, {"X": (100, 0.5, 0.25)}, sh)
    assert (thick.accounts_hit, mid.accounts_hit, thin.accounts_hit) == (1, 5, 5)
    # The same accounts on a thinner book sell at lower prices.
    assert thin.markets["X"].prices[-1] < mid.markets["X"].prices[-1]


def test_graph_shock_moves_dependents_by_their_beta():
    sh = stress.graph_shock("lab", "Lab", ["L"], -0.20, {"N": 0.5, "L": 9.0})
    assert sh.moves == {"L": -0.20, "N": pytest.approx(-0.10)}
    assert sh.shocked == ("L",) and sh.key == "graph:lab@-20"


def test_drawdowns_are_found_once_per_fall():
    t0 = datetime(2026, 11, 1, tzinfo=UTC)
    marks = [100, 99, 95, 90, 91, 100, 100, 100]
    pts = {"X": [(t0 + timedelta(hours=i), m) for i, m in enumerate(marks)]}
    eps = stress.find_drawdowns(pts, 0.08, 24)
    assert eps == [("X", t0, t0 + timedelta(hours=3), pytest.approx(-0.10))]
    assert stress.find_drawdowns(pts, 0.2, 24) == []


def test_within_tolerance():
    assert stress.within(120, 100, 0.5) and not stress.within(200, 100, 0.5)


# ---- account margin from clearinghouseState ----------------------------------


def test_account_row_reads_the_margin_summary():
    st = {
        "marginSummary": {"accountValue": "13109.48", "totalMarginUsed": "4.96", "totalNtlPos": "100.02"},
        "crossMarginSummary": {"accountValue": "13104.51"},
        "crossMaintenanceMarginUsed": "1.5",
        "withdrawable": "13104.51",
    }
    row = account_row(st, [("X", 1, 1, 1, 1, "isolated", 1, 1), ("Y", 1, 1, 1, 1, "cross", 1, 1)])
    assert row == (13109.48, 13104.51, 4.96, 1.5, 100.02, 13104.51, "mixed")
    assert account_row({}, []) is None


# ---- the graph ---------------------------------------------------------------

NODES = {
    "lab": {"name": "Lab", "kind": "lab", "public": False, "markets": ["L"]},
    "cloud": {"name": "Cloud", "kind": "neocloud", "public": True, "markets": ["N"]},
    "chip": {"name": "Chip", "kind": "chip", "public": True, "markets": ["C"]},
    "hyper": {"name": "Hyper", "kind": "hyperscaler", "public": True},
}


def edge(i, src, dst, typ, **kw):
    return {
        "id": i,
        "from": src,
        "to": dst,
        "type": typ,
        "date": "2026-01-10",
        "source": "https://example.com/filing",
        "confidence": "filed",
        "verified_at": "2026-11-01",
        **kw,
    }


def graph():
    return load_graph(
        {
            "nodes": NODES,
            "edges": [
                edge("lab-cloud", "lab", "cloud", "compute_contract", size_usd=10e9, status="contracted"),
                edge("cloud-chip", "cloud", "chip", "supply", size_usd=5e9, status="announced"),
                edge("chip-lab", "chip", "lab", "equity", size_usd=1e9),
                edge("hyper-cloud", "hyper", "cloud", "revenue_concentration", share=0.6),
                edge("draft", "hyper", "lab", "equity", verified_at=None),
            ],
        }
    )


def test_drafts_are_kept_apart_from_verified_edges():
    g = graph()
    assert [e.id for e in g.drafts] == ["draft"]
    assert "draft" not in {e.id for e in g.edges}


def test_dependents_follow_money_out_of_a_node():
    g = graph()
    assert g.dependents("lab") == {"cloud": 1, "chip": 2}
    assert g.dependents("lab", depth=1) == {"cloud": 1}


def test_a_deal_is_circular_when_the_money_comes_back():
    g = graph()
    e = {x.id: x for x in g.edges}
    assert g.circular(e["chip-lab"])  # chip -> lab -> cloud -> chip
    assert not g.circular(e["hyper-cloud"])


def test_exposures_and_paths():
    g = graph()
    x = g.exposures()
    assert x["cloud"]["top_customer"] == "hyper" and x["cloud"]["top_customer_share"] == 0.6
    assert x["lab"]["commitments_contracted_usd"] == 10e9
    assert x["cloud"]["commitments_announced_usd"] == 5e9
    assert {tuple(e.id for e in p) for p in g.paths("lab", "chip", 2)} == {("chip-lab",), ("lab-cloud", "cloud-chip")}


@pytest.mark.parametrize(
    "bad",
    [
        {"type": "vibes"},
        {"confidence": "rumour"},
        {"source": "a friend"},
        {"to": "nobody"},
        {"type": "revenue_concentration"},  # without a share
        {"status": "maybe"},
    ],
)
def test_a_malformed_edge_fails_the_whole_file(bad):
    with pytest.raises(ValueError):
        load_graph({"nodes": NODES, "edges": [{**edge("e", "lab", "cloud", "equity"), **bad}]})


def test_the_repo_graph_loads_and_gives_every_market_a_node():
    g = load_graph_file()
    u = load_universe_config()
    assert set(u.markets_all()) - set(g.node_of_market()) <= {"xyz:H100", "io:H100"}
    for e in g.edges + g.drafts:
        assert e.source.startswith("http")


# ---- the standing series -----------------------------------------------------

D0 = date(2026, 1, 1)


def series(rets: list[float], start: float = 100.0) -> dict[date, float]:
    out, p = {D0: start}, start
    for i, r in enumerate(rets, start=1):
        p *= 1 + r
        out[D0 + timedelta(days=i)] = p
    return out


def test_returns_skip_gaps():
    cl = {D0: 100.0, D0 + timedelta(days=1): 110.0, D0 + timedelta(days=3): 99.0}
    assert loop.returns(cl) == {D0 + timedelta(days=1): pytest.approx(0.1)}


def test_corr_and_beta():
    xs = [0.01, -0.02, 0.03, -0.01]
    assert loop.corr(xs, [2 * x for x in xs]) == pytest.approx(1.0)
    assert loop.beta([2 * x for x in xs], xs) == pytest.approx(2.0)
    assert loop.corr([1, 1, 1], [1, 2, 3]) is None


def test_regime_alerts_when_labs_and_a_basket_start_moving_as_one():
    import random

    rnd = random.Random(1)
    n = 120
    labs = [rnd.gauss(0, 0.02) for _ in range(n)]
    # Independent for 90 days, then the cloud basket tracks labs.
    cloud = [rnd.gauss(0, 0.02) if i < 90 else labs[i] * 1.2 for i in range(n)]
    closes = {"L": series(labs), "N": series(cloud)}
    cfg = {"corr_days": 20, "selloff_days": 60, "min_days": 10, "min_history_days": 30, "extreme_pctile": 0.95}
    day = D0 + timedelta(days=n)
    stats, extremes = loop.regime(closes, {"labs": ["L"], "clouds": ["N"]}, "labs", day, cfg)
    by = {s.metric: s.value for s in stats}
    assert by["corr"] > 0.95 and by["corr_pctile"] >= 0.95
    assert [x.subject for x in extremes] == ["regime:labs~clouds"]
    early = loop.regime(closes, {"labs": ["L"], "clouds": ["N"]}, "labs", D0 + timedelta(days=80), cfg)[1]
    assert early == []


def test_tension_measures_beta_against_revenue_share():
    import random

    rnd = random.Random(2)
    x = [rnd.gauss(0, 0.02) for _ in range(100)]
    g = load_graph(
        {
            "nodes": {
                "big": {"name": "Big", "kind": "lab", "public": True, "markets": ["B"]},
                "small": {"name": "Small", "kind": "neocloud", "public": True, "markets": ["S"]},
            },
            "edges": [edge("big-small", "big", "small", "revenue_concentration", share=0.6)],
        }
    )
    closes = {"B": series(x), "S": series([0.2 * v for v in x])}
    cfg = {"tension_days": 60, "min_days": 10, "min_history_days": 1000, "extreme_pctile": 0.95}
    stats, _ = loop.tension(closes, g, D0 + timedelta(days=100), cfg)
    by = {s.metric: s.value for s in stats}
    assert by["beta"] == pytest.approx(0.2) and by["gap"] == pytest.approx(-0.4)


def test_deal_moves_and_quarterly_summary():
    g = graph()
    e = {x.id: x for x in g.edges}["chip-lab"]  # dated 2026-01-10, circular
    base = date(2026, 1, 9)
    closes = {
        "C": {base: 100.0, base + timedelta(days=1): 104.0, base + timedelta(days=6): 98.0},
        "L": {base: 50.0, base + timedelta(days=1): 55.0},
    }
    ms = loop.deal_moves(closes, g, e, 5)
    by = {m.coin: m for m in ms}
    assert by["C"].side == "src" and by["C"].day0 == pytest.approx(0.04) and by["C"].day_n == pytest.approx(-0.02)
    assert by["L"].side == "dst" and by["L"].day0 == pytest.approx(0.10) and by["L"].day_n is None
    assert all(m.circular for m in ms)
    summ = loop.deal_summary([(m, e.date) for m in ms])
    assert summ == {"2026Q1": {"deals": 1, "day0": pytest.approx(0.10), "day_n": None}}


def test_sell_off_betas_use_only_down_days():
    x = [0.02, -0.01, 0.02, -0.03] * 15
    y = [0.0 if r > 0 else 2 * r for r in x]  # moves only when the node falls
    g = graph()
    closes = {"L": series(x), "N": series(y)}
    b = loop.sell_off_betas(closes, g, "lab", ["N"], D0 + timedelta(days=60), 90, 10)
    assert b["N"] == pytest.approx(2.0)
