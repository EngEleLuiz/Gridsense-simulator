"""Shared violation-checking logic for all three hosting-capacity
methods. Runs one AC power flow and reports whether -- and where --
any limit in a NetworkLimits was exceeded.

Using one function for all three methods is deliberate: it's what
makes the deterministic/stochastic/QSTS comparison a controlled one.
If each method re-implemented its own violation check, subtle
differences (e.g. one method checking transformer loading and another
forgetting to) would silently invalidate the comparison.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pandapower as pp

from .limits import NetworkLimits


@dataclass
class ViolationReport:
    """Result of a single power-flow violation check."""

    converged: bool
    has_violation: bool
    voltage_violations: dict[int, float] = field(default_factory=dict)  # bus -> vm_pu
    line_violations: dict[int, float] = field(default_factory=dict)  # line -> loading_percent
    trafo_violations: dict[int, float] = field(default_factory=dict)  # trafo -> loading_percent

    def binding_constraint(self) -> str | None:
        """Return a human-readable label for the first/worst violation.

        The dissertation's Chapter 5 needs to say *where* hosting
        capacity runs out, not just the MW number, so this picks the
        single most-exceeded element across all three violation
        types.
        """
        if not self.has_violation:
            return None
        candidates: list[tuple[float, str]] = []
        for bus, vm_pu in self.voltage_violations.items():
            candidates.append((abs(vm_pu - 1.0), f"voltage@bus_{bus} ({vm_pu:.4f} pu)"))
        for line, loading in self.line_violations.items():
            candidates.append((loading / 100.0, f"line_loading@line_{line} ({loading:.1f}%)"))
        for trafo, loading in self.trafo_violations.items():
            candidates.append((loading / 100.0, f"trafo_loading@trafo_{trafo} ({loading:.1f}%)"))
        return max(candidates, key=lambda c: c[0])[1]


def check_violations(net: pp.pandapowerNet, limits: NetworkLimits) -> ViolationReport:
    """Run pp.runpp(net) and check the result against `limits`.

    A non-converged power flow is reported as a violation (has_violation
    == True, all violation dicts empty) rather than raised, since "the
    grid can't even solve" is itself the binding constraint at that
    penetration level -- treating it as a hard failure would break the
    bisection search's assumption that it always gets a yes/no answer.
    """
    try:
        pp.runpp(net)
    except pp.LoadflowNotConverged:
        return ViolationReport(converged=False, has_violation=True)

    voltage_violations = {
        int(bus): float(vm_pu)
        for bus, vm_pu in net.res_bus["vm_pu"].items()
        if vm_pu < limits.v_min_pu or vm_pu > limits.v_max_pu
    }
    line_violations = {
        int(line): float(loading)
        for line, loading in net.res_line["loading_percent"].items()
        if loading > limits.max_line_loading_percent
    }
    trafo_violations = {
        int(trafo): float(loading)
        for trafo, loading in net.res_trafo["loading_percent"].items()
        if loading > limits.max_trafo_loading_percent
    } if not net.res_trafo.empty else {}

    has_violation = bool(voltage_violations or line_violations or trafo_violations)
    return ViolationReport(
        converged=True,
        has_violation=has_violation,
        voltage_violations=voltage_violations,
        line_violations=line_violations,
        trafo_violations=trafo_violations,
    )
