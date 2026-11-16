"""Source (external grid) voltage as a study condition on balanced networks.

Tap control is out of scope (fixed taps). This sensitivity shows how far a
capacity depends on the voltage the feeder is operated at.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

import run_hosting_capacity_study as study  # noqa: E402


def _run(tmp_path, *extra: str) -> dict:
    import pyarrow.parquet as pq

    study.main(["--network", "cigre_lv", "--methods", "deterministic", "--framework", "prodist_m8_bt",
                "--output-dir", str(tmp_path), *extra])
    files = sorted(tmp_path.rglob("*.parquet"), key=lambda p: p.stat().st_mtime)
    return json.loads(pq.read_table(files[-1]).to_pylist()[0]["raw_value"])


def test_lower_source_voltage_leaves_more_room_for_pv(tmp_path) -> None:
    base = _run(tmp_path / "a")
    low = _run(tmp_path / "b", "--source-vm-pu", "0.98")
    same = _run(tmp_path / "c", "--source-vm-pu", str(low["params"]["source_vm_pu_network_default"][0]))
    assert base["status"] == low["status"] == "ok"
    assert "source_vm_pu" not in base["params"] and "source_vm_pu" not in base
    assert low["params"]["source_vm_pu"] == 0.98 and low["source_vm_pu"] == 0.98
    assert low["lambda_max"] > base["lambda_max"]          # overvoltage-bound: more headroom
    assert same["lambda_max"] == pytest.approx(base["lambda_max"])   # overriding with the default changes nothing


def test_source_voltage_at_the_upper_limit_is_a_recorded_infeasible_baseline(tmp_path) -> None:
    high = _run(tmp_path, "--source-vm-pu", "1.06")         # above the PRODIST adequate band (1.05)
    assert high["status"] == "baseline_infeasible" and high["bounded"] is False


def test_source_voltage_reaches_the_duration_qsts(tmp_path) -> None:
    import pyarrow.parquet as pq

    common = ["--network", "cigre_lv", "--methods", "qsts", "--qsts-criterion", "prodist_m8_drp_drc",
              "--qsts-total-steps", "168", "--qsts-steps-per-day", "24", "--qsts-peak-load", "1.0",
              "--tolerance", "0.05"]
    out = {}
    for name, extra in (("default", []), ("low", ["--source-vm-pu", "0.98"])):
        study.main([*common, *extra, "--output-dir", str(tmp_path / name)])
        out[name] = json.loads(pq.read_table(next((tmp_path / name).rglob("*.parquet"))).to_pylist()[0]["raw_value"])
    assert out["low"]["status"] == out["default"]["status"] == "ok"
    assert out["low"]["lambda_max"] > out["default"]["lambda_max"]


def test_source_voltage_is_validated() -> None:
    with pytest.raises(SystemExit):
        study.parse_args(["--network", "cigre_lv", "--source-vm-pu", "1.3"])
