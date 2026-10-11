"""Three-phase unbalanced hosting capacity (Phase 7.4).

Real LV feeders supply single-phase customers. A balanced model spreads
every load and every PV system evenly over the three phases and cannot
see the voltage rise on lightly loaded phases or the neutral-point shift
(Navarro & Ochoa report that balanced studies underestimate voltage
problems). This module runs the hosting-capacity search on pandapower's
three-phase power flow (``runpp_3ph``) for networks whose customers are
``asymmetric_load`` elements (e.g. the IEEE European LV test feeder, UK).

Phase models, **same solver in both**:

* ``"unbalanced"`` -- each customer's load and PV stay on the customer's
  own phase (single-phase inverters);
* ``"balanced_equivalent"`` -- every load and PV system is split evenly
  over the three phases, keeping each total unchanged.

The difference between the two isolates the effect of unbalance alone.

Capacity unit: PV is allocated *equally per customer* (residential PV is
sized per house, not per kW of the snapshot's load), so ``lambda`` is
**kWp per customer**; ``total_pv_mw`` is the comparable quantity across
networks.

Checks per power flow, at every customer bus and phase:

* voltage outside the framework's band (same ``FRAMEWORKS`` as the
  balanced path);
* line and transformer loading above their limits;
* optionally, the voltage unbalance factor above ``max_vuf_percent``
  (reported in every case). The limit is not enforced by default:
  PRODIST and EN 50160 define unbalance statistically over a week
  [VERIFY limits].

``source_vm_pu`` overrides the feeder's source voltage. The IEEE European
LV feeder is specified at 1.05 pu, which already sits on the PRODIST
upper limit off-peak, so this parameter is a sensitivity factor, not a
detail.
"""

from __future__ import annotations

import copy
import math
from dataclasses import dataclass, field

import numpy as np
import pandapower as pp

from .conditions import StudyConditions
from .errors import BaselineInfeasibleError
from .limits import limits_for
from .quantiles import quantile_with_ci
from .search import SEARCH_DEFAULTS, bisect_max_feasible, bisect_within

PHASE_MODELS = ("unbalanced", "balanced_equivalent")
_PH = ("a", "b", "c")


def is_unbalanced_network(net: pp.pandapowerNet) -> bool:
    """True when customers are modelled as single/three-phase ``asymmetric_load``."""
    return len(net.asymmetric_load) > 0 and len(net.load) == 0


@dataclass
class UnbalancedHCResult:
    network_name: str
    phase_model: str
    framework: str
    lambda_max: float
    lambda_fail: float | None
    bounded: bool
    resolved: bool
    lambda_unit: str
    n_customers: int
    total_pv_mw: float
    pv_mw_per_phase: dict[str, float]
    binding_constraint: str | None
    max_vuf_percent: float | None
    vmin_pu: float | None
    vmax_pu: float | None
    source_vm_pu: float
    load_scale: float
    power_flows: int
    conditions: StudyConditions
    notes: list[str] = field(default_factory=list)


@dataclass
class UnbalancedStochasticResult:
    network_name: str
    phase_model: str
    framework: str
    alpha: float
    hc_lambda: float | None
    hc_lambda_ci_low: float | None
    hc_lambda_ci_high: float | None
    hc_mw: float | None
    lambda_unit: str
    n_scenarios: int
    critical_lambdas: list[float]
    adoption_fraction: tuple[float, float]
    source_vm_pu: float
    load_scale: float
    power_flows: int
    conditions: StudyConditions


class _Feeder:
    """A working copy with one PV unit per customer and phase-model-aware updates."""

    def __init__(self, net, network_name, framework, phase_model, load_scale, source_vm_pu,
                 max_vuf_percent):
        if phase_model not in PHASE_MODELS:
            raise ValueError(f"phase_model must be one of {PHASE_MODELS}.")
        if not is_unbalanced_network(net):
            raise ValueError("Unbalanced hosting capacity needs a network of asymmetric_load customers.")
        if load_scale < 0:
            raise ValueError("load_scale must be >= 0.")
        self.limits = limits_for(network_name, framework)
        self.net = copy.deepcopy(net)
        n = self.net
        if source_vm_pu is not None:
            n.ext_grid["vm_pu"] = float(source_vm_pu)
        self.source_vm = float(n.ext_grid["vm_pu"].iloc[0])
        al = n.asymmetric_load
        p = al[["p_a_mw", "p_b_mw", "p_c_mw"]].to_numpy(dtype=float) * load_scale
        q = al[["q_a_mvar", "q_b_mvar", "q_c_mvar"]].to_numpy(dtype=float) * load_scale
        # customer phase: where its load sits (largest share); ties -> phase a
        self.phase = np.argmax(np.abs(al[["p_a_mw", "p_b_mw", "p_c_mw"]].to_numpy(dtype=float)), axis=1)
        if phase_model == "balanced_equivalent":
            p = np.repeat(p.sum(axis=1, keepdims=True) / 3.0, 3, axis=1)
            q = np.repeat(q.sum(axis=1, keepdims=True) / 3.0, 3, axis=1)
        for j, ph in enumerate(_PH):
            n.asymmetric_load[f"p_{ph}_mw"] = p[:, j]
            n.asymmetric_load[f"q_{ph}_mvar"] = q[:, j]
        self.phase_model = phase_model
        self.customers = al["bus"].astype(int).to_numpy()
        self.scope = sorted(set(self.customers.tolist()))
        if len(n.asymmetric_sgen):
            n.asymmetric_sgen.drop(n.asymmetric_sgen.index, inplace=True)
        for b in self.customers:
            pp.create_asymmetric_sgen(n, int(b), p_a_mw=0.0, p_b_mw=0.0, p_c_mw=0.0, name="hc_pv")
        self.max_vuf = max_vuf_percent
        self.power_flows = 0
        self.last: dict = {}

    def set_pv(self, kw_per_customer: np.ndarray) -> None:
        mw = np.asarray(kw_per_customer, dtype=float) / 1000.0
        sg = self.net.asymmetric_sgen
        if self.phase_model == "balanced_equivalent":
            for ph in _PH:
                sg[f"p_{ph}_mw"] = mw / 3.0
        else:
            for j, ph in enumerate(_PH):
                sg[f"p_{ph}_mw"] = np.where(self.phase == j, mw, 0.0)

    def violation(self) -> str | None:
        self.power_flows += 1
        try:
            pp.runpp_3ph(self.net)
        except Exception:  # noqa: BLE001 -- any solver failure counts as a violation (conservative)
            self.last = {}
            return "non_convergence"
        r = self.net.res_bus_3ph.loc[self.scope]
        vm = r[["vm_a_pu", "vm_b_pu", "vm_c_pu"]].to_numpy()
        vuf = float(np.nanmax(r["unbalance_percent"])) if "unbalance_percent" in r else math.nan
        self.last = {"vmin": float(np.nanmin(vm)), "vmax": float(np.nanmax(vm)), "vuf": vuf}
        lim = self.limits
        if np.nanmax(vm) > lim.v_max_pu + 1e-9:
            i, j = np.unravel_index(np.nanargmax(vm), vm.shape)
            return f"overvoltage@bus_{self.scope[i]}_phase_{_PH[j]} ({vm[i, j]:.4f} pu)"
        if np.nanmin(vm) < lim.v_min_pu - 1e-9:
            i, j = np.unravel_index(np.nanargmin(vm), vm.shape)
            return f"undervoltage@bus_{self.scope[i]}_phase_{_PH[j]} ({vm[i, j]:.4f} pu)"
        if len(self.net.trafo):
            tl = self.net.res_trafo_3ph["loading_percent"].to_numpy()
            if np.nanmax(tl) > lim.max_trafo_loading_percent + 1e-9:
                return f"trafo_loading@trafo_{int(np.nanargmax(tl))} ({np.nanmax(tl):.1f}%)"
        ll = self.net.res_line_3ph["loading_percent"].to_numpy()
        if len(ll) and np.nanmax(ll) > lim.max_line_loading_percent + 1e-9:
            return f"line_loading@line_{int(self.net.res_line_3ph.index[np.nanargmax(ll)])} ({np.nanmax(ll):.1f}%)"
        if self.max_vuf is not None and vuf > self.max_vuf + 1e-9:
            return f"voltage_unbalance ({vuf:.2f}%)"
        return None

    def conditions(self, network_name, framework, load_scale) -> StudyConditions:
        return StudyConditions.build(network_name, self.limits, load_scale, len(self.scope))


def find_hosting_capacity_unbalanced(
    net: pp.pandapowerNet,
    network_name: str,
    framework: str | None = None,
    phase_model: str = "unbalanced",
    load_scale: float = 1.0,
    source_vm_pu: float | None = None,
    max_vuf_percent: float | None = None,
    tolerance: float = 0.05,
    max_expansions: int = SEARCH_DEFAULTS.max_expansions,
    max_bisections: int = SEARCH_DEFAULTS.max_bisections,
) -> UnbalancedHCResult:
    """Deterministic: equal PV per customer, bisection on kWp per customer.

    ``load_scale`` multiplies the network's own snapshot loads (choose the
    snapshot with the network factory, e.g. an off-peak scenario).
    """
    f = _Feeder(net, network_name, framework, phase_model, load_scale, source_vm_pu, max_vuf_percent)
    n_cust = len(f.customers)
    ones = np.ones(n_cust)
    f.set_pv(0.0 * ones)
    base = f.violation()
    if base is not None:
        raise BaselineInfeasibleError(
            f"Baseline (PV = 0) already violates {f.limits.framework!r}: {base} "
            f"(source {f.source_vm:.3f} pu, phase model {phase_model}).", report=None)
    binding: dict[str, str | None] = {}

    def violates(kw: float) -> bool:
        f.set_pv(kw * ones)
        v = f.violation()
        binding[kw] = v
        return v is not None

    out = bisect_max_feasible(violates, tolerance=tolerance, lambda_start=1.0,
                              max_expansions=max_expansions, max_bisections=max_bisections)
    lam = out.lambda_max
    f.set_pv(lam * ones)
    f.violation()
    at_hc = dict(f.last)
    per_phase = {ph: round(float(f.net.asymmetric_sgen[f"p_{ph}_mw"].sum()), 6) for ph in _PH}
    notes = []
    if phase_model == "unbalanced":
        counts = np.bincount(f.phase, minlength=3)
        notes.append("customers per phase a/b/c: " + "/".join(str(int(c)) for c in counts))
    return UnbalancedHCResult(
        network_name=network_name, phase_model=phase_model, framework=f.limits.framework,
        lambda_max=lam, lambda_fail=out.lambda_fail, bounded=out.bounded, resolved=out.resolved,
        lambda_unit="kWp_per_customer", n_customers=n_cust,
        total_pv_mw=round(lam * n_cust / 1000.0, 6), pv_mw_per_phase=per_phase,
        binding_constraint=binding.get(out.lambda_fail) if out.lambda_fail is not None else None,
        max_vuf_percent=at_hc.get("vuf"), vmin_pu=at_hc.get("vmin"), vmax_pu=at_hc.get("vmax"),
        source_vm_pu=f.source_vm, load_scale=load_scale, power_flows=f.power_flows,
        conditions=f.conditions(network_name, framework, load_scale), notes=notes,
    )


def estimate_hosting_capacity_unbalanced_stochastic(
    net: pp.pandapowerNet,
    network_name: str,
    framework: str | None = None,
    phase_model: str = "unbalanced",
    load_scale: float = 1.0,
    source_vm_pu: float | None = None,
    n_scenarios: int = 60,
    alpha: float = 0.10,
    adoption_fraction: tuple[float, float] = (0.3, 1.0),
    seed: int | None = 42,
    tolerance: float = 0.05,
    max_bisections: int = SEARCH_DEFAULTS.max_bisections,
) -> UnbalancedStochasticResult:
    """Random adopters (a fraction of customers), equal kWp each; critical kWp per scenario.

    ``hc_lambda`` is the ``alpha`` quantile of the critical kWp per adopting
    customer, with a distribution-free binomial confidence interval.
    """
    lo, hi = adoption_fraction
    if not 0 < lo <= hi <= 1:
        raise ValueError("adoption_fraction must satisfy 0 < lo <= hi <= 1.")
    f = _Feeder(net, network_name, framework, phase_model, load_scale, source_vm_pu, None)
    n_cust = len(f.customers)
    f.set_pv(np.zeros(n_cust))
    base = f.violation()
    if base is not None:
        raise BaselineInfeasibleError(f"Baseline (PV = 0) already violates: {base}.", report=None)
    rng = np.random.default_rng(seed)
    crit: list[float] = []
    for _ in range(n_scenarios):
        k = max(1, round(rng.uniform(lo, hi) * n_cust))
        mask = np.zeros(n_cust)
        mask[rng.choice(n_cust, size=k, replace=False)] = 1.0

        def violates(kw: float, m=mask) -> bool:
            f.set_pv(kw * m)
            return f.violation() is not None

        out = bisect_max_feasible(violates, tolerance=tolerance, lambda_start=1.0,
                                  max_bisections=max_bisections)
        if out.bounded and not out.resolved and out.lambda_fail is not None:
            out = bisect_within(violates, out.lambda_max, out.lambda_fail, tolerance=tolerance)
        crit.append(float(out.lambda_max))
    est = quantile_with_ci(crit, alpha)
    mean_adopters = None if est.point is None else est.point
    return UnbalancedStochasticResult(
        network_name=network_name, phase_model=phase_model, framework=f.limits.framework, alpha=alpha,
        hc_lambda=est.point, hc_lambda_ci_low=est.low, hc_lambda_ci_high=est.high,
        hc_mw=None if mean_adopters is None else round(est.point * n_cust * (lo + hi) / 2 / 1000.0, 6),
        lambda_unit="kWp_per_adopting_customer", n_scenarios=n_scenarios, critical_lambdas=crit,
        adoption_fraction=adoption_fraction, source_vm_pu=f.source_vm, load_scale=load_scale,
        power_flows=f.power_flows, conditions=f.conditions(network_name, framework, load_scale),
    )


__all__ = [
    "PHASE_MODELS", "UnbalancedHCResult", "UnbalancedStochasticResult",
    "estimate_hosting_capacity_unbalanced_stochastic", "find_hosting_capacity_unbalanced",
    "is_unbalanced_network",
]
