"""Event rules. Pure: each returns the events an observation crosses.

Dedupe keys decide how often a condition can alert. Position events and
slow-moving market conditions alert at most once per market (and address)
per ET day; liquidations are bursts and alert once per market per hour.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

WHALE_FLIP = "whale_flip"
WHALE_ENTRY = "whale_entry"
WHALE_NET_MOVE = "whale_net_move"
FUNDING = "funding"
PREMIUM = "premium"
LIQUIDATIONS = "liquidations"
# The standing series (tape/loop.py). Their subject is not a market: a basket
# pair (regime:labs~neoclouds) or a graph edge (edge:<id>).
REGIME_EXTREME = "regime_extreme"
TENSION_EXTREME = "tension_extreme"
DEAL_STUDY = "deal_study"
STANDING = {REGIME_EXTREME, TENSION_EXTREME, DEAL_STUDY}


@dataclass(frozen=True)
class EventRules:
    whale_flip_min_usd: float = 250_000
    whale_entry_min_usd: float = 500_000
    whale_net_move: float = 0.05
    funding_hourly: float = 0.0005
    premium_bps: float = 50
    liquidation_share: float = 0.03

    @classmethod
    def from_config(cls, c: dict) -> EventRules:
        return cls(**{k: float(c[k]) for k in cls.__dataclass_fields__})


@dataclass(frozen=True)
class Event:
    time: datetime
    coin: str
    kind: str
    dedupe_key: str
    address: str | None = None
    size_usd: float | None = None
    detail: dict[str, Any] = field(default_factory=dict)


def _sign(x: float) -> int:
    return (x > 0) - (x < 0)


def position_events(
    *,
    time: datetime,
    et_day: str,
    coin: str,
    address: str,
    prev_size: float,
    new_size: float,
    mark: float,
    is_whale: bool,
    rules: EventRules,
) -> list[Event]:
    """Whale flip and whale entry for one address on one market.

    Callers pass only addresses whose previous size is known (they were in the
    sweep before this observation); an address seen for the first time has an
    unknown starting position and would otherwise look like an entry.
    """
    out: list[Event] = []
    new_usd = abs(new_size) * mark
    prev_usd = abs(prev_size) * mark
    detail = {"prev_size": prev_size, "new_size": new_size, "mark": mark}
    if is_whale:
        ps, ns = _sign(prev_size), _sign(new_size)
        if ps and ns and ps != ns and new_usd > rules.whale_flip_min_usd:
            side = "long" if ns > 0 else "short"
            out.append(
                Event(
                    time,
                    coin,
                    WHALE_FLIP,
                    f"{WHALE_FLIP}:{coin}:{address}:{et_day}",
                    address,
                    new_usd,
                    {**detail, "new_side": side},
                )
            )
    elif new_usd > rules.whale_entry_min_usd and prev_usd <= rules.whale_entry_min_usd:
        side = "long" if new_size > 0 else "short"
        out.append(
            Event(
                time,
                coin,
                WHALE_ENTRY,
                f"{WHALE_ENTRY}:{coin}:{address}:{et_day}",
                address,
                new_usd,
                {**detail, "side": side},
            )
        )
    return out


def market_events(
    *,
    time: datetime,
    hour_key: str,
    et_day: str,
    coin: str,
    values: dict[str, float | None],
    rules: EventRules,
) -> list[Event]:
    """Threshold events from one market's metrics for one hour.

    `values` maps metric name to value for the market (see compute.METRICS).
    """
    out: list[Event] = []
    cov = values.get("coverage")
    change = values.get("whale_net_change")
    if change is not None and abs(change) > rules.whale_net_move:
        out.append(
            Event(
                time,
                coin,
                WHALE_NET_MOVE,
                f"{WHALE_NET_MOVE}:{coin}:{et_day}",
                detail={"change": change, "whale_net": values.get("whale_net"), "coverage": cov},
            )
        )
    funding = values.get("funding")
    if funding is not None and abs(funding) > rules.funding_hourly:
        out.append(
            Event(
                time,
                coin,
                FUNDING,
                f"{FUNDING}:{coin}:{et_day}",
                detail={"funding": funding, "funding_ann": values.get("funding_ann")},
            )
        )
    premium = values.get("premium_bps")
    if premium is not None and abs(premium) > rules.premium_bps:
        out.append(Event(time, coin, PREMIUM, f"{PREMIUM}:{coin}:{et_day}", detail={"premium_bps": premium}))
    liq = values.get("liq_share")
    if liq is not None and liq > rules.liquidation_share:
        oi_usd = values.get("oi_usd") or 0.0
        out.append(
            Event(
                time,
                coin,
                LIQUIDATIONS,
                f"{LIQUIDATIONS}:{coin}:{hour_key}",
                size_usd=liq * oi_usd,
                detail={"liq_share": liq, "oi_usd": oi_usd},
            )
        )
    return out


def describe(kind: str, coin: str, size_usd: float | None, detail: dict[str, Any]) -> str:
    """One line for the alert and the events page. Never names a person."""

    def usd(x: float | None) -> str:
        if x is None:
            return "?"
        return f"${x / 1e6:.2f}M" if abs(x) >= 1e6 else f"${x / 1e3:.0f}k"

    if kind == WHALE_FLIP:
        return f"Whale flipped {detail.get('new_side')} {coin}, {usd(size_usd)} on the new side"
    if kind == WHALE_ENTRY:
        return f"New wallet opened {usd(size_usd)} {detail.get('side')} {coin}"
    if kind == WHALE_NET_MOVE:
        return f"Whale net on {coin} moved {detail['change'] * 100:+.1f}% of OI in 24h"
    if kind == FUNDING:
        return f"{coin} funding {detail['funding'] * 100:+.4f}%/h ({(detail.get('funding_ann') or 0) * 100:+.0f}% annualized)"
    if kind == PREMIUM:
        return f"{coin} mark {detail['premium_bps']:+.0f} bps to oracle"
    if kind == LIQUIDATIONS:
        return f"{usd(size_usd)} liquidated on {coin} in one hour, {detail['liq_share'] * 100:.1f}% of OI"
    if kind == REGIME_EXTREME:
        since = detail.get("since")
        when = (
            f"its highest since {since}"
            if since
            else f"its highest since the series began ({detail.get('history_from')})"
        )
        return f"Labs and {detail.get('basket')} now trade as one: 30-day co-movement {detail['corr']:.2f}, {when}"
    if kind == TENSION_EXTREME:
        return (
            f"{detail.get('dst_name', detail.get('dst'))} gets ~{detail['share'] * 100:.0f}% of revenue from "
            f"{detail.get('src_name', detail.get('src'))}, yet moves {detail['beta'] * 100:.0f}% when it does"
        )
    if kind == DEAL_STUDY:

        def pc(x):
            return "n/a" if x is None else f"{x * 100:+.1f}%"

        loop_txt = "circular deal" if detail.get("circular") else "deal"
        return (
            f"{detail.get('src_name')} → {detail.get('dst_name')} {loop_txt} ({detail.get('date')}): "
            f"{detail.get('dst_name')} {pc(detail.get('dst_day0'))} on the day, {pc(detail.get('dst_day_n'))} "
            f"after {detail.get('days')} days"
        )
    return f"{kind} {coin}"
