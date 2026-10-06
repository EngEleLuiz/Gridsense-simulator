"""scripts/run_hosting_capacity_study.py: payload v2, status rows, provenance."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pyarrow.parquet as pq

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import run_hosting_capacity_study as study  # noqa: E402


def _records(**over):
    kw = dict(
        network_name="cigre_lv",
        methods=["deterministic", "stochastic"],
        run_id="r1",
        run_timestamp="2026-10-05T00:00:00+00:00",
        deterministic_kwargs={"framework": "prodist_m8_bt", "load_scale": 0.25, "tolerance": 0.05},
        stochastic_kwargs={"framework": "prodist_m8_bt", "load_scale": 0.25,
                           "tolerance": 0.05, "n_scenarios": 4, "seed": 1},
        qsts_kwargs={},
        stochastic_alpha=0.25,
    )
    kw.update(over)
    return study.build_records(**kw)


def test_payload_v2_envelope_and_provenance() -> None:
    for rec in _records():
        p = json.loads(rec["raw_value"])
        assert p["schema_version"] == 2 and p["status"] == "ok"
        assert p["conditions"]["framework"] == "prodist_m8_bt"
        assert p["conditions"]["load_scale"] == 0.25
        assert "pandapower" in p["provenance"]["packages"]
        assert p["bounded"] is True and p["total_pv_mw"] > 0


def test_stochastic_row_reports_alpha_quantile() -> None:
    p = json.loads(_records(methods=["stochastic"])[0]["raw_value"])
    assert p["alpha"] == 0.25
    assert p["n_scenarios"] == 4
    assert p["lambda_max"] == p["lambda_max"]  # finite, not NaN


def test_baseline_infeasible_is_recorded_not_raised() -> None:
    rec = _records(
        methods=["deterministic"],
        deterministic_kwargs={"framework": "prodist_m8_bt", "load_scale": 1.0},
    )[0]
    p = json.loads(rec["raw_value"])
    assert p["status"] == "baseline_infeasible"
    assert p["bounded"] is False and "bus_35" in p["error"]
    assert p["conditions"]["framework"] == "prodist_m8_bt"
    assert p["conditions"]["load_scale"] == 1.0


def test_parquet_roundtrip(tmp_path) -> None:
    recs = _records(methods=["deterministic"])
    path = study.write_records_to_parquet(recs, tmp_path)
    rows = pq.read_table(path).to_pylist()
    assert rows[0]["method"] == "deterministic"
    assert json.loads(rows[0]["raw_value"])["schema_version"] == 2


def test_unexpected_error_is_recorded_and_sibling_results_survive() -> None:
    """R08 regression: a non-HostingCapacityError in one method used to
    propagate out of build_records and discard the methods already run."""
    recs = _records(methods=["deterministic", "qsts"], qsts_kwargs={"total_steps": 0})
    by_method = {r["method"]: json.loads(r["raw_value"]) for r in recs}
    assert by_method["deterministic"]["status"] == "ok"
    assert by_method["qsts"]["status"] == "error"
    assert by_method["qsts"]["error"].startswith("ValueError")
    assert by_method["qsts"]["bounded"] is False


def test_main_exits_nonzero_but_writes_rows_when_a_method_errors(tmp_path, monkeypatch) -> None:
    def boom(*_a, **_k):
        raise RuntimeError("simulated crash")

    monkeypatch.setattr(study, "_run_stochastic", boom)
    import pytest

    with pytest.raises(SystemExit) as info:
        study.main(["--network", "cigre_lv", "--methods", "deterministic", "stochastic",
                    "--tolerance", "0.05", "--output-dir", str(tmp_path)])
    assert info.value.code == 1
    rows = [r for f in tmp_path.rglob("*.parquet") for r in pq.read_table(f).to_pylist()]
    assert {r["method"] for r in rows} == {"deterministic", "stochastic"}


def test_invalid_arguments_fail_before_any_power_flow(monkeypatch) -> None:
    """R09 regression: --mc-alpha 1.5 used to fail only after the Monte Carlo."""
    import pytest

    def must_not_run(*_a, **_k):
        raise AssertionError("an estimator ran before argument validation")

    monkeypatch.setattr(study, "build_records", must_not_run)
    bad_args = [
        ["--mc-alpha", "1.5"],
        ["--mc-alpha", "0"],
        ["--mc-n-scenarios", "0"],
        ["--mc-adoption-min", "0.8", "--mc-adoption-max", "0.5"],
        ["--mc-size-dispersion", "1.0"],
        ["--qsts-total-steps", "0"],
        ["--load-scale", "0"],
        ["--tolerance", "-0.1"],
    ]
    for extra in bad_args:
        with pytest.raises(SystemExit) as info:
            study.main(["--network", "cigre_lv", *extra])
        assert info.value.code == 2, extra


def test_payload_carries_resolved_flag() -> None:
    for rec in _records():
        assert json.loads(rec["raw_value"])["resolved"] is True
