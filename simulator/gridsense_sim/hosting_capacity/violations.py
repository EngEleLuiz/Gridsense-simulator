"""Shared violation physics for all hosting-capacity methods.

One implementation for all three methods is what makes the comparison
*controlled*: they can only differ in how they choose PV scenarios,
never in how a scenario is judged.

The module separates two steps so that a future surrogate oracle
(Phase 7, ``HybridOracle``) can replace the expensive one without
touching the judgement:

* :func:`run_power_flow` -- the expensive physics call;
* :func:`assess_violations` -- a pure function of ``net.res_*``.

:func:`check_violations` composes both and is what the estimators use.

Binding-constraint ranking (review finding C10)
-----------------------------------------------
The old ranking compared ``|vm - 1|`` (e.g. 0.11) with
``loading / 100`` (e.g. 1.005), so *any* overload outranked *any*
overvoltage. Every violation is now ranked by its **relative
exceedance beyond its own limit**, a dimensionless number that is
comparable across types:

* overvoltage:  ``(vm - v_max) / v_max``
* undervoltage: ``(v_min - vm) / v_min``
* overload:     ``(loading - limit) / limit``

Isolated buses (review finding R04)
-----------------------------------
A bus in scope whose voltage comes back ``NaN`` is not energized (an
open line or switch disconnected it). Comparisons with ``NaN`` are
always false, so such a bus used to pass as "no violation" -- its load
silently unserved. It is now reported in ``isolated_buses``, counts as a
violation, and outranks every other violation (``exceedance = inf``).
Thermal results that are ``NaN`` (out-of-service branches) carry no
loading and are ignored.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Iterable

import pandapower as pp

from .limits import NetworkLimits
from .scope import voltage_scope_buses


@dataclass
class ViolationReport:
    """Outcome of judging one power-flow state.

    Attributes:
        converged: Whether the AC power flow converged.
        has_violation: True if any limit is exceeded **or** the power
            flow did not converge (see :func:`check_violations`).
        voltage_violations: bus -> vm_pu, buses in scope only.
        line_violations: line -> loading_percent.
        trafo_violations: trafo -> loading_percent.
        reverse_power_flow: trafo -> p_hv_mw for trafos exporting to the
            upstream grid (p_hv_mw < 0). Informational, not a violation.
        isolated_buses: Buses in scope with no voltage solution (NaN),
            i.e. not energized. Always a violation.
    """

    converged: bool
    has_violation: bool
    voltage_violations: dict[int, float] = field(default_factory=dict)
    line_violations: dict[int, float] = field(default_factory=dict)
    trafo_violations: dict[int, float] = field(default_factory=dict)
    reverse_power_flow: dict[int, float] = field(default_factory=dict)
    isolated_buses: list[int] = field(default_factory=list)
    _limits: NetworkLimits | None = field(default=None, repr=False, compare=False)

    def ranked_violations(self) -> list[tuple[float, str]]:
        """All violations as ``(relative_exceedance, label)``, worst first."""
        if self._limits is None:
            raise RuntimeError("ViolationReport was built without limits; cannot rank.")
        lim = self._limits
        out: list[tuple[float, str]] = [
            (math.inf, f"isolated@bus_{bus} (not energized)") for bus in self.isolated_buses
        ]
        for bus, vm in self.voltage_violations.items():
            if vm > lim.v_max_pu:
                exc, kind = (vm - lim.v_max_pu) / lim.v_max_pu, "overvoltage"
            else:
                exc, kind = (lim.v_min_pu - vm) / lim.v_min_pu, "undervoltage"
            out.append((exc, f"{kind}@bus_{bus} ({vm:.4f} pu)"))
        for line, loading in self.line_violations.items():
            exc = (loading - lim.max_line_loading_percent) / lim.max_line_loading_percent
            out.append((exc, f"line_loading@line_{line} ({loading:.1f}%)"))
        for trafo, loading in self.trafo_violations.items():
            exc = (loading - lim.max_trafo_loading_percent) / lim.max_trafo_loading_percent
            out.append((exc, f"trafo_loading@trafo_{trafo} ({loading:.1f}%)"))
        return sorted(out, key=lambda c: c[0], reverse=True)

    def binding_constraint(self) -> str | None:
        """Label of the most-exceeded element, ``None`` if no violation.

        Non-convergence returns ``"power_flow_non_convergence"``: a
        numerical (not physical) limit, but a valid reportable outcome.
        """
        if not self.has_violation:
            return None
        if not self.converged:
            return "power_flow_non_convergence"
        ranked = self.ranked_violations()
        return ranked[0][1] if ranked else None


def run_power_flow(net: pp.pandapowerNet) -> bool:
    """Run an AC power flow in place. Returns ``False`` on non-convergence."""
    try:
        pp.runpp(net)
    except pp.LoadflowNotConverged:
        return False
    return True


def assess_violations(
    net: pp.pandapowerNet,
    limits: NetworkLimits,
    voltage_buses: Iterable[int] | None = None,
) -> ViolationReport:
    """Judge the converged results already stored in ``net.res_*``.

    Args:
        net: Network with a converged power-flow result.
        limits: Criterion to apply.
        voltage_buses: Buses to voltage-check; defaults to
            :func:`voltage_scope_buses`. Pass a precomputed tuple in hot
            loops to avoid recomputing the scope.
    """
    scope = tuple(voltage_buses) if voltage_buses is not None else voltage_scope_buses(net, limits)
    vm = net.res_bus["vm_pu"].reindex(list(scope))
    isolated = sorted(int(bus) for bus, v in vm.items() if not math.isfinite(float(v)))
    voltage_violations = {
        int(bus): float(v)
        for bus, v in vm.items()
        if math.isfinite(float(v)) and (v < limits.v_min_pu or v > limits.v_max_pu)
    }
    line_violations = {
        int(i): float(x)
        for i, x in net.res_line["loading_percent"].items()
        if math.isfinite(float(x)) and x > limits.max_line_loading_percent
    } if not net.res_line.empty else {}
    trafo_violations: dict[int, float] = {}
    reverse: dict[int, float] = {}
    if not net.res_trafo.empty:
        trafo_violations = {
            int(i): float(x)
            for i, x in net.res_trafo["loading_percent"].items()
            if math.isfinite(float(x)) and x > limits.max_trafo_loading_percent
        }
        reverse = {
            int(i): float(p)
            for i, p in net.res_trafo["p_hv_mw"].items()
            if math.isfinite(float(p)) and p < 0.0
        }
    return ViolationReport(
        converged=True,
        has_violation=bool(isolated or voltage_violations or line_violations or trafo_violations),
        voltage_violations=voltage_violations,
        line_violations=line_violations,
        trafo_violations=trafo_violations,
        reverse_power_flow=reverse,
        isolated_buses=isolated,
        _limits=limits,
    )


def check_violations(
    net: pp.pandapowerNet,
    limits: NetworkLimits,
    voltage_buses: Iterable[int] | None = None,
) -> ViolationReport:
    """Run a power flow on ``net`` and judge it against ``limits``.

    A non-converged power flow is reported as a violation (with empty
    detail dicts) rather than raised: "the grid cannot be solved at
    this penetration" is itself the binding outcome, and the bisection
    needs a yes/no answer for every candidate.
    """
    if not run_power_flow(net):
        return ViolationReport(converged=False, has_violation=True, _limits=limits)
    return assess_violations(net, limits, voltage_buses)
