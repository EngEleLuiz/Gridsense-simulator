"""Phase 7.4: three-phase hosting capacity on single-phase customers (IEEE European LV, UK)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pyarrow.parquet as pq
import pytest
from gridsense_sim.hosting_capacity import BaselineInfeasibleError
from gridsense_sim.hosting_capacity.unbalanced import (
    estimate_hosting_capacity_unbalanced_stochastic,
    find_hosting_capacity_unbalanced,
    is_unbalanced_network,
)
from gridsense_sim.networks import make_network

OFF = "ieee_european_lv_offpeak"


@pytest.fixture(scope="module")
def off():
    return make_network(OFF)


def test_detection_and_customers(off) -> None:
    assert is_unbalanced_network(off)
    assert not is_unbalanced_network(make_network("cigre_lv"))
    assert len(off.asymmetric_load) == 55


def test_unbalance_lowers_capacity_same_solver(off) -> None:
    u = find_hosting_capacity_unbalanced(off, OFF, framework="en50160_envelope", phase_model="unbalanced")
    b = find_hosting_capacity_unbalanced(off, OFF, framework="en50160_envelope", phase_model="balanced_equivalent")
    assert u.bounded and b.bounded and u.lambda_unit == "kWp_per_customer"
    assert u.lambda_max < b.lambda_max  # balanced model overestimates HC
    assert u.max_vuf_percent > b.max_vuf_percent >= 0
    assert u.total_pv_mw == pytest.approx(u.lambda_max * 55 / 1000, abs=1e-6)  # rounded to 6 dp
    assert sum(b.pv_mw_per_phase.values()) == pytest.approx(b.total_pv_mw, rel=1e-3)
    assert "customers per phase" in u.notes[0]


def test_source_voltage_is_a_sensitivity_factor(off) -> None:
    hi = find_hosting_capacity_unbalanced(off, OFF, framework="en50160_envelope")
    lo = find_hosting_capacity_unbalanced(off, OFF, framework="en50160_envelope", source_vm_pu=1.0)
    assert lo.source_vm_pu == 1.0 and hi.source_vm_pu == pytest.approx(1.05, abs=1e-6)
    assert lo.lambda_max > hi.lambda_max


def test_onpeak_unbalance_violates_prodist_without_pv() -> None:
    on = make_network("ieee_european_lv_onpeak")
    with pytest.raises(BaselineInfeasibleError, match="overvoltage"):
        find_hosting_capacity_unbalanced(on, "ieee_european_lv_onpeak", framework="prodist_m8_bt")
    b = find_hosting_capacity_unbalanced(on, "ieee_european_lv_onpeak", framework="prodist_m8_bt",
                                         phase_model="balanced_equivalent")
    assert b.bounded  # the balanced model does not see the problem


def test_vuf_limit_and_validation(off) -> None:
    r = find_hosting_capacity_unbalanced(off, OFF, framework="en50160_envelope", max_vuf_percent=0.2)
    assert r.binding_constraint and "unbalance" in r.binding_constraint
    with pytest.raises(ValueError):
        find_hosting_capacity_unbalanced(off, OFF, phase_model="x")
    with pytest.raises(ValueError, match="asymmetric_load"):
        find_hosting_capacity_unbalanced(make_network("cigre_lv"), "cigre_lv")


def test_stochastic_quantile_with_ci(off) -> None:
    r = estimate_hosting_capacity_unbalanced_stochastic(off, OFF, framework="en50160_envelope",
                                                        n_scenarios=12, alpha=0.5, tolerance=0.2)
    assert r.hc_lambda is not None and len(r.critical_lambdas) == 12
    assert min(r.critical_lambdas) <= r.hc_lambda <= max(r.critical_lambdas)


def test_runner_three_phase(tmp_path) -> None:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
    import run_hosting_capacity_study as study

    out = tmp_path / "hc"
    study.main(["--network", OFF, "--methods", "deterministic", "--framework", "en50160_envelope",
                "--phase-model", "balanced_equivalent", "--source-vm-pu", "1.0", "--output-dir", str(out)])
    row = json.loads(pq.read_table(next(out.rglob("*.parquet"))).to_pylist()[0]["raw_value"])
    assert row["status"] == "ok" and row["phase_model"] == "balanced_equivalent"
    assert row["source_vm_pu"] == 1.0 and row["conditions"]["load_scale"] == 1.0
    assert row["params"]["network_country"] == "UK"
    with pytest.raises(SystemExit):
        study.main(["--network", OFF, "--methods", "qsts", "--output-dir", str(out)])
