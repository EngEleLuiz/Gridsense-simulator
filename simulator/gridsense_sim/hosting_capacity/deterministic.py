"""Deterministic hosting capacity: one coordinated penetration factor.

Searches the largest ``lambda_`` such that PV = ``allocation.mw_at(lambda_)``
on every bus at once, at the **critical operating point**
(``load_scale``, default :data:`~.conditions.DEFAULT_CRITICAL_LOAD_SCALE`),
causes no violation.

Changes vs the original module (doc 06 review):

* C1 -- evaluated at a configurable minimum-load operating point, not
  nominal load; the factor is recorded in ``conditions``.
* C3 -- baseline (PV = 0) must be feasible, else
  :class:`~.errors.BaselineInfeasibleError`.
* C10/C11 -- relative-exceedance ranking, slack/gen/MV buses excluded.
* N1 -- an unbounded search is flagged (``bounded=False``), never
  reported as a capacity.
* N2 -- the caller's ``net`` is never mutated (deep copy).
"""

from __future__ import annotations

import copy
from dataclasses import dataclass

import pandapower as pp

from .allocation import PVAllocation, apply_allocation, proportional_to_load
from .baseline import require_feasible_baseline
from .conditions import DEFAULT_CRITICAL_LOAD_SCALE, StudyConditions, apply_load_scale
from .limits import limits_for
from .scope import voltage_scope_buses
from .search import bisect_max_feasible
from .violations import check_violations


@dataclass
class DeterministicHCResult:
    """Result of a deterministic hosting-capacity search.

    Attributes:
        lambda_max: Largest verified-feasible penetration (PV / nominal load).
        lambda_fail: Smallest verified-infeasible penetration, ``None`` if unbounded.
        bounded: ``False`` means no violation was found up to the cap --
            ``lambda_max`` is then a lower bound, not a capacity.
        pv_mw_per_bus, total_pv_mw: PV at ``lambda_max``.
        binding_constraint: What fails first at ``lambda_fail``.
        reverse_power_flow_at_hc: Trafos exporting at ``lambda_max`` (MW, negative).
        iterations: Power flows spent in the search.
        conditions: Criterion and operating point used.
    """

    network_name: str
    lambda_max: float
    lambda_fail: float | None
    bounded: bool
    pv_mw_per_bus: dict[int, float]
    total_pv_mw: float
    binding_constraint: str | None
    reverse_power_flow_at_hc: dict[int, float]
    iterations: int
    conditions: StudyConditions


def find_hosting_capacity(
    net: pp.pandapowerNet,
    network_name: str,
    allocation: PVAllocation | None = None,
    *,
    framework: str | None = None,
    load_scale: float = DEFAULT_CRITICAL_LOAD_SCALE,
    tolerance: float = 0.01,
    max_expansions: int = 20,
    max_bisections: int = 40,
) -> DeterministicHCResult:
    """Deterministic HC by monotone bisection on ``lambda_``.

    Args:
        net: Nominal network. **Not mutated.**
        network_name: Registered network key (selects default limits).
        allocation: PV basis at ``lambda_ = 1``; defaults to
            :func:`proportional_to_load` on the *nominal* net.
        framework: Voltage framework key; ``None`` = network default.
        load_scale: Load multiplier for the critical operating point.
            Pass ``1.0`` to reproduce the old nominal-load number.
        tolerance, max_expansions, max_bisections: Search controls.

    Raises:
        BaselineInfeasibleError: the network violates at PV = 0.
        ValueError: empty allocation or invalid arguments.
    """
    limits = limits_for(network_name, framework)
    work = copy.deepcopy(net)
    allocation = allocation or proportional_to_load(work)  # nominal basis
    allocation.require_non_empty()

    apply_load_scale(work, load_scale)
    scope = voltage_scope_buses(work, limits)
    conditions = StudyConditions.build(network_name, limits, load_scale, len(scope))

    apply_allocation(work, allocation, 0.0)
    require_feasible_baseline(
        work, limits, scope, context=f"(deterministic, load_scale={load_scale})"
    )

    def _violates(lam: float) -> bool:
        apply_allocation(work, allocation, lam)
        return check_violations(work, limits, scope).has_violation

    outcome = bisect_max_feasible(
        _violates,
        tolerance=tolerance,
        max_expansions=max_expansions,
        max_bisections=max_bisections,
    )
    pf_count = outcome.evaluations

    binding: str | None = None
    if outcome.bounded and outcome.lambda_fail is not None:
        apply_allocation(work, allocation, outcome.lambda_fail)
        binding = check_violations(work, limits, scope).binding_constraint()
        pf_count += 1

    apply_allocation(work, allocation, outcome.lambda_max)
    at_hc = check_violations(work, limits, scope)
    pf_count += 1

    pv = allocation.mw_at(outcome.lambda_max)
    return DeterministicHCResult(
        network_name=network_name,
        lambda_max=outcome.lambda_max,
        lambda_fail=outcome.lambda_fail,
        bounded=outcome.bounded,
        pv_mw_per_bus=pv,
        total_pv_mw=round(sum(pv.values()), 6),
        binding_constraint=binding,
        reverse_power_flow_at_hc=at_hc.reverse_power_flow,
        iterations=pf_count,
        conditions=conditions,
    )
