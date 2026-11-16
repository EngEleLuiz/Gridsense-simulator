"""Statistics for comparing hosting capacities.

What each number can and cannot say
-----------------------------------
**Measurement resolution.** Every capacity is the lower end of a bisection
bracket of width ``tolerance`` (0.01 in the protocols). Two capacities that
differ by no more than that are a tie: :func:`relative_effect` reports the
difference and whether it is distinguishable.

**Weekly distribution (QSTS).** The annual capacity is the *minimum* of the
weekly capacities. A minimum hangs on one week, and resampling does not fix
that: the bootstrap is not consistent for an extreme order statistic. So the
minimum is reported together with how much it depends on the critical week
(the gap to the second-lowest week), and resampling is used only for
quantiles (p10, p50), with a circular block bootstrap because consecutive
weeks share weather and season. These intervals describe variability *within*
the simulated year; they say nothing about other weather years.

**Across networks.** With a handful of networks, the per-network relative
differences, their median and their range are the result. A Wilcoxon
signed-rank test is reported for completeness with its sample size: with
``n = 7`` the smallest attainable two-sided p-value is 2 / 2**7 = 0.016, and
with ``n = 3`` it is 0.25, so a non-significant outcome is no evidence of
equality.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from typing import Mapping, Sequence

import numpy as np


@dataclass(frozen=True)
class Effect:
    """Difference between two capacities, ``a`` relative to the reference ``b``."""

    a: float
    b: float
    difference: float              # a - b, in lambda
    relative: float                # (a - b) / b
    resolution: float              # the larger of the two search tolerances
    distinguishable: bool          # |a - b| > resolution

    def to_dict(self) -> dict:
        return asdict(self)


def relative_effect(a: float, b: float, tolerance_a: float = 0.01, tolerance_b: float | None = None) -> Effect:
    """Effect of ``a`` relative to ``b`` (``b`` is the reference, e.g. the baseline run).

    Each capacity lies in ``[value, value + tolerance)``, so the difference of
    the true thresholds lies within ``tolerance`` of the reported difference;
    anything at or below that is reported as not distinguishable.
    """
    if not (math.isfinite(a) and math.isfinite(b)):
        raise ValueError("Both capacities must be finite (censored searches have no capacity).")
    if b <= 0:
        raise ValueError("The reference capacity must be positive.")
    tol = max(tolerance_a, tolerance_a if tolerance_b is None else tolerance_b)
    diff = a - b
    return Effect(a, b, diff, diff / b, tol, abs(diff) > tol + 1e-12)


@dataclass(frozen=True)
class WeeklySummary:
    """Distribution of the weekly capacities of one QSTS run."""

    n_weeks: int                   # weeks with an exceedance (a capacity)
    n_censored: int                # weeks without one (only a lower bound)
    minimum: float
    second_lowest: float | None
    gap_to_second: float | None    # second_lowest - minimum: how much the result hangs on one week
    relative_gap: float | None     # gap / minimum
    critical_week: int             # position in the sequence passed in
    p10: float
    p50: float
    p90: float

    def to_dict(self) -> dict:
        return asdict(self)


def weekly_summary(weekly_lambdas: Sequence[float], n_censored: int = 0) -> WeeklySummary:
    """Summary of the weekly capacities; ``weekly_lambdas`` holds only weeks with an exceedance."""
    x = np.asarray(weekly_lambdas, dtype=float)
    if x.size == 0 or not np.all(np.isfinite(x)):
        raise ValueError("Need at least one finite weekly capacity.")
    order = np.sort(x)
    second = float(order[1]) if x.size > 1 else None
    gap = None if second is None else second - float(order[0])
    return WeeklySummary(
        n_weeks=int(x.size), n_censored=int(n_censored), minimum=float(order[0]),
        second_lowest=second, gap_to_second=gap,
        relative_gap=None if gap is None or order[0] <= 0 else gap / float(order[0]),
        critical_week=int(np.argmin(x)),
        p10=float(np.percentile(x, 10)), p50=float(np.percentile(x, 50)), p90=float(np.percentile(x, 90)),
    )


def block_bootstrap_quantile(weekly_lambdas: Sequence[float], q: float = 0.10, *, block: int = 4,
                             n_boot: int = 2000, confidence: float = 0.95, seed: int = 0) -> dict:
    """Percentile interval of a quantile of the weekly capacities (circular block bootstrap).

    Args:
        weekly_lambdas: Weekly capacities in chronological order.
        q: Quantile in (0, 1). The minimum is deliberately not offered (see module docstring).
        block: Block length in weeks (4 keeps roughly one month of autocorrelation together).
        n_boot, confidence, seed: Resampling controls; the seed makes the interval reproducible.

    Returns:
        ``{"q", "point", "low", "high", "confidence", "n", "block", "n_boot"}``.
    """
    x = np.asarray(weekly_lambdas, dtype=float)
    n = x.size
    if not 0.0 < q < 1.0:
        raise ValueError("q must be in (0, 1); the bootstrap is not valid for the minimum.")
    if n < 8:
        raise ValueError("Need at least 8 weekly capacities for a block bootstrap.")
    if not 1 <= block <= n // 2:
        raise ValueError("block must be between 1 and half the number of weeks.")
    rng = np.random.default_rng(seed)
    n_blocks = math.ceil(n / block)
    starts = rng.integers(0, n, size=(n_boot, n_blocks))
    idx = (starts[:, :, None] + np.arange(block)[None, None, :]) % n
    samples = x[idx.reshape(n_boot, -1)[:, :n]]
    stats = np.percentile(samples, 100 * q, axis=1)
    half = (1.0 - confidence) / 2.0
    return {"q": q, "point": float(np.percentile(x, 100 * q)),
            "low": float(np.quantile(stats, half)), "high": float(np.quantile(stats, 1.0 - half)),
            "confidence": confidence, "n": int(n), "block": block, "n_boot": n_boot}


@dataclass(frozen=True)
class PairedSummary:
    """Paired comparison of two conditions across networks (``a`` relative to ``b``)."""

    n: int
    relative: dict                 # network -> (a - b) / b
    median_relative: float
    min_relative: float
    max_relative: float
    n_positive: int
    n_negative: int
    n_ties: int                    # pairs not distinguishable at the search resolution
    wilcoxon_p: float | None       # exact two-sided; None when undefined (n < 1 non-tied pair)
    smallest_attainable_p: float | None

    def to_dict(self) -> dict:
        return asdict(self)


def paired_summary(a: Mapping[str, float], b: Mapping[str, float], tolerance: float = 0.01) -> PairedSummary:
    """Compare condition ``a`` with reference ``b`` on the networks they share."""
    keys = sorted(set(a) & set(b))
    if not keys:
        raise ValueError("No network in common.")
    eff = {k: relative_effect(float(a[k]), float(b[k]), tolerance) for k in keys}
    rel = np.array([eff[k].relative for k in keys])
    clear = [k for k in keys if eff[k].distinguishable]
    diffs = np.array([eff[k].difference for k in clear])
    p = attainable = None
    if len(clear) >= 1:
        from scipy.stats import wilcoxon

        attainable = 2.0 / 2 ** len(clear)
        if len(clear) >= 2 or diffs.size == 1:
            try:
                p = float(wilcoxon(diffs, alternative="two-sided", method="exact").pvalue)
            except ValueError:
                p = None
    return PairedSummary(
        n=len(keys), relative={k: eff[k].relative for k in keys},
        median_relative=float(np.median(rel)), min_relative=float(rel.min()), max_relative=float(rel.max()),
        n_positive=int(sum(eff[k].difference > 0 for k in clear)),
        n_negative=int(sum(eff[k].difference < 0 for k in clear)),
        n_ties=len(keys) - len(clear), wilcoxon_p=p, smallest_attainable_p=attainable,
    )


def resolution_bias(capacity_by_block_minutes: Mapping[int, float], reference_minutes: int,
                    tolerance: float = 0.01) -> dict[int, Effect]:
    """Relative bias of a capacity computed on block-averaged data, ``b(w)``.

    ``capacity_by_block_minutes`` maps a block length ``w`` (minutes) to the
    capacity obtained on the series averaged over ``w``; the entry at
    ``reference_minutes`` (the criterion's own reading length) is the reference::

        b(w) = (lambda(w) - lambda(ref)) / lambda(ref)

    A positive value means that coarser data overstate the capacity.
    """
    if reference_minutes not in capacity_by_block_minutes:
        raise ValueError(f"No capacity at the reference resolution ({reference_minutes} min).")
    ref = float(capacity_by_block_minutes[reference_minutes])
    return {int(w): relative_effect(float(v), ref, tolerance)
            for w, v in sorted(capacity_by_block_minutes.items()) if int(w) != reference_minutes}


__all__ = [
    "Effect", "PairedSummary", "WeeklySummary", "block_bootstrap_quantile", "paired_summary",
    "relative_effect", "resolution_bias", "weekly_summary",
]
