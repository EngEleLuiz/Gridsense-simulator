"""Baseline feasibility gate (review finding C3).

Every estimator calls :func:`require_feasible_baseline` with PV = 0 at
its operating point *before* searching. See
:class:`~.errors.BaselineInfeasibleError` for why a violated baseline
invalidates bisection.
"""

from __future__ import annotations

from typing import Iterable

import pandapower as pp

from .errors import BaselineInfeasibleError
from .limits import NetworkLimits
from .violations import ViolationReport, check_violations


def require_feasible_baseline(
    net: pp.pandapowerNet,
    limits: NetworkLimits,
    voltage_buses: Iterable[int],
    context: str = "",
    step: int | None = None,
) -> ViolationReport:
    """Check ``net`` (assumed to carry zero HC PV) and raise if it violates.

    Returns the clean report on success, so callers can log margins.
    """
    report = check_violations(net, limits, voltage_buses)
    if report.has_violation:
        where = f" at step {step}" if step is not None else ""
        raise BaselineInfeasibleError(
            f"Baseline (PV = 0){where} already violates '{limits.framework}' "
            f"[{limits.v_min_pu}, {limits.v_max_pu}] pu: "
            f"{report.binding_constraint()}. {context}".strip(),
            report=report,
            step=step,
        )
    return report
