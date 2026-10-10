"""Duration criteria (finding C7): weekly sweep, PV attribution, search exactness, same-data design."""

from __future__ import annotations

import math
from dataclasses import replace

import numpy as np
import pandapower.networks as pn
import pytest
from gridsense_sim.hosting_capacity import (
    BaselineInfeasibleError,
    find_hosting_capacity_deterministic,
    find_hosting_capacity_qsts,
)
from gridsense_sim.hosting_capacity.duration import (
    DURATION_CRITERIA,
    WeekThresholds,
    get_criterion,
    weekly_capacity,
)
from gridsense_sim.hosting_capacity.qsts_duration import (
    _bisect_threshold,
    _Bounds,
    _Evaluator,
    _kinds,
    find_hosting_capacity_duration,
    result_to_dict,
)
from gridsense_sim.hosting_capacity.study_design import critical_point
from gridsense_sim.hosting_capacity.timeseries import TimeSeries

PRODIST = DURATION_CRITERIA["prodist_m8_drp_drc"]


# ------------------------------------------------------------- criteria
def test_prodist_allowances_over_1008_readings() -> None:
    assert PRODIST.readings_per_window() == 1008
    assert PRODIST.allowed(PRODIST.upper_precarious_max, 1008) == 30
    assert PRODIST.allowed(PRODIST.upper_critical_max, 1008) == 5
    assert PRODIST.adequate_hi_pu == pytest.approx(1.05) and PRODIST.critical_hi_pu == pytest.approx(1.0591, abs=1e-4)
    assert PRODIST.to_verify


def test_en50160_upper_side_has_zero_tolerance() -> None:
    en = DURATION_CRITERIA["en50160_weekly"]
    assert not en.has_upper_precarious_band
    assert en.upper_critical_max == 0.0 and en.lower_precarious_max == 0.05


def test_criterion_validation_and_lookup() -> None:
    with pytest.raises(ValueError):
        replace(PRODIST, adequate_hi_pu=0.8)
    with pytest.raises(ValueError):
        replace(PRODIST, upper_critical_max=1.0)
    with pytest.raises(KeyError):
        get_criterion("nope")
    assert get_criterion(PRODIST) is PRODIST


# ----------------------------------------------------------- weekly sweep
def test_sweep_critical_binds_at_sixth_smallest() -> None:
    th = WeekThresholds(1008, cap=9.0, critical=[2.0, 1.1, 1.5, 1.3, 1.2, 1.4, 1.8],
                        precarious=[2.0, 1.1, 1.5, 1.3, 1.2, 1.4, 1.8])
    out = weekly_capacity(th, PRODIST)
    assert out.bounded and out.binding == "critical" and out.lambda_max == 1.8  # 6th smallest: 5 allowed


def test_sweep_precarious_binds_at_31st() -> None:
    prec = [1.0 + 0.01 * i for i in range(40)]
    out = weekly_capacity(WeekThresholds(1008, cap=9.0, precarious=prec), PRODIST)
    assert out.binding == "precarious" and out.lambda_max == pytest.approx(prec[30])


def test_sweep_first_exceedance_with_readings_moving_to_critical() -> None:
    # 31 readings leave the adequate band at 1.0; they become critical only at 3.0:
    # the precarious count is exceeded at 1.0 even though it would drop later.
    th = WeekThresholds(1008, cap=9.0, precarious=[1.0] * 31, critical=[3.0] * 31)
    out = weekly_capacity(th, PRODIST)
    assert out.lambda_max == 1.0 and out.binding == "precarious"


def test_sweep_base_relative_allowance_and_unbounded() -> None:
    # 7 readings are already critical without PV (> 5 allowed): PV may not add an 8th.
    th = WeekThresholds(1008, cap=9.0, critical=[0.0] * 7 + [2.0, 3.0], precarious=[0.0] * 7 + [2.0, 3.0])
    out = weekly_capacity(th, PRODIST)
    assert out.allowances["critical"] == 7 and out.lambda_max == 2.0 and out.binding == "critical"
    assert not weekly_capacity(WeekThresholds(1008, cap=4.0, critical=[1.0]), PRODIST).bounded


# --------------------------------------------------------- QSTS (small)
DAY = replace(PRODIST, name="prodist_daily_test", window_days=1, reading_minutes=60,
              upper_precarious_max=0.10, upper_critical_max=0.05)


def _day_series(days: int = 1, load_peak: float = 0.6) -> TimeSeries:
    h = np.arange(24 * days) % 24
    pv = np.clip(np.sin(np.pi * (h - 6) / 12), 0, None)
    pv[(h < 6) | (h > 18)] = 0
    load = 0.3 + (load_peak - 0.3) * np.exp(-((h - 19.5) ** 2) / 4)
    return TimeSeries(load_mult=load, pv_mult=pv, steps_per_day=24)


@pytest.fixture(scope="module")
def cigre():
    return pn.create_cigre_network_lv()


def test_search_matches_exhaustive_thresholds(cigre) -> None:
    ts = _day_series()
    r = find_hosting_capacity_duration(cigre, "cigre_lv", ts, DAY, tolerance=0.01)
    assert r.bounded and len(r.windows) == 1 and r.resolution_matches_criterion
    # exhaustive: every daylight step's thresholds by full bisection, then the same sweep
    ev = _Evaluator(cigre, "cigre_lv", ts, None, DAY, None)
    bounds = _Bounds()
    th = WeekThresholds(24, cap=math.inf)
    for t in range(24):
        ev.baseline(t)
    for t in range(24):
        if ts.pv_mult[t] <= 0:
            continue
        for k in _kinds(DAY):
            hi = 8.0
            e = ev.events(t, hi)
            bounds.record(t, hi, e)
            if not e[k]:
                continue
            lo, _ = _bisect_threshold(ev, bounds, t, k, 0.01, 40)
            getattr(th, k).append(lo)
    exact = weekly_capacity(th, DAY)
    assert r.lambda_max == pytest.approx(exact.lambda_max, abs=0.011)
    assert r.windows[0].binding == exact.binding
    assert r.verification["any_bus_upper_drp"] <= DAY.upper_precarious_max + 1e-9


def test_zero_tolerance_reproduces_classic_qsts(cigre) -> None:
    ts = _day_series(load_peak=0.6)
    zt = replace(DURATION_CRITERIA["prodist_m8_zero_tolerance"], window_days=1, reading_minutes=60)
    new = find_hosting_capacity_duration(cigre, "cigre_lv", ts, zt, tolerance=0.01)
    old = find_hosting_capacity_qsts(cigre, "cigre_lv", series=ts, framework="prodist_m8_bt", tolerance=0.01)
    assert new.lambda_max == pytest.approx(old.lambda_max, abs=0.011)


def test_duration_allowance_never_lowers_capacity(cigre) -> None:
    ts = _day_series()
    zt = replace(DURATION_CRITERIA["prodist_m8_zero_tolerance"], window_days=1, reading_minutes=60)
    a = find_hosting_capacity_duration(cigre, "cigre_lv", ts, zt).lambda_max
    b = find_hosting_capacity_duration(cigre, "cigre_lv", ts, DAY).lambda_max
    assert b >= a - 0.011


def test_load_driven_undervoltage_does_not_block_and_is_reported(cigre) -> None:
    ts = _day_series(load_peak=1.15)  # evening peak violates the lower PRODIST band without PV
    with pytest.raises(BaselineInfeasibleError):
        find_hosting_capacity_qsts(cigre, "cigre_lv", series=ts, framework="prodist_m8_bt")
    r = find_hosting_capacity_duration(cigre, "cigre_lv", ts, DAY)
    assert r.bounded and r.lambda_max > 0
    assert not r.baseline_lower_compliant
    low = r.windows[0].baseline_lower
    assert low["max_drp"] + low["max_drc"] > 0
    d = result_to_dict(r)
    assert d["criterion"]["name"] == DAY.name and d["conditions"]["criterion_kind"] == "statistical"


def test_windows_resolution_and_validation(cigre) -> None:
    ts = _day_series(days=2)
    r = find_hosting_capacity_duration(cigre, "cigre_lv", ts, DAY, windows=[1], verify=False)
    assert [w.index for w in r.windows] == [1]
    week = find_hosting_capacity_duration
    with pytest.raises(ValueError, match="shorter than one"):
        week(cigre, "cigre_lv", ts, PRODIST)
    r2 = find_hosting_capacity_duration(cigre, "cigre_lv", ts, DAY, verify=False)
    assert r2.resolution_matches_criterion and r2.window_lambda_quantiles["n_windows"] == 2
    with pytest.raises(ValueError, match="load_matrix"):
        find_hosting_capacity_duration(cigre, "cigre_lv", ts, DAY, load_matrix=np.ones((3, 3)))


def test_per_load_matrix_changes_the_result(cigre) -> None:
    ts = _day_series()
    n_loads = len(cigre.load)
    flat = np.tile(ts.load_mult[:, None], (1, n_loads))
    same = find_hosting_capacity_duration(cigre, "cigre_lv", ts, DAY, load_matrix=flat, verify=False)
    base = find_hosting_capacity_duration(cigre, "cigre_lv", ts, DAY, verify=False)
    assert same.lambda_max == pytest.approx(base.lambda_max, abs=1e-9)
    lighter = flat * 0.5
    low = find_hosting_capacity_duration(cigre, "cigre_lv", ts, DAY, load_matrix=lighter, verify=False)
    assert low.lambda_max <= base.lambda_max + 1e-9  # less load at midday -> less room for PV


# --------------------------------------------------------- same-data design
def test_critical_point_rules() -> None:
    ts = _day_series()
    cp = critical_point(ts)
    assert ts.pv_mult[cp.step] >= 0.9 * ts.pv_mult.max()
    assert cp.load_mult == min(ts.load_mult[ts.pv_mult >= 0.9 * ts.pv_mult.max()])
    cp2 = critical_point(ts, rule="max_pv_to_load")
    day = ts.pv_mult > 0
    assert cp2.pv_mult / cp2.load_mult == pytest.approx(max(ts.pv_mult[day] / ts.load_mult[day]))
    with pytest.raises(ValueError):
        critical_point(ts, rule="x")


def test_zero_tolerance_qsts_never_exceeds_deterministic_at_series_point(cigre) -> None:
    ts = _day_series()
    cp = critical_point(ts)
    det = find_hosting_capacity_deterministic(cigre, "cigre_lv", framework="prodist_m8_bt",
                                             load_scale=cp.load_mult, pv_scale=cp.pv_mult, tolerance=0.01)
    q0 = find_hosting_capacity_qsts(cigre, "cigre_lv", series=ts, framework="prodist_m8_bt", tolerance=0.01)
    assert q0.lambda_max <= det.lambda_max + 0.011
    # installed MW are reported, not the injection at the point
    assert det.total_pv_mw == pytest.approx(det.lambda_max * sum(cigre.load.p_mw), rel=1e-6)


# ------------------------------------------------------------------ runner
def _runner_series(tmp_path):
    import sys
    from pathlib import Path

    from gridsense_sim.datasets import build_series, save_series
    from test_datasets_series import FLO, SOUTH_LOAD_SITE, _irr_set, _load_set

    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
    real = build_series(_load_set(SOUTH_LOAD_SITE), _irr_set(), target=FLO, start="2023-02-01",
                        days=7, steps_per_day=24, peak_load_mult=0.6)
    return save_series(real, tmp_path / "series"), real


def test_runner_duration_criterion_and_series_critical_point(tmp_path) -> None:
    import json

    import pyarrow.parquet as pq

    path, real = _runner_series(tmp_path)
    import run_hosting_capacity_study as study

    out = tmp_path / "hc"
    study.main(["--network", "cigre_lv", "--methods", "deterministic", "qsts",
                "--qsts-series", str(path), "--qsts-criterion", "prodist_m8_drp_drc",
                "--critical-point-from-series", "--framework", "prodist_m8_bt", "--output-dir", str(out)])
    rows = {}
    for f in out.rglob("*.parquet"):
        for r in pq.read_table(f).to_pylist():
            rows[r["method"]] = json.loads(r["raw_value"])
    q, d = rows["qsts"], rows["deterministic"]
    assert q["status"] == "ok" and q["criterion"] == "prodist_m8_drp_drc"
    assert q["conditions"]["framework"] == "prodist_m8_drp_drc" and q["conditions"]["criterion_kind"] == "statistical"
    assert len(q["windows"]) == 1 and not q["resolution_matches_criterion"] and q["notes"]
    assert q["lambda_fail"] is None or q["lambda_fail"] > q["lambda_max"]
    cp = critical_point(real.series)
    assert d["status"] == "ok" and d["params"]["critical_point"]["step"] == cp.step
    assert d["conditions"]["load_scale"] == pytest.approx(cp.load_mult)
    assert q["lambda_max"] >= 0


def test_runner_rejects_inconsistent_options(tmp_path) -> None:
    path, _ = _runner_series(tmp_path)
    import run_hosting_capacity_study as study

    with pytest.raises(SystemExit):
        study.parse_args(["--network", "cigre_lv", "--critical-point-from-series"])
    with pytest.raises(SystemExit):
        study.parse_args(["--network", "cigre_lv", "--qsts-series", str(path), "--qsts-windows", "0"])
    args = study.parse_args(["--network", "cigre_lv", "--qsts-series", str(path),
                             "--qsts-criterion", "en50160_weekly", "--qsts-windows", "0"])
    assert args.qsts_windows == [0]


# --------------------------------------------------------- study design (7.2)
def test_block_average_conserves_energy_and_removes_variability() -> None:
    from gridsense_sim.hosting_capacity.study_design import block_average

    h = np.arange(144) / 6
    pv = np.clip(np.sin(np.pi * (h - 6) / 12), 0, None) * (1 + 0.3 * np.sin(np.arange(144)))
    ts = TimeSeries(load_mult=0.5 + 0.1 * np.cos(h), pv_mult=np.clip(pv, 0, 1), steps_per_day=144)
    hourly = block_average(ts, 60)
    assert hourly.n_steps == ts.n_steps and hourly.steps_per_day == 144
    assert hourly.pv_mult.sum() == pytest.approx(ts.pv_mult.sum())
    assert hourly.pv_mult.max() <= ts.pv_mult.max() + 1e-12
    assert np.allclose(hourly.pv_mult.reshape(-1, 6).std(axis=1), 0)
    assert hourly.source.endswith("|block60min")
    with pytest.raises(ValueError):
        block_average(ts, 7)
