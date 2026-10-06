"""Stochastic hosting capacity: distribution of the critical penetration.

Why the estimator was replaced (review finding C4)
--------------------------------------------------
The original ``run_monte_carlo`` sampled PV per bus in
``U(0, max_pv_mw_per_bus)`` and reported percentiles of the *feasible
totals*. Its p50 scales with the sampling ceiling (0.151 / 0.377 /
0.695 / 0 MW for ceilings 0.02 / 0.05 / 0.10 / 0.20 MW/bus, doc 06):
it measures the **sampling budget**, not the network. It also took
percentiles over feasible trials only (survivorship bias).

Correct formulation (Torquato et al., 2018 framing)
---------------------------------------------------
For each random adoption scenario ``s`` (which buses adopt, and how
much relative to each other), compute its **critical penetration**
``lambda*_s`` by the same monotone bisection the deterministic method
uses. Then

* the scenario's PV pattern is normalized so that total PV =
  ``lambda * total nominal load`` -- lambda means the same MW as in
  the deterministic method;
* ``F(lambda) = P(lambda* <= lambda)`` is the probability that a
  random adoption pattern at penetration ``lambda`` violates;
* the stochastic HC at risk level ``alpha`` is ``F^-1(alpha)``,
  e.g. ``alpha = 0.10``: "90% of adoption patterns are still feasible".

No arbitrary ceiling enters the answer. The adoption model's
parameters (adoption-fraction range, size dispersion) remain
assumptions until real PV-sizing data arrives (Phase 8) and must be
covered by a sensitivity analysis before any claim.

``run_monte_carlo`` is kept, unchanged in behaviour, for reproducing
old numbers only, and emits a :class:`DeprecationWarning`.
"""

from __future__ import annotations

import copy
import math
import random
import warnings
from dataclasses import dataclass, field

import numpy as np
import pandapower as pp

from .allocation import PVAllocation, apply_allocation, proportional_to_load
from .baseline import require_feasible_baseline
from .conditions import DEFAULT_CRITICAL_LOAD_SCALE, StudyConditions, apply_load_scale
from .limits import limits_for
from .quantiles import QuantileEstimate, quantile_with_ci
from .scope import voltage_scope_buses
from .search import bisect_max_feasible
from .violations import check_violations


@dataclass(frozen=True)
class AdoptionModel:
    """Random uncoordinated-adoption model.

    Attributes:
        adoption_fraction: Range of the fraction of load buses that adopt
            PV in a scenario, sampled uniformly.
        size_dispersion: Each adopter's size relative to its load is
            multiplied by ``U(1 - d, 1 + d)``; ``0`` = proportional to load.
    """

    adoption_fraction: tuple[float, float] = (0.3, 1.0)
    size_dispersion: float = 0.5

    def __post_init__(self) -> None:
        lo, hi = self.adoption_fraction
        if not 0.0 < lo <= hi <= 1.0:
            raise ValueError("adoption_fraction must satisfy 0 < lo <= hi <= 1.")
        if not 0.0 <= self.size_dispersion < 1.0:
            raise ValueError("size_dispersion must be in [0, 1).")

    def sample(self, nominal: PVAllocation, rng: np.random.Generator) -> tuple[float, PVAllocation]:
        """Draw one scenario: ``(adoption_fraction, PV shape at lambda = 1)``.

        The returned shape sums to ``nominal.total_base_mw``.
        """
        buses = sorted(b for b, w in nominal.base_mw_per_bus.items() if w > 0.0)
        frac = float(rng.uniform(*self.adoption_fraction))
        k = max(1, round(frac * len(buses)))
        adopters = rng.choice(buses, size=k, replace=False)
        d = self.size_dispersion
        raw = {
            int(b): nominal.base_mw_per_bus[int(b)] * float(rng.uniform(1 - d, 1 + d))
            for b in adopters
        }
        scale = nominal.total_base_mw / sum(raw.values())
        return frac, PVAllocation({b: w * scale for b, w in raw.items()})


@dataclass
class ScenarioResult:
    """Critical penetration of one adoption scenario."""

    index: int
    adoption_fraction: float
    n_adopters: int
    lambda_critical: float  # largest feasible; inf if unbounded (censored)
    bounded: bool
    binding_constraint: str | None
    power_flows: int


@dataclass
class StochasticHCEstimate:
    """Distribution of ``lambda*`` over random adoption scenarios."""

    network_name: str
    total_nominal_load_mw: float
    scenarios: list[ScenarioResult]
    conditions: StudyConditions
    adoption_model: AdoptionModel
    seed: int | None

    @property
    def lambdas(self) -> list[float]:
        return [s.lambda_critical for s in self.scenarios]

    @property
    def n_censored(self) -> int:
        """Scenarios that never violated up to the expansion cap."""
        return sum(1 for s in self.scenarios if not s.bounded)

    @property
    def power_flows(self) -> int:
        return sum(s.power_flows for s in self.scenarios)

    def violation_probability(self, lambda_: float) -> float:
        """Empirical ``F(lambda)``: share of scenarios infeasible at ``lambda_``."""
        return sum(1 for x in self.lambdas if x < lambda_) / len(self.scenarios)

    def hc_lambda(self, alpha: float, confidence: float = 0.95) -> QuantileEstimate:
        """``F^-1(alpha)`` with a distribution-free confidence interval.

        Censored scenarios enter as ``+inf``, which is exact for any
        quantile below the censored share and makes higher quantiles
        come out as ``inf`` (i.e. "not determined") rather than wrong.
        """
        return quantile_with_ci(self.lambdas, alpha, confidence)

    def hc_mw(self, alpha: float) -> float:
        """Total PV MW at ``F^-1(alpha)``."""
        return self.hc_lambda(alpha).point * self.total_nominal_load_mw


def estimate_hosting_capacity(
    net: pp.pandapowerNet,
    network_name: str,
    *,
    n_scenarios: int = 60,
    adoption_model: AdoptionModel | None = None,
    framework: str | None = None,
    load_scale: float = DEFAULT_CRITICAL_LOAD_SCALE,
    tolerance: float = 0.01,
    max_expansions: int = 12,
    seed: int | None = 42,
) -> StochasticHCEstimate:
    """Monte Carlo over adoption scenarios; one bisection per scenario.

    Args:
        net: Nominal network. **Not mutated.**
        network_name: Registered network key.
        n_scenarios: Number of random adoption scenarios.
        adoption_model: See :class:`AdoptionModel`.
        framework, load_scale, tolerance: As in the deterministic method.
        max_expansions: Doubling cap per scenario (2**12 = 4096x load).
        seed: RNG seed (numpy ``default_rng``).

    Raises:
        BaselineInfeasibleError: violation at PV = 0.
    """
    if n_scenarios < 1:
        raise ValueError("n_scenarios must be >= 1.")
    model = adoption_model or AdoptionModel()
    limits = limits_for(network_name, framework)
    base_net = copy.deepcopy(net)
    nominal = proportional_to_load(base_net)
    nominal.require_non_empty()

    apply_load_scale(base_net, load_scale)
    scope = voltage_scope_buses(base_net, limits)
    conditions = StudyConditions.build(network_name, limits, load_scale, len(scope))
    require_feasible_baseline(
        copy.deepcopy(base_net), limits, scope, context=f"(stochastic, load_scale={load_scale})"
    )

    rng = np.random.default_rng(seed)
    results: list[ScenarioResult] = []
    for i in range(n_scenarios):
        frac, shape = model.sample(nominal, rng)
        work = copy.deepcopy(base_net)

        def _violates(lam: float, _w: pp.pandapowerNet = work, _s: PVAllocation = shape) -> bool:
            apply_allocation(_w, _s, lam)
            return check_violations(_w, limits, scope).has_violation

        out = bisect_max_feasible(_violates, tolerance=tolerance, max_expansions=max_expansions)
        binding = None
        pfs = out.evaluations
        if out.bounded and out.lambda_fail is not None:
            apply_allocation(work, shape, out.lambda_fail)
            binding = check_violations(work, limits, scope).binding_constraint()
            pfs += 1
        results.append(
            ScenarioResult(
                index=i,
                adoption_fraction=frac,
                n_adopters=len(shape.base_mw_per_bus),
                lambda_critical=out.lambda_max if out.bounded else math.inf,
                bounded=out.bounded,
                binding_constraint=binding,
                power_flows=pfs,
            )
        )

    return StochasticHCEstimate(
        network_name=network_name,
        total_nominal_load_mw=nominal.total_base_mw,
        scenarios=results,
        conditions=conditions,
        adoption_model=model,
        seed=seed,
    )


# --------------------------------------------------------------------------
# Legacy estimator (deprecated). Kept only to reproduce pre-review numbers.
# --------------------------------------------------------------------------


@dataclass
class MonteCarloTrial:
    """A single legacy Monte Carlo trial."""

    pv_mw_per_bus: dict[int, float]
    has_violation: bool
    binding_constraint: str | None


@dataclass
class StochasticHCResult:
    """Legacy result. Its percentiles measure the sampling budget -- do not cite."""

    network_name: str
    trials: list[MonteCarloTrial] = field(default_factory=list)

    @property
    def feasible_totals_mw(self) -> list[float]:
        return [sum(t.pv_mw_per_bus.values()) for t in self.trials if not t.has_violation]

    @property
    def violation_rate(self) -> float:
        if not self.trials:
            return 0.0
        return sum(1 for t in self.trials if t.has_violation) / len(self.trials)

    def hosting_capacity_mw(self, percentile: float) -> float:
        totals = sorted(self.feasible_totals_mw)
        if not totals:
            return 0.0
        idx = min(len(totals) - 1, int(percentile * (len(totals) - 1)))
        return totals[idx]


def run_monte_carlo(
    net: pp.pandapowerNet,
    network_name: str,
    n_trials: int = 500,
    max_pv_mw_per_bus: float = 0.02,
    seed: int | None = 42,
) -> StochasticHCResult:
    """DEPRECATED -- see module docstring. Use :func:`estimate_hosting_capacity`."""
    warnings.warn(
        "run_monte_carlo measures the sampling ceiling, not the network "
        "(review finding C4). Use estimate_hosting_capacity().",
        DeprecationWarning,
        stacklevel=2,
    )
    limits = limits_for(network_name)
    work = copy.deepcopy(net)
    scope = voltage_scope_buses(work, limits)
    rng = random.Random(seed)
    load_buses = sorted({int(b) for b in work.load["bus"]})
    if not load_buses:
        raise ValueError("Network has no load buses to sample PV onto.")
    sgen_by_bus: dict[int, int] = {}
    trials: list[MonteCarloTrial] = []
    for _ in range(n_trials):
        pv = {bus: rng.uniform(0.0, max_pv_mw_per_bus) for bus in load_buses}
        for bus, mw in pv.items():
            if bus in sgen_by_bus:
                work.sgen.at[sgen_by_bus[bus], "p_mw"] = mw
            else:
                sgen_by_bus[bus] = pp.create_sgen(
                    work, bus=bus, p_mw=mw, q_mvar=0.0, name=f"hosting_capacity_mc_pv_{bus}"
                )
        report = check_violations(work, limits, scope)
        trials.append(MonteCarloTrial(pv, report.has_violation, report.binding_constraint()))
    return StochasticHCResult(network_name=network_name, trials=trials)
