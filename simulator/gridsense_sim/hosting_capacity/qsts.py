"""QSTS hosting capacity (zero-tolerance criterion).

Bisects the same ``lambda_`` as the deterministic method, but a
candidate is infeasible if **any** step of the time series violates.

Changes vs the original module (doc 06 review):

* C5 -- a window with no daylight raises :class:`~.errors.NoDaylightError`.
* C6 -- one precomputed :class:`~.timeseries.TimeSeries` for all candidates.
* C12 -- load Q is scaled together with P.
* C3 -- the PV-free baseline must be clean at every step.
* Binding report -- taken at the *actual* first violating step of
  ``lambda_fail`` (the old code re-applied PV at peak with whatever
  load the last step left behind).
* Night steps are skipped during the search. Justification: with the
  baseline verified clean at every step and PV output 0 at night, the
  night state equals the baseline state, so it cannot violate. This
  halves the cost without changing the answer.
* N2 -- the caller's ``net`` is never mutated.

STILL OPEN (S1 work, review findings C7/C8): no duration criterion
tau_bar (Eq. 3.20-3.21), no tap controllers (Eq. 3.18-3.19). The
result records ``criterion="zero_tolerance"`` so this is never hidden.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass

import numpy as np
import pandapower as pp

from .allocation import PVAllocation, apply_allocation, proportional_to_load
from .baseline import require_feasible_baseline
from .conditions import StudyConditions
from .errors import NoDaylightError
from .limits import limits_for
from .scope import voltage_scope_buses
from .search import bisect_max_feasible
from .timeseries import TimeSeries, build_synthetic_series
from .violations import ViolationReport, check_violations

DEFAULT_STEPS_PER_DAY = 288
DEFAULT_TOTAL_STEPS = DEFAULT_STEPS_PER_DAY * 60


@dataclass
class QstsHCResult:
    """Result of a zero-tolerance QSTS hosting-capacity search."""

    network_name: str
    lambda_max: float
    lambda_fail: float | None
    bounded: bool
    pv_mw_per_bus: dict[int, float]
    total_pv_mw: float
    binding_constraint: str | None
    first_violating_step: int | None  # absolute step index at lambda_fail
    total_steps: int
    steps_per_day: int
    daylight_steps: int
    power_flows: int
    criterion: str
    conditions: StudyConditions


def find_hosting_capacity(
    net: pp.pandapowerNet,
    network_name: str,
    allocation: PVAllocation | None = None,
    *,
    series: TimeSeries | None = None,
    total_steps: int = DEFAULT_TOTAL_STEPS,
    steps_per_day: int = DEFAULT_STEPS_PER_DAY,
    start_step: int = 0,
    framework: str | None = None,
    tolerance: float = 0.01,
    max_expansions: int = 20,
    max_bisections: int = 20,
    profile_seed: int | None = 42,
) -> QstsHCResult:
    """Zero-tolerance QSTS HC.

    Args:
        net: Nominal network. **Not mutated.**
        network_name: Registered network key.
        allocation: PV basis at ``lambda_ = 1`` (nominal-load proportional by default).
        series: Precomputed series; if ``None``, a synthetic one is built
            from ``total_steps``/``steps_per_day``/``start_step``/``profile_seed``.
        framework: Voltage framework key.
        tolerance, max_expansions, max_bisections: Search controls.

    Raises:
        NoDaylightError: the series has no step with PV output > 0.
        BaselineInfeasibleError: some step violates with PV = 0.
    """
    limits = limits_for(network_name, framework)
    work = copy.deepcopy(net)
    allocation = allocation or proportional_to_load(work)
    allocation.require_non_empty()

    ts = series or build_synthetic_series(total_steps, steps_per_day, profile_seed, start_step)
    daylight = ts.daylight_steps
    if daylight.size == 0:
        raise NoDaylightError(
            f"Time window [{ts.start_step}, {ts.start_step + ts.n_steps}) at "
            f"{ts.steps_per_day} steps/day has no daylight; QSTS hosting capacity "
            f"is undefined for it."
        )

    scope = voltage_scope_buses(work, limits)
    conditions = StudyConditions.build(network_name, limits, None, len(scope))
    base_p = work.load["p_mw"].to_numpy(copy=True)
    base_q = work.load["q_mvar"].to_numpy(copy=True)
    power_flows = 0

    def _set_step(t: int, lam: float) -> None:
        m = float(ts.load_mult[t])
        work.load["p_mw"] = base_p * m
        work.load["q_mvar"] = base_q * m
        apply_allocation(work, allocation, lam * float(ts.pv_mult[t]))

    # Baseline gate over the whole series.
    for t in range(ts.n_steps):
        _set_step(t, 0.0)
        power_flows += 1
        require_feasible_baseline(
            work, limits, scope, context="(qsts)", step=ts.start_step + t
        )

    first_fail: dict[float, tuple[int, ViolationReport]] = {}

    def _violates(lam: float) -> bool:
        nonlocal power_flows
        for t in daylight:
            _set_step(int(t), lam)
            power_flows += 1
            report = check_violations(work, limits, scope)
            if report.has_violation:
                first_fail[lam] = (ts.start_step + int(t), report)
                return True
        return False

    out = bisect_max_feasible(
        _violates,
        tolerance=tolerance,
        max_expansions=max_expansions,
        max_bisections=max_bisections,
    )

    step: int | None = None
    binding: str | None = None
    if out.bounded and out.lambda_fail is not None:
        step, report = first_fail[out.lambda_fail]
        binding = report.binding_constraint()

    pv = allocation.mw_at(out.lambda_max)
    return QstsHCResult(
        network_name=network_name,
        lambda_max=out.lambda_max,
        lambda_fail=out.lambda_fail,
        bounded=out.bounded,
        pv_mw_per_bus=pv,
        total_pv_mw=round(sum(pv.values()), 6),
        binding_constraint=binding,
        first_violating_step=step,
        total_steps=ts.n_steps,
        steps_per_day=ts.steps_per_day,
        daylight_steps=int(daylight.size),
        power_flows=power_flows,
        criterion="zero_tolerance",
        conditions=conditions,
    )


__all__ = ["QstsHCResult", "find_hosting_capacity", "DEFAULT_STEPS_PER_DAY", "DEFAULT_TOTAL_STEPS"]
