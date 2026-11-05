"""The leading-indicator test bench (design doc, layer 3).

A candidate (customs data, rents, Hyperliquid overnight moves, interconnection
queue changes) is promoted only if it beats a naive baseline outside the
period it was fitted on. The score is the one the reopen study uses
(research/reopen.py `score`): the share of the target's movement explained,
against the naive forecast of no change,

    skill = 1 - sum((actual - forecast)^2) / sum(actual^2)

so 0 is no better than "nothing happens" and 1 is perfect. A candidate's
forecast is a one-variable least-squares fit of the target on the indicator,
fitted on the first part of the sample and scored on the rest.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass


def skill(actual: Sequence[float], forecast: Sequence[float]) -> float | None:
    sst = sum(a * a for a in actual)
    if not actual or sst == 0:
        return None
    return 1 - sum((a - f) ** 2 for a, f in zip(actual, forecast, strict=True)) / sst


def fit(xs: Sequence[float], ys: Sequence[float]) -> tuple[float, float]:
    """Least squares y = a + b x."""
    n = len(xs)
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    b = sum((x - mx) * (y - my) for x, y in zip(xs, ys, strict=True)) / sxx if sxx else 0.0
    return my - b * mx, b


@dataclass(frozen=True)
class Verdict:
    n_fit: int
    n_test: int
    coef: tuple[float, float]
    in_sample: float | None
    out_of_sample: float | None
    baseline: float | None  # the baseline's out-of-sample skill (0 for "no change")
    promoted: bool


def test(
    indicator: Sequence[float],
    target: Sequence[float],
    fit_share: float = 0.6,
    baseline: Sequence[float] | None = None,
    min_test: int = 8,
    min_gain: float = 0.02,
) -> Verdict:
    """Fit on the first `fit_share` of the paired sample, score on the rest.
    `baseline` is an alternative forecast of the target (aligned with it), for
    candidates that must beat something better than "no change". Promotion
    needs out-of-sample skill at least `min_gain` above the baseline's: a
    hair above zero is noise."""
    if len(indicator) != len(target):
        raise ValueError("indicator and target must be aligned")
    k = int(len(target) * fit_share)
    if k < 3 or len(target) - k < min_test:
        return Verdict(k, len(target) - k, (0.0, 0.0), None, None, None, False)
    a, b = fit(indicator[:k], target[:k])
    pred = [a + b * x for x in indicator]
    ins = skill(target[:k], pred[:k])
    oos = skill(target[k:], pred[k:])
    base = skill(target[k:], baseline[k:]) if baseline is not None else 0.0
    promoted = oos is not None and base is not None and oos >= base + min_gain
    return Verdict(k, len(target) - k, (a, b), ins, oos, base, promoted)
