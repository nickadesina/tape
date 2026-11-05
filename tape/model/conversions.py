"""Market price -> physical or economic terms (design doc, layer 2).

Each conversion is a documented formula. It takes market inputs (a
Hyperliquid close), quantity inputs (figures in the stack model) and
assumptions (stated ranges, printed on the exhibit), and returns two Ranges:
what the market implies and what the model says, in the same unit. The gap
between them is layer 3's business (gaps.py).

  neocloud_rent      neocloud valuation -> implied rent x utilization per GPU-hour,
                     against traded rents x a utilization range
  lab_revenue        lab pre-IPO valuation -> implied annual revenue at a range of
                     multiples, against the revenue its compute commitments require
  accelerator_units  accelerator-maker valuation -> implied units a year,
                     against the modelled packaging constraint

Not built: memory-stock moves -> implied price cycle. It needs contract
memory prices, which are an open question (licensed data or makers'
disclosures).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from .core import Range

HOURS_PER_YEAR = 8760.0


@dataclass(frozen=True)
class Conversion:
    id: str
    unit: str
    formula: str
    market_roles: tuple[str, ...]  # Hyperliquid closes
    input_roles: tuple[str, ...]  # quantity keys
    assumption_roles: tuple[str, ...]
    market: Callable[[dict[str, Range], dict[str, Range]], Range]
    model: Callable[[dict[str, Range], dict[str, Range]], Range]
    # Which assumptions move which side, for the sensitivity table.
    model_assumptions: tuple[str, ...] = field(default=())


def _neocloud_market(x: dict[str, Range], a: dict[str, Range]) -> Range:
    ev = x["price"] * x["shares"] + x["net_debt"]
    revenue = ev / a["ev_revenue"]
    return revenue / (x["gpus"].scale(HOURS_PER_YEAR))


def _neocloud_model(x: dict[str, Range], a: dict[str, Range]) -> Range:
    return x["rent"] * a["utilization"]


def _lab_market(x: dict[str, Range], a: dict[str, Range]) -> Range:
    valuation = x["price"] * a["valuation_per_price"]
    return valuation / a["ev_revenue"]


def _lab_model(x: dict[str, Range], a: dict[str, Range]) -> Range:
    return x["commitments_per_year"] / a["compute_share"]


def _accel_market(x: dict[str, Range], a: dict[str, Range]) -> Range:
    ev = x["price"] * x["shares"] + x["net_debt"]
    revenue = ev / a["ev_revenue"]
    return revenue * a["accelerator_share"] / x["asp"]


def _accel_model(x: dict[str, Range], a: dict[str, Range]) -> Range:
    return x["units_capacity"]


CONVERSIONS: dict[str, Conversion] = {
    c.id: c
    for c in (
        Conversion(
            id="neocloud_rent",
            unit="usd_per_gpu_hour",
            formula=(
                "market: (price x shares + net debt) / (EV/revenue) / (GPUs deployed x 8,760 h) = implied rent x "
                "utilization per GPU-hour; model: traded rent x utilization"
            ),
            market_roles=("price",),
            input_roles=("shares", "net_debt", "gpus", "rent"),
            assumption_roles=("ev_revenue", "utilization"),
            market=_neocloud_market,
            model=_neocloud_model,
            model_assumptions=("utilization",),
        ),
        Conversion(
            id="lab_revenue",
            unit="usd_per_year",
            formula=(
                "market: price x valuation per price unit / (EV/revenue) = implied annual revenue; "
                "model: compute commitments a year / compute's share of revenue"
            ),
            market_roles=("price",),
            input_roles=("commitments_per_year",),
            assumption_roles=("valuation_per_price", "ev_revenue", "compute_share"),
            market=_lab_market,
            model=_lab_model,
            model_assumptions=("compute_share",),
        ),
        Conversion(
            id="accelerator_units",
            unit="units_per_year",
            formula=(
                "market: (price x shares + net debt) / (EV/revenue) x accelerator share of revenue / ASP = implied "
                "units a year; model: units the packaging constraint allows"
            ),
            market_roles=("price",),
            input_roles=("shares", "net_debt", "asp", "units_capacity"),
            assumption_roles=("ev_revenue", "accelerator_share"),
            market=_accel_market,
            model=_accel_model,
        ),
    )
}


@dataclass(frozen=True)
class Result:
    market: Range
    model: Range
    sensitivity: list[dict]  # [{name, side, low, high}]: that side's mid with the assumption at its lo / hi


def run(conv: Conversion, inputs: dict[str, Range], assumptions: dict[str, Range]) -> Result:
    """Both sides of the comparison, and how each moves with each assumption."""
    need = set(conv.market_roles) | set(conv.input_roles)
    if missing := need - set(inputs):
        raise KeyError(f"{conv.id}: missing inputs {sorted(missing)}")
    if missing := set(conv.assumption_roles) - set(assumptions):
        raise KeyError(f"{conv.id}: missing assumptions {sorted(missing)}")
    market = conv.market(inputs, assumptions)
    model = conv.model(inputs, assumptions)
    sens = []
    for name in conv.assumption_roles:
        side = "model" if name in conv.model_assumptions else "market"
        fn = conv.model if side == "model" else conv.market
        r = assumptions[name]
        lo = fn(inputs, {**assumptions, name: Range.point(r.lo)}).mid
        hi = fn(inputs, {**assumptions, name: Range.point(r.hi)}).mid
        sens.append({"name": name, "side": side, "low": lo, "high": hi})
    return Result(market, model, sens)
