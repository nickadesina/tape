"""Stack Engine: ranges, the stack config, estimates, conversions, gaps,
scenarios, the ledger, the indicator bench and the loaders' parsers. Pure."""

from datetime import UTC, date, datetime

import httpx
import pytest

from tape.loaders import edgar, korea, sfcompute, taiwan
from tape.model import conversions, gaps, indicators, ledger
from tape.model.core import Quantity, Range, Series, change, evaluate, load_stack, revision
from tape.model.scenarios import load_scenarios, propagate

D = date(2026, 6, 30)

# ---- ranges ----------------------------------------------------------------


def test_range_arithmetic_is_interval_arithmetic():
    a, b = Range(1, 2, 3), Range(4, 5, 6)
    assert (a + b).as_list() == [5, 7, 9]
    assert (b - a).as_list() == [1, 3, 5]
    assert (a * b).as_list() == [4, 10, 18]
    assert (b / a).as_list() == pytest.approx([4 / 3, 2.5, 6])
    assert a.min(Range(0, 2.5, 2.5)).as_list() == [0, 2, 2.5]
    with pytest.raises(ZeroDivisionError):
        a / Range(-1, 1, 2)
    with pytest.raises(ValueError):
        Range(2, 1, 3)
    assert Range.of([1, 3]).as_list() == [1, 2, 3]
    assert change(Range(10, 10, 10), Range(9, 9, 9)).mid == pytest.approx(-0.1)


# ---- the stack config ------------------------------------------------------

ENTITIES = {
    "entities": {
        "coreweave": {"name": "CoreWeave", "kind": "company", "segment": "cloud", "node": "coreweave"},
        "h100": {"name": "H100", "kind": "sku", "segment": "accelerators"},
    }
}
SOURCES = {"sources": {"rel": {"url": "https://example.com/release"}}}


def qty(key, value, period=D, kind="company", source="rel", verified=True, **kw):
    return {"key": key, "unit": "x", "period": period, "value": value, "kind": kind, "source": source,
            "verified_at": "2026-11-01" if verified else None, **kw}  # fmt: skip


def stack(qs=(), est=(), **kw):
    return load_stack(
        ENTITIES, SOURCES, {"quantities": list(qs), "estimates": list(est)}, {"coreweave": ("xyz:CRWV",)}, **kw
    )


def test_stack_loads_verified_figures_and_keeps_drafts_apart():
    st = stack([qty("coreweave.active_power", 1500), qty("coreweave.contracted_power", 3700, verified=False)])
    assert [q.key for q in st.quantities] == ["coreweave.active_power"]
    assert [q.key for q in st.drafts] == ["coreweave.contracted_power"]
    assert st.entities["coreweave"].markets == ("xyz:CRWV",)
    assert st.quantities[0].value == Range.point(1500)
    # Drafts treated as verified for a dry run.
    assert (
        len(stack([qty("coreweave.contracted_power", 3700, verified=False)], drafts_as_verified=True).quantities) == 1
    )


def test_a_hedged_figure_carries_its_range_and_url_sources_register():
    st = stack([qty("coreweave.contracted_power", 3700, range=[3650, 3750], source="https://example.com/x")])
    q = st.quantities[0]
    assert q.value.as_list() == [3650, 3700, 3750]
    assert st.sources[q.source].url == "https://example.com/x"


@pytest.mark.parametrize(
    "qs,est,msg",
    [
        ([qty("nobody.x", 1)], [], "not an entity"),
        ([qty("coreweave.x", 1, kind="estimate")], [], "kind must be"),
        ([qty("coreweave.x", 1, source="nope")], [], "source must be"),
        ([qty("coreweave.x", 5, range=[1, 2])], [], "outside its range"),
        ([qty("coreweave.x", 1), qty("coreweave.x", 2)], [], "same period twice"),
        ([], [{"key": "coreweave.y", "unit": "x", "method": "ratio", "inputs": ["coreweave.a"]}], "needs 2"),
        ([], [{"key": "coreweave.y", "unit": "x", "method": "magic", "inputs": ["coreweave.a"]}], "method must be"),
        (
            [],
            [
                {
                    "key": "coreweave.y",
                    "unit": "x",
                    "method": "judgment",
                    "period": D,
                    "range": [1, 1],
                    "basis": "b",
                    "source": "rel",
                    "verified_at": D,
                }
            ],
            "never a single point",
        ),  # fmt: skip
        ([], [{"key": "coreweave.y", "unit": "x", "method": "judgment", "range": [1, 2]}], "needs a range, a period"),
        (
            [],
            [
                {"key": "coreweave.a", "unit": "x", "method": "sum", "inputs": ["coreweave.b"]},
                {"key": "coreweave.b", "unit": "x", "method": "sum", "inputs": ["coreweave.a"]},
            ],
            "cycle",
        ),
    ],
)
def test_stack_refuses_malformed_entries(qs, est, msg):
    with pytest.raises(ValueError, match=msg):
        stack(qs, est)


def test_the_repo_stack_config_loads():
    from tape.modeljob import load_files

    f = load_files()
    assert "coreweave" in f.stack.entities
    assert f.stack.entities["coreweave"].markets  # taken from its graph node
    assert all(c.reviewed_at is None for c in f.draft_comparisons)


# ---- estimates -------------------------------------------------------------


def q(key, v, end=D, start=None, origin="edgar", kind="filed"):
    return Quantity(key, "usd", start or end, end, Range.of(v), kind, origin, source="s")


def test_estimates_compute_in_dependency_order_as_of_each_period():
    series = Series([q("coreweave.debt", 30, date(2026, 3, 31)), q("coreweave.debt", 35), q("coreweave.cash", 5)])
    st = stack(
        [qty("coreweave.active_power", 1500)],
        [
            {"key": "coreweave.gpus", "unit": "gpus", "method": "ratio",
             "inputs": ["coreweave.active_power", "coreweave.mw_per_gpu"]},
            {"key": "coreweave.net_debt", "unit": "usd", "method": "difference",
             "inputs": ["coreweave.debt", "coreweave.cash"]},
            {"key": "coreweave.mw_per_gpu", "unit": "mw", "method": "judgment", "period": D,
             "range": [0.0015, 0.002, 0.0025], "basis": "b", "source": "rel", "verified_at": D},
        ],
    )  # fmt: skip
    for x in st.quantities:
        series.add(x)
    out, missing = evaluate(series, st.estimates)
    by = {(x.key, x.period_end): x for x in out}
    # Net debt at 2026-03-31 has no cash figure yet: missing, not guessed.
    assert by[("coreweave.net_debt", D)].value == Range.point(30)
    assert any(m.key == "coreweave.net_debt" and m.period_end == date(2026, 3, 31) for m in missing)
    gpus = by[("coreweave.gpus", D)]
    assert gpus.value.as_list() == pytest.approx([600_000, 750_000, 1_000_000])
    assert (
        gpus.kind == "estimate"
        and gpus.method == "ratio"
        and gpus.inputs == ("coreweave.active_power", "coreweave.mw_per_gpu")
    )


def test_an_unverified_judgment_is_a_draft_and_its_dependents_wait():
    st = stack(
        [qty("coreweave.active_power", 1500)],
        [
            {"key": "coreweave.mw_per_gpu", "unit": "mw", "method": "judgment", "period": D, "range": [1, 2],
             "basis": "b", "source": "rel"},
            {"key": "coreweave.gpus", "unit": "gpus", "method": "ratio",
             "inputs": ["coreweave.active_power", "coreweave.mw_per_gpu"]},
        ],
    )  # fmt: skip
    assert [e.key for e in st.draft_estimates] == ["coreweave.mw_per_gpu"]
    out, missing = evaluate(Series(st.quantities), st.estimates)
    assert out == [] and missing[0].inputs == ("coreweave.mw_per_gpu",)


def test_a_config_revision_needs_a_new_reason():
    old = Quantity("coreweave.x", "mw", D, D, Range.point(1), "company", "config", "s", revision_reason=None)
    assert revision(None, old) == (True, None)
    assert revision(old, old) == (False, None)
    changed = Quantity("coreweave.x", "mw", D, D, Range.point(2), "company", "config", "s")
    with pytest.raises(ValueError, match="revision_reason"):
        revision(old, changed)
    ok = Quantity("coreweave.x", "mw", D, D, Range.point(2), "company", "config", "s", revision_reason="restated")
    assert revision(old, ok) == (True, "restated")
    with pytest.raises(ValueError):  # the same reason again is not a new reason
        revision(
            ok,
            Quantity("coreweave.x", "mw", D, D, Range.point(3), "company", "config", "s", revision_reason="restated"),
        )
    est = Quantity(
        "coreweave.y", "mw", D, D, Range(1, 2, 3), "estimate", "model", method="sum", inputs=("coreweave.x",)
    )
    assert revision(est, Quantity("coreweave.y", "mw", D, D, Range(1, 2.5, 3), "estimate", "model", method="sum",
                                  inputs=("coreweave.x",)))[1] == "recomputed from coreweave.x"  # fmt: skip


# ---- conversions and gaps ----------------------------------------------------


def test_neocloud_conversion_by_hand():
    c = conversions.CONVERSIONS["neocloud_rent"]
    x = {"price": Range.point(100.0), "shares": Range.point(500e6), "net_debt": Range.point(30e9),
         "gpus": Range(400_000, 500_000, 600_000), "rent": Range(2.0, 2.2, 2.4)}  # fmt: skip
    a = {"ev_revenue": Range(6, 8, 10), "utilization": Range(0.7, 0.8, 0.9)}
    res = conversions.run(c, x, a)
    # EV = 100 x 500M + 30B = 80B; revenue = 10B at 8x; / (500k GPUs x 8,760 h) = 2.283 per GPU-hour.
    assert res.market.mid == pytest.approx(80e9 / 8 / (500_000 * 8760))
    assert res.market.lo == pytest.approx(80e9 / 10 / (600_000 * 8760))
    assert res.market.hi == pytest.approx(80e9 / 6 / (400_000 * 8760))
    assert res.model.as_list() == pytest.approx([1.4, 1.76, 2.16])
    sens = {r["name"]: r for r in res.sensitivity}
    assert sens["ev_revenue"]["side"] == "market" and sens["ev_revenue"]["low"] > sens["ev_revenue"]["high"]
    assert sens["utilization"]["side"] == "model" and sens["utilization"]["low"] == pytest.approx(2.2 * 0.7)
    with pytest.raises(KeyError):
        conversions.run(c, {k: v for k, v in x.items() if k != "gpus"}, a)


def test_lab_and_accelerator_conversions():
    lab = conversions.run(
        conversions.CONVERSIONS["lab_revenue"],
        {"price": Range.point(500.0), "commitments_per_year": Range.point(20e9)},
        {
            "valuation_per_price": Range.point(1e9),
            "ev_revenue": Range(20, 25, 50),
            "compute_share": Range(0.5, 0.6, 0.7),
        },
    )
    assert lab.market.mid == pytest.approx(500e9 / 25) and lab.model.mid == pytest.approx(20e9 / 0.6)
    acc = conversions.run(
        conversions.CONVERSIONS["accelerator_units"],
        {"price": Range.point(200.0), "shares": Range.point(24e9), "net_debt": Range.point(-50e9),
         "asp": Range.point(30_000.0), "units_capacity": Range(5e6, 6e6, 7e6)},
        {"ev_revenue": Range.point(20.0), "accelerator_share": Range.point(0.9)},
    )  # fmt: skip
    assert acc.market.mid == pytest.approx((200 * 24e9 - 50e9) / 20 * 0.9 / 30_000)
    assert acc.model.as_list() == [5e6, 6e6, 7e6]


def test_gap_counts_consecutive_days_apart_and_flags_persistence():
    model, far, near = Range(1, 2, 3), Range(4, 5, 6), Range(2.5, 3, 4)
    g = gaps.measure(model, far, 0, persist_days=3)
    assert (g.distance, g.overlap, g.days_apart, g.flagged) == (pytest.approx(1.5), False, 1, False)
    assert gaps.measure(model, far, 2, 3).flagged
    assert gaps.measure(model, far, 2, 3, consecutive=False).days_apart == 1  # a missed day restarts the count
    assert gaps.measure(model, near, 5, 3) == gaps.Gap(pytest.approx(0.5), True, 0, False)


# ---- scenarios -------------------------------------------------------------


def test_hbm_shortfall_caps_units_and_takes_revenue():
    ents = {"entities": {k: {"name": k, "kind": "theme", "segment": "memory"} for k in ("hbm", "h100", "nvidia")}}
    raw = {
        "links": [
            {"from": "hbm.bits_supply", "to": "nvidia.units", "kind": "cap", "per": "h100.hbm_per_unit", "basis": "b"},
            {"from": "nvidia.units", "to": "nvidia.accelerator_revenue", "kind": "elasticity",
             "elasticity": [1, 1, 1], "basis": "identity"},
        ],
        "scenarios": [{"id": "s", "label": "HBM short", "shock": {"key": "hbm.bits_supply", "change": -0.2}}],
    }  # fmt: skip
    links, specs = load_scenarios(raw, lambda k: isinstance(k, str) and k.split(".")[0] in ents["entities"])
    series = Series([
        q("hbm.bits_supply", 800e6), q("h100.hbm_per_unit", 80.0), q("nvidia.units", [9e6, 9.5e6, 10e6]),
        q("nvidia.accelerator_revenue", 300e9),
    ])  # fmt: skip
    o = propagate(series, links, specs[0], D, {"nvidia": "Nvidia"})
    # 800M GB x 0.8 / 80 GB = 8M units: binds below every point of the base range.
    assert o.path[1]["shocked"] == [8e6, 8e6, 8e6]
    assert o.path[2]["shocked"][1] == pytest.approx(300e9 * 8e6 / 9.5e6)
    [risk] = o.revenue_at_risk
    assert risk["name"] == "Nvidia" and risk["mid"] == pytest.approx(300e9 * (1 - 8e6 / 9.5e6))
    assert risk["lo"] <= risk["mid"] <= risk["hi"]
    # More supply than the units need binds nothing, so nothing downstream moves.
    loose = propagate(series, links, specs[0].__class__("t", "t", "hbm.bits_supply", 0.1), D, {})
    assert loose.path[1]["shocked"] == loose.path[1]["base"]
    with pytest.raises(ValueError, match="basis"):
        load_scenarios(
            {"links": [{"from": "hbm.a", "to": "hbm.b", "kind": "elasticity", "elasticity": 1}]}, lambda k: True
        )


# ---- ledger and indicators -------------------------------------------------


def test_ledger_scores_and_calibrates():
    a = ledger.score(110, 100, 120, 100)
    b = ledger.score(90, 85, 95, 100)
    assert (a.error, a.within) == (pytest.approx(0.1), True)
    assert (b.error, b.within) == (pytest.approx(-0.1), False)
    cal = ledger.calibration([a, b])
    assert cal == {"scored": 2, "within_share": 0.5, "median_abs_error": pytest.approx(0.1)}
    assert ledger.calibration([])["scored"] == 0


def test_indicator_bench_promotes_only_out_of_sample_skill():
    xs = [((i * 37) % 11 - 5) / 5 for i in range(40)]
    good = indicators.test(xs, [0.5 * x for x in xs])
    assert good.promoted and good.out_of_sample == pytest.approx(1.0)
    noise = indicators.test(xs, [((i * 13) % 7 - 3) / 3 for i in range(40)])
    assert not noise.promoted
    assert not indicators.test(xs[:5], xs[:5]).promoted  # too short to test


# ---- loaders: parsers --------------------------------------------------------


def fact(start, end, val, accn, filed, form="10-Q"):
    f = {"end": end, "val": val, "accn": accn, "filed": filed, "form": form}
    if start:
        f["start"] = start
    return f


def test_edgar_parse_settles_derives_quarters_and_keeps_first_source():
    doc = {"facts": {"us-gaap": {
        "PaymentsToAcquirePropertyPlantAndEquipment": {"units": {"USD": [
            fact("2026-01-01", "2026-03-31", 100, "a-1", "2026-05-01"),
            fact("2026-01-01", "2026-06-30", 250, "a-2", "2026-08-01"),  # year to date: Q2 = 150
        ]}},
        "LongTermDebt": {"units": {"USD": [
            fact(None, "2026-03-31", 10, "a-1", "2026-05-01"),
            fact(None, "2026-03-31", 10, "a-2", "2026-08-01"),  # repeated as a comparative
            fact(None, "2026-06-30", 12, "a-2", "2026-08-01"),
            fact(None, "2026-06-30", 13, "a-3", "2026-09-01", "10-Q/A"),  # restated
        ]}},
        "WeightedAverageNumberOfDilutedSharesOutstanding": {"units": {"shares": [
            fact("2026-01-01", "2026-03-31", 5, "a-1", "2026-05-01"),
            fact("2026-01-01", "2026-06-30", 6, "a-2", "2026-08-01"),
        ]}},
    }}}  # fmt: skip
    metrics = {
        "capex": {"type": "duration", "unit": "usd", "concepts": ["PaymentsToAcquirePropertyPlantAndEquipment"]},
        "debt": {"type": "instant", "unit": "usd", "concepts": ["LongTermDebt"]},
        "shares_diluted": {"type": "duration", "unit": "shares", "xbrl_unit": "shares", "additive": False,
                           "concepts": ["WeightedAverageNumberOfDilutedSharesOutstanding"]},
    }  # fmt: skip
    qs, srcs = edgar.parse("coreweave", 1769628, doc, metrics, date(2025, 1, 1))
    by = {(x.key, x.period_end): x for x in qs}
    q2 = by[("coreweave.capex", date(2026, 6, 30))]
    assert q2.value.mid == 150 and q2.period_start == date(2026, 4, 1) and "derived" in q2.note
    d1 = by[("coreweave.debt", date(2026, 3, 31))]
    assert d1.source == edgar.source_id(edgar.filing_url(1769628, "a-1"))  # the first filing that said it
    d2 = by[("coreweave.debt", date(2026, 6, 30))]
    assert d2.value.mid == 13 and d2.revision_reason.startswith("restated")
    assert ("coreweave.shares_diluted", date(2026, 6, 30)) not in by  # never derived
    assert all(x.kind == "filed" and x.origin == "edgar" for x in qs)
    assert {x.url for x in srcs} >= {"https://www.sec.gov/Archives/edgar/data/1769628/a1/"}


def test_taiwan_parse_reads_roc_months_in_thousands():
    rows = [{"出表日期": "1151017", "資料年月": "11509", "公司代號": "2330", "營業收入-當月營收": "511857399", "備註": "-"},
            {"出表日期": "1151017", "資料年月": "11509", "公司代號": "9999", "營業收入-當月營收": "1"}]  # fmt: skip
    qs, srcs = taiwan.parse(rows, {"2330": "tsmc"})
    [x] = qs
    assert x.key == "tsmc.revenue_monthly" and (x.period_start, x.period_end) == (date(2026, 9, 1), date(2026, 9, 30))
    assert x.value.mid == 511_857_399_000 and x.note is None
    assert srcs[0].published == date(2026, 10, 17)


def test_korea_parse_sums_matching_hs_codes():
    xml = """<response><header><resultCode>00</resultCode></header><body><items>
      <item><year>2026.09</year><hsCd>854232</hsCd><expDlr>1,000</expDlr><impDlr>5</impDlr></item>
      <item><year>2026.09</year><hsCd>854231</hsCd><expDlr>500</expDlr><impDlr>5</impDlr></item>
      <item><year>2026.09</year><hsCd>847130</hsCd><expDlr>99</expDlr><impDlr>5</impDlr></item>
      <item><year>총계</year><hsCd>-</hsCd><expDlr>1599</expDlr><impDlr>15</impDlr></item>
    </items></body></response>"""
    assert korea.parse(xml, ["8542"]) == {(2026, 9): 1500.0}
    with pytest.raises(ValueError, match="unexpected item fields"):
        korea.parse("<r><item><foo>1</foo></item></r>", ["8542"])


WINDOWS = {
    "object": "list", "has_more": False,
    "data": [
        {"instance_sku": "sku_a", "start_at": 1777507200, "end_at": 1778112000, "duration_hours": 168,
         "total_bid_order_count": 3, "total_ask_order_count": 5,
         "best_bid": {"dollars_per_node_hour": "17.380000",
                      "allocation_schedule_delta": [{"start_at": 1777507200, "end_at": 1778112000, "node_count": 4},
                                                    {"start_at": 1778112000, "end_at": None, "node_count": 0}]},
         "best_ask": {"dollars_per_node_hour": "17.420000",
                      "allocation_schedule_delta": [{"start_at": 1777507200, "end_at": 1778112000, "node_count": 2}]}},
        {"instance_sku": "sku_b", "start_at": 1777593600, "end_at": 1778198400, "duration_hours": 168,
         "total_bid_order_count": 0, "total_ask_order_count": 1,
         "best_ask": {"dollars_per_node_hour": "16.950000",
                      "allocation_schedule_delta": [{"start_at": 1777593600, "end_at": 1778198400, "node_count": 8}]}},
    ],
}  # fmt: skip


def test_sfcompute_windows_become_per_gpu_quotes():
    now = datetime(2026, 4, 29, 1, 2, 3, tzinfo=UTC)
    rows = sfcompute.parse_windows("h100", WINDOWS["data"], now, 8)
    a, b = rows
    assert a[0] == datetime(2026, 4, 29, 1, 2, tzinfo=UTC)
    assert a[6:] == (pytest.approx(17.38 / 8), pytest.approx(17.42 / 8), 4, 2, 3, 5)
    assert b[6] is None and b[7] == pytest.approx(16.95 / 8) and b[9] == 8


def test_sfcompute_client_pages_and_sends_the_token():
    seen = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        if "starting_after" not in req.url.params:
            return httpx.Response(200, json={"data": [1], "has_more": True, "cursor": "c1"})
        return httpx.Response(200, json={"data": [2], "has_more": False})

    http = httpx.Client(transport=httpx.MockTransport(handler), headers={"authorization": "Bearer t"})
    assert sfcompute.Client("t", http).pages("windows", {"requirements": "accelerator:h100"}) == [1, 2]
    assert seen[0].url.path == "/preview/v2/orderbook/windows"
    assert seen[0].headers["authorization"] == "Bearer t" and seen[1].url.params["starting_after"] == "c1"


# ---- the stress test's monthly cadence ---------------------------------------


def test_stress_runs_on_the_first_sunday_of_the_month():
    import dataclasses

    from tape import stressjob
    from tape.settings import load

    base = load()
    s = dataclasses.replace(base, tape={**base.tape, "stress": {**base.tape["stress"], "cadence": "monthly"}})
    assert stressjob.is_stress_day(s, date(2026, 11, 1))  # first Sunday
    assert not stressjob.is_stress_day(s, date(2026, 11, 8))
    assert not stressjob.is_stress_day(s, date(2026, 11, 2))  # a Monday
    assert stressjob.stress_day(s, datetime(2026, 11, 20, 12, tzinfo=UTC)) == date(2026, 11, 1)
    assert stressjob.stress_day(s, datetime(2026, 12, 3, 12, tzinfo=UTC)) == date(2026, 11, 1)
    weekly = dataclasses.replace(base, tape={**base.tape, "stress": {**base.tape["stress"], "cadence": "weekly"}})
    assert stressjob.is_stress_day(weekly, date(2026, 11, 8))
