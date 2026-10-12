"""Run a pre-registered experiment specification locally (resumable, parallel).

Usage (from the project root)::

    py -3.14 scripts\\run_experiments.py experiments\\experiment_spec.json
    py -3.14 scripts\\run_experiments.py experiments\\experiment_spec.json --only E1_london_2018_06
    py -3.14 scripts\\run_experiments.py experiments\\experiment_spec.json --report-only

Design rules:

* Every cell is a call to ``scripts/run_hosting_capacity_study.py`` with
  its own output folder. Nothing is computed here that the study runner
  does not compute.
* A cell is skipped when its ``manifest.json`` already exists with
  ``returncode == 0``: an interrupted night resumes where it stopped.
* Each cell's manifest records the exact command, the SHA-256 of the
  series it used (from the series' own provenance), the git commit, the
  package versions, the wall-clock time and the output file hashes.
* Threads are capped at one per cell (``OMP_NUM_THREADS=1`` and friends)
  so that the parallel cells do not fight over cores.
* After the run, the report checks invariants the project tests in code:
  zero-tolerance QSTS must not exceed the deterministic value at the same
  operating point, and a duration criterion must not lower the zero-
  tolerance capacity on the same series.
"""

from __future__ import annotations

import argparse
import concurrent.futures as cf
import hashlib
import json
import os
import platform
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "scripts" / "run_hosting_capacity_study.py"
THREAD_ENV = {
    "OMP_NUM_THREADS": "1", "MKL_NUM_THREADS": "1", "OPENBLAS_NUM_THREADS": "1",
    "NUMEXPR_NUM_THREADS": "1", "NUMBA_NUM_THREADS": "1", "PYTHONUTF8": "1",
}


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def git_commit() -> str | None:
    try:
        out = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True,
                             text=True, check=True)
        dirty = subprocess.run(["git", "status", "--porcelain", "--", "simulator", "scripts", "transform"],
                               cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip()
        return out.stdout.strip() + ("-dirty" if dirty else "")
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None


def package_versions() -> dict[str, str]:
    from importlib import metadata

    names = ["pandapower", "pandas", "numpy", "scipy", "pyarrow", "simbench"]
    out = {}
    for n in names:
        try:
            out[n] = metadata.version(n)
        except metadata.PackageNotFoundError:
            out[n] = "not installed"
    return out


def series_provenance(path: str | None) -> dict | None:
    """SHA-256 of the series file and the manifest hash embedded by build-series."""
    if not path:
        return None
    p = ROOT / path
    if not p.exists():
        return {"path": path, "missing": True}
    import pyarrow.parquet as pq

    meta = (pq.read_schema(p).metadata or {}).get(b"gridsense_series_manifest")
    manifest = json.loads(meta) if meta else {}
    return {
        "path": path,
        "file_sha256": sha256_file(p),
        "manifest_sha256": manifest.get("manifest_sha256"),
        "source": (manifest.get("load") or {}).get("dataset", "") + "+" + (manifest.get("pv") or {}).get("dataset", ""),
        "start_local": manifest.get("start_local"),
        "days": manifest.get("days"),
        "steps_per_day": manifest.get("steps_per_day"),
    }


def build_args(cell: dict, out_dir: Path) -> list[str]:
    args = [sys.executable, str(RUNNER), "--network", cell["network"], "--methods", *cell["methods"],
            "--output-dir", str(out_dir), "-v"]
    if cell.get("framework"):
        args += ["--framework", cell["framework"]]
    if cell.get("qsts_series"):
        args += ["--qsts-series", cell["qsts_series"]]
    if cell.get("qsts_criterion"):
        args += ["--qsts-criterion", cell["qsts_criterion"]]
    if cell.get("qsts_network_profiles"):
        args += ["--qsts-network-profiles", "--qsts-start", cell["qsts_start"],
                 "--qsts-days", str(cell["qsts_days"])]
    if cell.get("critical_point_from_series"):
        args += ["--critical-point-from-series"]
    if cell.get("block_average_min"):
        args += ["--qsts-block-average", str(cell["block_average_min"])]
    if cell.get("existing_pv"):
        args += ["--existing-pv", cell["existing_pv"]]
    if cell.get("phase_model"):
        args += ["--phase-model", cell["phase_model"]]
    if cell.get("source_vm_pu") is not None:
        args += ["--source-vm-pu", str(cell["source_vm_pu"])]
    if cell.get("mc_n_scenarios"):
        args += ["--mc-n-scenarios", str(cell["mc_n_scenarios"]), "--seed", str(cell.get("seed", 42))]
    return args


def run_cell(cell: dict, base: Path) -> dict:
    cell_dir = base / cell["id"]
    if cell.get("qsts_series") and not (ROOT / cell["qsts_series"]).exists():
        return {"id": cell["id"], "status": f"MISSING series {cell['qsts_series']} (build it first)"}
    manifest_path = cell_dir / "manifest.json"
    if manifest_path.exists() and json.loads(manifest_path.read_text(encoding="utf-8")).get("returncode") == 0:
        return {"id": cell["id"], "status": "skipped (done)"}
    hc_dir = cell_dir / "hc"
    if hc_dir.exists():
        import shutil

        shutil.rmtree(hc_dir)  # a failed or interrupted cell restarts clean
    cell_dir.mkdir(parents=True, exist_ok=True)
    args = build_args(cell, hc_dir)
    env = {**os.environ, **THREAD_ENV}
    t0 = time.time()
    proc = subprocess.run(args, cwd=ROOT, capture_output=True, text=True, env=env, encoding="utf-8",
                          errors="replace", check=False)
    elapsed = time.time() - t0
    (cell_dir / "stdout.log").write_text(proc.stdout, encoding="utf-8")
    (cell_dir / "stderr.log").write_text(proc.stderr, encoding="utf-8")
    outputs = sorted(hc_dir.rglob("*.parquet")) if hc_dir.exists() else []
    manifest = {
        "id": cell["id"],
        "description": cell.get("description", ""),
        "cell": cell,
        "command": " ".join(args),
        "returncode": proc.returncode,
        "started_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "seconds": round(elapsed, 1),
        "git_commit": git_commit(),
        "python": platform.python_version(),
        "platform": platform.platform(),
        "packages": package_versions(),
        "series": series_provenance(cell.get("qsts_series")),
        "outputs": [{"path": str(p.relative_to(cell_dir)), "sha256": sha256_file(p)} for p in outputs],
    }
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    status = "ok" if proc.returncode == 0 else f"FAILED rc={proc.returncode}"
    return {"id": cell["id"], "status": status, "seconds": round(elapsed, 1)}


def read_results(base: Path) -> list[dict]:
    """Flatten every cell's results into rows (one per method result)."""
    import pyarrow.dataset as ds

    rows = []
    for cell_dir in sorted(p for p in base.iterdir() if p.is_dir()):
        mpath = cell_dir / "manifest.json"
        if not mpath.exists():
            continue
        manifest = json.loads(mpath.read_text(encoding="utf-8"))
        hc = cell_dir / "hc"
        if not hc.exists():
            rows.append({"cell": manifest["id"], "method": "-", "status": f"rc={manifest['returncode']}"})
            continue
        table = ds.dataset(hc, format="parquet", partitioning="hive").to_table()
        for r in table.to_pylist():
            payload = json.loads(r["raw_value"])
            params = payload.get("params") or {}
            cell_spec = manifest.get("cell", {})
            series_key = cell_spec.get("qsts_series") or (
                f"{cell_spec.get('network')}|profiles:{cell_spec.get('qsts_start')}+{cell_spec.get('qsts_days')}d"
                if cell_spec.get("qsts_network_profiles") else f"snapshot:{cell_spec.get('network')}")
            rows.append({
                "cell": manifest["id"],
                "series_key": series_key,
                "method": r["method"],
                "status": payload.get("status"),
                "criterion": payload.get("criterion") or (payload.get("conditions") or {}).get("framework"),
                "lambda_max": payload.get("lambda_max"),
                "total_pv_mw": payload.get("total_pv_mw"),
                "binding": payload.get("binding_constraint"),
                "bounded": payload.get("bounded"),
                "series_manifest": (params.get("series_manifest_sha256") or payload.get("series_manifest_sha256")),
                "seconds": manifest["seconds"],
            })
    return rows


def checks(rows: list[dict], tol: float = 0.011) -> list[str]:
    """Invariants the project tests in code, applied per series (not per cell).

    Cells are grouped by the series they read, because the tau = 0 and the
    duration QSTS are separate cells in the plan. Comparisons are only made
    between results that share one series (or one SimBench profile set).
    """
    out = []
    by_series: dict[str, list[dict]] = {}
    for r in rows:
        if r.get("lambda_max") is not None:
            by_series.setdefault(r.get("series_key", "?"), []).append(r)
    for key, rs in by_series.items():
        det = [r for r in rs if r["method"] == "deterministic"]
        zero = [r for r in rs if r["method"] == "qsts" and r.get("criterion") == "prodist_m8_zero_tolerance"]
        duration = [r for r in rs if r["method"] == "qsts" and r.get("criterion") == "prodist_m8_drp_drc"]
        if det and zero:
            ok = zero[0]["lambda_max"] <= det[0]["lambda_max"] + tol
            out.append(f"[{'PASS' if ok else 'FAIL'}] {key}: QSTS(tau=0) {zero[0]['lambda_max']:.3f} "
                       f"<= deterministic {det[0]['lambda_max']:.3f}")
        if zero and duration:
            ok = duration[0]["lambda_max"] >= zero[0]["lambda_max"] - tol
            out.append(f"[{'PASS' if ok else 'FAIL'}] {key}: duration {duration[0]['lambda_max']:.3f} "
                       f">= tau=0 {zero[0]['lambda_max']:.3f}")
    return out


def write_report(base: Path, spec: dict, rows: list[dict]) -> Path:
    stamp = datetime.now(timezone.utc).isoformat(timespec="minutes")
    header = f"Generated {stamp} UTC on `{platform.platform()}`, Python {platform.python_version()}."
    lines = [f"# Experiment report: {spec.get('name', 'unnamed')}", "", header, "", "## Invariant checks", ""]
    found = checks(rows)
    lines += [f"- {c}" for c in found] if found else ["- no pairs available to check yet"]
    lines += ["", "## Results", ""]
    lines += ["| cell | method | criterion | λ | MW | binding | bounded | status | s |",
              "|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        lam = "" if r.get("lambda_max") is None else f"{r['lambda_max']:.3f}"
        mw = "" if r.get("total_pv_mw") is None else f"{r['total_pv_mw']:.3f}"
        lines.append(f"| {r['cell']} | {r.get('method', '')} | {r.get('criterion', '') or ''} | {lam} | {mw} | "
                     f"{r.get('binding', '') or ''} | {r.get('bounded', '')} | {r.get('status', '')} | "
                     f"{r.get('seconds', '')} |")
    path = base / "REPORT.md"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("spec", help="experiment specification (JSON)")
    p.add_argument("--only", nargs="*", default=None, help="run only these cell ids")
    p.add_argument("--workers", type=int, default=None, help="parallel cells (default: spec or 4)")
    p.add_argument("--report-only", action="store_true")
    args = p.parse_args(argv)
    spec = json.loads(Path(args.spec).read_text(encoding="utf-8"))
    base = Path(spec.get("output_dir", "experiments/results"))
    base = base if base.is_absolute() else ROOT / base
    base.mkdir(parents=True, exist_ok=True)
    cells = [c for c in spec["cells"] if not args.only or c["id"] in args.only]
    if not args.report_only:
        workers = args.workers or spec.get("workers", 4)
        print(f"{len(cells)} cell(s), {workers} in parallel -> {base}", flush=True)
        with cf.ThreadPoolExecutor(max_workers=workers) as ex:
            futures = {ex.submit(run_cell, c, base): c["id"] for c in cells}
            for fut in cf.as_completed(futures):
                print(json.dumps(fut.result(), ensure_ascii=False), flush=True)
    rows = read_results(base)
    report = write_report(base, spec, rows)
    print(f"report: {report}", flush=True)
    for c in checks(rows):
        print(c, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
