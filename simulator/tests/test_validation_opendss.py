"""pandapower agrees with OpenDSS, an independent power-flow engine."""

from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

import numpy as np
import pandapower as pp
import pytest

pytest.importorskip("opendssdirect")

from gridsense_sim.hosting_capacity import find_hosting_capacity_deterministic as det  # noqa: E402
from gridsense_sim.hosting_capacity.allocation import (  # noqa: E402
    apply_allocation,
    proportional_to_load,
    reactive_per_active,
)
from gridsense_sim.networks import make_network  # noqa: E402
from gridsense_sim.validation import (  # noqa: E402
    UnsupportedForOpenDss,
    compare_power_flow,
    hosting_capacity_with_opendss,
    solve_with_opendss,
    to_opendss,
)

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

MAX_DV, MAX_DLOADING = 1e-4, 0.1


@pytest.mark.parametrize("key", ["cigre_lv", "kerber_landnetz"])
@pytest.mark.parametrize("lam, pf", [(0.0, 1.0), (1.2, 1.0), (2.5, 1.0), (1.5, 0.92)])
def test_voltages_and_loadings_agree(key, lam, pf) -> None:
    net = make_network(key)
    net.load["p_mw"] *= 0.25
    net.load["q_mvar"] *= 0.25
    apply_allocation(net, proportional_to_load(make_network(key)), lam, reactive_per_active(pf))
    r = compare_power_flow(net)
    assert r["pandapower_converged"] and r["opendss_converged"]
    assert r["max_abs_dv_pu"] < MAX_DV
    assert r["max_abs_dline_percent"] < MAX_DLOADING and r["max_abs_dtrafo_percent"] < MAX_DLOADING


def test_without_a_magnetising_branch_the_engines_coincide() -> None:
    """cigre_lv transformers have no no-load data: the residual is then numerical (about 2e-8 pu)."""
    assert compare_power_flow(make_network("cigre_lv"))["max_abs_dv_pu"] < 1e-6
    net = make_network("kerber_landnetz")
    with_branch = compare_power_flow(net)["max_abs_dv_pu"]
    net.trafo["pfe_kw"] = 0.0
    net.trafo["i0_percent"] = 0.0
    assert compare_power_flow(net)["max_abs_dv_pu"] < 1e-6 < with_branch < MAX_DV


@pytest.mark.parametrize("key, framework", [
    ("cigre_lv", "prodist_m8_bt"), ("cigre_lv", "en50160_envelope"), ("kerber_landnetz", "prodist_m8_bt"),
])
def test_hosting_capacity_agrees_end_to_end(key, framework) -> None:
    ref = det(make_network(key), key, framework=framework)
    got = hosting_capacity_with_opendss(make_network(key), key, framework=framework)
    assert got["status"] == "ok" and got["bounded"] and got["resolved"]
    assert abs(got["lambda_max"] - ref.lambda_max) <= 0.01
    assert got["binding_constraint"].split("@")[0] == ref.binding_constraint.split("@")[0]


def test_infeasible_baseline_is_reported_by_both_engines() -> None:
    got = hosting_capacity_with_opendss(make_network("cigre_lv"), "cigre_lv", framework="prodist_m8_bt",
                                        load_scale=1.0)
    assert got["status"] == "baseline_infeasible" and got["binding_constraint"].startswith("undervoltage@bus_35")


def test_translation_details() -> None:
    net = make_network("cigre_lv")
    before = copy.deepcopy(net)
    cmds = to_opendss(net)
    for table in ("bus", "line", "trafo", "load", "sgen", "switch", "ext_grid"):   # caller's network not mutated
        assert net[table].equals(before[table]), table
    assert sum(c.startswith("New Line.") for c in cmds) == int(net.line["in_service"].sum())
    assert sum(c.startswith("New Transformer.") for c in cmds) == len(net.trafo)
    assert all("model=1 vminpu=0.01 vmaxpu=10" in c for c in cmds if c.startswith("New Load."))
    # closed bus-bus switches: both buses report the same voltage
    sol = solve_with_opendss(net)
    for _, s in net.switch[(net.switch["et"] == "b") & net.switch["closed"]].iterrows():
        a, b = int(s["bus"]), int(s["element"])
        assert sol.vm_pu[a] * net.bus.at[a, "vn_kv"] == pytest.approx(sol.vm_pu[b] * net.bus.at[b, "vn_kv"])
    # an off-nominal tap moves the low-voltage side the same way in both engines
    tapped = make_network("kerber_landnetz")
    tapped.trafo["tap_pos"], tapped.trafo["tap_neutral"] = 2, 0
    tapped.trafo["tap_step_percent"], tapped.trafo["tap_side"] = 2.5, "hv"
    if "tap_changer_type" in tapped.trafo:          # pandapower 3 applies a tap only with a declared type
        tapped.trafo["tap_changer_type"] = "Ratio"
    assert compare_power_flow(tapped)["max_abs_dv_pu"] < MAX_DV
    lv = int(tapped.trafo["lv_bus"].iloc[0])
    assert solve_with_opendss(tapped).vm_pu[lv] < solve_with_opendss(make_network("kerber_landnetz")).vm_pu[lv] - 0.03
    # parallel lines carry the same total current and are rated accordingly
    doubled = make_network("kerber_landnetz")
    doubled.line["parallel"] = 2
    r = compare_power_flow(doubled)
    assert r["max_abs_dv_pu"] < MAX_DV and r["max_abs_dline_percent"] < MAX_DLOADING


def test_unsupported_networks_are_refused_not_approximated() -> None:
    net = make_network("cigre_lv")
    two = copy.deepcopy(net)
    pp.create_ext_grid(two, bus=int(two.bus.index[5]))
    with pytest.raises(UnsupportedForOpenDss, match="external grid"):
        to_opendss(two)
    opened = copy.deepcopy(net)
    pp.create_switch(opened, bus=int(opened.line["from_bus"].iloc[0]), element=int(opened.line.index[0]),
                     et="l", closed=False)
    with pytest.raises(UnsupportedForOpenDss, match="switch"):
        to_opendss(opened)
    shunt = copy.deepcopy(net)
    pp.create_shunt(shunt, bus=int(shunt.bus.index[3]), q_mvar=0.01)
    with pytest.raises(UnsupportedForOpenDss, match="shunt"):
        to_opendss(shunt)
    zip_load = copy.deepcopy(net)
    zip_load.load["const_z_p_percent"] = 50.0
    with pytest.raises(UnsupportedForOpenDss, match="ZIP"):
        to_opendss(zip_load)
    shifter = make_network("kerber_landnetz")
    shifter.trafo["tap_pos"], shifter.trafo["tap_neutral"], shifter.trafo["tap_step_percent"] = 1, 0, 2.5
    shifter.trafo["tap_step_degree"] = 5.0
    if "tap_changer_type" in shifter.trafo:
        shifter.trafo["tap_changer_type"] = "Ideal"
    with pytest.raises(UnsupportedForOpenDss, match="[Tt]ap"):
        to_opendss(shifter)
    with pytest.raises(UnsupportedForOpenDss, match="unbalanced"):
        to_opendss(make_network("ieee_european_lv_offpeak"))


def test_validation_script(tmp_path, capsys) -> None:
    import validate_against_opendss as script

    code = script.main(["--networks", "cigre_lv", "kerber_landnetz", "ieee_european_lv_offpeak",
                        "--frameworks", "prodist_m8_bt", "--out", str(tmp_path)])
    assert code == 0                                                     # unsupported networks do not fail the check
    report = json.loads((tmp_path / "opendss.json").read_text(encoding="utf-8"))
    status = {r["network"]: r["status"] for r in report["rows"]}
    assert status == {"cigre_lv": "ok", "kerber_landnetz": "ok", "ieee_european_lv_offpeak": "unsupported"}
    ok = [r for r in report["rows"] if r["status"] == "ok"]
    assert all(r["abs_dlambda"] <= 0.01 and set(r["states"]) == {"no_pv", "at_capacity", "above_capacity"} for r in ok)
    assert all(np.isfinite(r["max_abs_dv_pu"]) for r in ok)
    assert "2 agree, 0 differ, 1 not covered" in capsys.readouterr().out
    assert "| cigre_lv | prodist_m8_bt |" in (tmp_path / "opendss.md").read_text(encoding="utf-8")
    # an impossible acceptance threshold turns the same comparison into a failure
    assert script.main(["--networks", "kerber_landnetz", "--frameworks", "prodist_m8_bt", "--max-dv", "1e-9",
                        "--out", str(tmp_path / "strict")]) == 1
