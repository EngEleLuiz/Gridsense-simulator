"""Deterministic hosting capacity via bisection on a single global PV
penetration factor `lambda_`, applied simultaneously across all load
buses (coordinated adoption) -- as opposed to the stochastic method's
uncoordinated, independently-sampled per-bus sizing.

This models "how far can penetration go, growing everywhere at once,
before the first violation anywhere" -- a single scalar answer, which
is the point of the deterministic method: fast, repeatable, and a
useful upper/lower reference bound against which the stochastic and
QSTS distributions can be compared.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandapower as pp

from .allocation import PVAllocation, apply_allocation, proportional_to_load
from .limits import limits_for
from .violations import check_violations


@dataclass
class DeterministicHCResult:
    """Result of a deterministic (bisection) hosting-capacity search."""

    network_name: str
    lambda_max: float
    pv_mw_per_bus: dict[int, float]
    total_pv_mw: float
    binding_constraint: str | None
    iterations: int


def find_hosting_capacity(
    net: pp.pandapowerNet,
    network_name: str,
    allocation: PVAllocation | None = None,
    tolerance: float = 0.01,
    max_expansions: int = 20,
    max_bisections: int = 40,
) -> DeterministicHCResult:
    """Bisect the largest `lambda_` such that PV = allocation.mw_at(lambda_),
    applied to every load bus at once, causes no violation.

    Args:
        net: A pandapower network already at its base-case load. Not
            mutated in a way that outlives this call in any
            problematic sense -- sgens are added/updated in place
            (see allocation.apply_allocation), but the caller owns
            the net and can discard/reset it as needed.
        network_name: Used to look up the violation criterion via
            limits.limits_for -- see limits.py for why this can't be
            a single global constant.
        allocation: How PV is distributed across buses at lambda_ ==
            1.0. Defaults to proportional_to_load(net).
        tolerance: Bisection stops once (hi - lo) <= tolerance, in
            units of lambda_ (not MW).
        max_expansions: Safety cap on the initial doubling search for
            an upper bound that does violate. If this is hit, the
            network likely tolerates arbitrarily large PV under this
            allocation (unusual; probably means the allocation basis
            is degenerate, e.g. all buses have ~zero load).
        max_bisections: Safety cap on bisection iterations,
            independent of tolerance, so a pathological case can't
            loop indefinitely.

    Returns:
        DeterministicHCResult with the largest safe lambda_, the
        resulting per-bus PV sizes, and which element bound first at
        the failing lambda_ just above lambda_max (for Chapter 5's
        "where does it run out" narrative).
    """
    limits = limits_for(network_name)
    allocation = allocation or proportional_to_load(net)

    if not allocation.base_mw_per_bus:
        raise ValueError(
            "Allocation has no buses with load — cannot search for a "
            "PV penetration factor with nothing to scale."
        )

    iterations = 0

    def _violates(lambda_: float) -> bool:
        nonlocal iterations
        iterations += 1
        apply_allocation(net, allocation, lambda_)
        return check_violations(net, limits).has_violation

    lo, hi = 0.0, 1.0
    expansions = 0
    while not _violates(hi):
        lo, hi = hi, hi * 2
        expansions += 1
        if expansions >= max_expansions:
            break

    bisections = 0
    while (hi - lo) > tolerance and bisections < max_bisections:
        mid = (lo + hi) / 2
        if _violates(mid):
            hi = mid
        else:
            lo = mid
        bisections += 1

    # One more evaluation just above lambda_max to capture what binds.
    apply_allocation(net, allocation, hi)
    binding_report = check_violations(net, limits)

    pv_mw_per_bus = allocation.mw_at(lo)
    return DeterministicHCResult(
        network_name=network_name,
        lambda_max=lo,
        pv_mw_per_bus=pv_mw_per_bus,
        total_pv_mw=round(sum(pv_mw_per_bus.values()), 4),
        binding_constraint=binding_report.binding_constraint(),
        iterations=iterations,
    )
