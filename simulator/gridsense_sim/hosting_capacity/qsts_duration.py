"""QSTS hosting capacity under a duration criterion (PRODIST DRP/DRC, EN 50160).

The series is split into compliance windows (one week by default). For
each window the capacity is the first exceedance of the criterion's
allowances by PV-attributable events (see :mod:`.duration`). The annual
capacity is the minimum over windows; the per-window capacities are
returned too, because their spread is a result in itself (seasonality).

Search, per window (``U`` = current upper bound)
------------------------------------------------
1. **Baseline** -- one power flow per step at ``lambda = 0``: no-PV
   voltages and loadings (needed for base-relative attribution) and the
   lower-side compliance report.
2. **Bracket** -- evaluate every daylight step at ``U`` (one power flow
   each). If the criterion is not exceeded at ``U``, double ``U`` (only
   steps without an event at the old ``U`` need re-evaluation). Warm
   start from the previous window's capacity.
3. **Shrink** -- while many steps have events at ``U``, try a lower
   ``U`` using *only those steps* (monotonicity: a step without an event
   at ``U`` has none below it); keep it if the criterion is still
   exceeded there.
4. **Thresholds** -- bisect each event threshold of the remaining steps
   in ``[0, U]`` and sweep (:func:`.duration.weekly_capacity`).

Typical cost is one power flow per step plus a few hundred for the
bisections, instead of one bisection per step.

Per-consumer vs any-bus
-----------------------
PRODIST counts readings per consumer. Thresholds here are taken on the
*worst* bus of the voltage scope, so counts are an upper bound of every
consumer's count and the capacity is conservative. The result verifies
the critical window at the reported capacity and reports both the
any-bus and the worst per-bus counts, so the size of that margin is
measured, not assumed.

Requirements: series resolution equal to the criterion's reading length
(10 min for PRODIST/EN 50160). Other resolutions are accepted and the
mismatch is recorded (counts are then per step, not per 10-min reading).
"""

from __future__ import annotations

import copy
import math
from dataclasses import asdict, dataclass, field

import numpy as np
import pandapower as pp

from .allocation import PVAllocation, apply_allocation, proportional_to_load
from .conditions import StudyConditions
from .duration import DurationCriterion, WeekThresholds, get_criterion, weekly_capacity
from .errors import NoDaylightError
from .limits import limits_for
from .scope import voltage_scope_buses
from .search import SEARCH_DEFAULTS
from .timeseries import TimeSeries
from .violations import run_power_flow

_EPS = 1e-6


@dataclass
class WindowResult:
    index: int
    start_step: int
    n_readings: int
    lambda_max: float
    lambda_fail: float | None
    bounded: bool
    binding: str | None
    counts_at_exceedance: dict
    allowances: dict
    baseline_upper_counts: dict
    baseline_lower: dict
    power_flows: int


@dataclass
class QstsDurationResult:
    network_name: str
    criterion: dict
    lambda_max: float
    lambda_fail: float | None
    bounded: bool
    resolved: bool
    critical_window: int | None
    binding_constraint: str | None
    pv_mw_per_bus: dict[int, float]
    total_pv_mw: float
    windows: list[WindowResult]
    window_lambda_quantiles: dict[str, float]
    verification: dict
    baseline_lower_compliant: bool
    resolution_minutes: float
    resolution_matches_criterion: bool
    total_steps: int
    steps_per_day: int
    power_flows: int
    conditions: StudyConditions
    series_source: str = "synthetic"
    series_manifest_sha256: str | None = None
    notes: list[str] = field(default_factory=list)


class _Evaluator:
    """Power flows at (step, lambda) with PV-side event classification."""

    def __init__(self, net, network_name, ts, allocation, criterion, load_matrix):
        self.limits = limits_for(network_name, None)
        self.net = copy.deepcopy(net)
        self.allocation = allocation or proportional_to_load(self.net)
        self.allocation.require_non_empty()
        self.scope = list(voltage_scope_buses(self.net, self.limits))
        self.ts = ts
        self.crit = criterion
        self.base_p = self.net.load["p_mw"].to_numpy(copy=True)
        self.base_q = self.net.load["q_mvar"].to_numpy(copy=True)
        if load_matrix is not None:
            load_matrix = np.asarray(load_matrix, dtype=float)
            if load_matrix.shape != (ts.n_steps, len(self.base_p)):
                raise ValueError(f"load_matrix must have shape {(ts.n_steps, len(self.base_p))}.")
        self.load_matrix = load_matrix
        self.power_flows = 0
        self.base_line: dict[int, np.ndarray] = {}
        self.base_trafo: dict[int, np.ndarray] = {}
        self.base_vm: dict[int, np.ndarray] = {}

    def _set(self, t: int, lam: float) -> None:
        m = self.load_matrix[t] if self.load_matrix is not None else float(self.ts.load_mult[t])
        self.net.load["p_mw"] = self.base_p * m
        self.net.load["q_mvar"] = self.base_q * m
        apply_allocation(self.net, self.allocation, lam * float(self.ts.pv_mult[t]))

    def solve(self, t: int, lam: float):
        """Return (converged, vm over scope, line loading, trafo loading)."""
        self._set(t, lam)
        self.power_flows += 1
        ok = run_power_flow(self.net)
        if not ok:
            return False, None, None, None
        vm = self.net.res_bus.loc[self.scope, "vm_pu"].to_numpy()
        line = self.net.res_line["loading_percent"].to_numpy() if len(self.net.line) else np.zeros(0)
        trafo = self.net.res_trafo["loading_percent"].to_numpy() if len(self.net.trafo) else np.zeros(0)
        return True, vm, np.nan_to_num(line), np.nan_to_num(trafo)

    def baseline(self, t: int):
        ok, vm, line, trafo = self.solve(t, 0.0)
        if not ok:
            raise RuntimeError(f"Power flow did not converge without PV at step {self.ts.start_step + t}.")
        self.base_line[t], self.base_trafo[t] = line, trafo
        return vm

    def events(self, t: int, lam: float) -> dict[str, bool]:
        """PV-side events at (t, lam). Non-convergence counts as critical (conservative)."""
        ok, vm, line, trafo = self.solve(t, lam)
        if not ok:
            return {"precarious": True, "critical": True, "thermal": True}
        vmax = float(np.nanmax(vm))
        lim_line = self.limits.max_line_loading_percent
        lim_trafo = self.limits.max_trafo_loading_percent
        therm = bool(
            np.any(line > np.maximum(lim_line, self.base_line[t]) + _EPS)
            or np.any(trafo > np.maximum(lim_trafo, self.base_trafo[t]) + _EPS)
        )
        return {
            "precarious": vmax > self.crit.adequate_hi_pu + _EPS,
            "critical": vmax > self.crit.critical_hi_pu + _EPS,
            "thermal": therm,
        }


def _kinds(crit: DurationCriterion) -> tuple[str, ...]:
    return ("precarious", "critical", "thermal") if crit.has_upper_precarious_band else ("critical", "thermal")


def _exceeded_at(event_sets: dict[str, set[int]], base: dict[str, set[int]], n: int,
                 crit: DurationCriterion) -> bool:
    """Is the criterion exceeded at a lambda whose events per kind are given?"""
    th = WeekThresholds(n_readings=n, cap=math.inf)
    # Encode: baseline events at 0, other events just below the evaluated lambda (any positive value).
    for kind, steps in event_sets.items():
        lst = getattr(th, kind)
        for s in steps:
            lst.append(0.0 if s in base.get(kind, set()) else 1.0)
    return weekly_capacity(th, crit).bounded


class _Bounds:
    """Per (step, kind): largest lambda seen without the event, smallest seen with it."""

    def __init__(self) -> None:
        self.no: dict[tuple[int, str], float] = {}
        self.yes: dict[tuple[int, str], float] = {}

    def record(self, t: int, lam: float, events: dict[str, bool]) -> None:
        for k, v in events.items():
            key = (t, k)
            if v:
                self.yes[key] = min(self.yes.get(key, math.inf), lam)
            else:
                self.no[key] = max(self.no.get(key, 0.0), lam)


def _probe(ev: _Evaluator, bounds: _Bounds, t: int, lam: float) -> dict[str, bool]:
    e = ev.events(t, lam)
    bounds.record(t, lam, e)
    return e


def _bisect_threshold(ev, bounds: _Bounds, t: int, kind: str, tol: float, max_bis: int) -> tuple[float, float]:
    """Narrow ``kind``'s threshold at step t starting from the bounds already observed."""
    lo = bounds.no.get((t, kind), 0.0)
    hi = bounds.yes[(t, kind)]
    for _ in range(max_bis):
        if hi - lo <= tol:
            break
        mid = 0.5 * (lo + hi)
        if _probe(ev, bounds, t, mid)[kind]:
            hi = mid
        else:
            lo = mid
        lo, hi = bounds.no.get((t, kind), lo), bounds.yes.get((t, kind), hi)
    return lo, hi


def _window_search(ev, idx, crit, u0, tol, max_exp, max_bis, baseline_report, shrink_budget):
    n = len(idx)
    ts = ev.ts
    pf0 = ev.power_flows
    kinds = _kinds(crit)
    bounds = _Bounds()
    # 1. baseline (no PV)
    lower = {"n_scope_buses": len(ev.scope)}
    vm_rows = []
    base_events: dict[str, set[int]] = {k: set() for k in kinds}
    daylight = [int(t) for t in idx if ts.pv_mult[t] > 0]
    for t in (idx if baseline_report else daylight):
        vm = ev.baseline(int(t))
        ev.base_vm[int(t)] = vm
        vm_rows.append(vm)
        vmax = float(np.nanmax(vm))
        if vmax > crit.critical_hi_pu + _EPS:
            base_events["critical"].add(int(t))
        if "precarious" in kinds and vmax > crit.adequate_hi_pu + _EPS:
            base_events["precarious"].add(int(t))
    if baseline_report and vm_rows:
        vmat = np.vstack(vm_rows)
        prec = (vmat < crit.adequate_lo_pu - _EPS).sum(axis=0)
        critc = (vmat < crit.critical_lo_pu - _EPS).sum(axis=0)
        drp, drc = (prec - critc) / n, critc / n
        lower.update({
            "max_drp": float(drp.max()), "max_drc": float(drc.max()),
            "share_buses_drc_positive": float((drc > 0).mean()),
            "compliant": bool(drp.max() <= crit.lower_precarious_max + 1e-12
                              and drc.max() <= crit.lower_critical_max + 1e-12),
        })
    stages = {"baseline": ev.power_flows - pf0}
    lower["power_flows_by_stage"] = stages
    if not daylight:
        return None, lower, base_events, ev.power_flows - pf0
    mark = ev.power_flows
    night_base = {k: {t for t in base_events[k] if ts.pv_mult[t] <= 0} for k in kinds}
    risk = sorted(daylight, key=lambda t: -float(ts.pv_mult[t]) / float(ts.load_mult[t]))

    def exceeded(evented: dict[int, dict[str, bool]]) -> bool:
        sets = {k: {t for t, e in evented.items() if e[k]} | night_base[k] for k in kinds}
        return _exceeded_at(sets, base_events, n, crit)

    # 2. bracket: start from the riskiest step's threshold when there is no warm start
    if u0 is None:
        t1 = risk[0]
        probe_k = kinds[0]
        u = 1.0
        for _ in range(max_exp):
            if _probe(ev, bounds, t1, u)[probe_k]:
                break
            u *= 2.0
        lo1, hi1 = _bisect_threshold(ev, bounds, t1, probe_k, max(tol, 0.05), max_bis)
        u = max(hi1 * 1.5, 4 * tol)
        # Below the riskiest step's first threshold no step can have an event
        # (it is the highest-ratio step), so the window capacity is >= lo1.
        floor_u = lo1
    else:
        u = max(u0, 4 * tol)
        floor_u = 0.0
    def evented(evs: dict[int, dict[str, bool]]) -> dict[int, dict[str, bool]]:
        return {t: e for t, e in evs.items() if any(e[k] for k in kinds)}

    # Bracket: one full pass per u (every step evaluated once), so the shrink
    # below only has to look at steps with an event. Inside the shrink a pass
    # stops as soon as the criterion is exceeded: exceedance shown on a subset
    # holds for the whole window (counts only grow).
    known: dict[int, dict[str, bool]] = {}   # events at the current u
    bounded = False
    for _ in range(max_exp + 1):
        for t in risk:
            if t in known and all(known[t][k] for k in kinds):
                continue  # all kinds true at a smaller u stay true (monotone)
            known[t] = _probe(ev, bounds, t, u)
        if exceeded(evented(known)):
            bounded = True
            break
        u *= 2.0
        known = {t: e for t, e in known.items() if all(e[k] for k in kinds)}
    stages["bracket"] = ev.power_flows - mark
    mark = ev.power_flows
    if not bounded:
        return (weekly_capacity(WeekThresholds(n_readings=n, cap=u), crit), None), lower, \
            base_events, ev.power_flows - pf0
    # 3. shrink: candidates = steps with an event at u, or not yet evaluated at u
    cands = [t for t in risk if t not in known or any(known[t][k] for k in kinds)]
    lo_u = min(floor_u, u)
    while len(cands) > shrink_budget and (u - lo_u) > 4 * tol:
        mid = 0.5 * (lo_u + u)
        trial = {t: _probe(ev, bounds, t, mid) for t in cands}  # full pass: prunes candidates
        if exceeded(evented(trial)):
            u = mid
            known = trial
            cands = [t for t in cands if any(trial[t][k] for k in kinds)]
        else:
            lo_u = mid
    for t in cands:  # make sure every candidate's events at u are known
        if t not in known:
            known[t] = _probe(ev, bounds, t, u)
    evented_final = {t: known[t] for t in cands if any(known[t][k] for k in kinds)}
    stages["shrink"] = ev.power_flows - mark
    stages["evented_after_shrink"] = len(evented_final)
    mark = ev.power_flows
    # 4. thresholds + exact sweep
    th = WeekThresholds(n_readings=n, cap=u)
    brackets: dict[tuple[str, float], float] = {}
    for t, e in evented_final.items():
        for k in kinds:
            if not e[k]:
                continue
            if t in base_events[k]:
                getattr(th, k).append(0.0)
                continue
            lo, hi = _bisect_threshold(ev, bounds, t, k, tol, max_bis)
            getattr(th, k).append(lo)
            brackets[(k, lo)] = min(hi, brackets.get((k, lo), math.inf))
    for k in kinds:
        getattr(th, k).extend(0.0 for _ in night_base[k])
    stages["thresholds"] = ev.power_flows - mark
    outcome = weekly_capacity(th, crit)
    lam_fail = None
    if outcome.bounded:
        lam_fail = brackets.get((outcome.binding, outcome.lambda_max), outcome.lambda_max + tol)
    return (outcome, lam_fail), lower, base_events, ev.power_flows - pf0


def _verify(ev: _Evaluator, idx, lam: float, crit: DurationCriterion) -> dict:
    """Exact per-bus upper counts in one window at ``lam`` (all steps)."""
    n = len(idx)
    rows = []
    for t in idx:
        t = int(t)
        if ev.ts.pv_mult[t] <= 0 and t in ev.base_vm:  # no PV at night: reuse the baseline solution
            rows.append(ev.base_vm[t])
            continue
        ok, vm, _, _ = ev.solve(t, lam)
        rows.append(vm if ok else np.full(len(ev.scope), np.inf))
    vmat = np.vstack(rows)
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
        # Regulatory allowance only (no baseline relief): True means every
        # consumer is compliant at this lambda by the letter of the criterion.
        "within_regulatory_allowance_per_bus": bool(per_p.max() <= allow_p and per_c.max() <= allow_c),
    }


def find_hosting_capacity_duration(
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
    shrink_budget: int | None = None,
) -> QstsDurationResult:
    """Hosting capacity under a weekly duration criterion (see module docstring).

    Args:
        net: Nominal network (not mutated).
        series: QSTS series; resolution should equal the criterion's reading length.
        criterion: Key of :data:`.duration.DURATION_CRITERIA` or a criterion object.
        windows: Window indices to evaluate (default: all complete windows).
        baseline_report: Solve night steps too, for the lower-side compliance report.
        verify: Re-solve the critical window at the capacity and report per-bus counts.
        load_matrix: Optional per-load multipliers ``(n_steps, n_loads)`` (diversity).
        shrink_budget: Stop shrinking the bracket at this many evented steps.
    """
    crit = get_criterion(criterion)
    ts = series
    if ts.daylight_steps.size == 0:
        raise NoDaylightError("The series has no step with PV output > 0.")
    res_min = 1440.0 / ts.steps_per_day
    per_window = round(crit.window_days * 24 * 60 / res_min)
    n_windows = ts.n_steps // per_window
    notes: list[str] = []
    if n_windows == 0:
        raise ValueError(f"Series of {ts.n_steps} steps is shorter than one {crit.window_days}-day window.")
    if ts.n_steps % per_window:
        notes.append(f"{ts.n_steps % per_window} trailing step(s) outside complete windows ignored")
    matches = abs(res_min - crit.reading_minutes) < 1e-9
    if not matches:
        notes.append(f"series step {res_min:g} min != criterion reading {crit.reading_minutes} min: "
                     "counts are per step")
    wanted = list(range(n_windows)) if windows is None else [w for w in windows if 0 <= w < n_windows]
    if not wanted:
        raise ValueError("No valid window selected.")
    ev = _Evaluator(net, network_name, ts, allocation, crit, load_matrix)
    # Exceedance needs at most (allowances + 1) steps with events: shrink the
    # bracket until no more than that remain (or the bracket is tight), so
    # only those steps go through threshold bisection.
    budget = shrink_budget or (
        crit.allowed(crit.upper_precarious_max, per_window)
        + crit.allowed(crit.upper_critical_max, per_window)
        + crit.allowed(crit.thermal_max, per_window) + 1)

    results: list[WindowResult] = []
    warm: float | None = lambda_start
    for w in wanted:
        idx = list(range(w * per_window, (w + 1) * per_window))
        out, lower, base_events, pfs = _window_search(
            ev, idx, crit, warm, tolerance, max_expansions, max_bisections, baseline_report, budget)
        if out is None:  # no daylight in this window
            results.append(WindowResult(w, ts.start_step + idx[0], len(idx), math.inf, None, False, None,
                                        {}, {}, {}, lower, pfs))
            continue
        outcome, lam_fail = out
        results.append(WindowResult(
            index=w, start_step=ts.start_step + idx[0], n_readings=len(idx),
            lambda_max=outcome.lambda_max, lambda_fail=lam_fail, bounded=outcome.bounded,
            binding=outcome.binding, counts_at_exceedance=outcome.counts_at_exceedance,
            allowances=outcome.allowances,
            baseline_upper_counts={k: len(v) for k, v in base_events.items()},
            baseline_lower=lower, power_flows=pfs,
        ))
        if outcome.bounded and outcome.lambda_max > 0:
            warm = outcome.lambda_max * 1.25  # warm start for the next window

    finite = [r for r in results if math.isfinite(r.lambda_max)]
    critical = min(finite, key=lambda r: r.lambda_max) if finite else None
    lam = critical.lambda_max if critical else math.inf
    bounded = bool(critical and critical.bounded)
    lam_fail = critical.lambda_fail if critical else None
    verification: dict = {}
    if verify and critical and bounded and lam > 0:
        idx = list(range(critical.index * per_window, (critical.index + 1) * per_window))
        verification = _verify(ev, idx, lam, crit)
    vals = np.array([r.lambda_max for r in finite]) if finite else np.array([np.nan])
    quant = {f"p{q}": float(np.nanpercentile(vals, q)) for q in (10, 50, 90)}
    quant.update({"min": float(np.nanmin(vals)), "max": float(np.nanmax(vals)), "n_windows": len(finite)})
    lower_ok = all(r.baseline_lower.get("compliant", True) for r in results)
    alloc = ev.allocation
    pv = alloc.mw_at(lam if math.isfinite(lam) else 0.0)
    conditions = StudyConditions(
        network_name=network_name, framework=crit.name, criterion_kind="statistical",
        v_min_pu=crit.adequate_lo_pu, v_max_pu=crit.adequate_hi_pu, load_scale=None,
        n_voltage_buses=len(ev.scope),
    )
    binding = None
    if critical and critical.binding:
        binding = {"critical": "upper_voltage_critical", "precarious": "upper_voltage_precarious",
                   "thermal": "pv_attributable_overload"}[critical.binding]
    return QstsDurationResult(
        network_name=network_name, criterion=crit.to_dict(), lambda_max=lam, lambda_fail=lam_fail,
        bounded=bounded, resolved=bool(lam_fail is not None and lam_fail - lam <= tolerance + 1e-12),
        critical_window=critical.index if critical else None, binding_constraint=binding,
        pv_mw_per_bus=pv, total_pv_mw=round(sum(pv.values()), 6), windows=results,
        window_lambda_quantiles=quant, verification=verification, baseline_lower_compliant=lower_ok,
        resolution_minutes=res_min, resolution_matches_criterion=matches, total_steps=ts.n_steps,
        steps_per_day=ts.steps_per_day, power_flows=ev.power_flows, conditions=conditions,
        series_source=ts.source, series_manifest_sha256=ts.manifest_sha256, notes=notes,
    )


def result_to_dict(r: QstsDurationResult) -> dict:
    d = asdict(r)
    d["windows"] = [asdict(w) for w in r.windows]
    return d


__all__ = ["QstsDurationResult", "WindowResult", "find_hosting_capacity_duration", "result_to_dict"]
