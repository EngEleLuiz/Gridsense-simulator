"""Phase 7.5: the frozen protocol is valid and its executor is resumable."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import run_hosting_capacity_study as study
import run_protocol

PROTOCOL = ROOT / "protocol" / "protocol-v1.json"


def test_every_protocol_run_passes_the_runner_validation() -> None:
    protocol, _ = run_protocol.load_protocol(PROTOCOL)
    names = [r["name"] for r in protocol["runs"]]
    assert len(names) == len(set(names)), "run names must be unique"
    for r in protocol["runs"]:
        args = [*r["args"], "--output-dir", "x", "--run-label", f"{protocol['id']}/{r['name']}"]
        if any(a.endswith(".parquet") for a in args):  # file existence is checked at run time
            continue
        study.parse_args(args)  # raises SystemExit on any invalid combination


def test_protocol_covers_the_decided_design() -> None:
    protocol, _ = run_protocol.load_protocol(PROTOCOL)
    groups = {r["group"] for r in protocol["runs"]}
    assert {"A_deterministic_panel", "B_three_phase_uk", "C_stochastic", "D_simbench_annual",
            "E_sensitivity_simbench", "F_london_secondary"} == groups
    d = [r for r in protocol["runs"] if r["group"] == "D_simbench_annual"]
    assert all("--qsts-network-profiles" in r["args"] for r in d)
    assert not any("--qsts-aggregate-load" in r["args"] for r in d)  # per-load is the main case
    assert protocol["fixed_parameters"]["stochastic_scenarios"] >= 36  # p10 CI needs 36


def test_list_dry_run_and_resume(tmp_path, capsys, monkeypatch) -> None:
    protocol, _ = run_protocol.load_protocol(PROTOCOL)
    small = {**protocol, "output_dir": str(tmp_path / "out"), "runs": protocol["runs"][:2]}
    pfile = tmp_path / "p.json"
    pfile.write_text(json.dumps(small), encoding="utf-8")
    monkeypatch.setattr(run_protocol, "ROOT", tmp_path)
    assert run_protocol.main(["--protocol", str(pfile), "--list"]) == 0
    assert "pending" in capsys.readouterr().out
    assert run_protocol.main(["--protocol", str(pfile), "--dry-run"]) == 0
    assert "--run-label" in capsys.readouterr().out
    marks = tmp_path / "out" / "_runs"
    marks.mkdir(parents=True)
    (marks / f"{small['runs'][0]['name']}.json").write_text(json.dumps({"exit_code": 0}), encoding="utf-8")
    run_protocol.main(["--protocol", str(pfile), "--list"])
    out = capsys.readouterr().out
    assert "done" in out and "pending" in out


def test_refuses_dirty_tree(tmp_path, monkeypatch) -> None:
    pfile = tmp_path / "p.json"
    pfile.write_text(json.dumps({"id": "t", "output_dir": str(tmp_path / "o"), "runs": []}), encoding="utf-8")
    monkeypatch.setattr(run_protocol, "git", lambda *a: " M file.py" if a[0] == "status" else "abc")
    assert run_protocol.main(["--protocol", str(pfile)]) == 2
    assert run_protocol.main(["--protocol", str(pfile), "--allow-dirty"]) == 0
