"""Shared monotone bisection used by every estimator.

Contract: ``violates(0.0)`` is False (guaranteed by the baseline gate)
and ``violates`` is monotone non-decreasing in lambda (P-only PV, no
Volt-VAr -- see package docstring). Under that contract the feasible
set is ``[0, lambda*)`` and the search brackets ``lambda*`` within
``tolerance``.

Unbounded searches (review finding N1)
--------------------------------------
If doubling reaches ``max_expansions`` without a violation, the old
code returned the last feasible lambda **as if it were the hosting
capacity**. Here the outcome is flagged ``bounded=False`` and
``lambda_fail=None``; consumers must not report it as a capacity.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable


@dataclass(frozen=True)
class BisectionOutcome:
    """Result of :func:`bisect_max_feasible`.

    Attributes:
        lambda_max: Largest lambda verified feasible (conservative HC).
        lambda_fail: Smallest lambda verified infeasible, ``None`` if unbounded.
        bounded: Whether an infeasible lambda was found at all.
        evaluations: Calls made to ``violates``.
        resolved: ``True`` if ``lambda_fail - lambda_max <= tolerance``.
    """

    lambda_max: float
    lambda_fail: float | None
    bounded: bool
    evaluations: int
    resolved: bool


def bisect_max_feasible(
    violates: Callable[[float], bool],
    *,
    tolerance: float = 0.01,
    lambda_start: float = 1.0,
    max_expansions: int = 20,
    max_bisections: int = 60,
) -> BisectionOutcome:
    """Bracket the feasibility threshold of a monotone predicate.

    Args:
        violates: ``lambda -> True if infeasible``.
        tolerance: Bracket width at which to stop (lambda units).
        lambda_start: First upper candidate.
        max_expansions: Cap on doublings while searching an infeasible point.
        max_bisections: Cap on halvings once bracketed.
    """
    if tolerance <= 0:
        raise ValueError("tolerance must be > 0.")
    if lambda_start <= 0:
        raise ValueError("lambda_start must be > 0.")

    evaluations = 0

    def _v(lam: float) -> bool:
        nonlocal evaluations
        evaluations += 1
        return violates(lam)

    lo, hi = 0.0, lambda_start
    expansions = 0
    while not _v(hi):
        lo = hi
        expansions += 1
        if expansions >= max_expansions:
            return BisectionOutcome(lo, None, False, evaluations, False)
        hi *= 2.0

    bisections = 0
    while (hi - lo) > tolerance and bisections < max_bisections:
        mid = 0.5 * (lo + hi)
        if _v(mid):
            hi = mid
        else:
            lo = mid
        bisections += 1

    return BisectionOutcome(lo, hi, True, evaluations, (hi - lo) <= tolerance)
