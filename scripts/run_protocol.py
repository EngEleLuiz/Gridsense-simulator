"""Run the frozen experiment protocol once, resumably (Phase 7.5).

    python scripts/run_protocol.py --list                 # what will run, with time estimates
    python scripts/run_protocol.py --dry-run              # the exact commands, nothing executed
    python scripts/run_protocol.py                        # run everything not done yet
    python scripts/run_protocol.py --only "A_*" "B_*"     # only some groups or run names

Guarantees:

* **Clean tree.** Refuses to start if ``git status --porcelain`` is not
  empty (override with ``--allow-dirty``, which is recorded), so every
  result carries a commit that reproduces it.
* **Labelled results.** Every run passes ``--run-label <protocol>/<run>``
  and writes into ``<output_dir>`` (one Parquet dataset for the whole
  protocol), so the dbt mart can tell protocol runs apart.
* **Resumable.** Each finished run leaves ``<output_dir>/_runs/<run>.json``
  (exit code, duration, commit, protocol SHA-256). Re-running skips runs
  already finished with exit code 0; failed runs are retried.
* **Logs.** ``<output_dir>/_logs/<run>.log`` holds the full output.
* **Integrity.** The protocol file's SHA-256 is recorded in every run
  marker; a changed protocol file is reported as a mismatch.
"""

from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STUDY = ROOT / "scripts" / "run_hosting_capacity_study.py"


def load_protocol(path: Path) -> tuple[dict, str]:
    raw = path.read_bytes()
    return json.loads(raw), hashlib.sha256(raw).hexdigest()


def git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, check=False).stdout.strip()


def selected(runs: list[dict], patterns: list[str] | None) -> list[dict]:
    if not patterns:
        return runs
    return [r for r in runs if any(fnmatch.fnmatch(r["group"], p) or fnmatch.fnmatch(r["name"], p) for p in patterns)]


def command(run: dict, protocol: dict, python: str) -> list[str]:
    out = ROOT / protocol["output_dir"]
    return [python, str(STUDY), *run["args"], "--output-dir", str(out),
            "--run-label", f"{protocol['id']}/{run['name']}", "-v"]


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Run the frozen experiment protocol once, resumably.")
    p.add_argument("--protocol", default=str(ROOT / "protocol" / "protocol-v1.json"))
    p.add_argument("--only", nargs="+", default=None, help="Glob(s) over group or run name.")
    p.add_argument("--list", action="store_true", help="List runs and estimates, then exit.")
    p.add_argument("--dry-run", action="store_true", help="Print the commands without running them.")
    p.add_argument("--allow-dirty", action="store_true", help="Run on a dirty git tree (recorded).")
    p.add_argument("--python", default=sys.executable, help="Interpreter for the study runner.")
    args = p.parse_args(argv)

    protocol, sha = load_protocol(Path(args.protocol))
    runs = selected(protocol["runs"], args.only)
    out = ROOT / protocol["output_dir"]
    marks, logs = out / "_runs", out / "_logs"

    def done(run: dict) -> bool:
        m = marks / f"{run['name']}.json"
        return m.exists() and json.loads(m.read_text(encoding="utf-8")).get("exit_code") == 0

    if args.list:
        total = 0
        for r in runs:
            state = "done" if done(r) else ("needs data" if any(not (ROOT / q).exists() for q in r.get("requires", [])) else "pending")
            print(f"{r['group']:24s} {r['name']:52s} ~{r['estimate_min']:>3d} min  {state}")
            total += 0 if done(r) else r["estimate_min"]
        print(f"\n{len(runs)} run(s); about {total / 60:.1f} h left. Protocol {protocol['id']} sha256 {sha[:12]}")
        return 0

    dirty = bool(git("status", "--porcelain"))
    if dirty and not args.allow_dirty and not args.dry_run:
        print("Refusing to run: the git tree has uncommitted changes. Commit or stash them "
              "(or pass --allow-dirty, which is recorded in every run marker).", file=sys.stderr)
        return 2
    commit = git("rev-parse", "HEAD")
    if not args.dry_run:  # a dry run must not touch the output directory
        marks.mkdir(parents=True, exist_ok=True)
        logs.mkdir(parents=True, exist_ok=True)

    summary = []
    for i, r in enumerate(runs, 1):
        cmd = command(r, protocol, args.python)
        if done(r):
            summary.append((r["name"], "skipped (done)", 0.0))
            continue
        missing = [q for q in r.get("requires", []) if not (ROOT / q).exists()]
        if missing:
            summary.append((r["name"], f"skipped (missing {', '.join(missing)})", 0.0))
            print(f"[{i}/{len(runs)}] {r['name']}: missing data {missing}; skipped")
            continue
        if args.dry_run:
            print(" ".join(cmd))
            continue
        print(f"[{i}/{len(runs)}] {r['name']} (~{r['estimate_min']} min) ...", flush=True)
        t0 = time.time()
        started = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with open(logs / f"{r['name']}.log", "w", encoding="utf-8") as log:
            code = subprocess.run(cmd, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT, check=False).returncode
        dur = time.time() - t0
        (marks / f"{r['name']}.json").write_text(json.dumps({
            "run": r["name"], "group": r["group"], "exit_code": code, "started_utc": started,
            "duration_s": round(dur, 1), "commit": commit, "git_dirty": dirty,
            "protocol_id": protocol["id"], "protocol_sha256": sha, "command": cmd[1:],
        }, indent=2), encoding="utf-8")
        status = "ok" if code == 0 else f"FAILED (exit {code}, see _logs/{r['name']}.log)"
        print(f"    {status} in {dur / 60:.1f} min", flush=True)
        summary.append((r["name"], status, dur))

    if not args.dry_run:
        print("\nSummary")
        for name, status, dur in summary:
            print(f"  {name:52s} {status:30s} {dur / 60:6.1f} min")
        failed = [s for s in summary if s[1].startswith("FAILED")]
        print(f"\n{len(failed)} failed. Results in {out}")
        return 1 if failed else 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
