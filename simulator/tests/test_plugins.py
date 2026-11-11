"""Phase 8.3: networks and criteria are registered data, not code."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pandapower as pp
import pandapower.networks as pn
import pytest

from gridsense_sim.hosting_capacity import limits_for
from gridsense_sim.hosting_capacity.duration import (
    DURATION_CRITERIA,
    criterion_from_dict,
    load_criterion_file,
    register_criterion,
)
from gridsense_sim.networks import (
    NETWORKS,
    make_network,
    register_network,
    register_network_file,
    unregister_network,
)

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))


def test_register_network_needs_explicit_limits_and_protects_builtins(tmp_path) -> None:
    with pytest.raises(ValueError, match="built-in"):
        register_network("cigre_lv", pn.create_cigre_network_lv, default_framework="prodist_m8_bt",
                         voltage_levels_kv=(0.4,))
    with pytest.raises(ValueError, match="Unknown voltage framework"):
        register_network("custom_x", pn.create_cigre_network_lv, default_framework="nope", voltage_levels_kv=(0.4,))
    try:
        register_network("custom_x", pn.create_cigre_network_lv, default_framework="prodist_m8_bt",
                         voltage_levels_kv=(0.4,), country="Nowhere")
        lim = limits_for("custom_x")
        assert lim.framework == "prodist_m8_bt" and lim.voltage_levels_kv == (0.4,)
        assert "custom_x" in NETWORKS and not NETWORKS["custom_x"].hc_panel
    finally:
        unregister_network("custom_x")
    with pytest.raises(ValueError):
        limits_for("custom_x")
    unregister_network("cigre_lv")                   # built-ins survive
    assert "cigre_lv" in NETWORKS


def test_network_file_without_metadata_needs_explicit_limits(tmp_path) -> None:
    path = tmp_path / "net.json"
    pp.to_json(pn.create_cigre_network_lv(), str(path))
    with pytest.raises(ValueError, match="no sidecar metadata"):
        register_network_file("file_x", path)
    with pytest.raises(FileNotFoundError):
        register_network_file("file_x", tmp_path / "missing.json", default_framework="prodist_m8_bt",
                              voltage_levels_kv=(0.4,))
    try:
        info = register_network_file("file_x", path, default_framework="en50160_envelope", voltage_levels_kv=(0.4,))
        assert info.source.startswith("file:") and len(info.source) == 5 + 64
        assert len(make_network("file_x").load) == len(pn.create_cigre_network_lv().load)
    finally:
        unregister_network("file_x")


def test_criterion_files(tmp_path) -> None:
    spec = json.loads((ROOT / "protocol" / "criteria" / "prodist_m8_127v.json").read_text(encoding="utf-8"))
    crit = criterion_from_dict(spec)
    assert crit.adequate_lo_pu == pytest.approx(117 / 127) and crit.critical_hi_pu == pytest.approx(135 / 127)
    assert crit.to_verify
    try:
        assert load_criterion_file(ROOT / "protocol" / "criteria" / "prodist_m8_127v.json")[0] == crit
        assert load_criterion_file(ROOT / "protocol" / "criteria" / "prodist_m8_127v.json")[0] == crit   # idempotent
        clash = tmp_path / "clash.json"
        clash.write_text(json.dumps({**spec, "upper_precarious_max": 0.5}), encoding="utf-8")
        with pytest.raises(ValueError, match="already registered"):
            load_criterion_file(clash)
    finally:
        DURATION_CRITERIA.pop(crit.name, None)
    with pytest.raises(ValueError, match="already registered"):   # a built-in name cannot change meaning
        register_criterion(criterion_from_dict({**spec, "name": "prodist_m8_drp_drc"}))
    with pytest.raises(ValueError, match="Unknown criterion field"):
        criterion_from_dict({**spec, "typo": 1})


def test_runner_selects_a_criterion_from_a_file() -> None:
    import run_hosting_capacity_study as study

    path = str(ROOT / "protocol" / "criteria" / "prodist_m8_127v.json")
    try:
        args = study.parse_args(["--network", "cigre_lv", "--methods", "qsts", "--qsts-criterion-file", path,
                                 "--qsts-criterion", "prodist_m8_drp_drc_127v"])
        assert args.qsts_criterion == "prodist_m8_drp_drc_127v"
    finally:
        DURATION_CRITERIA.pop("prodist_m8_drp_drc_127v", None)
    with pytest.raises(SystemExit):
        study.parse_args(["--network", "cigre_lv", "--methods", "qsts", "--qsts-criterion", "prodist_m8_drp_drc_127v"])
    with pytest.raises(SystemExit):   # a network file needs limits (sidecar or flags)
        study.parse_args(["--network", "nofile", "--network-file", "does-not-exist.json"])
