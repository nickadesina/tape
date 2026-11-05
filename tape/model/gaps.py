"""The reconciliation gap: the model's range against the market-implied range.

For each node: the distance between the two middles as a share of the
model's, whether the ranges overlap, and how many consecutive days they have
not. A gap is flagged when the ranges have not overlapped for
`persist_days` days in a row (stack.gap_persist_days in config/tape.yaml).
"""

from __future__ import annotations

from dataclasses import dataclass

from .core import Range


@dataclass(frozen=True)
class Gap:
    distance: float  # (market mid - model mid) / model mid
    overlap: bool
    days_apart: int
    flagged: bool


def measure(model: Range, market: Range, prev_days_apart: int, persist_days: int, consecutive: bool = True) -> Gap:
    """`prev_days_apart` is the last stored day's count; `consecutive` says
    whether that day was the day before (a missed day restarts the count)."""
    if model.mid == 0:
        raise ZeroDivisionError("model middle is zero")
    overlap = model.overlaps(market)
    days = 0 if overlap else (prev_days_apart if consecutive else 0) + 1
    return Gap((market.mid - model.mid) / model.mid, overlap, days, days >= persist_days)
