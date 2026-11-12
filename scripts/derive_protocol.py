"""Derive an accelerated protocol from a frozen one (Phase 8).

    python scripts/derive_protocol.py --source protocol/protocol-v1.json \\
        --id protocol-v2 --out protocol/protocol-v2.json

Every run of the source protocol is kept with the same arguments. Runs that
use a duration criterion get ``--qsts-backend torch`` (and a new time
estimate); everything else is copied unchanged. The derived protocol
therefore answers exactly the same questions on exactly the same networks
and data: comparing its results with the source protocol's is the
acceptance test of the batched solver (capacities must agree within the
search tolerance).

The source protocol file is never modified: a frozen protocol stays frozen.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path

DURATION_FLAG = "--qsts-criterion"


def _methods(args: list[str]) -> list[str]:
    if "--methods" not in args:
        return ["deterministic", "stochastic", "qsts"]   # the runner's default
    i = args.index("--methods") + 1
    methods = []
    while i < len(args) and not args[i].startswith("--"):
        methods.append(args[i])
        i += 1
    return methods


def uses_duration_qsts(args: list[str]) -> bool:
    """True when the run evaluates QSTS under a duration criterion."""
    if DURATION_FLAG not in args or args[args.index(DURATION_FLAG) + 1] == "zero_tolerance":
        return False
    return "qsts" in _methods(args)


def derive(source: dict, source_sha256: str, new_id: str, device: str) -> dict:
    out = copy.deepcopy(source)
    out["id"] = new_id
    out["output_dir"] = f"data/{new_id}"
    out["derived_from"] = {"id": source["id"], "sha256": source_sha256}
    out["description"] = (f"{source['description']} Derived from {source['id']}: identical runs; "
                          "duration-criterion QSTS on the batched solver (--qsts-backend torch).")
    for run in out["runs"]:
        if uses_duration_qsts(run["args"]):
            run["args"] = [*run["args"], "--qsts-backend", "torch", "--device", device]
            if _methods(run["args"]) == ["qsts"]:      # mixed runs keep their (pandapower) estimate
                run["estimate_min"] = max(1, round(run["estimate_min"] / 20))
            run["backend"] = "torch"
        else:
            run["backend"] = "pandapower"
    return out


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--source", default="protocol/protocol-v1.json")
    p.add_argument("--id", default="protocol-v2")
    p.add_argument("--out", default=None, help="Default: protocol/<id>.json")
    p.add_argument("--device", default="auto", help="Device recorded in the runs (auto, cpu, cuda).")
    args = p.parse_args(argv)
    raw = Path(args.source).read_bytes()
    derived = derive(json.loads(raw), hashlib.sha256(raw).hexdigest(), args.id, args.device)
    out = Path(args.out or f"protocol/{args.id}.json")
    out.write_text(json.dumps(derived, indent=1) + "\n", encoding="utf-8")
    n = sum(r["backend"] == "torch" for r in derived["runs"])
    print(f"wrote {out}: {len(derived['runs'])} run(s), {n} on the batched solver")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
