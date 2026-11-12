"""Compare the results of two protocol runs, run by run (Phase 8 acceptance check).

    python scripts/compare_protocols.py data/protocol-v1 data/protocol-v2

Rows are matched by run name (the part of ``params.run_label`` after the
protocol id) and method; when a run was executed more than once the latest
row is used. For every pair the capacities are compared against
``--tolerance`` (default 0.01, the protocols' search tolerance).

Exit code 1 when a pair differs by more than the tolerance or changes status,
so the comparison can gate a merge. Runs present on one side only are listed
and do not fail the check (a protocol may be run in parts).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pyarrow.parquet as pq


def load(directory: str | Path) -> dict[tuple[str, str], dict]:
    """(run name, method) -> latest payload under ``directory``."""
    rows: dict[tuple[str, str], tuple[str, dict]] = {}
    for path in sorted(Path(directory).rglob("*.parquet")):
        for row in pq.read_table(path).to_pylist():
            payload = json.loads(row["raw_value"])
            label = (payload.get("params") or {}).get("run_label") or ""
            name = label.split("/", 1)[1] if "/" in label else label
            if not name:
                continue
            key = (name, row["method"])
            if key not in rows or row["run_timestamp"] > rows[key][0]:
                rows[key] = (row["run_timestamp"], payload)
    return {k: v[1] for k, v in rows.items()}


def compare(a: dict, b: dict, tolerance: float) -> tuple[list[dict], list[tuple[str, str]], list[tuple[str, str]]]:
    out = []
    for key in sorted(set(a) & set(b)):
        pa, pb = a[key], b[key]
        la, lb = pa.get("lambda_max"), pb.get("lambda_max")
        same_status = pa.get("status") == pb.get("status") and bool(pa.get("bounded")) == bool(pb.get("bounded"))
        if la is None or lb is None:
            diff, ok = None, same_status and la == lb
        else:
            diff = abs(la - lb)
            # a censored capacity is a lower bound, not a value: only its status is compared
            ok = same_status and (diff <= tolerance + 1e-12 or not pa.get("bounded"))
        out.append({"run": key[0], "method": key[1], "a": la, "b": lb, "abs_diff": diff, "ok": ok,
                    "status_a": pa.get("status"), "status_b": pb.get("status"),
                    "bounded_a": pa.get("bounded"), "bounded_b": pb.get("bounded"),
                    "backend_a": pa.get("backend", "pandapower"), "backend_b": pb.get("backend", "pandapower"),
                    "binding_a": pa.get("binding_constraint"), "binding_b": pb.get("binding_constraint")})
    return out, sorted(set(a) - set(b)), sorted(set(b) - set(a))


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("a", help="Results directory of the reference protocol (e.g. data/protocol-v1).")
    p.add_argument("b", help="Results directory to check (e.g. data/protocol-v2).")
    p.add_argument("--tolerance", type=float, default=0.01)
    p.add_argument("--only-backend-changes", action="store_true",
                   help="Only list pairs whose backend differs (the runs the batched solver replaced).")
    args = p.parse_args(argv)
    rows, only_a, only_b = compare(load(args.a), load(args.b), args.tolerance)
    if args.only_backend_changes:
        rows = [r for r in rows if r["backend_a"] != r["backend_b"]]
    fmt = lambda v: "   -    " if v is None else f"{v:8.4f}"  # noqa: E731
    print(f"{'run':52s} {'method':13s} {'a':>8s} {'b':>8s} {'|diff|':>8s}  backends              result")
    for r in rows:
        note = "ok" if r["ok"] else "DIFFERENT"
        if r["binding_a"] != r["binding_b"]:
            note += f" (binding {r['binding_a']} -> {r['binding_b']})"
        print(f"{r['run']:52s} {r['method']:13s} {fmt(r['a'])} {fmt(r['b'])} {fmt(r['abs_diff'])}  "
              f"{r['backend_a']:>10s} -> {r['backend_b']:<7s} {note}")
    bad = [r for r in rows if not r["ok"]]
    print(f"\n{len(rows)} pair(s) compared, {len(bad)} outside tolerance {args.tolerance}; "
          f"{len(only_a)} only in a, {len(only_b)} only in b")
    for label, missing in (("only in a", only_a), ("only in b", only_b)):
        for run, method in missing:
            print(f"  {label}: {run} [{method}]")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
