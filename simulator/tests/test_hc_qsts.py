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
