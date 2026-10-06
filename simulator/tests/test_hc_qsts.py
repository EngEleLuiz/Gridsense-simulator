"""qsts.py: C5, C6, C12, baseline gate, N2."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from gridsense_sim.hosting_capacity import (
    BaselineInfeasibleError,
    NoDaylightError,
    TimeSeries,
    build_synthetic_series,
    find_hosting_capacity_qsts as qsts,
)

# One hourly day: fast, and contains daylight steps 6..18.
FAST = {"total_steps": 24, "steps_per_day": 24, "tolerance": 0.1}


def test_night_only_window_raises_no_daylight(cigre) -> None:
    """C5 regression: steps 0-23 at 288/day used to yield lambda ~ 1e6."""
    with pytest.raises(NoDaylightError):
        qsts(cigre, "cigre_lv", total_steps=24, steps_per_day=288)


def test_short_horizon_gives_a_physical_bounded_answer(cigre) -> None:
    r = qsts(cigre, "cigre_lv", **FAST)
    assert r.bounded and 0.5 < r.lambda_max < 10
    assert r.criterion == "zero_tolerance"
    assert r.first_violating_step is not None
    assert 6 <= r.first_violating_step % 24 <= 18  # violation happens in daylight


def test_series_is_sampled_once_so_runs_are_identical(cigre) -> None:
    """C6: same seed -> identical answer (old code re-sampled noise per candidate)."""
    a = qsts(cigre, "cigre_lv", **FAST)
    b = qsts(cigre, "cigre_lv", **FAST)
    assert (a.lambda_max, a.first_violating_step) == (b.lambda_max, b.first_violating_step)


def test_explicit_series_is_honoured(cigre) -> None:
    ts = build_synthetic_series(24, 24, seed=1)
    r = qsts(cigre, "cigre_lv", series=ts, tolerance=0.1)
    assert r.total_steps == 24 and r.daylight_steps == int(ts.daylight_steps.size)


def test_prodist_baseline_violation_is_detected(cigre) -> None:
    """Synthetic load peaks at midday (C9) and violates PRODIST before any PV."""
    with pytest.raises(BaselineInfeasibleError):
        qsts(cigre, "cigre_lv", framework="prodist_m8_bt", **FAST)


def test_caller_network_is_not_mutated(cigre) -> None:
    before = cigre.load.copy()
    qsts(cigre, "cigre_lv", **FAST)
    pd.testing.assert_frame_equal(cigre.load, before)


def test_time_series_validation_and_immutability() -> None:
    with pytest.raises(ValueError):
        TimeSeries(np.ones(3), np.ones(2), 24)
    with pytest.raises(ValueError):
        TimeSeries(np.ones(2), np.array([0.0, 1.5]), 24)
    ts = TimeSeries(np.ones(2), np.array([0.0, 0.5]), 24)
    with pytest.raises(ValueError):
        ts.pv_mult[0] = 1.0


def test_time_series_does_not_freeze_the_callers_arrays() -> None:
    """R10 regression: np.asarray aliased float64 input and set it read-only."""
    load = np.ones(4)
    pv = np.array([0.0, 0.5, 0.5, 0.0])
    ts = TimeSeries(load, pv, 4)
    assert load.flags.writeable and pv.flags.writeable
    load[0] = 2.0  # mutating the caller's copy must not leak into the series
    assert ts.load_mult[0] == 1.0


def test_time_series_equality_and_hash_are_identity_based() -> None:
    """R10 regression: generated __eq__/__hash__ crashed on ndarray fields."""
    a = TimeSeries(np.ones(3), np.full(3, 0.1), 3)
    b = TimeSeries(np.ones(3), np.full(3, 0.1), 3)
    assert a == a and a != b
    assert len({a, b}) == 2


# --------------------------------------------------------------- R11 / R12 / R22

THREE_DAYS = {"total_steps": 72, "steps_per_day": 24, "profile_seed": 3, "tolerance": 0.01}


def test_min_over_steps_brackets_the_same_threshold_as_global_bisection(cigre) -> None:
    """R11: the fast strategy must answer the same question.

    Both brackets contain the true zero-tolerance threshold and are at
    most `tolerance` wide, so they must overlap; the chronological first
    violating step and the binding element must agree as well.
    """
    fast = qsts(cigre, "cigre_lv", strategy="min_over_steps", **THREE_DAYS)
    slow = qsts(cigre, "cigre_lv", strategy="global_bisection", **THREE_DAYS)
    assert fast.bounded and slow.bounded and fast.resolved and slow.resolved
    assert max(fast.lambda_max, slow.lambda_max) < min(fast.lambda_fail, slow.lambda_fail)
    assert fast.first_violating_step == slow.first_violating_step
    assert fast.binding_constraint.split(" (")[0] == slow.binding_constraint.split(" (")[0]
    search_fast = fast.power_flows - fast.total_steps
    search_slow = slow.power_flows - slow.total_steps
    assert search_fast * 3 < search_slow  # measured ~4x on this series


def test_unknown_strategy_is_rejected(cigre) -> None:
    with pytest.raises(ValueError):
        qsts(cigre, "cigre_lv", strategy="chronological", **FAST)


def test_raw_synthetic_profile_is_baseline_infeasible_at_5min(cigre) -> None:
    """R22: noise peaks (~1.19x) undervolt bus 35 with no PV under EN 50160.

    Documents why long QSTS runs need an explicit peak normalization.
    """
    with pytest.raises(BaselineInfeasibleError) as info:
        qsts(cigre, "cigre_lv", total_steps=576, steps_per_day=288, profile_seed=3)
    assert 35 in info.value.report.voltage_violations


def test_peak_normalized_series_has_the_requested_peak() -> None:
    ts = build_synthetic_series(576, 288, seed=3, peak_load_mult=1.0)
    assert ts.load_mult.max() == pytest.approx(1.0)
    raw = build_synthetic_series(576, 288, seed=3)
    assert raw.load_mult.max() > 1.12  # what makes R22 bite
    with pytest.raises(ValueError):
        build_synthetic_series(24, 24, peak_load_mult=0.0)


def test_qsts_result_records_strategy_and_peak(cigre) -> None:
    r = qsts(cigre, "cigre_lv", peak_load_mult=1.0, **FAST)
    assert r.strategy == "min_over_steps" and r.peak_load_mult == 1.0
