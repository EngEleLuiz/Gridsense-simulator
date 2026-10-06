"""limits.py (C2) and scope.py (C11)."""

from __future__ import annotations

import pandapower.networks as pn
import pytest

from gridsense_sim.hosting_capacity import (
    CriterionKind,
    NetworkLimits,
    check_violations,
    limits_for,
    voltage_scope_buses,
)


def test_cigre_default_framework_is_unchanged_en50160_envelope() -> None:
    lim = limits_for("cigre_lv")
    assert (lim.v_min_pu, lim.v_max_pu) == (0.90, 1.10)
    assert lim.framework == "en50160_envelope"
    assert lim.kind is CriterionKind.STATISTICAL


def test_prodist_framework_is_selectable_and_instantaneous() -> None:
    lim = limits_for("cigre_lv", "prodist_m8_bt")
    assert (lim.v_min_pu, lim.v_max_pu) == (0.92, 1.05)
    assert lim.kind is CriterionKind.INSTANTANEOUS


def test_transmission_cases_default_to_ansi_range_a() -> None:
    lim = limits_for("case14")
    assert (lim.v_min_pu, lim.v_max_pu, lim.framework) == (0.95, 1.05, "ansi_c84_range_a")


@pytest.mark.parametrize("network, framework", [("nope", None), ("cigre_lv", "nope")])
def test_unknown_network_or_framework_raises(network: str, framework: str | None) -> None:
    with pytest.raises(ValueError):
        limits_for(network, framework)


def test_network_limits_rejects_inverted_band() -> None:
    with pytest.raises(ValueError):
        NetworkLimits(v_min_pu=1.05, v_max_pu=0.95)


def test_cigre_scope_is_lv_only(cigre) -> None:
    scope = voltage_scope_buses(cigre, limits_for("cigre_lv"))
    assert all(cigre.bus.at[b, "vn_kv"] == pytest.approx(0.4) for b in scope)
    assert int(cigre.ext_grid.bus.iloc[0]) not in scope
    assert len(scope) == int((cigre.bus.vn_kv == 0.4).sum())


def test_case14_scope_excludes_slack_and_generator_buses() -> None:
    net = pn.case14()
    scope = set(voltage_scope_buses(net, limits_for("case14")))
    assert not scope & {int(b) for b in net.ext_grid.bus}
    assert not scope & {int(b) for b in net.gen.bus}


def test_case14_slack_is_never_reported_as_a_violation() -> None:
    """C11 regression: the slack (1.06 pu setpoint) is out of scope.

    Finding recorded during this review: case14 still violates ANSI
    Range A at baseline on *load* buses (6, 8, 9, 11, 12 at ~1.055-1.062
    pu, driven by the case's generator setpoints). Hosting-capacity
    studies on case14 therefore raise BaselineInfeasibleError under
    ANSI Range A -- by design, see errors.BaselineInfeasibleError.
    """
    net = pn.case14()
    report = check_violations(net, limits_for("case14"))
    slack = {int(b) for b in net.ext_grid.bus}
    assert not slack & set(report.voltage_violations)
    assert {6, 8, 9, 11, 12} <= set(report.voltage_violations)
