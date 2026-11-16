"""Fixed inductive power factor of the study PV (sensitivity to the unity-power-factor assumption)."""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import numpy as np
import pandapower as pp
import pytest

from gridsense_sim.hosting_capacity import (
    estimate_hosting_capacity_stochastic as stochastic,
    find_hosting_capacity_deterministic as det,
    find_hosting_capacity_qsts as qsts,
    proportional_to_load,
)
from gridsense_sim.hosting_capacity.allocation import HC_SGEN_PREFIX, apply_allocation, reactive_per_active
from gridsense_sim.hosting_capacity.qsts_duration import find_hosting_capacity_duration
from gridsense_sim.hosting_capacity.timeseries import TimeSeries
from gridsense_sim.networks import make_network

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))


def hourly_week(seed: int = 4) -> TimeSeries:
    rng = np.random.default_rng(seed)
    h = np.arange(168) % 24
    load = 0.35 + 0.45 * np.exp(-0.5 * ((h - 20) / 2.5) ** 2) + 0.15 * np.exp(-0.5 * ((h - 8) / 2.0) ** 2)
    pv = np.clip(np.sin((h - 6) / 12 * np.pi), 0, None) * 0.9 * rng.uniform(0.3, 1.0, h.size)
    return TimeSeries(load_mult=load * rng.uniform(0.9, 1.1, h.size), pv_mult=np.clip(pv, 0, 1), steps_per_day=24)


def test_reactive_per_active() -> None:
    assert reactive_per_active(1.0) == 0.0
    assert reactive_per_active(0.92) == pytest.approx(-math.tan(math.acos(0.92)))
    assert reactive_per_active(0.92) < 0            # absorbing, generator convention
    for bad in (0.0, 0.79, 1.01, -0.95):
        with pytest.raises(ValueError):
            reactive_per_active(bad)


def test_apply_allocation_sets_and_updates_q(cigre) -> None:
    import copy

    net = copy.deepcopy(cigre)
    alloc = proportional_to_load(net)
    k = reactive_per_active(0.95)
    apply_allocation(net, alloc, 1.0, k)
    pv = net.sgen[net.sgen["name"].astype(str).str.startswith(HC_SGEN_PREFIX)]
    assert np.allclose(pv["q_mvar"], pv["p_mw"] * k) and (pv["q_mvar"] < 0).all()
    apply_allocation(net, alloc, 2.0, k)            # update path keeps the ratio
    pv = net.sgen[net.sgen["name"].astype(str).str.startswith(HC_SGEN_PREFIX)]
    assert np.allclose(pv["q_mvar"], pv["p_mw"] * k)
    apply_allocation(net, alloc, 2.0)               # back to unity
    assert (net.sgen.loc[pv.index, "q_mvar"] == 0).all()


def test_default_is_unity_and_recorded(cigre) -> None:
    a = det(cigre, "cigre_lv", framework="prodist_m8_bt")
    b = det(cigre, "cigre_lv", framework="prodist_m8_bt", pv_power_factor=1.0)
    assert a.lambda_max == b.lambda_max and a.conditions.pv_power_factor == 1.0


def test_absorbing_reactive_power_raises_a_voltage_bound_capacity(cigre) -> None:
    """On cigre_lv under PRODIST the unity-pf capacity is bound by overvoltage (bus 16)."""
    unity = det(cigre, "cigre_lv", framework="prodist_m8_bt")
    lagging = det(cigre, "cigre_lv", framework="prodist_m8_bt", pv_power_factor=0.92)
    assert unity.binding_constraint.startswith("overvoltage")
    assert lagging.lambda_max > unity.lambda_max + 0.01
    assert lagging.conditions.pv_power_factor == 0.92
    assert lagging.total_pv_mw > unity.total_pv_mw           # lambda still measures active power


def test_reactive_power_lowers_a_thermally_bound_capacity(cigre) -> None:
    """Under the EN 50160 envelope the transformer binds: the extra current costs capacity."""
    unity = det(cigre, "cigre_lv", framework="en50160_envelope")
    lagging = det(cigre, "cigre_lv", framework="en50160_envelope", pv_power_factor=0.92)
    assert unity.binding_constraint.startswith("trafo_loading")
    assert lagging.lambda_max < unity.lambda_max - 0.01


def test_stochastic_and_zero_tolerance_qsts_accept_the_power_factor(cigre) -> None:
    s1 = stochastic(cigre, "cigre_lv", framework="prodist_m8_bt", n_scenarios=6, seed=1)
    s2 = stochastic(cigre, "cigre_lv", framework="prodist_m8_bt", n_scenarios=6, seed=1, pv_power_factor=0.92)
    assert s2.conditions.pv_power_factor == 0.92
    assert sorted(s2.lambdas) != sorted(s1.lambdas)
    ts = hourly_week()
    q1 = qsts(cigre, "cigre_lv", series=ts, framework="en50160_envelope")
    q2 = qsts(cigre, "cigre_lv", series=ts, framework="en50160_envelope", pv_power_factor=0.92)
    assert q2.conditions.pv_power_factor == 0.92 and q2.lambda_max != q1.lambda_max


def test_duration_qsts_reports_the_lower_side_at_the_capacity() -> None:
    ts = hourly_week()
    unity = find_hosting_capacity_duration(make_network("cigre_lv"), "cigre_lv", ts, tolerance=0.02)
    lag = find_hosting_capacity_duration(make_network("cigre_lv"), "cigre_lv", ts, tolerance=0.02,
                                         pv_power_factor=0.92)
    assert lag.conditions.pv_power_factor == 0.92 and unity.conditions.pv_power_factor == 1.0
    for r in (unity, lag):
        assert {"worst_bus_lower_drp", "worst_bus_lower_drc", "lower_side_compliant"} <= set(r.verification)
    assert any("power factor" in n for n in lag.notes) and not any("power factor" in n for n in unity.notes)
    assert lag.lambda_max != unity.lambda_max


def test_batched_backend_matches_pandapower_with_reactive_power() -> None:
    pytest.importorskip("torch")
    from gridsense_sim.accel.model import build_model, solve_reference
    from gridsense_sim.hosting_capacity.limits import limits_for
    from gridsense_sim.hosting_capacity.qsts_batched import find_hosting_capacity_duration_batched
    from gridsense_sim.hosting_capacity.scope import voltage_scope_buses

    net = make_network("cigre_lv")
    alloc = proportional_to_load(net)
    scope = list(voltage_scope_buses(net, limits_for("cigre_lv", None)))
    k = reactive_per_active(0.9)
    model = build_model(net, scope, alloc.base_mw_per_bus, pv_q_per_p=k)
    vm, _, trafo = solve_reference(model, model.injections(np.array([0.4]), np.array([0.8]), 1.5)[0])
    ref = make_network("cigre_lv")
    ref.load["p_mw"] *= 0.4
    ref.load["q_mvar"] *= 0.4
    apply_allocation(ref, alloc, 1.5 * 0.8, k)
    pp.runpp(ref)
    assert np.max(np.abs(vm - ref.res_bus.loc[scope, "vm_pu"].to_numpy())) < 1e-7
    assert np.max(np.abs(trafo - ref.res_trafo["loading_percent"].to_numpy())) < 1e-4

    ts = hourly_week()
    a = find_hosting_capacity_duration(make_network("cigre_lv"), "cigre_lv", ts, tolerance=0.02,
                                       pv_power_factor=0.92)
    b = find_hosting_capacity_duration_batched(make_network("cigre_lv"), "cigre_lv", ts, tolerance=0.02,
                                               device="cpu", pv_power_factor=0.92)
    assert abs(a.lambda_max - b.lambda_max) <= 0.02 and a.binding_constraint == b.binding_constraint
    assert b.verification["worst_bus_lower_drp"] == pytest.approx(a.verification["worst_bus_lower_drp"])
    assert b.conditions.pv_power_factor == 0.92


def test_runner_records_the_power_factor_and_rejects_three_phase(tmp_path) -> None:
    import pyarrow.parquet as pq
    import run_hosting_capacity_study as study

    study.main(["--network", "cigre_lv", "--methods", "deterministic", "--framework", "prodist_m8_bt",
                "--pv-power-factor", "0.92", "--output-dir", str(tmp_path)])
    payload = json.loads(pq.read_table(next(tmp_path.rglob("*.parquet"))).to_pylist()[0]["raw_value"])
    assert payload["status"] == "ok"
    assert payload["params"]["pv_power_factor"] == 0.92 and payload["conditions"]["pv_power_factor"] == 0.92
    with pytest.raises(SystemExit):
        study.parse_args(["--network", "cigre_lv", "--pv-power-factor", "0.5"])
    with pytest.raises(SystemExit, match="three-phase"):
        study.main(["--network", "ieee_european_lv_offpeak", "--methods", "deterministic",
                    "--pv-power-factor", "0.95", "--output-dir", str(tmp_path / "uk")])


def test_unity_runs_keep_their_parameter_set(tmp_path) -> None:
    """Old rows and new unity rows must stay comparable field by field."""
    import pyarrow.parquet as pq
    import run_hosting_capacity_study as study

    study.main(["--network", "cigre_lv", "--methods", "deterministic", "--output-dir", str(tmp_path)])
    payload = json.loads(pq.read_table(next(tmp_path.rglob("*.parquet"))).to_pylist()[0]["raw_value"])
    assert "pv_power_factor" not in payload["params"] and payload["conditions"]["pv_power_factor"] == 1.0
