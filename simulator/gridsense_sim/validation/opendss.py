"""Cross-check of pandapower against OpenDSS (EPRI), an independent power-flow engine.

Why
---
Every capacity in this project rests on pandapower; the batched solver is
checked against pandapower, and pandapower against nothing. This module
writes a balanced pandapower network as an OpenDSS circuit, solves it with
the OpenDSS engine (``opendssdirect.py``: a different code base, a
different algorithm -- current injection on the full three-phase
admittance matrix) and compares voltages, branch loadings and, end to end,
the deterministic hosting capacity.

What is translated
------------------
==================  =========================================================
pandapower          OpenDSS
==================  =========================================================
one ``ext_grid``    ``Circuit`` (stiff source at ``vm_pu``)
``line``            ``Line`` with r1/x1/c1 (zero sequence = positive sequence)
``trafo``           two-winding ``Transformer``, wye-wye: XHL, %loadloss,
                    %noloadloss, %imag, off-nominal tap
``load``            constant-power ``Load`` (``model=1``, voltage window
                    opened so OpenDSS never switches it to constant impedance)
``sgen``            constant-power ``Load`` with negative power
closed bus switch   the two buses become one node
==================  =========================================================

Voltage magnitudes do not depend on the transformer vector group in a
balanced network, so every transformer is written wye-wye and only
magnitudes are compared. Anything else (several external grids, open line
or transformer switches, shunts, generators, three-winding transformers,
ZIP loads, three-phase unbalanced elements) raises
:class:`UnsupportedForOpenDss` instead of being approximated.

Measured agreement (see ``scripts/validate_against_opendss.py``)
---------------------------------------------------------------
Without a magnetising branch (``cigre_lv``) the two engines agree to about
2e-8 pu. With one, they differ by up to about 4e-5 pu on the panel: the two
programs represent the no-load losses and the magnetising current of a
transformer differently (the difference vanishes when ``pfe_kw`` and
``i0_percent`` are set to zero, and does not depend on pandapower's
``trafo_model``). That is an order of magnitude below the voltage change of
one search tolerance (about 6e-4 pu per 0.01 of lambda on ``cigre_lv``), and
the deterministic capacities of the two engines coincide.
"""

from __future__ import annotations

import copy
import math
from dataclasses import asdict, dataclass

import numpy as np
import pandapower as pp

try:  # optional dependency
    import opendssdirect as dss
except ImportError:  # pragma: no cover - exercised only without the extra
    dss = None

INSTALL_HINT = 'The OpenDSS cross-check needs opendssdirect.py: pip install -e "simulator[validation]".'
SQRT3 = math.sqrt(3.0)


class UnsupportedForOpenDss(ValueError):
    """The network uses something this translation does not cover."""


def opendss_available() -> bool:
    return dss is not None


@dataclass
class OpenDssSolution:
    """Result of one OpenDSS solve, in pandapower's units and table order."""

    converged: bool
    vm_pu: dict[int, float]            # pandapower bus index -> voltage magnitude (pu of the bus nominal voltage)
    line_loading_percent: np.ndarray   # NaN for lines out of service
    trafo_loading_percent: np.ndarray
    iterations: int


def _bus_groups(net: pp.pandapowerNet) -> dict[int, int]:
    """Bus -> representative bus after fusing closed bus-bus switches (union-find)."""
    parent = {int(b): int(b) for b in net.bus.index}

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    if len(net.switch):
        for _, s in net.switch.iterrows():
            if s["et"] == "b" and bool(s["closed"]):
                a, b = find(int(s["bus"])), find(int(s["element"]))
                if a != b:
                    parent[max(a, b)] = min(a, b)
    return {b: find(b) for b in parent}


def _check_supported(net: pp.pandapowerNet) -> None:
    if len(net.get("asymmetric_load", [])) or len(net.get("asymmetric_sgen", [])):
        raise UnsupportedForOpenDss("Three-phase unbalanced networks are not translated.")
    if len(net.ext_grid[net.ext_grid["in_service"]]) != 1:
        raise UnsupportedForOpenDss("Exactly one external grid in service is supported.")
    for table in ("gen", "shunt", "trafo3w", "impedance", "ward", "xward", "storage", "dcline"):
        if table in net and len(net[table]) and bool(net[table].get("in_service", True).any()
                                                      if hasattr(net[table].get("in_service", True), "any")
                                                      else True):
            raise UnsupportedForOpenDss(f"Element table '{table}' is not translated.")
    if len(net.switch):
        open_branch = net.switch[(net.switch["et"] != "b") & ~net.switch["closed"].astype(bool)]
        if len(open_branch):
            raise UnsupportedForOpenDss("Open line or transformer switches are not translated.")
    for col in ("const_z_p_percent", "const_i_p_percent", "const_z_q_percent", "const_i_q_percent",
                "const_z_percent", "const_i_percent"):
        if col in net.load and float(np.abs(np.nan_to_num(net.load[col].to_numpy(dtype=float))).sum()) > 0:
            raise UnsupportedForOpenDss("Voltage-dependent (ZIP) loads are not translated.")
    if len(net.line) and "g_us_per_km" in net.line \
            and float(np.abs(np.nan_to_num(net.line["g_us_per_km"].to_numpy(dtype=float))).sum()) > 0:
        raise UnsupportedForOpenDss("Line shunt conductance is not translated.")
    if not bool(net.bus["in_service"].all()):
        raise UnsupportedForOpenDss("Buses out of service are not translated.")
    if len(net.trafo) and "tap_dependent_impedance" in net.trafo \
            and bool(net.trafo["tap_dependent_impedance"].fillna(False).astype(bool).any()):
        raise UnsupportedForOpenDss("Tap-dependent transformer impedance is not translated.")


def _missing(v) -> bool:
    return v is None or (isinstance(v, float) and math.isnan(v))


def _tap_ratio(row) -> tuple[float, float]:
    """Per-unit taps (hv, lv) of a pandapower transformer row, as pandapower applies them.

    pandapower 3 only applies a tap when ``tap_changer_type`` is set: ``"Ratio"`` is
    a plain voltage ratio (translated); phase-shifting types are refused. Older
    versions have no such column and apply ``tap_pos`` whenever it is given.
    """
    if "tap_changer_type" in row.index:
        kind = row["tap_changer_type"]
        if _missing(kind) or str(kind) in ("", "None", "nan"):
            return 1.0, 1.0
        if str(kind) != "Ratio":
            raise UnsupportedForOpenDss(f"Tap changer type '{kind}' is not translated (only 'Ratio').")
    pos, neutral, step = row.get("tap_pos"), row.get("tap_neutral"), row.get("tap_step_percent")
    if any(_missing(v) for v in (pos, neutral, step)):
        return 1.0, 1.0
    degree = row.get("tap_step_degree")
    if not _missing(degree) and float(degree) != 0.0:
        raise UnsupportedForOpenDss("Phase-shifting tap steps (tap_step_degree) are not translated.")
    ratio = 1.0 + (float(pos) - float(neutral)) * float(step) / 100.0
    return (ratio, 1.0) if str(row.get("tap_side")) != "lv" else (1.0, ratio)


def to_opendss(net: pp.pandapowerNet) -> list[str]:
    """OpenDSS commands that build ``net`` (not mutated). See the module docstring."""
    _check_supported(net)
    group = _bus_groups(net)
    f_hz = float(net.f_hz)
    ext = net.ext_grid[net.ext_grid["in_service"]].iloc[0]
    slack = group[int(ext["bus"])]
    cmds = [
        "Clear",
        f"Set DefaultBaseFrequency={f_hz:g}",
        f"New Circuit.gridsense bus1=b{slack} basekv={float(net.bus.at[int(ext['bus']), 'vn_kv']):.12g} "
        f"pu={float(ext['vm_pu']):.12g} angle={float(ext.get('va_degree', 0.0) or 0.0):.12g} phases=3 "
        f"frequency={f_hz:g} MVAsc3=1e12 MVAsc1=1e12",
    ]
    for idx, ln in net.line.iterrows():
        if not bool(ln["in_service"]):
            continue
        par = float(ln["parallel"])
        r, x, c = float(ln["r_ohm_per_km"]) / par, float(ln["x_ohm_per_km"]) / par, float(ln["c_nf_per_km"]) * par
        a, b = group[int(ln["from_bus"])], group[int(ln["to_bus"])]
        if a == b:
            continue  # both ends on the same fused node
        cmds.append(
            f"New Line.l{idx} bus1=b{a} bus2=b{b} phases=3 length={float(ln['length_km']):.12g} units=km "
            f"r1={r:.12g} x1={x:.12g} c1={c:.12g} r0={r:.12g} x0={x:.12g} c0={c:.12g}")
    for idx, tr in net.trafo.iterrows():
        if not bool(tr["in_service"]):
            continue
        par = float(tr["parallel"])
        sn_kva = float(tr["sn_mva"]) * 1000.0 * par
        vk, vkr = float(tr["vk_percent"]), float(tr["vkr_percent"])
        pfe = float(tr["pfe_kw"]) * par
        i0 = float(tr["i0_percent"])
        noload = 100.0 * pfe / sn_kva
        imag = math.sqrt(max(i0 ** 2 - noload ** 2, 0.0))
        tap_hv, tap_lv = _tap_ratio(tr)
        cmds.append(
            f"New Transformer.t{idx} phases=3 windings=2 buses=(b{group[int(tr['hv_bus'])]}, "
            f"b{group[int(tr['lv_bus'])]}) conns=(wye, wye) kVs=({float(tr['vn_hv_kv']):.12g}, "
            f"{float(tr['vn_lv_kv']):.12g}) kVAs=({sn_kva:.12g}, {sn_kva:.12g}) "
            f"taps=({tap_hv:.12g}, {tap_lv:.12g}) XHL={math.sqrt(max(vk ** 2 - vkr ** 2, 0.0)):.12g} "
            f"%loadloss={vkr:.12g} %noloadloss={noload:.12g} %imag={imag:.12g}")

    def injection(name: str, bus: int, p_mw: float, q_mvar: float) -> str:
        return (f"New Load.{name} bus1=b{group[bus]} phases=3 conn=wye kV={float(net.bus.at[bus, 'vn_kv']):.12g} "
                f"kW={1000.0 * p_mw:.12g} kvar={1000.0 * q_mvar:.12g} model=1 vminpu=0.01 vmaxpu=10")

    for idx, ld in net.load.iterrows():
        if bool(ld["in_service"]):
            s = float(ld.get("scaling", 1.0))
            cmds.append(injection(f"ld{idx}", int(ld["bus"]), float(ld["p_mw"]) * s, float(ld["q_mvar"]) * s))
    for idx, sg in net.sgen.iterrows():
        if bool(sg["in_service"]):
            s = float(sg.get("scaling", 1.0))
            cmds.append(injection(f"sg{idx}", int(sg["bus"]), -float(sg["p_mw"]) * s, -float(sg["q_mvar"]) * s))
    cmds += ["Set controlmode=off", "Set tolerance=1e-10", "Set maxiterations=500", "Set mode=snapshot"]
    return cmds


def solve_with_opendss(net: pp.pandapowerNet) -> OpenDssSolution:
    """Solve ``net`` with the OpenDSS engine and return results in pandapower's terms."""
    if dss is None:
        raise ImportError(INSTALL_HINT)
    group = _bus_groups(net)
    for cmd in to_opendss(net):
        dss.Text.Command(cmd)
    dss.Solution.Solve()
    converged = bool(dss.Solution.Converged())
    vm: dict[int, float] = {}
    by_node: dict[int, float] = {}
    for b in sorted(set(group.values())):
        dss.Circuit.SetActiveBus(f"b{b}")
        mags = dss.Bus.VMagAngle()[0::2]
        by_node[b] = float(np.mean(mags)) if len(mags) else float("nan")
    for b, node in group.items():
        vm[b] = by_node[node] * SQRT3 / (float(net.bus.at[b, "vn_kv"]) * 1000.0)

    def terminal_currents(element: str) -> tuple[float, float]:
        dss.Circuit.SetActiveElement(element)
        mags = np.asarray(dss.CktElement.CurrentsMagAng()[0::2])
        n = len(mags) // 2                      # conductors per terminal
        return float(np.max(mags[:3])), float(np.max(mags[n:n + 3]))

    line = np.full(len(net.line), np.nan)
    for k, (idx, ln) in enumerate(net.line.iterrows()):
        if not bool(ln["in_service"]):
            continue
        if group[int(ln["from_bus"])] == group[int(ln["to_bus"])]:
            line[k] = 0.0
            continue
        i_from, i_to = terminal_currents(f"Line.l{idx}")
        rating_a = float(ln["max_i_ka"]) * 1000.0 * float(ln["df"]) * float(ln["parallel"])
        line[k] = 100.0 * max(i_from, i_to) / rating_a
    trafo = np.full(len(net.trafo), np.nan)
    for k, (idx, tr) in enumerate(net.trafo.iterrows()):
        if not bool(tr["in_service"]):
            continue
        i_hv, i_lv = terminal_currents(f"Transformer.t{idx}")
        sn_va = float(tr["sn_mva"]) * 1e6 * float(tr["df"]) * float(tr["parallel"])
        rated_hv = sn_va / (SQRT3 * float(tr["vn_hv_kv"]) * 1000.0)
        rated_lv = sn_va / (SQRT3 * float(tr["vn_lv_kv"]) * 1000.0)
        trafo[k] = 100.0 * max(i_hv / rated_hv, i_lv / rated_lv)
    return OpenDssSolution(converged, vm, line, trafo, int(dss.Solution.Iterations()))


def compare_power_flow(net: pp.pandapowerNet, buses: list[int] | None = None) -> dict:
    """Solve ``net`` with both engines and report the largest differences.

    Args:
        net: Balanced network in the state to compare (not mutated).
        buses: Buses whose voltages are compared; default: every bus.

    Returns:
        ``max_abs_dv_pu`` with the bus it occurs at, ``max_abs_dline_percent``
        and ``max_abs_dtrafo_percent`` (percentage points of loading), the
        convergence flags and the element counts.
    """
    ref = copy.deepcopy(net)
    try:
        pp.runpp(ref)
        pp_ok = True
    except pp.LoadflowNotConverged:
        pp_ok = False
    sol = solve_with_opendss(net)
    out = {"pandapower_converged": pp_ok, "opendss_converged": sol.converged,
           "n_buses": len(net.bus), "n_lines": len(net.line), "n_trafos": len(net.trafo),
           "opendss_iterations": sol.iterations}
    if not (pp_ok and sol.converged):
        return {**out, "max_abs_dv_pu": None, "max_abs_dline_percent": None, "max_abs_dtrafo_percent": None}
    buses = [int(b) for b in (buses if buses is not None else net.bus.index)]
    dv = np.array([abs(sol.vm_pu[b] - float(ref.res_bus.at[b, "vm_pu"])) for b in buses])
    worst = int(np.nanargmax(dv))
    line_ref = ref.res_line["loading_percent"].to_numpy(dtype=float) if len(ref.line) else np.zeros(0)
    trafo_ref = ref.res_trafo["loading_percent"].to_numpy(dtype=float) if len(ref.trafo) else np.zeros(0)
    dl = np.abs(sol.line_loading_percent - line_ref)
    dt = np.abs(sol.trafo_loading_percent - trafo_ref)
    return {
        **out,
        "max_abs_dv_pu": float(dv[worst]), "bus_of_max_dv": buses[worst],
        "vm_pu_pandapower_at_max_dv": float(ref.res_bus.at[buses[worst], "vm_pu"]),
        "max_abs_dline_percent": float(np.nanmax(dl)) if dl.size and np.isfinite(dl).any() else 0.0,
        "max_abs_dtrafo_percent": float(np.nanmax(dt)) if dt.size and np.isfinite(dt).any() else 0.0,
    }


def hosting_capacity_with_opendss(net: pp.pandapowerNet, network_name: str, *, framework: str | None = None,
                                  load_scale: float | None = None, tolerance: float | None = None,
                                  pv_power_factor: float = 1.0) -> dict:
    """Deterministic hosting capacity with OpenDSS as the only power-flow engine.

    Same question, limits, scope, allocation and bisection as
    :func:`gridsense_sim.hosting_capacity.find_hosting_capacity_deterministic`;
    only the solver differs. The two capacities should agree within the search
    tolerance.
    """
    from ..hosting_capacity.allocation import apply_allocation, proportional_to_load, reactive_per_active
    from ..hosting_capacity.conditions import DEFAULT_CRITICAL_LOAD_SCALE, apply_load_scale
    from ..hosting_capacity.limits import limits_for
    from ..hosting_capacity.scope import voltage_scope_buses
    from ..hosting_capacity.search import SEARCH_DEFAULTS, bisect_max_feasible

    if dss is None:
        raise ImportError(INSTALL_HINT)
    _check_supported(net)                    # refuse before anything is computed
    limits = limits_for(network_name, framework)
    load_scale = DEFAULT_CRITICAL_LOAD_SCALE if load_scale is None else load_scale
    tolerance = SEARCH_DEFAULTS.tolerance if tolerance is None else tolerance
    q_per_p = reactive_per_active(pv_power_factor)
    work = copy.deepcopy(net)
    allocation = proportional_to_load(work)
    allocation.require_non_empty()
    apply_load_scale(work, load_scale)
    scope = [int(b) for b in voltage_scope_buses(work, limits)]
    solves = 0

    def violation(lam: float) -> str | None:
        nonlocal solves
        apply_allocation(work, allocation, lam, q_per_p)
        solves += 1
        sol = solve_with_opendss(work)
        if not sol.converged:
            return "not_converged"
        v = np.array([sol.vm_pu[b] for b in scope])
        if np.any(v > limits.v_max_pu):
            return f"overvoltage@bus_{scope[int(np.argmax(v))]} ({float(v.max()):.4f} pu)"
        if np.any(v < limits.v_min_pu):
            return f"undervoltage@bus_{scope[int(np.argmin(v))]} ({float(v.min()):.4f} pu)"
        if sol.trafo_loading_percent.size and np.nanmax(sol.trafo_loading_percent) > limits.max_trafo_loading_percent:
            k = int(np.nanargmax(sol.trafo_loading_percent))
            return f"trafo_loading@trafo_{int(net.trafo.index[k])} ({float(sol.trafo_loading_percent[k]):.1f}%)"
        if sol.line_loading_percent.size and np.nanmax(sol.line_loading_percent) > limits.max_line_loading_percent:
            k = int(np.nanargmax(sol.line_loading_percent))
            return f"line_loading@line_{int(net.line.index[k])} ({float(sol.line_loading_percent[k]):.1f}%)"
        return None

    baseline = violation(0.0)
    if baseline is not None:
        return {"status": "baseline_infeasible", "lambda_max": None, "binding_constraint": baseline,
                "opendss_solves": solves, "framework": limits.framework, "load_scale": load_scale}
    out = bisect_max_feasible(lambda lam: violation(lam) is not None, tolerance=tolerance)
    binding = violation(out.lambda_fail) if out.bounded and out.lambda_fail is not None else None
    return {"status": "ok", "lambda_max": out.lambda_max, "lambda_fail": out.lambda_fail,
            "bounded": out.bounded, "resolved": out.resolved, "binding_constraint": binding,
            "opendss_solves": solves, "framework": limits.framework, "load_scale": load_scale,
            "tolerance": tolerance}


def solution_to_dict(sol: OpenDssSolution) -> dict:
    d = asdict(sol)
    d["line_loading_percent"] = sol.line_loading_percent.tolist()
    d["trafo_loading_percent"] = sol.trafo_loading_percent.tolist()
    return d


__all__ = [
    "INSTALL_HINT", "OpenDssSolution", "UnsupportedForOpenDss", "compare_power_flow",
    "hosting_capacity_with_opendss", "opendss_available", "solution_to_dict", "solve_with_opendss", "to_opendss",
]
