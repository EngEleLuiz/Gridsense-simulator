"""violations.py: ranking (C10), non-convergence, baseline regression."""

from __future__ import annotations

from gridsense_sim.hosting_capacity import NetworkLimits, ViolationReport, check_violations, limits_for

LIM = NetworkLimits(v_min_pu=0.90, v_max_pu=1.10)


def test_ranking_uses_relative_exceedance_not_mixed_units() -> None:
    """C10 regression: 1.12 pu (1.8% over) must outrank a 100.5% trafo (0.5% over).

    The old ranking compared |vm-1| = 0.12 with loading/100 = 1.005 and
    always picked the trafo.
    """
    r = ViolationReport(
        converged=True, has_violation=True,
        voltage_violations={7: 1.12}, trafo_violations={0: 100.5}, _limits=LIM,
    )
    assert r.binding_constraint().startswith("overvoltage@bus_7")


def test_undervoltage_is_labelled_as_such() -> None:
    r = ViolationReport(True, True, voltage_violations={3: 0.85}, _limits=LIM)
    assert r.binding_constraint().startswith("undervoltage@bus_3")


def test_non_convergence_is_a_reportable_binding_constraint() -> None:
    r = ViolationReport(converged=False, has_violation=True, _limits=LIM)
    assert r.binding_constraint() == "power_flow_non_convergence"


def test_no_violation_has_no_binding_constraint() -> None:
    assert ViolationReport(True, False, _limits=LIM).binding_constraint() is None


def test_cigre_base_case_feasible_under_default_limits(cigre) -> None:
    import copy

    assert check_violations(copy.deepcopy(cigre), limits_for("cigre_lv")).has_violation is False
