"""Stochastic hosting capacity via Monte Carlo sampling of independent,
randomly-sized and randomly-located PV per bus (uncoordinated
adoption) -- as opposed to the deterministic method's single
coordinated penetration factor applied identically everywhere.

This is deliberately NOT built on top of `simulator/gridsense_sim/
scenarios.py`'s `Contingency` class: that module samples which
elements go OUT of service (N-1/N-2 outages), a different kind of
randomness from "how much PV lands on which bus, independently per
trial". 03-Project-Overview.md's Phase 6 note suggesting reuse of
scenarios.py as a sampling base turned out not to fit once this was
implemented -- documented here so the discrepancy isn't silently
reintroduced later.

Follows Torquato et al. (2018)'s framing: report a distribution
(p50/p95) rather than a single number, since real-world PV adoption
is not coordinated.

KNOWN CALIBRATION GAP (documented, not fixed -- deferred to Phase 8):
`max_pv_mw_per_bus`'s default (0.02 MW) was found, in a first CIGRE LV
run (Sept/2026), to sit well below the network's actual hosting
capacity ceiling -- the deterministic method found ~1.57 MW total
(~0.10 MW/bus average) before its first violation, roughly 5x the
default sampling ceiling. At the default, 500/500 trials showed zero
violations, so the resulting p50/p95 only says "nothing breaks within
this budget" -- it does not locate where the network actually starts
to break, and is NOT a like-for-like comparison against the
deterministic/QSTS results in that state.

This is deliberately left uncalibrated here rather than patched with
a guessed multiplier: the right fix depends on real per-household PV
sizing data (synthetic per-bus load figures aren't a solid basis for
choosing a sampling ceiling), which Phase 8 brings in (see
03-Project-Overview.md sec. 4, item 2/5 datasets). Until then, any
call to run_monte_carlo should pass a deliberately-chosen
max_pv_mw_per_bus (e.g. informed by a prior
find_hosting_capacity_deterministic run on the same network) rather
than relying on the default for anything meant to be compared against
the other two methods.
"""

from __future__ import annotations

import random
import statistics
from dataclasses import dataclass, field

import pandapower as pp

from .limits import limits_for
from .violations import check_violations


@dataclass
class MonteCarloTrial:
    """A single Monte Carlo trial's sampled PV and outcome."""

    pv_mw_per_bus: dict[int, float]
    has_violation: bool
    binding_constraint: str | None


@dataclass
class StochasticHCResult:
    """Result of a Monte Carlo hosting-capacity study.

    hosting_capacity_mw is defined per trial as total sampled PV MW if
    no violation occurred, else 0.0 -- i.e. each trial answers "did
    this particular random PV configuration fit", and the aggregate
    distribution (not any single trial) is the actual result.
    """

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
        """E.g. percentile=0.5 for p50, 0.95 for p95, over feasible
        trials' total PV MW. Returns 0.0 if no trial was feasible.
        """
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
    """Run n_trials independent PV-adoption scenarios.

    Each trial samples, for every load bus independently, a PV size
    uniformly in [0, max_pv_mw_per_bus] MW -- modeling uncoordinated
    adoption (unlike the deterministic method's single scaling
    factor). max_pv_mw_per_bus should be sized relative to the
    network's typical per-bus load (the CIGRE LV feeder's individual
    loads are on the order of 0.01-0.03 MW; the default here is a
    starting point, not a validated figure -- revisit once Phase 8
    brings in real per-household load data).

    Args:
        net: A pandapower network at its base-case load.
        network_name: Used to look up the violation criterion.
        n_trials: Number of independent Monte Carlo trials.
        max_pv_mw_per_bus: Upper bound of the per-bus uniform sampling
            distribution.
        seed: Seed for reproducibility.
    """
    limits = limits_for(network_name)
    rng = random.Random(seed)
    load_buses = sorted({int(row["bus"]) for _, row in net.load.iterrows()})

    if not load_buses:
        raise ValueError("Network has no load buses to sample PV onto.")

    trials: list[MonteCarloTrial] = []
    existing_sgen_names = {
        int(net.sgen.at[idx, "bus"]): idx
        for idx in net.sgen.index
        if str(net.sgen.at[idx, "name"]).startswith("hosting_capacity_mc_pv")
    }

    for _ in range(n_trials):
        pv_mw_per_bus = {bus: rng.uniform(0.0, max_pv_mw_per_bus) for bus in load_buses}
        for bus, mw in pv_mw_per_bus.items():
            if bus in existing_sgen_names:
                net.sgen.at[existing_sgen_names[bus], "p_mw"] = mw
            else:
                idx = pp.create_sgen(
                    net, bus=bus, p_mw=mw, q_mvar=0.0, name=f"hosting_capacity_mc_pv_{bus}"
                )
                existing_sgen_names[bus] = idx

        report = check_violations(net, limits)
        trials.append(
            MonteCarloTrial(
                pv_mw_per_bus=pv_mw_per_bus,
                has_violation=report.has_violation,
                binding_constraint=report.binding_constraint(),
            )
        )

    return StochasticHCResult(network_name=network_name, trials=trials)
