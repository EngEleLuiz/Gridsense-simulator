"""Phase 7.3: network panel (German LV grids), existing-PV policy, SimBench own profiles."""

from __future__ import annotations

import json
import sys
from dataclasses import replace
from pathlib import Path

import pyarrow.parquet as pq
import pytest
from gridsense_sim.hosting_capacity import (
    find_hosting_capacity_deterministic,
    limits_for,
)
from gridsense_sim.hosting_capacity.duration import DURATION_CRITERIA
from gridsense_sim.hosting_capacity.qsts_duration import find_hosting_capacity_duration
from gridsense_sim.networks import NETWORKS, hc_panel, make_network

NO_SIMBENCH = ("kerber_landnetz", "kerber_vorstadt", "lv_schutterwald", "cigre_lv")


def test_registry_and_provenance() -> None:
    panel = hc_panel()
    assert "case14" not in panel and {"cigre_lv", "lv_schutterwald", "simbench_lv_urban6"} <= set(panel)
    for k in panel:
        info = NETWORKS[k]
        assert info.citation and info.country and info.kind in {"benchmark", "reference", "real"}
        assert limits_for(k).voltage_levels_kv in {(0.4,), (0.416,)}  # UK feeder: 416 V
    assert NETWORKS["lv_schutterwald"].kind == "real"
    with pytest.raises(KeyError):
        make_network("nope")
    with pytest.raises(ValueError):
        make_network("cigre_lv", existing_pv="x")


@pytest.mark.parametrize("key", NO_SIMBENCH)
def test_deterministic_runs_on_every_panel_network(key) -> None:
    net = make_network(key)
    r = find_hosting_capacity_deterministic(net, key, framework="prodist_m8_bt", load_scale=0.25, tolerance=0.02)
    assert r.bounded and r.lambda_max > 0 and r.binding_constraint


def test_existing_pv_policy_on_simbench() -> None:
    pytest.importorskip("simbench")
    kept = make_network("simbench_lv_urban6", existing_pv="keep")
    removed = make_network("simbench_lv_urban6")
    assert len(kept.sgen) > 0 and len(removed.sgen) == 0
    a = find_hosting_capacity_deterministic(removed, "simbench_lv_urban6", load_scale=0.25)
    b = find_hosting_capacity_deterministic(kept, "simbench_lv_urban6", load_scale=0.25)
    assert b.lambda_max <= a.lambda_max + 1e-9  # installed PV uses part of the room


def test_simbench_own_profiles_series_and_duration_qsts() -> None:
    pytest.importorskip("simbench")
    from gridsense_sim.datasets.simbench_profiles import network_series

    full = make_network("simbench_lv_rural1", existing_pv="keep")
    ts, matrix, info = network_series(full, "2016-06-01", days=1)
    assert ts.n_steps == 96 and matrix.shape == (96, len(full.load))
    assert ts.pv_mult.max() > 0 and 0 <= ts.pv_mult.min() and info["load_profiles"]
    day = replace(DURATION_CRITERIA["prodist_m8_drp_drc"], name="d", window_days=1, reading_minutes=15)
    r = find_hosting_capacity_duration(make_network("simbench_lv_rural1"), "simbench_lv_rural1", ts, day,
                                       load_matrix=matrix, tolerance=0.02)
    assert r.bounded and r.lambda_max > 0
    with pytest.raises(Exception, match="no step"):
        network_series(full, "2015-01-01", days=1)


def test_runner_on_a_german_grid(tmp_path) -> None:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
    import run_hosting_capacity_study as study

    out = tmp_path / "hc"
    study.main(["--network", "kerber_vorstadt", "--methods", "deterministic", "--framework", "prodist_m8_bt",
                "--output-dir", str(out)])
    row = json.loads(pq.read_table(next(out.rglob("*.parquet"))).to_pylist()[0]["raw_value"])
    assert row["status"] == "ok" and row["conditions"]["network_name"] == "kerber_vorstadt"
    assert row["params"]["existing_pv"] == "remove" and row["params"]["network_country"] == "Germany"
    with pytest.raises(SystemExit):
        study.parse_args(["--network", "cigre_lv", "--qsts-network-profiles", "--qsts-criterion",
                          "prodist_m8_drp_drc"])
    with pytest.raises(SystemExit):
        study.parse_args(["--network", "simbench_lv_rural1", "--qsts-network-profiles"])



def test_same_data_ordering_holds_with_per_load_profiles() -> None:
    """Regression: with one profile per load, the snapshot must use the per-load state of the
    critical step, or QSTS(tau=0) can exceed it (measured on simbench_lv_semiurb4: 2.664 > 2.523)."""
    pytest.importorskip("simbench")
    from gridsense_sim.datasets.simbench_profiles import network_series
    from gridsense_sim.hosting_capacity.study_design import critical_point

    key = "simbench_lv_semiurb4"
    ts, m, _ = network_series(make_network(key, "keep"), "2016-06-01", days=1)
    cp = critical_point(ts)
    det = find_hosting_capacity_deterministic(make_network(key), key, framework="prodist_m8_bt", load_scale=1.0,
                                             pv_scale=cp.pv_mult, load_multipliers=m[cp.step], tolerance=0.01)
    zt = replace(DURATION_CRITERIA["prodist_m8_zero_tolerance"], window_days=1, reading_minutes=15)
    q0 = find_hosting_capacity_duration(make_network(key), key, ts, zt, load_matrix=m, tolerance=0.01)
    assert q0.lambda_max <= det.lambda_max + 0.011
    with pytest.raises(ValueError, match="one value per load"):
        find_hosting_capacity_deterministic(make_network(key), key, load_multipliers=[1.0, 2.0])
