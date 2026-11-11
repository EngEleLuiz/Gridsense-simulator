"""Compact model of a balanced pandapower network for batched power flow.

Why not write a network model from scratch
------------------------------------------
pandapower already turns lines, transformers (taps, magnetising branch),
shunts and closed bus-bus switches into a bus admittance matrix. Rebuilding
that by hand is where a new solver usually goes wrong. This module reads the
matrices pandapower built for one reference power flow and keeps only what a
batched solver needs.

Formulation
-----------
Buses are split into slack buses ``S`` (voltage known), isolated buses (not
energised) and the rest ``N``. With ``Z = inv(Y_NN)`` and the no-load voltage
``w = -Z Y_NS V_S``::

    V_N = w + Z conj(S_N / V_N)

Only buses with an injection matter on the right-hand side. With ``I`` the set
of injection buses and ``J = conj(S_I / V_I)`` (the injected currents):

* voltages of the injection buses:  ``V_I = w_I + J @ Z_II.T``  (fixed point)
* voltages of the scope buses:      ``V_scope = w_scope + J @ Z_scope,I.T``
* branch currents:                  ``I_f = c_f + J @ A_f.T``, same for ``I_t``

so every output is one matrix product per batch. The matrices are dense; for
low-voltage networks (up to a few thousand buses) that is the fast choice on
both CPU and GPU.

Supported networks: balanced, constant-power loads and static generators, any
number of external grids, lines, two-winding transformers, shunts, switches.
Voltage-controlled generators and voltage-dependent (ZIP) loads raise
:class:`UnsupportedNetworkError`; three-phase unbalanced networks use
``runpp_3ph`` and are not covered.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field

import numpy as np
import pandapower as pp

BUS_TYPE_REF, BUS_TYPE_PV, BUS_TYPE_NONE = 3, 2, 4
_COL_BUS_TYPE, _COL_PD, _COL_QD, _COL_VM, _COL_VA, _COL_BASE_KV = 1, 2, 3, 7, 8, 9
_COL_BR_STATUS = 10


class UnsupportedNetworkError(ValueError):
    """The network uses an element the batched solver does not model."""


@dataclass
class BatchedModel:
    """Everything the batched solver needs, as NumPy arrays (per unit, base ``base_mva``).

    Index spaces: ``I`` = injection buses (``n_inj``), scope = voltage-checked
    buses (``n_scope``), lines and transformers in pandapower table order.
    """

    base_mva: float
    # fixed point on the injection buses
    w_inj: np.ndarray            # (n_inj,) no-load voltage
    z_inj: np.ndarray            # (n_inj, n_inj)
    # outputs
    w_scope: np.ndarray          # (n_scope,)
    z_scope: np.ndarray          # (n_scope, n_inj)
    scope_buses: tuple[int, ...]             # pandapower bus index per scope column
    scope_isolated: np.ndarray               # (n_scope,) bool: no voltage solution (NaN in pandapower)
    line_const: np.ndarray       # (2, n_line) branch-current constants, from / to side
    line_map: np.ndarray         # (2, n_line, n_inj)
    line_scale: np.ndarray       # (n_line,) loading_percent per unit of current; 0 = out of service
    trafo_const: np.ndarray      # (2, n_trafo)
    trafo_map: np.ndarray        # (2, n_trafo, n_inj)
    trafo_scale: np.ndarray      # (2, n_trafo) loading_percent per unit of current, hv / lv side
    # injections
    s_fixed: np.ndarray          # (n_inj,) complex: everything that is neither a load nor study PV
    load_p: np.ndarray           # (n_loads,) MW at multiplier 1 (scaling and in_service applied)
    load_q: np.ndarray           # (n_loads,)
    load_pos: np.ndarray         # (n_loads,) position in I, -1 if the load bus is isolated
    pv_base: np.ndarray          # (n_inj,) MW of study PV per bus at lambda = 1 and full output
    inj_buses: tuple[int, ...]   # one representative pandapower bus per injection position
    check: dict = field(default_factory=dict)

    @property
    def n_inj(self) -> int:
        return int(self.w_inj.size)

    @property
    def n_scope(self) -> int:
        return len(self.scope_buses)

    def injections(self, load_mult: np.ndarray, pv_mult: np.ndarray, lam: np.ndarray | float) -> np.ndarray:
        """Per-unit complex injections ``(B, n_inj)``.

        Args:
            load_mult: ``(B,)`` one multiplier for every load, or ``(B, n_loads)`` per load.
            pv_mult: ``(B,)`` PV output per unit of rating.
            lam: scalar or ``(B,)`` penetration.
        """
        load_mult = np.asarray(load_mult, dtype=float)
        pv = np.asarray(pv_mult, dtype=float) * np.asarray(lam, dtype=float)
        if load_mult.ndim == 1:
            load_mult = np.broadcast_to(load_mult[:, None], (load_mult.size, self.load_p.size))
        s = np.tile(self.s_fixed, (load_mult.shape[0], 1))
        ok = self.load_pos >= 0
        demand = load_mult[:, ok] * (self.load_p[ok] + 1j * self.load_q[ok]) / self.base_mva
        np.subtract.at(s, (slice(None), self.load_pos[ok]), demand)
        return s + pv[:, None] * self.pv_base[None, :] / self.base_mva


def _check_supported(net: pp.pandapowerNet) -> None:
    if len(net.get("asymmetric_load", [])) or len(net.get("asymmetric_sgen", [])):
        raise UnsupportedNetworkError("Three-phase unbalanced networks are not supported by the batched solver.")
    if len(net.gen) and bool(net.gen["in_service"].any()):
        raise UnsupportedNetworkError("Voltage-controlled generators (PV buses) are not supported.")
    for col in ("const_z_p_percent", "const_i_p_percent", "const_z_q_percent", "const_i_q_percent",
                "const_z_percent", "const_i_percent"):
        if col in net.load and float(np.nan_to_num(net.load[col].to_numpy(dtype=float)).__abs__().sum()) > 0:
            raise UnsupportedNetworkError("Voltage-dependent (ZIP) loads are not supported.")
    for table in ("storage", "ward", "xward", "svc", "tcsc", "ssc", "vsc", "dcline", "impedance", "trafo3w"):
        if table in net and len(net[table]):
            raise UnsupportedNetworkError(f"Element table '{table}' is not supported by the batched solver.")
    if len(net.trafo) and "tap_dependent_impedance" in net.trafo \
            and bool(net.trafo["tap_dependent_impedance"].fillna(False).astype(bool).any()):
        raise UnsupportedNetworkError("Tap-dependent transformer impedance is not supported.")
    if len(net.get("controller", [])):
        raise UnsupportedNetworkError("Networks with controllers are not supported (taps must stay fixed).")


def build_model(
    net: pp.pandapowerNet,
    scope_buses: tuple[int, ...] | list[int],
    pv_mw_per_bus: dict[int, float],
    *,
    check_tolerance: float = 1e-6,
) -> BatchedModel:
    """Extract a :class:`BatchedModel` from ``net`` (not mutated).

    Args:
        net: Balanced network at its nominal loads, without the study PV.
        scope_buses: pandapower indices of the voltage-checked buses.
        pv_mw_per_bus: Study PV per bus at ``lambda = 1`` (see ``PVAllocation``).
        check_tolerance: The model's own solution of the reference state must
            match pandapower within this (voltage in pu, loading in % / 100).

    Raises:
        UnsupportedNetworkError: unsupported element, or the self-check failed.
    """
    from pandapower.pypower.makeYbus import makeYbus

    _check_supported(net)
    net = copy.deepcopy(net)
    pp.runpp(net)  # same options as the pandapower backend (violations.run_power_flow)
    ppc = net._ppc
    bus, branch = ppc["bus"], ppc["branch"]
    base_mva = float(ppc["baseMVA"])
    n_bus = bus.shape[0]
    if not np.array_equal(bus[:, 0].real.astype(int), np.arange(n_bus)):
        raise UnsupportedNetworkError("Unexpected pandapower bus numbering; cannot build the batched model.")
    types = bus[:, _COL_BUS_TYPE].real.astype(int)
    if np.any(types == BUS_TYPE_PV):
        raise UnsupportedNetworkError("Voltage-controlled (PV) buses are not supported.")
    slack = np.flatnonzero(types == BUS_TYPE_REF)
    isolated = types == BUS_TYPE_NONE
    live = np.flatnonzero(~isolated & (types != BUS_TYPE_REF))
    in_service = branch[:, _COL_BR_STATUS].real > 0
    # A branch touching an isolated bus carries nothing; drop it from the admittance matrix.
    f_bus, t_bus = branch[:, 0].real.astype(int), branch[:, 1].real.astype(int)
    used = in_service & ~isolated[f_bus] & ~isolated[t_bus]
    ybus, yf, yt = makeYbus(base_mva, bus, branch[used])
    ybus = ybus.toarray()
    yf_all = np.zeros((branch.shape[0], n_bus), dtype=complex)
    yt_all = np.zeros_like(yf_all)
    yf_all[used], yt_all[used] = yf.toarray(), yt.toarray()

    v_ref = bus[:, _COL_VM].real * np.exp(1j * np.deg2rad(bus[:, _COL_VA].real))
    v_slack = v_ref[slack]
    z = np.linalg.inv(ybus[np.ix_(live, live)])
    w = -z @ (ybus[np.ix_(live, slack)] @ v_slack)
    pos_live = np.full(n_bus, -1)
    pos_live[live] = np.arange(live.size)

    lookup = net._pd2ppc_lookups["bus"]
    # --- injections ---------------------------------------------------------
    s_ref = np.zeros(n_bus, dtype=complex)
    s_ref[:] = -(bus[:, _COL_PD].real + 1j * bus[:, _COL_QD].real) / base_mva
    load_on = net.load["in_service"].to_numpy(dtype=bool)
    scaling = net.load["scaling"].to_numpy(dtype=float) if "scaling" in net.load else 1.0
    load_p = net.load["p_mw"].to_numpy(dtype=float) * scaling * load_on
    load_q = net.load["q_mvar"].to_numpy(dtype=float) * scaling * load_on
    load_ppc = lookup[net.load["bus"].to_numpy(dtype=int)]
    s_loads = np.zeros(n_bus, dtype=complex)
    np.add.at(s_loads, load_ppc, -(load_p + 1j * load_q) / base_mva)
    s_fixed_bus = s_ref - s_loads
    s_fixed_bus[np.abs(s_fixed_bus) < 1e-14] = 0.0
    pv_bus = np.zeros(n_bus)
    for b, mw in pv_mw_per_bus.items():
        pv_bus[lookup[int(b)]] += float(mw)
    has_inj = np.zeros(n_bus, dtype=bool)
    has_inj[load_ppc[(np.abs(load_p) + np.abs(load_q)) > 0]] = True
    has_inj |= (pv_bus != 0) | (s_fixed_bus != 0)
    has_inj &= pos_live >= 0          # injections on slack / isolated buses do not enter the equations
    inj = np.flatnonzero(has_inj)
    pos_inj = np.full(n_bus, -1)
    pos_inj[inj] = np.arange(inj.size)
    il = pos_live[inj]

    # --- outputs ------------------------------------------------------------
    scope_buses = tuple(int(b) for b in scope_buses)
    scope_ppc = lookup[np.asarray(scope_buses, dtype=int)]
    scope_isolated = isolated[scope_ppc]
    w_scope = np.ones(len(scope_buses), dtype=complex)
    z_scope = np.zeros((len(scope_buses), inj.size), dtype=complex)
    for k, b in enumerate(scope_ppc):
        if isolated[b]:
            continue
        if pos_live[b] >= 0:
            w_scope[k], z_scope[k] = w[pos_live[b]], z[pos_live[b], il]
        else:                           # a slack bus in scope (not the usual case)
            w_scope[k] = v_ref[b]

    def branch_maps(rows: np.ndarray):
        const = np.empty((2, rows.size), dtype=complex)
        maps = np.empty((2, rows.size, inj.size), dtype=complex)
        for side, y in enumerate((yf_all, yt_all)):
            const[side] = y[np.ix_(rows, live)] @ w + y[np.ix_(rows, slack)] @ v_slack
            maps[side] = y[np.ix_(rows, live)] @ z[:, il]
        return const, maps

    base_kv = bus[:, _COL_BASE_KV].real
    lines = net._pd2ppc_lookups["branch"].get("line", (0, 0))
    trafos = net._pd2ppc_lookups["branch"].get("trafo", (0, 0))
    line_rows = np.arange(*lines)
    trafo_rows = np.arange(*trafos)
    line_const, line_map = branch_maps(line_rows)
    trafo_const, trafo_map = branch_maps(trafo_rows)
    # pandapower: line loading = max(i_from, i_to) / (max_i_ka * df * parallel) * 100
    ka_per_pu = base_mva / (np.sqrt(3.0) * base_kv)
    line_scale = np.zeros(line_rows.size)
    if line_rows.size:
        ln = net.line
        rating = ln["max_i_ka"].to_numpy(dtype=float) * ln["df"].to_numpy(dtype=float) \
            * ln["parallel"].to_numpy(dtype=float)
        line_scale = ka_per_pu[f_bus[line_rows]] / rating * 100.0
        line_scale[~used[line_rows]] = 0.0
    # pandapower ("current" mode): max(i_hv * vn_hv, i_lv * vn_lv) * sqrt(3) / (sn * df * parallel) * 100
    trafo_scale = np.zeros((2, trafo_rows.size))
    if trafo_rows.size:
        tr = net.trafo
        sn = tr["sn_mva"].to_numpy(dtype=float) * tr["df"].to_numpy(dtype=float) \
            * tr["parallel"].to_numpy(dtype=float)
        vn_hv = tr["vn_hv_kv"].to_numpy(dtype=float)
        vn_lv = tr["vn_lv_kv"].to_numpy(dtype=float)
        trafo_scale[0] = ka_per_pu[f_bus[trafo_rows]] * vn_hv * np.sqrt(3.0) / sn * 100.0
        trafo_scale[1] = ka_per_pu[t_bus[trafo_rows]] * vn_lv * np.sqrt(3.0) / sn * 100.0
        trafo_scale[:, ~used[trafo_rows]] = 0.0

    model = BatchedModel(
        base_mva=base_mva, w_inj=w[il], z_inj=z[np.ix_(il, il)],
        w_scope=w_scope, z_scope=z_scope, scope_buses=scope_buses, scope_isolated=scope_isolated,
        line_const=line_const, line_map=line_map, line_scale=line_scale,
        trafo_const=trafo_const, trafo_map=trafo_map, trafo_scale=trafo_scale,
        s_fixed=s_fixed_bus[inj], load_p=load_p, load_q=load_q, load_pos=pos_inj[load_ppc],
        pv_base=pv_bus[inj], inj_buses=tuple(int(np.flatnonzero(lookup == b)[0]) for b in inj),
    )
    model.check = _self_check(model, net, check_tolerance)
    return model


def solve_reference(model: BatchedModel, s: np.ndarray, max_iter: int = 200, tol: float = 1e-12):
    """NumPy fixed point for ONE operating point; used for the self-check and in tests."""
    v = model.w_inj.copy()
    for _ in range(max_iter):
        v_new = model.w_inj + model.z_inj @ np.conj(s / v)
        done = np.max(np.abs(v_new - v)) < tol
        v = v_new
        if done:
            break
    j = np.conj(s / v)
    vm = np.abs(model.w_scope + model.z_scope @ j)
    vm[model.scope_isolated] = np.nan
    line = np.max(np.abs(model.line_const + model.line_map @ j), axis=0) * model.line_scale
    trafo = np.max(np.abs(model.trafo_const + model.trafo_map @ j) * model.trafo_scale, axis=0)
    return vm, line, trafo


def _self_check(model: BatchedModel, net: pp.pandapowerNet, tol: float) -> dict:
    """Solve the reference state with the model and compare with pandapower."""
    s = model.injections(np.ones(1), np.zeros(1), 0.0)[0]
    vm, line, trafo = solve_reference(model, s)
    ref_vm = net.res_bus.loc[list(model.scope_buses), "vm_pu"].to_numpy()
    both = np.isfinite(ref_vm) & np.isfinite(vm)
    if not np.array_equal(np.isfinite(ref_vm), np.isfinite(vm)):
        raise UnsupportedNetworkError("Batched model and pandapower disagree on which buses are energised.")
    dv = float(np.max(np.abs(vm[both] - ref_vm[both]))) if both.any() else 0.0
    ref_line = np.nan_to_num(net.res_line["loading_percent"].to_numpy()) if len(net.line) else np.zeros(0)
    ref_trafo = np.nan_to_num(net.res_trafo["loading_percent"].to_numpy()) if len(net.trafo) else np.zeros(0)
    dl = float(np.max(np.abs(line - ref_line))) if line.size else 0.0
    dt = float(np.max(np.abs(trafo - ref_trafo))) if trafo.size else 0.0
    report = {"max_abs_dv_pu": dv, "max_abs_dline_percent": dl, "max_abs_dtrafo_percent": dt,
              "n_injection_buses": model.n_inj, "n_scope_buses": model.n_scope}
    if dv > tol or dl > 100 * tol or dt > 100 * tol:
        raise UnsupportedNetworkError(
            f"Batched model does not reproduce pandapower on the reference state: {report}. "
            "Use the pandapower backend for this network.")
    return report


__all__ = ["BatchedModel", "UnsupportedNetworkError", "build_model", "solve_reference"]
