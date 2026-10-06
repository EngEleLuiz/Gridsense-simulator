"""Distribution-free quantile estimates from order statistics.

For i.i.d. samples X_1..X_n and the true q-quantile x_q, the number
of samples below x_q is B ~ Binomial(n, q). Hence

    P(X_(l) <= x_q < X_(u)) = P(l <= B <= u - 1),

which gives an exact (conservative) confidence interval without any
distributional assumption -- the right tool for the Monte Carlo
convergence section (dissertation sec. 3.8), replacing ad-hoc
"stop when p50 stabilizes" rules.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Sequence

from scipy.stats import binom


@dataclass(frozen=True)
class QuantileEstimate:
    """Point estimate and order-statistic confidence interval.

    ``low``/``high`` are ``None`` when ``n`` is too small for that side
    at the requested confidence. ``achieved_coverage`` is the exact
    binomial coverage of the returned interval (>= requested when both
    bounds exist).
    """

    q: float
    point: float
    low: float | None
    high: float | None
    confidence: float
    achieved_coverage: float | None
    n: int


def empirical_quantile(sorted_values: Sequence[float], q: float) -> float:
    """Inverse-ECDF quantile (type 1): ``X_(ceil(q n))``, 1-based."""
    n = len(sorted_values)
    if n == 0:
        raise ValueError("No samples.")
    if not 0.0 < q < 1.0:
        raise ValueError("q must be in (0, 1).")
    k = max(1, math.ceil(q * n))
    return float(sorted_values[k - 1])


def quantile_with_ci(values: Sequence[float], q: float, confidence: float = 0.95) -> QuantileEstimate:
    """Estimate the q-quantile of ``values`` with a distribution-free CI."""
    if not 0.0 < confidence < 1.0:
        raise ValueError("confidence must be in (0, 1).")
    xs = sorted(float(v) for v in values)
    n = len(xs)
    point = empirical_quantile(xs, q)
    delta = 1.0 - confidence

    def cdf(k: int) -> float:  # P(B <= k)
        return float(binom.cdf(k, n, q))

    lower_rank = None
    for k in range(1, n + 1):
        if cdf(k - 1) <= delta / 2:
            lower_rank = k
        else:
            break
    upper_rank = None
    for k in range(1, n + 1):
        if cdf(k - 1) >= 1 - delta / 2:
            upper_rank = k
            break

    low = xs[lower_rank - 1] if lower_rank is not None else None
    high = xs[upper_rank - 1] if upper_rank is not None else None
    coverage = None
    if lower_rank is not None and upper_rank is not None:
        coverage = cdf(upper_rank - 1) - cdf(lower_rank - 1)
    return QuantileEstimate(q, point, low, high, confidence, coverage, n)
