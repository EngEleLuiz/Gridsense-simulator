"""QSTS (quasi-static time series) hosting capacity: bisects the same
global penetration factor `lambda_` as the deterministic method, but
the violation check for a given `lambda_` runs an entire load/solar
time series instead of a single snapshot, and fails as soon as ANY
step in that series violates.

This reuses simulator/gridsense_sim/profiles.py's LoadProfile and
SolarProfile directly (unlike stochastic.py, which turned out not to
fit with scenarios.py -- see that module's docstring) -- this is the
reuse 03-Project-Overview.md's Phase 6 notes anticipated, and it does
fit here because profiles.py's per-step multipliers are exactly what
a time-varying penetration check needs.

PERFORMANCE WARNING: this is expensive. Each lambda_ candidate the
bisection tries can run up to `total_steps` power flows before it
finds a violation (or confirms none exist across the whole series).
With the default 60-day/288-steps-per-day horizon, that is up to
17,280 pp.runpp() calls per candidate, times up to ~20 bisection
iterations, i.e. up to ~350k power flows for one hosting-capacity
answer -- and a full 365-day run multiplies that further. This cost
is exactly the motivator documented in
Analise-Comparativa-Trabalhos-Relacionados.md sec 4.2 for the GNN/PINN
surrogate left as Phase 9 future work: it is not a bug, it is the
problem that future work is meant to solve. Use `total_steps` to run
a short horizon during development and only switch to the full
annual horizon for the final Chapter 5 numbers.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandapower as pp

from .allocation import PVAllocation, apply_allocation, proportional_to_load
from .limits import limits_for
from .violations import check_violations
from ..profiles import LoadProfile, ProfileConfig, SolarProfile

# Default horizon: 60 days at 288 steps/day (5-minute resolution) —
# chosen for fast local iteration. Pass total_steps=288*365 for the
# full annual QSTS run used in the dissertation's final Chapter 5
# numbers; that is roughly 6x the cost of this default.
DEFAULT_STEPS_PER_DAY = 288
DEFAULT_TOTAL_STEPS = DEFAULT_STEPS_PER_DAY * 60


@dataclass
class QstsHCResult:
    """Result of a QSTS (bisection-over-a-time-series) hosting-capacity
    search.
    """

    network_name: str
    lambda_max: float
    pv_mw_per_bus: dict[int, float]
    total_pv_mw: float
    binding_constraint: str | None
    first_violating_step: int | None  # step index at which lambda_max + a small
                                       # step would first violate; None if the
                                       # expansion cap was hit without violating
    total_steps: int
    steps_per_day: int


def find_hosting_capacity(
    net: pp.pandapowerNet,
    network_name: str,
    allocation: PVAllocation | None = None,
    total_steps: int = DEFAULT_TOTAL_STEPS,
    steps_per_day: int = DEFAULT_STEPS_PER_DAY,
    tolerance: float = 0.01,
    max_expansions: int = 20,
    max_bisections: int = 20,
    profile_seed: int | None = 42,
) -> QstsHCResult:
    """Bisect the largest lambda_ such that PV = allocation.mw_at(lambda_)
    times the solar profile, applied every step across the whole
    series, causes no violation at any step.

    Args:
        net: A pandapower network; net.load["p_mw"] is overwritten
            every step by this function (base loads are captured
            once at the start via net.load["p_mw"].copy(), same
            pattern as engine.py's _apply_load_profile).
        network_name: Used to look up the violation criterion.
        allocation: PV sizing basis at lambda_ == 1.0. Defaults to
            proportional_to_load(net).
        total_steps: Length of the time series to check per lambda_
            candidate. See DEFAULT_TOTAL_STEPS and the module
            docstring's performance warning before raising this.
        steps_per_day: Passed through to LoadProfile/SolarProfile.
        tolerance, max_expansions, max_bisections: Same role as in
            deterministic.py.
        profile_seed: Seed for the load/solar profile noise, so runs
            are reproducible.
    """
    limits = limits_for(network_name)
    allocation = allocation or proportional_to_load(net)
    if not allocation.base_mw_per_bus:
        raise ValueError(
            "Allocation has no buses with load — cannot search for a "
            "PV penetration factor with nothing to scale."
        )

    base_loads = net.load["p_mw"].copy()
    profile_cfg = ProfileConfig(seed=profile_seed)
    load_profile = LoadProfile(profile_cfg)
    solar_profile = SolarProfile(profile_cfg)

    def _series_violates(lambda_: float) -> int | None:
        """Return the first violating step for this lambda_, or None if
        the whole series is clean. Stops early on the first violation
        -- the point of QSTS's expense is checking the whole series
        when it DOESN'T violate; once it does, there's nothing more
        to learn from finishing the run.
        """
        for step in range(total_steps):
            load_mult = load_profile.value_at(step, steps_per_day)
            net.load["p_mw"] = base_loads * load_mult
            solar_mult = solar_profile.value_at(step, steps_per_day)
            apply_allocation(net, allocation, lambda_ * solar_mult)
            if check_violations(net, limits).has_violation:
                return step
        return None

    lo, hi = 0.0, 1.0
    expansions = 0
    while _series_violates(hi) is None:
        lo, hi = hi, hi * 2
        expansions += 1
        if expansions >= max_expansions:
            break

    bisections = 0
    while (hi - lo) > tolerance and bisections < max_bisections:
        mid = (lo + hi) / 2
        if _series_violates(mid) is not None:
            hi = mid
        else:
            lo = mid
        bisections += 1

    first_violating_step = _series_violates(hi)

    # Capture what binds at the failing step, at peak solar
    # (lambda_ * 1.0), for the same "where does it run out" reporting
    # as the deterministic method.
    apply_allocation(net, allocation, hi)
    binding_report = check_violations(net, limits)

    pv_mw_per_bus = allocation.mw_at(lo)
    return QstsHCResult(
        network_name=network_name,
        lambda_max=lo,
        pv_mw_per_bus=pv_mw_per_bus,
        total_pv_mw=round(sum(pv_mw_per_bus.values()), 4),
        binding_constraint=binding_report.binding_constraint(),
        first_violating_step=first_violating_step,
        total_steps=total_steps,
        steps_per_day=steps_per_day,
    )
