"""Order-book depth. Pure: books in, numbers out.

Hyperliquid's l2Book sends at most 20 levels a side. At full precision that
often reaches well under 2% from mid on a thin HIP-3 market, so the collector
subscribes to the same book at coarser aggregations too (nSigFigs 3 and 2) and
stitches them: the finest view up to where it stops, then each coarser view
beyond that. A view is ([bids], [asks]) with levels [px, sz], bids high to
low, asks low to high.

Distances are fractions of mid: 0.05 is 5% from mid.
"""

from __future__ import annotations

from bisect import bisect_left
from collections.abc import Sequence

BANDS = (0.02, 0.05, 0.10)
BID, ASK = 0, 1

Level = Sequence[float]  # [px, sz]
View = Sequence[Sequence[Level]]  # (bids, asks)


def mid_of(view: View) -> float | None:
    bids, asks = view
    if not bids or not asks:
        return None
    return (float(bids[0][0]) + float(asks[0][0])) / 2


def _dist(px: float, mid: float, side: int) -> float:
    return (mid - px) / mid if side == BID else (px - mid) / mid


def profile(views: Sequence[View], mid: float, side: int) -> list[tuple[float, float]]:
    """Cumulative resting size (coin units) by distance from mid on one side,
    stitched from the views finest first: [(distance, cumulative size), ...],
    distances increasing."""
    out: list[tuple[float, float]] = []
    reach = -1.0
    cum = 0.0
    for v in views:
        levels = v[side] if v else []
        last = reach
        for px, sz, *_ in levels:
            d = max(0.0, _dist(float(px), mid, side))
            if d <= reach:
                continue
            cum += float(sz)
            out.append((d, cum))
            last = max(last, d)
        reach = last
    return out


def reach_of(prof: list[tuple[float, float]]) -> float:
    return prof[-1][0] if prof else 0.0


def size_within(prof: list[tuple[float, float]], dist: float) -> float:
    """Cumulative coin size resting within `dist` of mid."""
    i = bisect_left([d for d, _ in prof], dist + 1e-12)
    return prof[i - 1][1] if i else 0.0


def bands_usd(views: Sequence[View], bands: Sequence[float] = BANDS) -> dict | None:
    """{mid, bid2, bid5, bid10, ask2, ask5, ask10, reach_bid, reach_ask} in USD,
    or None if the finest view has no two-sided book."""
    mid = mid_of(views[0]) if views else None
    if not mid:
        return None
    out: dict[str, float] = {"mid": mid}
    for side, name in ((BID, "bid"), (ASK, "ask")):
        prof = profile(views, mid, side)
        out[f"reach_{name}"] = reach_of(prof)
        for b in bands:
            out[f"{name}{round(b * 100)}"] = size_within(prof, b) * mid
    return out


def walk(prof: list[tuple[float, float]], qty: float) -> tuple[float, float]:
    """Consume `qty` coin units from one side of a book. Returns (distance the
    price reaches, quantity left unabsorbed past the book's reach)."""
    if qty <= 0:
        return 0.0, 0.0
    for d, cum in prof:
        if cum >= qty:
            return d, 0.0
    if not prof:
        return 0.0, qty
    return prof[-1][0], qty - prof[-1][1]


def scale_profile(prof: list[tuple[float, float]], k: float) -> list[tuple[float, float]]:
    return [(d, c * k) for d, c in prof]


def parse_ws_book(data: dict) -> tuple[list[list[float]], list[list[float]]]:
    """A WsBook's levels as ([[px, sz], ...], [[px, sz], ...])."""
    levels = data.get("levels") or [[], []]
    return (
        [[float(x["px"]), float(x["sz"])] for x in levels[0]],
        [[float(x["px"]), float(x["sz"])] for x in levels[1]],
    )
