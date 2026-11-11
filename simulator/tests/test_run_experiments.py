"""scripts/run_experiments.py: resume logic, manifest timing, invariant checks.

No hosting-capacity study is run here: the subprocess is replaced by a fake
and the results are tiny hand-written rows or Parquet files.
"""

from __future__ import annotations

import importlib.util
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "run_experiments.py"
_spec = importlib.util.spec_from_file_location("run_experiments", _SCRIPT)
rx = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rx)

CELL = {
    "id": "E1_demo_qsts_prodist",
    "network": "cigre_lv",
    "methods": ["qsts"],
    "qsts_criterion": "prodist_m8_drp_drc",
}


def _write_manifest(cell_dir: Path, **fields) -> Path:
    cell_dir.mkdir(parents=True, exist_ok=True)
    path = cell_dir / "manifest.json"
    path.write_text(json.dumps(fields), encoding="utf-8")
    return path


# --------------------------------------------------------------- is_done


def test_is_done_requires_success_and_identical_cell(tmp_path: Path) -> None:
    path = tmp_path / "manifest.json"
    assert not rx.is_done(path, CELL)  # missing

    _write_manifest(tmp_path, returncode=1, cell=CELL)
    assert not rx.is_done(path, CELL)  # failed run

    _write_manifest(tmp_path, returncode=0, cell={**CELL, "block_average_min": 60})
    assert not rx.is_done(path, CELL)  # spec changed under the same id

    _write_manifest(tmp_path, returncode=0)
    assert not rx.is_done(path, CELL)  # legacy manifest without the cell spec

    _write_manifest(tmp_path, returncode=0, cell=CELL)
    assert rx.is_done(path, CELL)


def test_is_done_treats_corrupt_manifest_as_not_done(tmp_path: Path) -> None:
    path = tmp_path / "manifest.json"
    path.write_text("{not json", encoding="utf-8")
    assert not rx.is_done(path, CELL)


# -------------------------------------------------------------- run_cell


@pytest.fixture()
def fake_runner(monkeypatch: pytest.MonkeyPatch) -> list[datetime]:
    """Replace the study subprocess with a slow no-op; record when it ran."""
    calls: list[datetime] = []

    def fake_run(args, **kwargs):
        calls.append(datetime.now(timezone.utc))
        time.sleep(1.1)  # long enough to move the clock past a whole second
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(rx.subprocess, "run", fake_run)
    monkeypatch.setattr(rx, "git_commit", lambda: "test")
    monkeypatch.setattr(rx, "package_versions", lambda: {})
    return calls


def test_started_utc_is_recorded_before_the_study_runs(tmp_path: Path, fake_runner) -> None:
    result = rx.run_cell(CELL, tmp_path)
    assert result["status"] == "ok"
    manifest = json.loads((tmp_path / CELL["id"] / "manifest.json").read_text(encoding="utf-8"))
    started = datetime.fromisoformat(manifest["started_utc"])
    assert started <= fake_runner[0]
    assert manifest["cell"] == CELL
    assert manifest["seconds"] >= 1.0


def test_run_cell_skips_only_an_unchanged_successful_cell(tmp_path: Path, fake_runner) -> None:
    rx.run_cell(CELL, tmp_path)
    assert len(fake_runner) == 1

    assert rx.run_cell(CELL, tmp_path)["status"] == "skipped (done)"
    assert len(fake_runner) == 1

    changed = {**CELL, "qsts_criterion": "prodist_m8_zero_tolerance"}
    assert rx.run_cell(changed, tmp_path)["status"] == "ok"
    assert len(fake_runner) == 2


# --------------------------------------------------------- read_results


def test_read_results_carries_operating_point_keys(tmp_path: Path) -> None:
    cell = {**CELL, "id": "E3_demo_hourly", "qsts_series": "data/series/demo.parquet",
            "block_average_min": 60}
    cell_dir = tmp_path / cell["id"]
    _write_manifest(cell_dir, id=cell["id"], cell=cell, returncode=0, seconds=1.0)
    part = cell_dir / "hc" / "method=qsts"
    part.mkdir(parents=True)
    payload = {"status": "ok", "criterion": "prodist_m8_drp_drc", "lambda_max": 1.5}
    pq.write_table(pa.table({"raw_value": [json.dumps(payload)]}), part / "part-0.parquet")

    (row,) = rx.read_results(tmp_path)
    assert row["method"] == "qsts"
    assert row["series_key"] == "data/series/demo.parquet"
    assert row["block_average_min"] == 60
    assert row["existing_pv"] == "remove"


# ---------------------------------------------------------------- checks


def _row(cell, method, criterion, lam, block=None, existing_pv=None, series="london"):
    return {"cell": cell, "series_key": series, "method": method, "criterion": criterion,
            "lambda_max": lam, "block_average_min": block, "existing_pv": existing_pv}


def _spec_like_rows(hourly_duration: float = 1.40) -> list[dict]:
    """Rows shaped like experiments/experiment_spec.json, in directory order."""
    return [
        _row("E1_det_stoch", "deterministic", "prodist_m8_bt", 1.30),
        _row("E1_qsts_en50160", "qsts", "en50160_weekly", 1.60),
        _row("E1_qsts_prodist", "qsts", "prodist_m8_drp_drc", 1.45),
        _row("E1_qsts_tau0", "qsts", "prodist_m8_zero_tolerance", 1.20),
        _row("E3_hourly_prodist", "qsts", "prodist_m8_drp_drc", hourly_duration, block=60),
    ]


def test_checks_never_pairs_hourly_with_ten_minute_results() -> None:
    # An hourly duration result far below the 10-min tau = 0 must not be
    # reported as a FAIL against it, nor silently dropped: it has no
    # counterpart at the same operating point, so it is a visible SKIP.
    out = rx.checks(_spec_like_rows(hourly_duration=0.50))
    assert not any(line.startswith("[FAIL]") for line in out), out
    assert any(line.startswith("[SKIP]") and "E3_hourly_prodist" in line for line in out), out
    assert any(line.startswith("[PASS]") and "E1_qsts_prodist" in line for line in out), out
    assert any(line.startswith("[PASS]") and "deterministic" in line for line in out), out


def test_checks_compares_every_matching_pair_not_only_the_first() -> None:
    rows = _spec_like_rows(hourly_duration=1.10) + [
        _row("E3_hourly_tau0", "qsts", "prodist_m8_zero_tolerance", 1.25, block=60),
    ]
    out = rx.checks(rows)
    hourly = [line for line in out if "E3_hourly_prodist" in line]
    assert len(hourly) == 1 and hourly[0].startswith("[FAIL]"), out  # 1.10 < 1.25
    # The hourly tau = 0 is never compared with the 10-min E1 duration cell.
    assert not any("E1_qsts_prodist" in line and "E3_hourly_tau0" in line for line in out), out


def test_checks_matches_existing_pv_policy_and_prodist_framework() -> None:
    rows = [
        _row("E5_det_keep", "deterministic", "prodist_m8_bt", 0.80, existing_pv="keep", series="sb"),
        _row("E5_det_en", "deterministic", "en50160_envelope", 0.10, existing_pv="keep", series="sb"),
        _row("E5_tau0_keep", "qsts", "prodist_m8_zero_tolerance", 0.70, existing_pv="keep", series="sb"),
        _row("E5_tau0_remove", "qsts", "prodist_m8_zero_tolerance", 0.75, series="sb"),
    ]
    out = rx.checks(rows)
    det_lines = [line for line in out if "deterministic" in line]
    assert any(line.startswith("[PASS]") and "E5_tau0_keep" in line for line in det_lines), out
    # The EN 50160 deterministic result is never used as the PRODIST bound.
    assert not any("E5_det_en" in line for line in out), out
    assert any(line.startswith("[SKIP]") and "E5_tau0_remove" in line for line in out), out


def test_checks_ignores_results_without_lambda() -> None:
    rows = [_row("x", "deterministic", "prodist_m8_bt", None),
            _row("y", "qsts", "prodist_m8_zero_tolerance", 1.0)]
    assert rx.checks(rows) == []
