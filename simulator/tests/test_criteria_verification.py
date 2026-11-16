"""Criteria carry their verification status, and cited runs can refuse unverified ones."""

from __future__ import annotations

import dataclasses
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import run_hosting_capacity_study as study  # noqa: E402
import run_protocol  # noqa: E402
from gridsense_sim.hosting_capacity.duration import (  # noqa: E402
    DURATION_CRITERIA,
    criterion_from_dict,
    register_criterion,
    unverified,
)


def test_every_unverified_criterion_says_what_was_checked() -> None:
    for name, crit in DURATION_CRITERIA.items():
        if crit.to_verify:
            assert crit.verification, f"{name}: to_verify without a verification note"
    spec = json.loads((ROOT / "protocol" / "criteria" / "prodist_m8_127v.json").read_text(encoding="utf-8"))
    assert spec["to_verify"] and spec["verification"]
    assert criterion_from_dict(spec).verification == spec["verification"]


def test_unverified_lists_only_flagged_criteria() -> None:
    assert "prodist_m8_drp_drc" in unverified(["prodist_m8_drp_drc", "zero_tolerance", "no_such_criterion"])
    verified = dataclasses.replace(DURATION_CRITERIA["prodist_m8_drp_drc"], name="_test_verified",
                                   to_verify=False, verification="checked in a test")
    register_criterion(verified)
    try:
        assert unverified(["_test_verified"]) == []
    finally:
        DURATION_CRITERIA.pop("_test_verified", None)


def test_runner_refuses_an_unverified_criterion_on_request() -> None:
    base = ["--network", "cigre_lv", "--methods", "qsts", "--qsts-criterion", "prodist_m8_drp_drc"]
    study.parse_args(base)                                             # default: allowed, flag stored with the result
    with pytest.raises(SystemExit):
        study.parse_args([*base, "--require-verified-criterion"])
    study.parse_args(["--network", "cigre_lv", "--methods", "deterministic", "--require-verified-criterion"])


def test_protocol_executor_reports_and_can_refuse(tmp_path, capsys, monkeypatch) -> None:
    protocol = {"id": "t", "output_dir": str(tmp_path / "o"), "runs": [
        {"group": "g", "name": "det", "args": ["--network", "cigre_lv", "--methods", "deterministic"],
         "estimate_min": 1},
        {"group": "g", "name": "drp", "args": ["--network", "cigre_lv", "--methods", "qsts",
                                              "--qsts-criterion", "prodist_m8_drp_drc"], "estimate_min": 1},
    ]}
    pfile = tmp_path / "p.json"
    pfile.write_text(json.dumps(protocol), encoding="utf-8")
    assert run_protocol.unverified_criteria(protocol["runs"]) == {"drp": "prodist_m8_drp_drc"}
    assert run_protocol.main(["--protocol", str(pfile), "--list"]) == 0
    assert "1 run(s) use a criterion still marked to_verify: prodist_m8_drp_drc" in capsys.readouterr().out
    monkeypatch.setattr(run_protocol, "git", lambda *a: "" if a[0] == "status" else "abc")
    assert run_protocol.main(["--protocol", str(pfile), "--require-verified-criteria"]) == 3
    assert "to_verify" in capsys.readouterr().err
    assert run_protocol.main(["--protocol", str(pfile), "--require-verified-criteria", "--only", "det",
                              "--dry-run"]) == 0


def test_frozen_protocols_name_their_unverified_criteria() -> None:
    """Not a gate (the edges are still to be read in Annex VIII): a visible, tested inventory."""
    for name in ("protocol-v1.json", "protocol-v2.json"):
        protocol, _ = run_protocol.load_protocol(ROOT / "protocol" / name)
        used = set(run_protocol.unverified_criteria(protocol["runs"]).values())
        assert used == {"prodist_m8_drp_drc", "prodist_m8_zero_tolerance", "en50160_weekly"}
