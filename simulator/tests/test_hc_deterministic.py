"""deterministic.py: C1, C3, N1, N2 and golden numbers from doc 06."""

from __future__ import annotations

import copy

import pandas as pd
import pytest

from gridsense_sim.hosting_capacity import (
    BaselineInfeasibleError,
    PVAllocation,
    find_hosting_capacity_deterministic as det,
)


@pytest.mark.parametrize(
    "framework, load_scale, lam, binding_prefix",
    [
        ("en50160_envelope", 1.0, 2.289, "trafo_loading@trafo_0"),   # old published number
        ("en50160_envelope", 0.25, 1.586, "trafo_loading@trafo_0"),  # C1: critical point
        ("prodist_m8_bt", 0.25, 1.164, "overvoltage@bus_16"),        # C2: PRODIST
    ],
)
def test_golden_numbers_match_doc06(cigre, framework, load_scale, lam, binding_prefix) -> None:
    r = det(cigre, "cigre_lv", framework=framework, load_scale=load_scale)
    assert r.bounded
    assert r.lambda_max == pytest.approx(lam, abs=0.01)
    assert r.binding_constraint.startswith(binding_prefix)
    assert r.conditions.load_scale == load_scale
    assert r.conditions.framework == framework


def test_critical_point_lowers_hosting_capacity(cigre) -> None:
    """C1: minimum load is the binding case for PV."""
    nominal = det(cigre, "cigre_lv", load_scale=1.0).lambda_max
    critical = det(cigre, "cigre_lv", load_scale=0.25).lambda_max
    assert critical < nominal


def test_prodist_at_nominal_load_raises_baseline_infeasible(cigre) -> None:
    """C3 regression: the old code returned a spurious lambda (2.102) here."""
    with pytest.raises(BaselineInfeasibleError) as info:
        det(cigre, "cigre_lv", framework="prodist_m8_bt", load_scale=1.0)
    assert info.value.status == "baseline_infeasible"
    assert 35 in info.value.report.voltage_violations


def test_caller_network_is_not_mutated(cigre) -> None:
    """N2 regression: the old function left PV at the violating lambda in net."""
    before_load = cigre.load.copy()
    n_sgen = len(cigre.sgen)
    det(cigre, "cigre_lv")
    assert len(cigre.sgen) == n_sgen
    pd.testing.assert_frame_equal(cigre.load, before_load)


def test_unbounded_search_is_flagged(cigre) -> None:
    tiny = PVAllocation({next(iter(cigre.load.bus.astype(int))): 1e-9})
    r = det(cigre, "cigre_lv", allocation=tiny, max_expansions=3)
    assert r.bounded is False
    assert r.lambda_fail is None and r.binding_constraint is None


def test_empty_allocation_raises(cigre) -> None:
    with pytest.raises(ValueError):
        det(cigre, "cigre_lv", allocation=PVAllocation({}))


def test_non_positive_load_scale_raises(cigre) -> None:
    with pytest.raises(ValueError):
        det(copy.deepcopy(cigre), "cigre_lv", load_scale=0.0)
