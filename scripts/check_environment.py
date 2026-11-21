"""Check that this machine can run the studies, and pick the device for the batched solver.

    python scripts/check_environment.py            # full check (about a minute)
    python scripts/check_environment.py --quick    # packages and GPU detection only

What it checks
--------------
1. Python and every package the studies need (required) or can use (optional),
   with versions.
2. PyTorch: which build is installed (CPU-only, CUDA for NVIDIA, ROCm for AMD)
   and whether it sees a GPU.
3. With a GPU: the GPU solves 256 operating points of ``simbench_lv_semiurb4``
   in ``float64`` and must match the CPU to 1e-10 pu. This is the same
   acceptance as ``test_cuda_matches_cpu``.
4. Unless ``--quick``: time per power flow on CPU and GPU at the batch sizes the
   duration QSTS uses, and a recommendation for ``--device``.
   Consumer GPUs run ``float64`` (the only precision for reported results) at a
   small fraction of their ``float32`` speed, so a GPU is not automatically
   faster than a good CPU; this step measures it on this machine.
5. Which input files the protocols need are present.

Exit code 0 when everything required is present (and, with a GPU, the GPU
agrees with the CPU); 1 otherwise. A JSON copy of the report goes to
``data/benchmarks/environment_<timestamp>.json`` with ``--save``.
"""

from __future__ import annotations

import argparse
import importlib
import json
import platform
import statistics
import sys
import time
import warnings
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "simulator"))

REQUIRED = ("numpy", "scipy", "pandas", "pandapower", "pyarrow", "gridsense_sim")
OPTIONAL = {
    "numba": "faster pandapower power flows (extra 'fast')",
    "simbench": "SimBench networks and profiles, the main case (extra 'data')",
    "torch": "batched solver for the duration QSTS, --qsts-backend torch (extra 'gpu' or a GPU build)",
    "matplotlib": "figures of scripts/analyze_protocol.py (extra 'plots')",
    "opendssdirect": "cross-check against OpenDSS (extra 'validation')",
    "pytest": "test suite (extra 'dev')",
}
CHECK_NETWORK = "simbench_lv_semiurb4"
FALLBACK_NETWORK = "cigre_lv"
BATCHES = (1008, 8064)
MAX_GPU_CPU_DV = 1e-10


def version_of(name: str) -> str | None:
    try:
        module = importlib.import_module(name)
    except Exception:  # noqa: BLE001 -- a broken install is reported like a missing one
        return None
    return str(getattr(module, "__version__", "installed"))


def mark(ok: bool | None) -> str:
    return {True: "ok  ", False: "FAIL", None: "--  "}[ok]


def torch_section() -> dict:
    out: dict = {"installed": False}
    try:
        import torch
    except ImportError:
        return out
    from gridsense_sim.accel.solver import gpu_backend

    out.update({"installed": True, "version": torch.__version__, "build": gpu_backend() or "cpu",
                "cuda_version": torch.version.cuda, "hip_version": getattr(torch.version, "hip", None),
                "gpu_available": bool(torch.cuda.is_available()), "threads": torch.get_num_threads()})
    if out["gpu_available"]:
        out["gpus"] = [torch.cuda.get_device_name(i) for i in range(torch.cuda.device_count())]
    return out


def _setup(key: str):
    from gridsense_sim.accel.model import build_model
    from gridsense_sim.hosting_capacity.allocation import proportional_to_load
    from gridsense_sim.hosting_capacity.limits import limits_for
    from gridsense_sim.hosting_capacity.scope import voltage_scope_buses
    from gridsense_sim.networks import make_network

    net = make_network(key)
    scope = list(voltage_scope_buses(net, limits_for(key)))
    alloc = proportional_to_load(net)
    return build_model(net, scope, alloc.base_mw_per_bus)


def _points(n: int, seed: int = 0):
    import numpy as np

    rng = np.random.default_rng(seed)
    return rng.uniform(0.2, 1.1, n), rng.uniform(0.0, 1.0, n), rng.uniform(0.0, 2.5, n)


def _time(solver, batch: int, repeats: int = 3) -> float:
    import torch

    load, pv, lam = _points(batch, 1)
    solver.solve(load, pv, lam)                                     # warm-up (kernels, allocations)
    times = []
    for _ in range(repeats):
        if solver.device.startswith("cuda"):
            torch.cuda.synchronize()
        start = time.perf_counter()
        solver.solve(load, pv, lam)
        if solver.device.startswith("cuda"):
            torch.cuda.synchronize()
        times.append(time.perf_counter() - start)
    return statistics.median(times) / batch * 1e6                   # microseconds per power flow


def solver_section(gpu: bool, quick: bool) -> dict:
    import numpy as np

    from gridsense_sim.accel.solver import BatchedSolver

    out: dict = {}
    try:
        model, key = _setup(CHECK_NETWORK), CHECK_NETWORK
    except Exception as exc:  # noqa: BLE001 -- simbench missing: fall back to a built-in network
        out["note"] = f"{CHECK_NETWORK} unavailable ({type(exc).__name__}); using {FALLBACK_NETWORK}"
        model, key = _setup(FALLBACK_NETWORK), FALLBACK_NETWORK
    out["network"] = key
    cpu = BatchedSolver(model, device="cpu")
    load, pv, lam = _points(256)
    ref = cpu.solve(load, pv, lam)
    out["cpu_converged"] = bool(ref.converged.all())
    if gpu:
        try:
            got = BatchedSolver(model, device="cuda").solve(load, pv, lam)
            dv = float(np.nanmax(np.abs(got.vm - ref.vm)))
            out.update({"gpu_max_abs_dv_pu": dv, "gpu_matches_cpu": bool(dv < MAX_GPU_CPU_DV and got.converged.all())})
        except Exception as exc:  # noqa: BLE001 -- driver/runtime problems are the point of this check
            out.update({"gpu_error": f"{type(exc).__name__}: {exc}", "gpu_matches_cpu": False})
    if quick:
        return out
    timing: dict = {}
    for device in ["cpu"] + (["cuda"] if gpu and out.get("gpu_matches_cpu") else []):
        solver = cpu if device == "cpu" else BatchedSolver(model, device="cuda")
        timing[device] = {str(b): _time(solver, b) for b in BATCHES}
    out["us_per_power_flow"] = timing
    if "cuda" in timing:
        ratio = {b: timing["cpu"][b] / timing["cuda"][b] for b in timing["cpu"]}
        out["gpu_speedup"] = ratio
        best = max(ratio.values())
        out["recommendation"] = ("--device cuda" if best >= 1.5 else
                                 "--device cpu (the GPU is not clearly faster in float64 on this machine)")
    else:
        out["recommendation"] = "--device cpu"
    return out


def data_section() -> dict:
    out = {}
    for name in ("protocol-v2.json", "protocol-v3.json"):
        path = ROOT / "protocol" / name
        if not path.exists():
            continue
        protocol = json.loads(path.read_text(encoding="utf-8"))
        for run in protocol["runs"]:
            for req in run.get("requires", []):
                entry = out.setdefault(req, {"present": (ROOT / req).exists(), "runs": 0, "protocols": set()})
                entry["runs"] += 1
                entry["protocols"].add(protocol["id"])
    return {k: {**v, "protocols": sorted(v["protocols"])} for k, v in out.items()}


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--quick", action="store_true", help="Skip the timing (packages, GPU detection, GPU check only).")
    p.add_argument("--save", action="store_true", help="Write the report to data/benchmarks/.")
    args = p.parse_args(argv)
    warnings.filterwarnings("ignore")
    report: dict = {"created_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    "python": platform.python_version(), "platform": platform.platform(),
                    "processor": platform.processor()}
    ok = True

    print(f"Python {report['python']} on {report['platform']}")
    if sys.version_info < (3, 10):
        print("FAIL Python 3.10 or newer is required")
        ok = False
    print("\nPackages")
    report["packages"] = {}
    for name in REQUIRED:
        v = version_of(name)
        report["packages"][name] = v
        ok &= v is not None
        print(f"  {mark(v is not None)} {name:15s} {v or 'missing (required)'}")
    for name, why in OPTIONAL.items():
        v = version_of(name)
        report["packages"][name] = v
        print(f"  {mark(True if v else None)} {name:15s} {v or 'missing'}  -- {why}")

    print("\nPyTorch")
    t = torch_section()
    report["torch"] = t
    if not t["installed"]:
        print("  --   not installed: the duration QSTS runs on pandapower only (much slower)")
    else:
        build = {"cpu": "CPU-only", "cuda": f"CUDA {t['cuda_version']} (NVIDIA)",
                 "rocm": f"ROCm/HIP {t['hip_version']} (AMD)"}[t["build"]]
        print(f"  {t['version']}, {build} build, {t['threads']} CPU threads")
        if t["gpu_available"]:
            print(f"  ok   GPU: {', '.join(t['gpus'])}")
        elif t["build"] != "cpu":
            # Not a failure: the default Linux wheel is a CUDA build even on machines without a GPU.
            print("  --   GPU build installed but no GPU visible: if this machine has one, check the driver "
                  "(docs/SETUP.md, step 4); otherwise the CPU is used")
        else:
            print("  --   no GPU build: install the ROCm (AMD) or CUDA (NVIDIA) build to use a GPU")

    if t["installed"]:
        print("\nBatched solver" + ("" if args.quick else " (timing takes about a minute)"))
        s = solver_section(t["gpu_available"], args.quick)
        report["solver"] = s
        if "note" in s:
            print(f"  --   {s['note']}")
        print(f"  {mark(s['cpu_converged'])} CPU solves {s['network']}")
        ok &= s["cpu_converged"]
        if t["gpu_available"]:
            if "gpu_error" in s:
                print(f"  FAIL GPU solve failed: {s['gpu_error']}")
            else:
                print(f"  {mark(s['gpu_matches_cpu'])} GPU matches CPU in float64: max |dV| = "
                      f"{s['gpu_max_abs_dv_pu']:.1e} pu (accepted < {MAX_GPU_CPU_DV:g})")
            ok &= bool(s["gpu_matches_cpu"])
        if "us_per_power_flow" in s:
            for device, row in s["us_per_power_flow"].items():
                cells = ", ".join(f"batch {b}: {us:.1f} us" for b, us in row.items())
                print(f"  {device:5s} per power flow  {cells}")
            if "gpu_speedup" in s:
                print("  GPU speed-up     " + ", ".join(f"batch {b}: {r:.1f}x" for b, r in s["gpu_speedup"].items()))
            print(f"  -> use {s['recommendation']}")

    print("\nInput files of the protocols")
    report["data"] = data_section()
    for path, d in report["data"].items():
        state = "ok  " if d["present"] else "--  "
        print(f"  {state} {path}  ({d['runs']} run(s) of {', '.join(d['protocols'])}"
              f"{'' if d['present'] else '; skipped until present'})")

    print("\n" + ("Ready." if ok else "Not ready: fix the FAIL lines above."))
    if args.save:
        out = ROOT / "data" / "benchmarks"
        out.mkdir(parents=True, exist_ok=True)
        path = out / f"environment_{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}.json"
        path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
        print(f"Saved {path}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
