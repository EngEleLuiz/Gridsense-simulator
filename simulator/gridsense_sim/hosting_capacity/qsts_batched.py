"""Duration-criterion QSTS on the batched solver (Phase 8.2).

Same question, same criterion and same result type as
:func:`.qsts_duration.find_hosting_capacity_duration`; only the way power
flows are run changes. pandapower solves one operating point at a time, so
that module is built to *avoid* power flows (bracket, shrink, bisect a few
steps). With a batched solver a power flow costs microseconds, so the search
here is the direct one:

1. **Baseline** -- every step of the window at ``lambda = 0`` in one batch.
2. **Bracket** -- every daylight step at ``U`` in one batch; double ``U``
   until the criterion is exceeded (or ``max_expansions`` is reached, which
   leaves the window censored at the largest ``U`` evaluated).
3. **Thresholds** -- bisect the threshold of *every* event seen at ``U``, all
   in parallel (one batch per bisection step), then the exact sweep of
   :func:`.duration.weekly_capacity`.

The capacities of the two backends agree within the search tolerance: both
report the lower end of a bracket of width ``<= tolerance`` around the same
threshold. They are not bit-identical because the brackets are reached by
different bisection sequences. ``tests/test_qsts_batched.py`` enforces the
agreement on the network panel.

Not covered: three-phase unbalanced networks and the elements listed in
:mod:`gridsense_sim.accel.model` (the model refuses them explicitly).
"""

from __future__ import annotations

import copy
import math

import numpy as np
import pandapower as pp

from ..accel.model import build_model
from ..accel.solver import BatchedSolver, device_info
from .allocation import PVAllocation, proportional_to_load
from .duration import DurationCriterion, WeekThresholds, get_criterion, weekly_capacity
from .errors import BaselineInfeasibleError
from .limits import limits_for
from .qsts_duration import (
    QstsDurationResult,
    WindowResult,
    _build_result,
    _critical_window,
    _exceeded_at,
    _kinds,
    _plan_windows,
)
from .scope import voltage_scope_buses
from .search import SEARCH_DEFAULTS
from .timeseries import TimeSeries

_EPS = 1e-6


class _BatchedEvaluator:
    """Batched counterpart of ``qsts_duration._Evaluator``."""

    def __init__(self, net, network_name, ts, allocation, criterion, load_matrix, device, dtype, memory_mb):
        self.limits = limits_for(network_name, None)
        net = copy.deepcopy(net)
        self.allocation = allocation or proportional_to_load(net)
        self.allocation.require_non_empty()
        self.scope = list(voltage_scope_buses(net, self.limits))
        self.ts = ts
        self.crit = criterion
        if load_matrix is not None:
            load_matrix = np.asarray(load_matrix, dtype=float)
            if load_matrix.shape != (ts.n_steps, len(net.load)):
                raise ValueError(f"load_matrix must have shape {(ts.n_steps, len(net.load))}.")
        self.load_matrix = load_matrix
        self.model = build_model(net, self.scope, self.allocation.base_mw_per_bus)
        self.solver = BatchedSolver(self.model, device=device, dtype=dtype, memory_mb=memory_mb)
        self.kinds = _kinds(criterion)

    @property
    def power_flows(self) -> int:
        return self.solver.power_flows

    def solve(self, steps: np.ndarray, lam: np.ndarray | float):
        load = self.load_matrix[steps] if self.load_matrix is not None else self.ts.load_mult[steps]
        return self.solver.solve(load, self.ts.pv_mult[steps], lam)

    def events(self, steps: np.ndarray, lam, base_line: np.ndarray, base_trafo: np.ndarray) -> dict[str, np.ndarray]:
        """PV-side events per step. Non-convergence counts as every event (conservative),
        exactly as in the pandapower backend."""
        r = self.solve(steps, lam)
        vmax = np.max(r.vm, axis=1) if r.vm.shape[1] else np.zeros(len(steps))
        lim_l, lim_t = self.limits.max_line_loading_percent, self.limits.max_trafo_loading_percent
        thermal = np.zeros(len(steps), dtype=bool)
        if r.line.shape[1]:
            thermal |= np.any(r.line > np.maximum(lim_l, base_line) + _EPS, axis=1)
        if r.trafo.shape[1]:
            thermal |= np.any(r.trafo > np.maximum(lim_t, base_trafo) + _EPS, axis=1)
        bad = ~r.converged
        return {
            "precarious": (vmax > self.crit.adequate_hi_pu + _EPS) | bad,
            "critical": (vmax > self.crit.critical_hi_pu + _EPS) | bad,
            "thermal": thermal | bad,
        }


def _window_search(ev: _BatchedEvaluator, idx: np.ndarray, crit: DurationCriterion, u0: float | None,
                   tol: float, max_exp: int, max_bis: int, baseline_report: bool):
    n = len(idx)
    ts = ev.ts
    kinds = ev.kinds
    pf0 = ev.power_flows
    daylight = idx[ts.pv_mult[idx] > 0]
    # 1. baseline (no PV)
    base_steps = idx if baseline_report else daylight
    lower: dict = {"n_scope_buses": len(ev.scope)}
    base_events: dict[str, set[int]] = {k: set() for k in kinds}
    base_line = base_trafo = None
    if len(base_steps):
        base = ev.solve(base_steps, 0.0)
        if not base.converged.all():
            step = ts.start_step + int(base_steps[np.flatnonzero(~base.converged)[0]])
            raise RuntimeError(f"Power flow did not converge without PV at step {step}.")
        if ev.model.scope_isolated.any():
            step = ts.start_step + int(base_steps[0])
            bus = ev.scope[int(np.flatnonzero(ev.model.scope_isolated)[0])]
            raise BaselineInfeasibleError(
                f"Baseline (PV = 0) at step {step} already violates '{crit.name}': "
                f"isolated@bus_{bus} (not energized).", report=None, step=step)
        vmax = base.vm.max(axis=1)
        base_events["critical"] = {int(t) for t in base_steps[vmax > crit.critical_hi_pu + _EPS]}
        if "precarious" in kinds:
            base_events["precarious"] = {int(t) for t in base_steps[vmax > crit.adequate_hi_pu + _EPS]}
        if baseline_report:
            prec = (base.vm < crit.adequate_lo_pu - _EPS).sum(axis=0)
            critc = (base.vm < crit.critical_lo_pu - _EPS).sum(axis=0)
            drp, drc = (prec - critc) / n, critc / n
            lower.update({
                "max_drp": float(drp.max()), "max_drc": float(drc.max()),
                "share_buses_drc_positive": float((drc > 0).mean()),
                "compliant": bool(drp.max() <= crit.lower_precarious_max + 1e-12
                                  and drc.max() <= crit.lower_critical_max + 1e-12),
            })
        day_rows = np.flatnonzero(ts.pv_mult[base_steps] > 0)
        base_line, base_trafo = base.line[day_rows], base.trafo[day_rows]
    stages = {"baseline": ev.power_flows - pf0}
    lower["power_flows_by_stage"] = stages
    if not len(daylight):
        return None, lower, base_events, ev.power_flows - pf0
    mark = ev.power_flows
    night_base = {k: {t for t in base_events[k] if ts.pv_mult[t] <= 0} for k in kinds}
    is_base = {k: np.array([int(t) in base_events[k] for t in daylight]) for k in kinds}

    def exceeded(e: dict[str, np.ndarray]) -> bool:
        sets = {k: {int(t) for t in daylight[e[k]]} | night_base[k] for k in kinds}
        return _exceeded_at(sets, base_events, n, crit)

    # 2. bracket: all daylight steps at u; double until the criterion is exceeded
    u = max(u0, 4 * tol) if u0 is not None else 1.0
    lo = {k: np.zeros(len(daylight)) for k in kinds}   # largest lambda seen without the event
    bounded = False
    at_u: dict[str, np.ndarray] = {}
    for _ in range(max_exp + 1):
        at_u = ev.events(daylight, u, base_line, base_trafo)
        if exceeded(at_u):
            bounded = True
            break
        for k in kinds:
            lo[k] = np.where(at_u[k], lo[k], u)
        u *= 2.0
    stages["bracket"] = ev.power_flows - mark
    mark = ev.power_flows
    if not bounded:
        cap = u / 2.0   # the largest lambda actually evaluated
        return (weekly_capacity(WeekThresholds(n_readings=n, cap=cap), crit), None), lower, \
            base_events, ev.power_flows - pf0

    # 3. thresholds: bisect every (step, kind) event seen at u, all in parallel
    th = WeekThresholds(n_readings=n, cap=u)
    brackets: dict[tuple[str, float], float] = {}
    pair_rows, pair_kind = [], []
    for k in kinds:
        rows = np.flatnonzero(at_u[k] & ~is_base[k])
        pair_rows.append(rows)
        pair_kind.extend([k] * len(rows))
        getattr(th, k).extend(0.0 for _ in range(int((at_u[k] & is_base[k]).sum())))
        getattr(th, k).extend(0.0 for _ in night_base[k])
    rows = np.concatenate(pair_rows) if pair_rows else np.zeros(0, dtype=int)
    kind_arr = np.array(pair_kind)
    p_lo = np.concatenate([lo[k][r] for k, r in zip(kinds, pair_rows)]) if len(rows) else np.zeros(0)
    p_hi = np.full(len(rows), u)
    for _ in range(max_bis):
        active = np.flatnonzero(p_hi - p_lo > tol)
        if not len(active):
            break
        mid = 0.5 * (p_lo[active] + p_hi[active])
        r = rows[active]
        e = ev.events(daylight[r], mid, base_line[r], base_trafo[r])
        hit = np.zeros(len(active), dtype=bool)
        for k in kinds:
            sel = kind_arr[active] == k
            hit[sel] = e[k][sel]
        p_hi[active] = np.where(hit, mid, p_hi[active])
        p_lo[active] = np.where(hit, p_lo[active], mid)
    for k, low, high in zip(kind_arr, p_lo, p_hi):
        getattr(th, str(k)).append(float(low))
        key = (str(k), float(low))
        brackets[key] = min(float(high), brackets.get(key, math.inf))
    stages["thresholds"] = ev.power_flows - mark
    stages["evented_at_bracket"] = int(len(rows))
    outcome = weekly_capacity(th, crit)
    lam_fail = brackets.get((outcome.binding, outcome.lambda_max)) if outcome.bounded else None
    return (outcome, lam_fail), lower, base_events, ev.power_flows - pf0


def _verify(ev: _BatchedEvaluator, idx: np.ndarray, lam: float, crit: DurationCriterion) -> dict:
    """Exact per-bus upper counts in one window at ``lam`` (all steps, one batch)."""
    n = len(idx)
    r = ev.solve(idx, lam)
    vmat = np.where(np.isfinite(r.vm), r.vm, np.inf)       # isolated buses count as critical
    vmat[~r.converged] = np.inf                            # so does non-convergence
    any_c = int((vmat.max(axis=1) > crit.critical_hi_pu + _EPS).sum())
    any_p = int((vmat.max(axis=1) > crit.adequate_hi_pu + _EPS).sum()) - any_c
    per_c = (vmat > crit.critical_hi_pu + _EPS).sum(axis=0)
    per_p = (vmat > crit.adequate_hi_pu + _EPS).sum(axis=0) - per_c
    allow_p = crit.allowed(crit.upper_precarious_max, n)
    allow_c = crit.allowed(crit.upper_critical_max, n)
    return {
        "lambda": lam, "n_readings": n,
        "any_bus_upper_drp": any_p / n, "any_bus_upper_drc": any_c / n,
        "worst_bus_upper_drp": float(per_p.max() / n), "worst_bus_upper_drc": float(per_c.max() / n),
        "within_regulatory_allowance_per_bus": bool(per_p.max() <= allow_p and per_c.max() <= allow_c),
    }


def find_hosting_capacity_duration_batched(
    net: pp.pandapowerNet,
    network_name: str,
    series: TimeSeries,
    criterion: str | DurationCriterion = "prodist_m8_drp_drc",
    allocation: PVAllocation | None = None,
    *,
    windows: list[int] | None = None,
    tolerance: float = SEARCH_DEFAULTS.tolerance,
    lambda_start: float | None = None,
    max_expansions: int = SEARCH_DEFAULTS.max_expansions,
    max_bisections: int = SEARCH_DEFAULTS.max_bisections,
    baseline_report: bool = True,
    verify: bool = True,
    load_matrix: np.ndarray | None = None,
    device: str = "auto",
    dtype: str = "float64",
    memory_mb: int = 1024,
) -> QstsDurationResult:
    """Hosting capacity under a weekly duration criterion, batched solver.

    Arguments are those of :func:`.qsts_duration.find_hosting_capacity_duration`
    plus:

    Args:
        device: ``"auto"`` (GPU when available), ``"cpu"`` or ``"cuda"``.
        dtype: ``"float64"`` (reported results) or ``"float32"`` (exploration only).
        memory_mb: Rough working-memory budget per batch; larger batches are split.

    Raises:
        ImportError: PyTorch is not installed.
        gridsense_sim.accel.UnsupportedNetworkError: the network cannot be
            represented by the batched model (use the pandapower backend).
    """
    crit = get_criterion(criterion)
    ts = series
    res_min, per_window, matches, notes, wanted = _plan_windows(ts, crit, windows)
    ev = _BatchedEvaluator(net, network_name, ts, allocation, crit, load_matrix, device, dtype, memory_mb)
    results: list[WindowResult] = []
    warm: float | None = lambda_start
    for w in wanted:
        idx = np.arange(w * per_window, (w + 1) * per_window)
        out, lower, base_events, pfs = _window_search(
            ev, idx, crit, warm, tolerance, max_expansions, max_bisections, baseline_report)
        if out is None:  # no daylight in this window
            results.append(WindowResult(w, ts.start_step + int(idx[0]), len(idx), math.inf, None, False, None,
                                        {}, {}, {}, lower, pfs))
            continue
        outcome, lam_fail = out
        results.append(WindowResult(
            index=w, start_step=ts.start_step + int(idx[0]), n_readings=len(idx),
            lambda_max=outcome.lambda_max, lambda_fail=lam_fail, bounded=outcome.bounded,
            binding=outcome.binding, counts_at_exceedance=outcome.counts_at_exceedance,
            allowances=outcome.allowances,
            baseline_upper_counts={k: len(v) for k, v in base_events.items()},
            baseline_lower=lower, power_flows=pfs,
        ))
        if outcome.bounded and outcome.lambda_max > 0:
            warm = outcome.lambda_max * 1.25
    critical, lam, bounded = _critical_window(results)
    verification: dict = {}
    if verify and critical and bounded and lam > 0:
        idx = np.arange(critical.index * per_window, (critical.index + 1) * per_window)
        verification = _verify(ev, idx, lam, crit)
    info = {**device_info(ev.solver.device), "dtype": dtype, "model_check": ev.model.check}
    return _build_result(network_name, crit, ts, results, verification, ev.allocation, len(ev.scope),
                         tolerance, res_min, matches, notes, ev.power_flows,
                         backend="torch", backend_info=info)


__all__ = ["find_hosting_capacity_duration_batched"]
