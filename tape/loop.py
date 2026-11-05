"""The three standing series. Pure: daily closes and the graph in, numbers out.

  one-asset regime      rolling co-movement between the labs basket and the
                        neocloud, chip and memory baskets, measured over all
                        days and separately over sell-off days (days the labs
                        basket fell). At an extreme against its own history it
                        alerts: "labs and their suppliers now trade as one".
  graph against market  for every edge with a measured exposure (a revenue
                        share), how much the dependent name moves with its
                        counterparty (beta) against how much the graph says it
                        should (the share). The gap either way is the claim.
  deal studies          every dated edge is an event: both names' moves at the
                        announcement and n days later, and whether the deal is
                        part of a loop.

Returns are daily, from UTC closes. Every value is descriptive: a fact about
the market now. None of it is a prediction.
"""

from __future__ import annotations

import math
from bisect import bisect_right
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, timedelta

from .graph import Edge, Graph

Closes = dict[str, dict[date, float]]  # coin -> day -> close
Returns = dict[date, float]


def returns(closes: dict[date, float]) -> Returns:
    """Day-over-day returns for consecutive days only (a gap is not a return)."""
    out: Returns = {}
    for d, c in closes.items():
        prev = closes.get(d - timedelta(days=1))
        if prev:
            out[d] = c / prev - 1
    return out


def basket(closes: Closes, coins: Sequence[str]) -> Returns:
    """Equal-weight daily return of the markets that have one that day."""
    per = [returns(closes[c]) for c in coins if c in closes]
    days = sorted({d for r in per for d in r})
    return {d: sum(r[d] for r in per if d in r) / sum(1 for r in per if d in r) for d in days}


def corr(xs: Sequence[float], ys: Sequence[float]) -> float | None:
    n = len(xs)
    if n < 3:
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys, strict=True))
    sxx = sum((x - mx) ** 2 for x in xs)
    syy = sum((y - my) ** 2 for y in ys)
    if sxx <= 0 or syy <= 0:
        return None
    return sxy / math.sqrt(sxx * syy)


def beta(ys: Sequence[float], xs: Sequence[float]) -> float | None:
    """Slope of y on x."""
    n = len(xs)
    if n < 3:
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    if sxx <= 0:
        return None
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys, strict=True)) / sxx


def _window(a: Returns, b: Returns, end: date, days: int, only=None) -> tuple[list[float], list[float]]:
    xs, ys = [], []
    for i in range(days):
        d = end - timedelta(days=i)
        if d in a and d in b and (only is None or only(d)):
            xs.append(a[d])
            ys.append(b[d])
    return xs, ys


def rolling(a: Returns, b: Returns, days: int, min_days: int, fn=corr, only=None) -> dict[date, float]:
    """fn(window of a, window of b) for every day both have, over the trailing `days`."""
    out: dict[date, float] = {}
    for d in sorted(set(a) & set(b)):
        xs, ys = _window(a, b, d, days, only)
        if len(xs) >= min_days:
            v = fn(xs, ys)
            if v is not None:
                out[d] = v
    return out


def pctile(value: float, history: Sequence[float]) -> float | None:
    """Share of `history` at or below `value`."""
    if not history:
        return None
    h = sorted(history)
    return bisect_right(h, value) / len(h)


def highest_since(series: dict[date, float], day: date) -> date | None:
    """The last day before `day` the series was at least as high; None if never."""
    v = series[day]
    for d in sorted((d for d in series if d < day), reverse=True):
        if series[d] >= v:
            return d
    return None


@dataclass(frozen=True)
class Stat:
    subject: str
    metric: str
    value: float | None


@dataclass(frozen=True)
class Extreme:
    subject: str
    kind: str  # regime_extreme | tension_extreme
    detail: dict


def regime(
    closes: Closes, baskets: dict[str, list[str]], anchor: str, day: date, cfg: dict
) -> tuple[list[Stat], list[Extreme]]:
    """Co-movement of the anchor basket with each other basket, on `day`."""
    rets = {k: basket(closes, coins) for k, coins in baskets.items()}
    a = rets.get(anchor) or {}
    stats: list[Stat] = []
    extremes: list[Extreme] = []
    for k, b in rets.items():
        if k == anchor:
            continue
        subject = f"regime:{anchor}~{k}"
        series = rolling(a, b, int(cfg["corr_days"]), int(cfg["min_days"]))
        sell = rolling(a, b, int(cfg["selloff_days"]), int(cfg["min_days"]), only=lambda d: a.get(d, 0) < 0)
        now, now_s = series.get(day), sell.get(day)
        hist = [v for d, v in series.items() if d < day]
        p = pctile(now, hist) if now is not None else None
        stats += [
            Stat(subject, "corr", now),
            Stat(subject, "corr_selloff", now_s),
            Stat(subject, "corr_pctile", p),
            Stat(subject, "history_days", float(len(hist))),
        ]
        if (
            now is not None
            and p is not None
            and len(hist) >= int(cfg["min_history_days"])
            and p >= float(cfg["extreme_pctile"])
        ):
            since = highest_since(series, day)
            extremes.append(
                Extreme(
                    subject,
                    "regime_extreme",
                    {
                        "anchor": anchor,
                        "basket": k,
                        "corr": now,
                        "corr_selloff": now_s,
                        "pctile": p,
                        "since": since.isoformat() if since else None,
                        "history_from": min(series).isoformat(),
                    },
                )
            )
    return stats, extremes


def node_returns(closes: Closes, g: Graph, key: str) -> Returns:
    return basket(closes, list(g.nodes[key].markets))


def tension(closes: Closes, g: Graph, day: date, cfg: dict) -> tuple[list[Stat], list[Extreme]]:
    """For each verified revenue-share edge between two tradable nodes: the
    dependent's beta to its counterparty against its revenue share."""
    stats: list[Stat] = []
    extremes: list[Extreme] = []
    days = int(cfg["tension_days"])
    for e in g.edges:
        if e.type != "revenue_concentration" or e.share is None:
            continue
        if not (g.tradable(e.src) and g.tradable(e.dst)):
            continue
        x = node_returns(closes, g, e.src)
        y = node_returns(closes, g, e.dst)
        betas = rolling(x, y, days, int(cfg["min_days"]), fn=lambda xs, ys: beta(ys, xs))
        b = betas.get(day)
        subject = f"edge:{e.id}"
        gaps = {d: v - e.share for d, v in betas.items()}
        gap = gaps.get(day)
        hist = [abs(v) for d, v in gaps.items() if d < day]
        p = pctile(abs(gap), hist) if gap is not None else None
        stats += [
            Stat(subject, "share", e.share),
            Stat(subject, "beta", b),
            Stat(subject, "gap", gap),
            Stat(subject, "gap_pctile", p),
        ]
        if (
            gap is not None
            and p is not None
            and len(hist) >= int(cfg["min_history_days"])
            and p >= float(cfg["extreme_pctile"])
        ):
            extremes.append(
                Extreme(
                    subject,
                    "tension_extreme",
                    {
                        "edge": e.id,
                        "src": e.src,
                        "dst": e.dst,
                        "share": e.share,
                        "beta": b,
                        "gap": gap,
                        "pctile": p,
                        "source": e.source,
                    },
                )
            )
    return stats, extremes


@dataclass(frozen=True)
class DealMove:
    edge: str
    coin: str
    side: str  # src | dst
    day0: float | None
    day_n: float | None
    circular: bool


def deal_moves(closes: Closes, g: Graph, e: Edge, n: int) -> list[DealMove]:
    """Each market of both nodes: close on the announcement day and n days
    later, each over the close the day before."""
    out = []
    circ = g.circular(e)
    before, d0, dn = e.date - timedelta(days=1), e.date, e.date + timedelta(days=n)
    for side, key in (("src", e.src), ("dst", e.dst)):
        for c in g.nodes[key].markets:
            cl = closes.get(c) or {}
            if before not in cl:
                continue
            base = cl[before]
            out.append(
                DealMove(
                    e.id,
                    c,
                    side,
                    cl[d0] / base - 1 if d0 in cl else None,
                    cl[dn] / base - 1 if dn in cl else None,
                    circ,
                )
            )
    return out


def quarter(d: date) -> str:
    return f"{d.year}Q{(d.month - 1) // 3 + 1}"


def deal_summary(moves: list[tuple[DealMove, date]]) -> dict[str, dict]:
    """By quarter: the average move of the receiving side (suppliers, landlords)
    of circular deals at announcement and n days later."""
    by_q: dict[str, list[DealMove]] = {}
    for m, d in moves:
        if m.side == "dst" and m.circular:
            by_q.setdefault(quarter(d), []).append(m)
    out = {}
    for q, ms in sorted(by_q.items()):
        d0 = [m.day0 for m in ms if m.day0 is not None]
        dn = [m.day_n for m in ms if m.day_n is not None]
        out[q] = {
            "deals": len({m.edge for m in ms}),
            "day0": sum(d0) / len(d0) if d0 else None,
            "day_n": sum(dn) / len(dn) if dn else None,
        }
    return out


def sell_off_betas(
    closes: Closes, g: Graph, key: str, coins: Sequence[str], day: date, days: int, min_days: int
) -> dict[str, float]:
    """Each market's beta to node `key` over the trailing window, on the days
    the node fell: the co-movement a graph shock applies."""
    x = node_returns(closes, g, key)
    out = {}
    for c in coins:
        y = returns(closes.get(c) or {})
        xs, ys = _window(x, y, day, days, only=lambda d: x.get(d, 0) < 0)
        if len(xs) >= min_days:
            b = beta(ys, xs)
            if b is not None:
                out[c] = max(0.0, b)
    return out
