"""Benchmark: pandapower vs the batched solver, per power flow (Phase 8.0).

    python scripts/benchmark_power_flow.py                       # panel, CPU and GPU if present
    python scripts/benchmark_power_flow.py --networks cigre_lv --batch-sizes 1 64 1008 8064
    python scripts/benchmark_power_flow.py --end-to-end cigre_lv # one PRODIST week, both backends

What is measured
----------------
* **pandapower**: mean wall time of ``runpp`` on random operating points of
  the network (the cost the pandapower backend pays per power flow). Whether
  numba is installed is recorded, because it changes this number a lot.
* **batched solver**: wall time per operating point for each batch size,
  device and precision (median of ``--repeats`` runs after one warm-up run;
  the GPU is synchronised before the clock stops). Transfers to and from the
  device are included, since a study pays for them too.
* **accuracy**: largest voltage difference to pandapower on the same points.

How to read it
--------------
The speed-up has two sources that should not be confused: the *algorithm*
(one matrix product for a whole batch instead of one sparse Newton solve per
point -- available on CPU) and the *hardware* (GPU instead of CPU for the
same algorithm). The table reports both, so a GPU number is always next to
the CPU number of the same method. The comparison is per power flow; the
effect on a whole study is measured with ``--end-to-end``.

Results go to ``data/benchmarks/`` (JSON, git-ignored) and to the terminal as
a Markdown table.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import platform
import statistics
import time
import warnings
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandapower as pp

from gridsense_sim.accel import UnsupportedNetworkError, build_model
from gridsense_sim.accel.solver import BatchedSolver, device_info, torch, torch_available
from gridsense_sim.hosting_capacity import limits_for, proportional_to_load, voltage_scope_buses
from gridsense_sim.hosting_capacity.allocation import apply_allocation
from gridsense_sim.networks import NETWORKS, make_network
from gridsense_sim.provenance import collect_provenance

DEFAULT_NETWORKS = ["cigre_lv", "kerber_landnetz", "kerber_vorstadt", "simbench_lv_rural1",
                    "simbench_lv_semiurb4", "simbench_lv_urban6", "lv_schutterwald"]


def operating_points(n: int, seed: int):
    rng = np.random.default_rng(seed)
    return rng.uniform(0.2, 1.0, n), rng.uniform(0.0, 1.0, n), rng.uniform(0.0, 2.0, n)


def time_pandapower(key: str, alloc, scope, points: int, seed: int):
    load, pv, lam = operating_points(points, seed)
    net = make_network(key)
    base_p, base_q = net.load["p_mw"].to_numpy(copy=True), net.load["q_mvar"].to_numpy(copy=True)
    apply_allocation(net, alloc, 0.0)
    pp.runpp(net)                                   # warm-up (numba compilation, caches)
    vm = np.empty((points, len(scope)))
    start = time.perf_counter()
    for i in range(points):
        net.load["p_mw"], net.load["q_mvar"] = base_p * load[i], base_q * load[i]
        apply_allocation(net, alloc, lam[i] * pv[i])
        pp.runpp(net)
        vm[i] = net.res_bus.loc[list(scope), "vm_pu"].to_numpy()
    return (time.perf_counter() - start) / points, vm, (load, pv, lam)


def time_batched(solver: BatchedSolver, batch: int, repeats: int, seed: int) -> float:
    load, pv, lam = operating_points(batch, seed)
    solver.solve(load, pv, lam)                     # warm-up
    times = []
    for _ in range(repeats):
        if solver.device.startswith("cuda"):
            torch.cuda.synchronize()
        start = time.perf_counter()
        solver.solve(load, pv, lam)
        if solver.device.startswith("cuda"):
            torch.cuda.synchronize()
        times.append(time.perf_counter() - start)
    return statistics.median(times) / batch


def bench_network(key: str, args, devices: list[str]) -> dict:
    net = make_network(key)
    scope = voltage_scope_buses(net, limits_for(key))
    alloc = proportional_to_load(net)
    row: dict = {"network": key, "buses": int(len(net.bus)), "loads": int(len(net.load))}
    pp_time, pp_vm, (load, pv, lam) = time_pandapower(key, alloc, scope, args.pandapower_points, args.seed)
    row["pandapower_ms_per_pf"] = pp_time * 1e3
    try:
        t0 = time.perf_counter()
        model = build_model(net, scope, alloc.base_mw_per_bus)
        row["model_build_s"] = time.perf_counter() - t0
    except UnsupportedNetworkError as exc:
        row["unsupported"] = str(exc)
        return row
    row["injection_buses"] = model.n_inj
    row["batched"] = []
    for device in devices:
        for dtype in args.dtypes:
            solver = BatchedSolver(model, device=device, dtype=dtype, memory_mb=args.memory_mb)
            check = solver.solve(load, pv, lam)
            dv = float(np.nanmax(np.abs(check.vm - pp_vm)))
            for batch in args.batch_sizes:
                per_pf = time_batched(solver, batch, args.repeats, args.seed + 1)
                row["batched"].append({
                    "device": device, "dtype": dtype, "batch": batch, "us_per_pf": per_pf * 1e6,
                    "speedup_vs_pandapower": pp_time / per_pf, "max_abs_dv_pu": dv})
    return row


def end_to_end(key: str, args, devices: list[str]) -> dict:
    """One PRODIST week (1008 ten-minute readings, synthetic profile) with both backends."""
    from gridsense_sim.hosting_capacity.qsts_batched import find_hosting_capacity_duration_batched
    from gridsense_sim.hosting_capacity.qsts_duration import find_hosting_capacity_duration
    from gridsense_sim.hosting_capacity.timeseries import build_synthetic_series

    ts = build_synthetic_series(1008, 144, seed=args.seed, peak_load_mult=1.0)
    out: dict = {"network": key, "criterion": "prodist_m8_drp_drc", "readings": 1008, "runs": []}
    t0 = time.perf_counter()
    ref = find_hosting_capacity_duration(make_network(key), key, ts)
    out["runs"].append({"backend": "pandapower", "seconds": time.perf_counter() - t0,
                        "lambda_max": ref.lambda_max, "power_flows": ref.power_flows})
    for device in devices:
        t0 = time.perf_counter()
        r = find_hosting_capacity_duration_batched(make_network(key), key, ts, device=device)
        out["runs"].append({"backend": f"torch/{device}", "seconds": time.perf_counter() - t0,
                            "lambda_max": r.lambda_max, "power_flows": r.power_flows,
                            "abs_diff_lambda": abs(r.lambda_max - ref.lambda_max)})
    return out


def markdown(rows: list[dict], batch: int) -> str:
    lines = ["| network | buses | pandapower ms/PF | device | dtype | batched us/PF | speed-up | max dV (pu) |",
             "|---|---:|---:|---|---|---:|---:|---:|"]
    for row in rows:
        if "unsupported" in row:
            lines.append(f"| {row['network']} | {row['buses']} | {row['pandapower_ms_per_pf']:.1f} | "
                         "- | - | not supported | - | - |")
            continue
        for b in row["batched"]:
            if b["batch"] != batch:
                continue
            lines.append(f"| {row['network']} | {row['buses']} | {row['pandapower_ms_per_pf']:.1f} | {b['device']} | "
                         f"{b['dtype']} | {b['us_per_pf']:.1f} | {b['speedup_vs_pandapower']:.0f}x | "
                         f"{b['max_abs_dv_pu']:.1e} |")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--networks", nargs="+", default=DEFAULT_NETWORKS, choices=sorted(NETWORKS))
    p.add_argument("--batch-sizes", type=int, nargs="+", default=[1, 64, 1008, 8064])
    p.add_argument("--devices", nargs="+", default=None, help="Default: cpu, plus cuda when available.")
    p.add_argument("--dtypes", nargs="+", default=["float64"], choices=["float64", "float32"])
    p.add_argument("--pandapower-points", type=int, default=30)
    p.add_argument("--repeats", type=int, default=5)
    p.add_argument("--memory-mb", type=int, default=1024)
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--end-to-end", nargs="*", default=None, metavar="NETWORK",
                   help="Also time one PRODIST week per given network with both backends (slow: minutes).")
    p.add_argument("--output-dir", default="data/benchmarks")
    args = p.parse_args(argv)
    if not torch_available():
        p.error('PyTorch is not installed: pip install -e "simulator[gpu]"')
    warnings.filterwarnings("ignore")
    devices = args.devices or (["cpu", "cuda"] if torch.cuda.is_available() else ["cpu"])
    report = {
        "created_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "machine": {"platform": platform.platform(), "processor": platform.processor(),
                    "python": platform.python_version()},
        "numba": importlib.util.find_spec("numba") is not None,
        "devices": {d: device_info(d) for d in devices},
        "provenance": collect_provenance().to_dict(),
        "settings": {k: v for k, v in vars(args).items() if k != "output_dir"},
        "per_power_flow": [], "end_to_end": [],
    }
    for key in args.networks:
        print(f"benchmarking {key} ...", flush=True)
        report["per_power_flow"].append(bench_network(key, args, devices))
    for key in args.end_to_end or []:
        print(f"end-to-end week on {key} ...", flush=True)
        report["end_to_end"].append(end_to_end(key, args, devices))
    out = Path(args.output_dir)
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"power_flow_{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}.json"
    path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    table_batch = 1008 if 1008 in args.batch_sizes else max(args.batch_sizes)
    print(f"\nnumba installed: {report['numba']}; devices: {report['devices']}\n")
    print(f"Per power flow, batch = {table_batch}\n")
    print(markdown(report["per_power_flow"], table_batch))
    for e in report["end_to_end"]:
        print(f"\nOne PRODIST week on {e['network']}:")
        for r in e["runs"]:
            extra = f", |d lambda| = {r['abs_diff_lambda']:.4f}" if "abs_diff_lambda" in r else ""
            print(f"  {r['backend']:14s} {r['seconds']:8.1f} s  lambda = {r['lambda_max']:.4f}  "
                  f"({r['power_flows']} power flows{extra})")
    print(f"\nwrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
