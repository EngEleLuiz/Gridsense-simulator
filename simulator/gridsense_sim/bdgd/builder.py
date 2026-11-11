"""Build the low-voltage network behind one BDGD distribution transformer.

What is built
-------------
A *balanced* (positive-sequence) pandapower network: the medium-voltage
side of the transformer as the source, the transformer, the LV segments
(and service drops), one load per LV consumer and one static generator per
existing LV generator. Each load keeps its number of phases in the extra
column ``phases`` so a three-phase model can be derived later; the studies
of this phase treat the network as balanced, which is optimistic for
feeders dominated by single-phase consumers (Phase 7.4 measured the effect
on the IEEE European LV feeder). This limitation is recorded in the report.

Modelling choices (all recorded in :class:`BuildReport`)
--------------------------------------------------------
* **Nominal load = peak demand.** BDGD gives monthly energy, not demand.
  Mean demand is ``annual energy / 8760 h``; the peak is ``mean / load
  factor``. The load factor comes from the consumer's typical load curve
  (``CRVCRG``) when the layer is present, otherwise from
  ``BuildOptions.load_factor`` -- which then must be given: there is no
  default, because the choice scales every capacity in MW.
* **Power factor** is not in BDGD; ``BuildOptions.power_factor`` (default
  0.92, the PRODIST reference power factor) applies to every load.
* **Transformer impedance** comes from the equipment layer when present
  (``R``, ``XHL`` in percent); otherwise the resistive part is derived from
  the copper losses and the short-circuit voltage must be given.
* **Line capacitance** is neglected (LV overhead lines).

Privacy
-------
Buses are renumbered, coordinates are never read and BDGD identifiers are
dropped unless ``keep_ids`` is set. The converted file should still be
treated as local data: check the licence of your BDGD release before
publishing anything derived from it.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass, field
from pathlib import Path

import numpy as np
import pandapower as pp
import pandas as pd

from .reader import to_number
from .schema import BdgdSchema

EXISTING_PV_NAME = "existing_pv_bdgd"
HOURS_PER_YEAR = 8760.0


class BdgdBuildError(ValueError):
    """The network cannot be built from the tables with the given options."""


@dataclass(frozen=True)
class BuildOptions:
    mv_kv: float                                   # primary voltage of the transformer, kV (line-line)
    lv_kv: float = 0.38                            # secondary voltage, kV (line-line)
    source_vm_pu: float = 1.0
    power_factor: float = 0.92
    load_factor: float | None = None               # mean / peak demand when no typical curve applies
    vk_percent: float | None = None                # transformer short-circuit voltage fallback
    vkr_percent: float | None = None               # its resistive part, fallback
    default_conductor: tuple[float, float, float] | None = None   # r, x [ohm/km], rated current [A]
    include_service_drops: bool = True
    max_dropped_load_share: float = 0.05
    keep_ids: bool = False

    def __post_init__(self) -> None:
        if self.mv_kv <= self.lv_kv or self.lv_kv <= 0:
            raise ValueError("Need mv_kv > lv_kv > 0 (both line-to-line, in kV).")
        if not 0 < self.power_factor <= 1:
            raise ValueError("power_factor must be in (0, 1].")
        if self.load_factor is not None and not 0 < self.load_factor <= 1:
            raise ValueError("load_factor must be in (0, 1].")
        if not 0 <= self.max_dropped_load_share < 1:
            raise ValueError("max_dropped_load_share must be in [0, 1).")


@dataclass
class BuildReport:
    transformer_id: str
    counts: dict = field(default_factory=dict)
    assumptions: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    nominal_state: dict = field(default_factory=dict)
    options: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


def _phase_count(code: str) -> int:
    """Number of phase conductors in a BDGD phase code such as ``ABCN`` or ``AN``."""
    n = len({ch for ch in str(code).upper() if ch in "ABC"})
    return n if n else 3


def _annual_kwh(df: pd.DataFrame, schema: BdgdSchema) -> pd.Series:
    """Annual energy; months without a value are filled with the mean of the others."""
    months = pd.concat([to_number(df[c]) for c in schema.monthly_energy_kwh], axis=1)
    months = months.where(months >= 0)
    return months.mean(axis=1, skipna=True).fillna(0.0) * 12.0


def _curve_load_factors(tables: dict[str, pd.DataFrame], schema: BdgdSchema) -> dict[str, float]:
    """Load factor (mean / peak) of each typical curve, over all its day types."""
    curves = tables.get(schema.load_curves)
    if curves is None or schema.id not in curves:
        return {}
    cols = [c for c in schema.curve_values if c in curves]
    if not cols:
        return {}
    values = pd.concat([to_number(curves[c]) for c in cols], axis=1)
    out: dict[str, float] = {}
    for code, rows in values.groupby(curves[schema.id]):
        arr = rows.to_numpy(dtype=float)
        arr = arr[np.isfinite(arr)]
        if arr.size and arr.max() > 0:
            out[str(code)] = float(arr.mean() / arr.max())
    return out


def _reachable(start: str, node_from: pd.Series, node_to: pd.Series) -> set[str]:
    """Nodes connected to ``start`` through the branches (breadth-first search)."""
    adjacency: dict[str, list[str]] = {}
    for a, b in zip(node_from, node_to):
        adjacency.setdefault(a, []).append(b)
        adjacency.setdefault(b, []).append(a)
    seen, queue = {start}, [start]
    while queue:
        for nxt in adjacency.get(queue.pop(), ()):
            if nxt not in seen:
                seen.add(nxt)
                queue.append(nxt)
    return seen


def list_transformers(tables: dict[str, pd.DataFrame], schema: BdgdSchema | None = None) -> pd.DataFrame:
    """One row per distribution transformer: size and what hangs on it (to choose feeders)."""
    schema = schema or BdgdSchema()
    tr = tables[schema.transformers]
    seg = tables[schema.lv_segments]
    con = tables[schema.lv_consumers]
    gen = tables.get(schema.lv_generators)
    out = pd.DataFrame({"transformer_id": tr[schema.id], "rated_kva": to_number(tr[schema.rated_kva])})
    out["lv_segments"] = out["transformer_id"].map(seg[schema.transformer_link].value_counts()).fillna(0).astype(int)
    out["lv_length_km"] = out["transformer_id"].map(
        to_number(seg[schema.length_m]).groupby(seg[schema.transformer_link]).sum() / 1000.0).fillna(0.0)
    out["consumers"] = out["transformer_id"].map(con[schema.transformer_link].value_counts()).fillna(0).astype(int)
    out["annual_mwh"] = out["transformer_id"].map(
        _annual_kwh(con, schema).groupby(con[schema.transformer_link]).sum() / 1000.0).fillna(0.0)
    if gen is not None and schema.transformer_link in gen:
        out["generators"] = out["transformer_id"].map(gen[schema.transformer_link].value_counts()).fillna(0).astype(int)
    else:
        out["generators"] = 0
    return out.sort_values(["consumers", "lv_segments"], ascending=False).reset_index(drop=True)


def _transformer_impedance(tables, schema, tid: str, kva: float, row: pd.Series, opt: BuildOptions,
                           report: BuildReport) -> tuple[float, float, float]:
    """(vk_percent, vkr_percent, pfe_kw)."""
    def num(series_row, col):
        if col in series_row.index:
            v = to_number(pd.Series([series_row[col]])).iloc[0]
            return float(v) if np.isfinite(v) else math.nan
        return math.nan

    pfe_w, ptot_w = num(row, schema.iron_losses_w), num(row, schema.total_losses_w)
    r_pct = x_pct = math.nan
    eq = tables.get(schema.transformer_equipment)
    if eq is not None and schema.equipment_link in eq:
        match = eq[eq[schema.equipment_link] == tid]
        if len(match):
            first = match.iloc[0]
            r_pct, x_pct = num(first, schema.r_percent), num(first, schema.x_percent)
            if not np.isfinite(pfe_w):
                pfe_w = num(first, schema.iron_losses_w)
            if not np.isfinite(ptot_w):
                ptot_w = num(first, schema.total_losses_w)
    if np.isfinite(r_pct) and np.isfinite(x_pct) and x_pct > 0:
        vkr, vk = r_pct, math.hypot(r_pct, x_pct)
        report.assumptions.append("transformer impedance from the equipment layer (R, XHL in percent)")
    else:
        if np.isfinite(pfe_w) and np.isfinite(ptot_w) and ptot_w > pfe_w:
            vkr = (ptot_w - pfe_w) / (kva * 1000.0) * 100.0
            report.assumptions.append("transformer resistance from copper losses (PER_TOT - PER_FER, in W)")
        elif opt.vkr_percent is not None:
            vkr = opt.vkr_percent
            report.assumptions.append(f"transformer vkr = {vkr} % from the options (not in the data)")
        else:
            raise BdgdBuildError(f"Transformer {tid}: no resistance in the data; pass vkr_percent.")
        if opt.vk_percent is None:
            raise BdgdBuildError(f"Transformer {tid}: no short-circuit voltage in the data; pass vk_percent.")
        vk = opt.vk_percent
        report.assumptions.append(f"transformer vk = {vk} % from the options (not in the data)")
    if vkr >= vk:
        raise BdgdBuildError(f"Transformer {tid}: vkr ({vkr:.2f} %) must be below vk ({vk:.2f} %).")
    pfe_kw = pfe_w / 1000.0 if np.isfinite(pfe_w) and pfe_w > 0 else 0.0
    if pfe_kw == 0.0:
        report.assumptions.append("transformer iron losses not in the data: taken as zero")
    return vk, vkr, pfe_kw


def build_lv_network(
    tables: dict[str, pd.DataFrame],
    transformer_id: str,
    options: BuildOptions,
    schema: BdgdSchema | None = None,
) -> tuple[pp.pandapowerNet, BuildReport]:
    """Convert the LV network of one distribution transformer (see module docstring)."""
    schema = schema or BdgdSchema()
    tid = str(transformer_id)
    report = BuildReport(transformer_id=tid if options.keep_ids else hashlib.sha256(tid.encode()).hexdigest()[:12],
                         options={**asdict(options), "schema": "default" if schema == BdgdSchema() else "custom"})
    tr_all = tables[schema.transformers]
    tr = tr_all[tr_all[schema.id] == tid]
    if tr.empty:
        raise BdgdBuildError(f"Transformer {tid!r} not found in layer {schema.transformers}.")
    if len(tr) > 1:
        report.warnings.append(f"{len(tr)} rows for the transformer id; the first one is used")
    tr_row = tr.iloc[0]
    kva = float(to_number(pd.Series([tr_row[schema.rated_kva]])).iloc[0])
    if not np.isfinite(kva) or kva <= 0:
        raise BdgdBuildError(f"Transformer {tid}: invalid rated power {tr_row[schema.rated_kva]!r}.")

    # --- branches -----------------------------------------------------------
    parts = [tables[schema.lv_segments].assign(_kind="segment")]
    drops = tables.get(schema.service_drops)
    if options.include_service_drops and drops is not None and schema.transformer_link in drops:
        needed = [schema.node_from, schema.node_to, schema.conductor_type, schema.length_m]
        if all(c in drops for c in needed):
            parts.append(drops.assign(_kind="service_drop"))
        else:
            report.warnings.append("service-drop layer ignored: expected columns missing")
    branches = pd.concat(parts, ignore_index=True, sort=False)
    branches = branches[branches[schema.transformer_link] == tid].copy()
    if branches.empty:
        raise BdgdBuildError(f"Transformer {tid}: no LV segment references it ({schema.transformer_link}).")
    branches["_len_km"] = to_number(branches[schema.length_m]) / 1000.0
    self_loops = branches[schema.node_from] == branches[schema.node_to]
    branches = branches[~self_loops]
    short = ~(branches["_len_km"] > 0)
    branches.loc[short, "_len_km"] = 1e-4
    cond = tables[schema.conductors].drop_duplicates(schema.id).set_index(schema.id)
    r = branches[schema.conductor_type].map(to_number(cond[schema.r_ohm_per_km]))
    x = branches[schema.conductor_type].map(to_number(cond[schema.x_ohm_per_km]))
    amp = branches[schema.conductor_type].map(to_number(cond[schema.rated_current_a]))
    bad = ~(np.isfinite(r) & np.isfinite(x) & np.isfinite(amp) & (r > 0) & (amp > 0))
    if bad.any():
        if options.default_conductor is None:
            codes = sorted(set(branches.loc[bad, schema.conductor_type]))[:8]
            raise BdgdBuildError(
                f"{int(bad.sum())} branch(es) have no usable conductor data (codes {codes}); "
                "fix the catalogue or pass default_conductor=(r, x, amps).")
        r, x, amp = (s.where(~bad, v) for s, v in zip((r, x, amp), options.default_conductor))
        report.assumptions.append(f"{int(bad.sum())} branch(es) use the default conductor {options.default_conductor}")

    # --- connectivity: keep only what is reachable from the transformer ------
    lv_node = str(tr_row[schema.node_to])
    reached = _reachable(lv_node, branches[schema.node_from].astype(str), branches[schema.node_to].astype(str))
    all_nodes = set(branches[schema.node_from].astype(str)) | set(branches[schema.node_to].astype(str))
    island_nodes = all_nodes - reached
    keep = branches[schema.node_from].astype(str).isin(reached).to_numpy()
    branches, r, x, amp = branches[keep], r[keep], x[keep], amp[keep]
    if branches.empty:
        raise BdgdBuildError(f"Transformer {tid}: no LV segment is connected to its secondary node {lv_node!r}.")

    # --- network ------------------------------------------------------------
    net = pp.create_empty_network(name="bdgd_lv_feeder", f_hz=60.0)
    nodes = pd.unique(pd.concat([pd.Series([lv_node]), branches[schema.node_from], branches[schema.node_to]]))
    bus_of = {str(n): pp.create_bus(net, vn_kv=options.lv_kv, name=str(n) if options.keep_ids else None,
                                    type="n") for n in nodes}
    mv_bus = pp.create_bus(net, vn_kv=options.mv_kv, name="mv_source", type="b")
    pp.create_ext_grid(net, mv_bus, vm_pu=options.source_vm_pu, name="mv_source")
    vk, vkr, pfe_kw = _transformer_impedance(tables, schema, tid, kva, tr_row, options, report)
    sn_mva = kva / 1000.0
    pp.create_transformer_from_parameters(
        net, hv_bus=mv_bus, lv_bus=bus_of[lv_node], sn_mva=sn_mva, vn_hv_kv=options.mv_kv,
        vn_lv_kv=options.lv_kv, vk_percent=vk, vkr_percent=vkr, pfe_kw=pfe_kw,
        i0_percent=pfe_kw / (sn_mva * 1000.0) * 100.0, shift_degree=0.0, name="distribution_transformer")
    pp.create_lines_from_parameters(
        net, from_buses=[bus_of[str(n)] for n in branches[schema.node_from]],
        to_buses=[bus_of[str(n)] for n in branches[schema.node_to]],
        length_km=branches["_len_km"].to_numpy(dtype=float),
        r_ohm_per_km=r.to_numpy(dtype=float), x_ohm_per_km=x.to_numpy(dtype=float),
        c_nf_per_km=0.0, max_i_ka=amp.to_numpy(dtype=float) / 1000.0,
        name=list(branches["_kind"]))

    # --- loads --------------------------------------------------------------
    con_all = tables[schema.lv_consumers]
    con = con_all[con_all[schema.transformer_link] == tid].copy()
    con["_kwh"] = _annual_kwh(con, schema)
    con = con[con["_kwh"] > 0]
    if con.empty:
        raise BdgdBuildError(f"Transformer {tid}: no LV consumer with energy.")
    curve_lf = _curve_load_factors(tables, schema)
    codes = con[schema.load_curve_type] if schema.load_curve_type in con else pd.Series("", index=con.index)
    lf = codes.map(curve_lf)
    from_curve = int(lf.notna().sum())
    if lf.isna().any():
        if options.load_factor is None:
            raise BdgdBuildError(
                f"{int(lf.isna().sum())} consumer(s) have no typical load curve; pass load_factor "
                "(mean / peak demand) -- there is no default because it scales every capacity in MW.")
        lf = lf.fillna(options.load_factor)
    con["_p_mw"] = con["_kwh"] / HOURS_PER_YEAR / lf / 1000.0
    attached = con[schema.node].map(lambda n: str(n) in bus_of)
    on_island = con[schema.node].map(lambda n: str(n) in island_nodes)
    dropped_share = float(con.loc[~attached, "_p_mw"].sum() / con["_p_mw"].sum())
    if dropped_share > options.max_dropped_load_share:
        raise BdgdBuildError(
            f"{dropped_share:.1%} of the load sits on nodes that are not in the LV network "
            f"connected to the transformer ({int((~attached).sum())} consumer(s), "
            f"{int(on_island.sum())} of them on disconnected segments). Include the service-drop "
            "layer, check the topology, or raise max_dropped_load_share.")
    con = con[attached]
    tan_phi = math.tan(math.acos(options.power_factor))
    phases = (con[schema.phases].map(_phase_count) if schema.phases in con
              else pd.Series(3, index=con.index)).to_numpy(dtype=int)
    pp.create_loads(net, buses=[bus_of[str(n)] for n in con[schema.node]],
                    p_mw=con["_p_mw"].to_numpy(dtype=float),
                    q_mvar=con["_p_mw"].to_numpy(dtype=float) * tan_phi)
    net.load["phases"] = phases

    # --- existing generation -------------------------------------------------
    n_gen = 0
    gen_all = tables.get(schema.lv_generators)
    if gen_all is not None and {schema.transformer_link, schema.node, schema.installed_kw} <= set(gen_all.columns):
        gen = gen_all[gen_all[schema.transformer_link] == tid]
        kw = to_number(gen[schema.installed_kw])
        ok = gen[schema.node].map(lambda n: str(n) in bus_of) & (kw > 0)
        for node, p_kw in zip(gen.loc[ok, schema.node], kw[ok]):
            pp.create_sgen(net, bus_of[str(node)], p_mw=float(p_kw) / 1000.0, q_mvar=0.0, name=EXISTING_PV_NAME)
            n_gen += 1

    try:
        pp.runpp(net)
    except pp.LoadflowNotConverged as exc:
        raise BdgdBuildError(
            f"Transformer {tid}: the power flow does not converge at nominal load. "
            "Check the load factor and the conductor data.") from exc
    lv = net.bus["vn_kv"] < options.mv_kv
    loops = len(net.line) - (int(lv.sum()) - 1)
    counts = np.bincount(net.load["phases"].to_numpy(dtype=int), minlength=4)
    report.counts = {
        "lv_buses": int(lv.sum()), "lines": int(len(net.line)), "loads": int(len(net.load)),
        "existing_generators": n_gen, "existing_pv_mw": float(net.sgen["p_mw"].sum()) if len(net.sgen) else 0.0,
        "peak_load_mw": float(net.load["p_mw"].sum()), "transformer_kva": kva,
        "lv_length_km": float(net.line["length_km"].sum()),
        "self_loop_branches_dropped": int(self_loops.sum()), "zero_length_branches": int(short.sum()),
        "consumers_not_in_network": int((~attached).sum()), "consumers_on_disconnected_segments": int(on_island.sum()),
        "dropped_load_share": dropped_share,
        "disconnected_nodes_removed": len(island_nodes), "independent_loops": int(max(loops, 0)),
        "load_factor_from_typical_curve": from_curve,
        "single_phase_loads": int(counts[1]), "two_phase_loads": int(counts[2]),
        "three_phase_loads": int(counts[3]),
    }
    report.assumptions += [
        f"nominal load = peak demand = annual energy / 8760 h / load factor "
        f"({from_curve} from typical curves, {len(net.load) - from_curve} from the option)",
        f"power factor {options.power_factor} for every load",
        "balanced (positive-sequence) model; the number of phases per load is kept in net.load.phases",
        "line capacitance neglected",
    ]
    if island_nodes:
        report.warnings.append(f"{len(island_nodes)} node(s) not connected to the transformer were left out")
    if counts[1] + counts[2]:
        share = (counts[1] + counts[2]) / len(net.load)
        report.warnings.append(
            f"{share:.0%} of the loads are single- or two-phase: the balanced model is optimistic for "
            "voltage (see Phase 7.4 / docs/BDGD.md)")
    if loops > 0:
        report.warnings.append(f"the LV network has {loops} loop(s) (not radial)")
    report.nominal_state = {
        "vm_min_pu": float(net.res_bus.loc[lv, "vm_pu"].min()),
        "vm_max_pu": float(net.res_bus.loc[lv, "vm_pu"].max()),
        "transformer_loading_percent": float(net.res_trafo["loading_percent"].max()),
        "max_line_loading_percent": float(net.res_line["loading_percent"].max()),
    }
    return net, report


def save_network(net: pp.pandapowerNet, report: BuildReport, path: str | Path, *, source_label: str,
                 default_framework: str = "prodist_m8_bt", description: str | None = None) -> Path:
    """Write the network (pandapower JSON) and its sidecar metadata ``<path>.meta.json``.

    Args:
        source_label: Where the data came from, e.g. ``"BDGD 2023, distributor X"``;
            it becomes part of the citation stored with every result.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    for table in ("res_bus", "res_line", "res_trafo", "res_load", "res_sgen", "res_ext_grid"):
        if table in net:
            net[table] = net[table].iloc[0:0]
    pp.to_json(net, str(path))
    lv_kv = float(net.bus["vn_kv"].min())
    meta = {
        "country": "Brazil", "kind": "real",
        "description": description or (
            f"LV network of one distribution transformer ({report.counts.get('transformer_kva', 0):g} kVA, "
            f"{report.counts.get('loads', 0)} consumers), converted from BDGD"),
        "citation": f"ANEEL, Base de Dados Geografica da Distribuidora (BDGD), PRODIST Module 10; {source_label}",
        "default_framework": default_framework, "voltage_levels_kv": [lv_kv],
        "network_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "build_report": report.to_dict(),
    }
    meta_path = Path(str(path) + ".meta.json")
    meta_path.write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")
    return meta_path


__all__ = ["BdgdBuildError", "BuildOptions", "BuildReport", "EXISTING_PV_NAME", "build_lv_network",
           "list_transformers", "save_network"]
