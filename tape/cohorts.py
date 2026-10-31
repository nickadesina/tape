"""Cohorts: whales, mid, small, fresh, bots.

COT splits futures positions by who holds them (commercials, managed money,
small speculators). The chain gives size and age instead, which is what those
categories were always a proxy for. All functions here are pure.
"""

from __future__ import annotations

from collections import defaultdict, deque
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta
from statistics import median

WHALE = "whale"
MID = "mid"
SMALL = "small"
BOT = "bot"
COHORTS = (WHALE, MID, SMALL, BOT)


@dataclass(frozen=True)
class CohortRules:
    whale_count: int = 50
    mid_floor_usd: float = 25_000
    fresh_days: int = 30
    bot_fills_per_day: float = 200
    bot_median_hold_s: float = 600

    @classmethod
    def from_config(cls, c: dict) -> CohortRules:
        return cls(
            whale_count=int(c["whale_count"]),
            mid_floor_usd=float(c["mid_floor_usd"]),
            fresh_days=int(c["fresh_days"]),
            bot_fills_per_day=float(c["bot_fills_per_day"]),
            bot_median_hold_s=float(c["bot_median_hold_s"]),
        )


@dataclass(frozen=True)
class Position:
    address: str
    coin: str
    size: float  # signed, coin units


def gross_by_address(positions: Iterable[Position], marks: dict[str, float]) -> dict[str, float]:
    out: dict[str, float] = defaultdict(float)
    for p in positions:
        out[p.address] += abs(p.size) * marks.get(p.coin, 0.0)
    return dict(out)


def rank_whales(gross: dict[str, float], bots: set[str], n: int) -> list[str]:
    """Top n addresses by gross notional across the complex, bots excluded.

    Ties break on address so the ranking is reproducible from the data.
    """
    eligible = [(g, a) for a, g in gross.items() if a not in bots and g > 0]
    eligible.sort(key=lambda x: (-x[0], x[1]))
    return [a for _, a in eligible[:n]]


def cohort_of(address: str, gross_usd: float, whales: set[str], bots: set[str], rules: CohortRules) -> str:
    if address in bots:
        return BOT
    if address in whales:
        return WHALE
    return MID if gross_usd >= rules.mid_floor_usd else SMALL


def is_fresh(first_fill: datetime | None, complete: bool | None, at: datetime, rules: CohortRules) -> bool:
    """Fresh: first fill in the complex under `fresh_days` days before `at`.

    An address whose history was truncated (Hyperliquid keeps 10,000 fills) and
    whose earliest visible complex fill is recent cannot be dated, so it is not
    counted fresh. The overlay understates rather than invents.
    """
    if first_fill is None or not complete:
        return False
    return at - first_fill < timedelta(days=rules.fresh_days)


@dataclass(frozen=True)
class SideFill:
    """One address's side of a fill: signed size (+ bought, - sold) at a time."""

    coin: str
    time_s: float
    delta: float


def median_hold_seconds(fills: Iterable[SideFill]) -> float | None:
    """Size-weighted median holding time, matching closes to opens FIFO per coin.

    Reductions larger than the lots opened inside the window close a position
    opened before it; that part has no known open time and is ignored.
    Returns None when nothing in the window was both opened and closed.
    """
    lots: dict[str, deque[list[float]]] = defaultdict(deque)  # coin -> [time, signed size]
    holds: list[tuple[float, float]] = []  # (seconds, size)
    for f in sorted(fills, key=lambda x: x.time_s):
        q = lots[f.coin]
        remaining = f.delta
        while remaining and q and (q[0][1] > 0) != (remaining > 0):
            lot = q[0]
            take = min(abs(lot[1]), abs(remaining))
            holds.append((f.time_s - lot[0], take))
            lot[1] += take if lot[1] < 0 else -take
            remaining += take if remaining < 0 else -take
            if abs(lot[1]) < 1e-12:
                q.popleft()
            if abs(remaining) < 1e-12:
                remaining = 0.0
        if remaining:
            q.append([f.time_s, remaining])
    if not holds:
        return None
    holds.sort()
    total = sum(s for _, s in holds)
    acc = 0.0
    for secs, size in holds:
        acc += size
        if acc >= total / 2:
            return secs
    return holds[-1][0]


def classify_bot(n_fills: int, days: float, hold_s: float | None, rules: CohortRules) -> tuple[float, bool]:
    per_day = n_fills / days if days > 0 else float(n_fills)
    bot = per_day > rules.bot_fills_per_day and hold_s is not None and hold_s < rules.bot_median_hold_s
    return per_day, bot


def simple_median(xs: list[float]) -> float | None:
    return median(xs) if xs else None
