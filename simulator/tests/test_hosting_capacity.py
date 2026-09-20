"""Tests for the hosting_capacity package: limits, violations,
allocation, and the three methodologies (deterministic, stochastic,
QSTS).

Run with: pytest simulator/tests/test_hosting_capacity.py -v
"""

from __future__ import annotations

import pandapower.networks as pn
import pytest

from gridsense_sim.hosting_capacity import (
    limits_for,
    proportional_to_load,
    check_violations,
    find_hosting_capacity_deterministic,
    find_hosting_capacity_qsts,
    run_monte_carlo,
)
from gridsense_sim.hosting_capacity.limits import NetworkLimits


def test_limits_for_cigre_lv_is_wider_than_ansi_range_a() -> None:
    cigre = limits_for("cigre_lv")
    ansi = limits_for("case14")
    assert cigre.v_min_pu < ansi.v_min_pu
    assert cigre.v_max_pu > ansi.v_max_pu


def test_limits_for_unknown_network_raises() -> None:
    with pytest.raises(ValueError):
        limits_for("not_a_real_network")


def test_cigre_lv_base_case_has_no_violation_under_its_own_limits() -> None:
    """Regression test for the exact bug this module exists to avoid:
    cigre_lv at zero PV must NOT show a violation under its own
    (+-10%) limits, even though it would under ANSI Range A.
    """
    net = pn.create_cigre_network_lv()
    limits = limits_for("cigre_lv")
    report = check_violations(net, limits)
    assert report.has_violation is False


def test_proportional_to_load_sums_multiple_loads_per_bus() -> None:
    net = pn.create_cigre_network_lv()
    allocation = proportional_to_load(net)
    assert len(allocation.base_mw_per_bus) > 0
    assert all(mw >= 0 for mw in allocation.base_mw_per_bus.values())
    # Total allocation basis should equal total network load.
    assert sum(allocation.base_mw_per_bus.values()) == pytest.approx(
        net.load["p_mw"].sum(), rel=1e-6
    )


def test_deterministic_hosting_capacity_finds_a_positive_lambda() -> None:
    net = pn.create_cigre_network_lv()
    result = find_hosting_capacity_deterministic(net, "cigre_lv", tolerance=0.05)
    assert result.lambda_max > 0.0
    assert result.total_pv_mw > 0.0
    assert result.binding_constraint is not None


def test_deterministic_hosting_capacity_raises_with_no_load_buses() -> None:
    from gridsense_sim.hosting_capacity.allocation import PVAllocation

    net = pn.create_cigre_network_lv()
    empty_allocation = PVAllocation(base_mw_per_bus={})
    with pytest.raises(ValueError):
        find_hosting_capacity_deterministic(net, "cigre_lv", allocation=empty_allocation)


def test_stochastic_hosting_capacity_returns_distribution() -> None:
    net = pn.create_cigre_network_lv()
    result = run_monte_carlo(net, "cigre_lv", n_trials=20, max_pv_mw_per_bus=0.05, seed=1)
    assert len(result.trials) == 20
    assert 0.0 <= result.violation_rate <= 1.0
    # p50 should never exceed p95 of the feasible-total distribution.
    assert result.hosting_capacity_mw(0.5) <= result.hosting_capacity_mw(0.95) + 1e-9


def test_qsts_hosting_capacity_short_horizon_finds_a_positive_lambda() -> None:
    """Uses a deliberately tiny horizon (a few hours, not 60 days) so
    this test runs fast -- it's checking correctness of the bisection
    logic itself, not validating a real annual result.
    """
    net = pn.create_cigre_network_lv()
    result = find_hosting_capacity_qsts(
        net, "cigre_lv", total_steps=24, steps_per_day=288, tolerance=0.1
    )
    assert result.lambda_max > 0.0
    assert result.total_pv_mw > 0.0
    assert result.total_steps == 24
