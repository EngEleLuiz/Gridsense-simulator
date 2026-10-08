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
* R11 -- default strategy ``"min_over_steps"``. With a zero-tolerance
  criterion the hosting capacity is ``min_t lambda*_t`` over daylight
  steps, and each step is monotone in lambda on its own. Steps are
  visited in risk order (highest PV-to-load ratio first) while a bracket
  ``[L, U]`` is kept: a step that is feasible at ``L`` cannot lower the
  minimum (one power flow); a step that violates at ``L`` is bisected
  alone inside ``[0, L]``. The contract is exactly that of the global
  bisection -- ``L`` verified feasible at every daylight step, ``U``
  verified infeasible at some step, ``U - L <= tolerance`` -- at roughly
  one power flow per step instead of one per step per feasible
  candidate. ``"global_bisection"`` keeps the old algorithm; the
  regression test checks that both brackets contain the same threshold.
  ``first_violating_step`` is always found chronologically at ``U``.

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
from .search import SEARCH_DEFAULTS, BisectionOutcome, bisect_max_feasible, bisect_within
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
    resolved: bool
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
    strategy: str = "min_over_steps"
    peak_load_mult: float | None = None
    series_source: str = "synthetic"
    series_manifest_sha256: str | None = None


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
    tolerance: float = SEARCH_DEFAULTS.tolerance,
    max_expansions: int = SEARCH_DEFAULTS.max_expansions,
    max_bisections: int = SEARCH_DEFAULTS.max_bisections,
    profile_seed: int | None = 42,
    strategy: str = "min_over_steps",
    peak_load_mult: float | None = None,
) -> QstsHCResult:
    """Zero-tolerance QSTS HC.

    Args:
        net: Nominal network. **Not mutated.**
        network_name: Registered network key.
        allocation: PV basis at ``lambda_ = 1`` (nominal-load proportional by default).
        series: Precomputed series; if ``None``, a synthetic one is built
            from ``total_steps``/``steps_per_day``/``start_step``/``profile_seed``.
        framework: Voltage framework key.
        tolerance, max_expansions, max_bisections: Search controls
            (shared defaults, see ``search.SEARCH_DEFAULTS``).
        peak_load_mult: Forwarded to :func:`~.timeseries.build_synthetic_series`
            when ``series`` is ``None`` (finding R22); ignored otherwise.
        strategy: ``"min_over_steps"`` (default, fastest) or
            ``"global_bisection"``. Same contract; see the module docstring.

    Raises:
        NoDaylightError: the series has no step with PV output > 0.
        BaselineInfeasibleError: some step violates with PV = 0.
    """
    if strategy not in ("min_over_steps", "global_bisection"):
        raise ValueError(
            f"strategy must be 'min_over_steps' or 'global_bisection', got {strategy!r}."
        )
    limits = limits_for(network_name, framework)
    work = copy.deepcopy(net)
    allocation = allocation or proportional_to_load(work)
    allocation.require_non_empty()

    ts = series or build_synthetic_series(
        total_steps, steps_per_day, profile_seed, start_step, peak_load_mult=peak_load_mult
    )
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

    def _step_report(t: int, lam: float) -> ViolationReport:
        nonlocal power_flows
        _set_step(t, lam)
        power_flows += 1
        return check_violations(work, limits, scope)

    if strategy == "global_bisection":
        out = bisect_max_feasible(
            lambda lam: any(_step_report(int(t), lam).has_violation for t in daylight),
            tolerance=tolerance,
            max_expansions=max_expansions,
            max_bisections=max_bisections,
        )
    else:
        out = _min_over_steps(
            _step_report, daylight, ts, tolerance, max_expansions, max_bisections
        )

    step: int | None = None
    binding: str | None = None
    if out.bounded and out.lambda_fail is not None:
        for t in daylight:  # chronological, once
            report = _step_report(int(t), out.lambda_fail)
            if report.has_violation:
                step, binding = ts.start_step + int(t), report.binding_constraint()
                break
        else:  # pragma: no cover - lambda_fail was verified infeasible at some step
            raise RuntimeError(f"lambda_fail={out.lambda_fail} has no violating step.")

    pv = allocation.mw_at(out.lambda_max)
    return QstsHCResult(
        network_name=network_name,
        lambda_max=out.lambda_max,
        lambda_fail=out.lambda_fail,
        bounded=out.bounded,
        resolved=out.resolved,
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
        strategy=strategy,
        peak_load_mult=peak_load_mult if series is None else None,
        series_source=ts.source,
        series_manifest_sha256=ts.manifest_sha256,
    )


def _min_over_steps(
    step_report,
    daylight: np.ndarray,
    ts: TimeSeries,
    tolerance: float,
    max_expansions: int,
    max_bisections: int,
) -> BisectionOutcome:
    """Bracket ``min_t lambda*_t`` visiting steps in risk order (R11)."""
    ratio = ts.pv_mult[daylight] / ts.load_mult[daylight]
    visit = [int(t) for t in daylight[np.argsort(-ratio, kind="stable")]]

    lo: float | None = None   # verified feasible at every step visited so far
    hi: float | None = None   # verified infeasible at some step
    evaluations = 0
    for t in visit:
        def violates(lam: float, _t: int = t) -> bool:
            return step_report(_t, lam).has_violation

        if hi is None:
            o = bisect_max_feasible(
                violates, tolerance=tolerance,
                max_expansions=max_expansions, max_bisections=max_bisections,
            )
            evaluations += o.evaluations
            lo = o.lambda_max if lo is None else min(lo, o.lambda_max)
            if o.bounded:
                lo, hi = o.lambda_max, o.lambda_fail
            continue
        evaluations += 1
        if not violates(lo):
            continue  # lambda*_t >= lo: this step cannot lower the minimum
        o = bisect_within(violates, 0.0, lo, tolerance=tolerance, max_bisections=max_bisections)
        evaluations += o.evaluations
        lo, hi = o.lambda_max, o.lambda_fail

    assert lo is not None  # daylight is non-empty
    if hi is None:
        return BisectionOutcome(lo, None, False, evaluations, False)
    return BisectionOutcome(lo, hi, True, evaluations, (hi - lo) <= tolerance)


__all__ = ["QstsHCResult", "find_hosting_capacity", "DEFAULT_STEPS_PER_DAY", "DEFAULT_TOTAL_STEPS"]
