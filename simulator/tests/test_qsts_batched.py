"""Phase 8.2: the batched duration QSTS agrees with the pandapower backend."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandapower.networks as pn
import pytest

pytest.importorskip("torch")

from gridsense_sim.hosting_capacity import BaselineInfeasibleError, TimeSeries  # noqa: E402
from gridsense_sim.hosting_capacity.qsts_batched import find_hosting_capacity_duration_batched  # noqa: E402
from gridsense_sim.hosting_capacity.qsts_duration import find_hosting_capacity_duration  # noqa: E402
from gridsense_sim.networks import make_network  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

TOL = 0.02


def hourly_week(seed: int = 0, weeks: int = 1, pv_peak: float = 0.9) -> TimeSeries:
    """One (or more) weeks at 1-hour steps: an evening load peak and a clear-sky-like PV shape with clouds."""
    rng = np.random.default_rng(seed)
    hours = np.arange(168 * weeks)
    h = hours % 24
    load = 0.35 + 0.45 * np.exp(-0.5 * ((h - 20) / 2.5) ** 2) + 0.15 * np.exp(-0.5 * ((h - 8) / 2.0) ** 2)
    load = load * rng.uniform(0.9, 1.1, hours.size)
    pv = np.clip(np.sin((h - 6) / 12 * np.pi), 0, None) * pv_peak * rng.uniform(0.3, 1.0, hours.size)
    return TimeSeries(load_mult=load, pv_mult=np.clip(pv, 0, 1), steps_per_day=24)


@pytest.mark.parametrize("key, criterion", [
    ("cigre_lv", "prodist_m8_drp_drc"),
    ("cigre_lv", "en50160_weekly"),
    ("kerber_landnetz", "prodist_m8_zero_tolerance"),
])
def test_capacity_agrees_with_pandapower_backend(key, criterion) -> None:
    ts = hourly_week(seed=4)
    ref = find_hosting_capacity_duration(make_network(key), key, ts, criterion, tolerance=TOL)
    got = find_hosting_capacity_duration_batched(make_network(key), key, ts, criterion, tolerance=TOL, device="cpu")
    assert got.bounded and ref.bounded
    assert abs(got.lambda_max - ref.lambda_max) <= TOL
    assert got.binding_constraint == ref.binding_constraint
    assert got.resolved and got.lambda_fail - got.lambda_max <= TOL + 1e-12
    # same baseline report, and the verification of the critical window is consistent with the capacity
    assert got.windows[0].baseline_lower["max_drp"] == pytest.approx(ref.windows[0].baseline_lower["max_drp"])
    assert got.windows[0].baseline_upper_counts == ref.windows[0].baseline_upper_counts
    assert got.verification["within_regulatory_allowance_per_bus"]
    assert got.backend == "torch" and ref.backend == "pandapower"
    assert got.backend_info["device"] == "cpu" and got.backend_info["model_check"]["max_abs_dv_pu"] < 1e-7
    assert got.total_pv_mw == pytest.approx(ref.total_pv_mw, rel=0.03)


def test_tighter_tolerance_converges_to_the_same_capacity() -> None:
    ts = hourly_week(seed=4)
    coarse = find_hosting_capacity_duration_batched(make_network("cigre_lv"), "cigre_lv", ts, tolerance=0.05, device="cpu")
    fine = find_hosting_capacity_duration_batched(make_network("cigre_lv"), "cigre_lv", ts, tolerance=0.001, device="cpu")
    assert 0 <= fine.lambda_max - coarse.lambda_max <= 0.05
    assert fine.lambda_fail - fine.lambda_max <= 0.001 + 1e-12


def test_per_load_matrix_and_window_selection(cigre) -> None:
    ts = hourly_week(seed=7, weeks=2)
    whole = find_hosting_capacity_duration_batched(cigre, "cigre_lv", ts, tolerance=TOL, device="cpu")
    matrix = np.tile(np.asarray(ts.load_mult)[:, None], (1, len(cigre.load)))
    per_load = find_hosting_capacity_duration_batched(cigre, "cigre_lv", ts, tolerance=TOL, device="cpu",
                                                      load_matrix=matrix)
    assert per_load.lambda_max == pytest.approx(whole.lambda_max, abs=1e-9)
    assert [w.index for w in whole.windows] == [0, 1]
    second = find_hosting_capacity_duration_batched(cigre, "cigre_lv", ts, tolerance=TOL, device="cpu", windows=[1])
    assert abs(second.lambda_max - whole.windows[1].lambda_max) <= TOL
    with pytest.raises(ValueError, match="load_matrix"):
        find_hosting_capacity_duration_batched(cigre, "cigre_lv", ts, device="cpu", load_matrix=matrix[:, :3])


def test_window_without_exceedance_is_censored(cigre) -> None:
    ts = hourly_week(seed=1, pv_peak=1e-4)       # almost no PV output: nothing is exceeded
    r = find_hosting_capacity_duration_batched(cigre, "cigre_lv", ts, tolerance=TOL, device="cpu", max_expansions=3)
    assert not r.bounded and r.lambda_fail is None and not r.resolved
    assert r.window_lambda_quantiles["n_windows_censored"] == 1
    assert r.lambda_max == 8.0                    # 1, 2, 4, 8: the largest lambda evaluated


def test_isolated_bus_fails_the_baseline() -> None:
    net = pn.create_cigre_network_lv()
    end_bus = int(net.line["to_bus"].iloc[-1])
    net.line.loc[net.line["to_bus"] == end_bus, "in_service"] = False
    with pytest.raises(BaselineInfeasibleError, match="isolated"):
        find_hosting_capacity_duration_batched(net, "cigre_lv", hourly_week(), device="cpu")


def test_network_is_not_mutated(cigre) -> None:
    before = (cigre.load.copy(), len(cigre.sgen))
    find_hosting_capacity_duration_batched(cigre, "cigre_lv", hourly_week(), tolerance=0.05, device="cpu")
    assert cigre.load.equals(before[0]) and len(cigre.sgen) == before[1]


def test_runner_torch_backend(tmp_path) -> None:
    pytest.importorskip("pyarrow")
    import pyarrow.parquet as pq

    import run_hosting_capacity_study as study

    study.main(["--network", "cigre_lv", "--methods", "qsts", "--qsts-criterion", "prodist_m8_drp_drc",
                "--qsts-total-steps", "168", "--qsts-steps-per-day", "24", "--qsts-peak-load", "1.0",
                "--qsts-backend", "torch", "--device", "cpu", "--tolerance", "0.05",
                "--output-dir", str(tmp_path)])
    payload = json.loads(pq.read_table(next(tmp_path.rglob("*.parquet"))).to_pylist()[0]["raw_value"])
    assert payload["status"] == "ok" and payload["backend"] == "torch"
    assert payload["backend_info"]["device"] == "cpu" and payload["params"]["backend"] == "torch"
    assert payload["lambda_max"] > 0
    with pytest.raises(SystemExit):   # classic zero-tolerance QSTS has no batched path
        study.parse_args(["--network", "cigre_lv", "--methods", "qsts", "--qsts-backend", "torch"])
