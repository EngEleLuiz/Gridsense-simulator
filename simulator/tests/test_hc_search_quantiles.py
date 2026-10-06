"""search.py (N1) and quantiles.py."""

from __future__ import annotations

import numpy as np
import pytest

from gridsense_sim.hosting_capacity import bisect_max_feasible, quantile_with_ci
from gridsense_sim.hosting_capacity.quantiles import empirical_quantile


def test_bisection_brackets_threshold() -> None:
    out = bisect_max_feasible(lambda x: x > 1.3, tolerance=0.01)
    assert out.bounded and out.resolved
    assert out.lambda_max <= 1.3 < out.lambda_fail
    assert out.lambda_fail - out.lambda_max <= 0.01


def test_unbounded_search_is_flagged_not_reported_as_capacity() -> None:
    """N1 regression: hitting the expansion cap used to return lo as the HC."""
    out = bisect_max_feasible(lambda x: False, max_expansions=5)
    assert out.bounded is False
    assert out.lambda_fail is None


def test_bisection_rejects_non_positive_tolerance() -> None:
    with pytest.raises(ValueError):
        bisect_max_feasible(lambda x: x > 1, tolerance=0.0)


def test_empirical_quantile_is_inverse_ecdf() -> None:
    xs = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0]
    assert empirical_quantile(xs, 0.10) == 1.0
    assert empirical_quantile(xs, 0.11) == 2.0
    assert empirical_quantile(xs, 0.50) == 5.0


def test_quantile_ci_contains_true_quantile_for_uniform_samples() -> None:
    rng = np.random.default_rng(0)
    hits = 0
    for _ in range(200):
        q = quantile_with_ci(rng.uniform(0, 1, 120), 0.10, 0.95)
        hits += q.low <= 0.10 <= q.high
        assert q.achieved_coverage >= 0.95
    assert hits / 200 >= 0.92  # conservative interval; allow MC noise


def test_quantile_ci_reports_missing_lower_bound_when_n_too_small() -> None:
    q = quantile_with_ci([1.0, 2.0, 3.0, 4.0, 5.0], 0.10, 0.95)
    assert q.low is None and q.achieved_coverage is None
