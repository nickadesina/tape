"""The forecast ledger: every published forecast, scored when the actual
figure is reported (a filed, company-stated or reported figure, never an
estimate), with calibration stats over everything scored."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from statistics import median


@dataclass(frozen=True)
class Score:
    error: float  # (forecast - actual) / actual
    within: bool  # lo <= actual <= hi


def score(value: float, lo: float, hi: float, actual: float) -> Score:
    if actual == 0:
        raise ZeroDivisionError("actual is zero")
    return Score((value - actual) / abs(actual), lo <= actual <= hi)


def calibration(scores: Sequence[Score]) -> dict:
    """Count scored, share inside the stated range, median absolute error.
    A well-calibrated ledger lands inside its ranges about as often as their
    width claims."""
    if not scores:
        return {"scored": 0, "within_share": None, "median_abs_error": None}
    return {
        "scored": len(scores),
        "within_share": sum(s.within for s in scores) / len(scores),
        "median_abs_error": median(abs(s.error) for s in scores),
    }
