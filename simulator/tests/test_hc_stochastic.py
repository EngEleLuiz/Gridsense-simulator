"""stochastic.py: new estimator (C4) and legacy deprecation."""

from __future__ import annotations

import numpy as np
import pytest

from gridsense_sim.hosting_capacity import (
    AdoptionModel,
    estimate_hosting_capacity_stochastic as est,
    proportional_to_load,
    run_monte_carlo,
)

N = 8  # keep CI fast; dissertation runs use >= 60


@pytest.fixture(scope="module")
def estimate(cigre):
    return est(cigre, "cigre_lv", n_scenarios=N, seed=7, tolerance=0.05)


def test_scenario_shapes_conserve_total_pv(cigre) -> None:
    """lambda must mean the same MW as in the deterministic method."""
    nominal = proportional_to_load(cigre)
    rng = np.random.default_rng(0)
    for _ in range(20):
        frac, shape = AdoptionModel().sample(nominal, rng)
        assert 0.3 <= frac <= 1.0
        assert shape.total_base_mw == pytest.approx(nominal.total_base_mw)


def test_estimate_measures_the_network_not_a_budget(estimate) -> None:
    """C4: every scenario has a finite critical penetration located by bisection."""
    assert len(estimate.scenarios) == N
    assert estimate.n_censored == 0
    assert all(0 < s.lambda_critical < 10 for s in estimate.scenarios)
    assert all(s.binding_constraint for s in estimate.scenarios)


def test_quantile_is_consistent_with_violation_probability(estimate) -> None:
    """Identity p_viol(lambda) = F(lambda): below F^-1(alpha), fewer than alpha violate."""
    alpha = 0.25
    q = estimate.hc_lambda(alpha)
    assert estimate.violation_probability(q.point) < alpha
    assert estimate.hc_mw(alpha) == pytest.approx(q.point * estimate.total_nominal_load_mw)


def test_estimate_is_reproducible_by_seed(cigre, estimate) -> None:
    again = est(cigre, "cigre_lv", n_scenarios=N, seed=7, tolerance=0.05)
    assert again.lambdas == estimate.lambdas


def test_adoption_model_validates_inputs() -> None:
    with pytest.raises(ValueError):
        AdoptionModel(adoption_fraction=(0.0, 1.0))
    with pytest.raises(ValueError):
        AdoptionModel(size_dispersion=1.0)


def test_legacy_monte_carlo_is_deprecated(cigre) -> None:
    with pytest.warns(DeprecationWarning):
        run_monte_carlo(cigre, "cigre_lv", n_trials=2)
