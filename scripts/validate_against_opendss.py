"""Cross-check pandapower against OpenDSS on the network panel.

    pip install -e "simulator[validation]"
    python scripts/validate_against_opendss.py                 # panel, both criteria
    python scripts/validate_against_opendss.py --networks cigre_lv --frameworks prodist_m8_bt

For every network and criterion:

1. the deterministic hosting capacity is computed twice, once with each
   engine as the only power-flow solver (same limits, scope, allocation and
   bisection);
2. voltages and branch loadings of the two engines are compared at three
   states of the critical operating point: no PV, PV at the capacity, and
   PV 20 % above it (where the network is past its limits and model
   differences are largest).

Acceptance (exit code 1 otherwise):

* voltages within ``--max-dv`` (default 1e-4 pu);
* branch loadings within ``--max-dloading`` (default 0.1 percentage point);
* capacities within the search tolerance, with the same kind of binding
  constraint.

Networks the translation does not cover are listed as ``unsupported`` with the
reason and do not fail the check: they are a stated limit of the validation,
not a silent gap. Results go to ``reports/validation/opendss.{json,md}``.
"""

from __future__ import annotations

import argparse
import copy
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "simulator"))

from gridsense_sim.hosting_capacity import HostingCapacityError  # noqa: E402
from gridsense_sim.hosting_capacity import find_hosting_capacity_deterministic  # noqa: E402
from gridsense_sim.hosting_capacity.allocation import apply_allocation, proportional_to_load  # noqa: E402
from gridsense_sim.hosting_capacity.conditions import DEFAULT_CRITICAL_LOAD_SCALE, apply_load_scale  # noqa: E402
from gridsense_sim.networks import make_network  # noqa: E402
from gridsense_sim.provenance import collect_provenance  # noqa: E402
from gridsense_sim.validation.opendss import (  # noqa: E402
    INSTALL_HINT,
    UnsupportedForOpenDss,
    compare_power_flow,
    hosting_capacity_with_opendss,
    opendss_available,
)

PANEL = ("cigre_lv", "kerber_landnetz", "kerber_vorstadt", "simbench_lv_rural1", "simbench_lv_semiurb4",
         "simbench_lv_urban6", "lv_schutterwald")
FRAMEWORKS = ("prodist_m8_bt", "en50160_envelope")
STATES = (("no_pv", 0.0), ("at_capacity", 1.0), ("above_capacity", 1.2))


def _family(binding: str | None) -> str | None:
    return None if not binding else binding.split("@")[0]


def check(network: str, framework: str, load_scale: float, tolerance: float, max_dv: float,
          max_dloading: float) -> dict:
    row: dict = {"network": network, "framework": framework, "load_scale": load_scale}
    try:
        net = make_network(network)
    except Exception as exc:  # noqa: BLE001 -- e.g. the optional simbench package is missing
        return {**row, "status": "unavailable", "reason": f"{type(exc).__name__}: {exc}"}
    try:
        dss_hc = hosting_capacity_with_opendss(net, network, framework=framework, load_scale=load_scale,
                                               tolerance=tolerance)
    except UnsupportedForOpenDss as exc:
        return {**row, "status": "unsupported", "reason": str(exc)}
    try:
        ref = find_hosting_capacity_deterministic(net, network, framework=framework, load_scale=load_scale,
                                                  tolerance=tolerance)
        pp_hc = {"status": "ok", "lambda_max": ref.lambda_max if ref.bounded else None,
                 "binding_constraint": ref.binding_constraint}
    except HostingCapacityError as exc:
        pp_hc = {"status": exc.status, "lambda_max": None, "binding_constraint": None}
    row.update({"pandapower": pp_hc, "opendss": dss_hc})
    same_status = pp_hc["status"] == dss_hc["status"]
    if pp_hc["lambda_max"] is None or dss_hc["lambda_max"] is None:
        # No capacity on one side (infeasible baseline or censored search): only the status can be compared.
        ok = same_status and pp_hc["lambda_max"] == dss_hc["lambda_max"]
        return {**row, "status": "ok" if ok else "DIFFERENT", "abs_dlambda": None, "states": {}}
    d_lambda = abs(pp_hc["lambda_max"] - dss_hc["lambda_max"])
    states = {}
    for name, factor in STATES:
        work = copy.deepcopy(net)
        allocation = proportional_to_load(work)
        apply_load_scale(work, load_scale)
        apply_allocation(work, allocation, factor * pp_hc["lambda_max"])
        states[name] = compare_power_flow(work)
    solved = [s for s in states.values() if s["max_abs_dv_pu"] is not None]
    worst_dv = max((s["max_abs_dv_pu"] for s in solved), default=None)
    worst_dl = max((max(s["max_abs_dline_percent"], s["max_abs_dtrafo_percent"]) for s in solved), default=None)
    ok = (same_status and d_lambda <= tolerance + 1e-12
          and _family(pp_hc["binding_constraint"]) == _family(dss_hc["binding_constraint"])
          and len(solved) == len(states) and worst_dv <= max_dv and worst_dl <= max_dloading)
    return {**row, "status": "ok" if ok else "DIFFERENT", "abs_dlambda": d_lambda, "states": states,
            "max_abs_dv_pu": worst_dv, "max_abs_dloading_percent": worst_dl}


def render(report: dict) -> str:
    acc = report["acceptance"]
    lines = ["# pandapower vs OpenDSS", "",
             f"Commit {str(report['provenance']['git_commit'])[:10]}, OpenDSS engine via opendssdirect.py "
             f"{report['opendssdirect']}. Acceptance: |dV| <= {acc['max_dv_pu']:g} pu, |d loading| <= "
             f"{acc['max_dloading_percent']:g} percentage point, |d lambda| <= {acc['tolerance']:g}.", "",
             "| network | criterion | lambda pandapower | lambda OpenDSS | max abs dV (pu) | max abs d loading (pp) "
             "| binding | result |", "|---|---|---|---|---|---|---|---|"]
    for r in report["rows"]:
        if r["status"] in ("unsupported", "unavailable"):
            lines.append(f"| {r['network']} | {r['framework']} | - | - | - | - | - | {r['status']}: {r['reason']} |")
            continue
        a, b = r["pandapower"], r["opendss"]
        fmt = lambda v, spec: "-" if v is None else format(v, spec)  # noqa: E731
        lines.append(
            f"| {r['network']} | {r['framework']} | {fmt(a['lambda_max'], '.4f') if a['lambda_max'] is not None else a['status']} "
            f"| {fmt(b['lambda_max'], '.4f') if b['lambda_max'] is not None else b['status']} "
            f"| {fmt(r.get('max_abs_dv_pu'), '.1e')} | {fmt(r.get('max_abs_dloading_percent'), '.1e')} "
            f"| {_family(a['binding_constraint']) or '-'} | {r['status']} |")
    n_bad = sum(r["status"] == "DIFFERENT" for r in report["rows"])
    n_skip = sum(r["status"] in ("unsupported", "unavailable") for r in report["rows"])
    lines += ["", f"{len(report['rows']) - n_bad - n_skip} agree, {n_bad} differ, {n_skip} not covered."]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--networks", nargs="+", default=list(PANEL))
    p.add_argument("--frameworks", nargs="+", default=list(FRAMEWORKS))
    p.add_argument("--load-scale", type=float, default=DEFAULT_CRITICAL_LOAD_SCALE)
    p.add_argument("--tolerance", type=float, default=0.01, help="Search tolerance of both capacities.")
    p.add_argument("--max-dv", type=float, default=1e-4, help="Accepted voltage difference, pu.")
    p.add_argument("--max-dloading", type=float, default=0.1, help="Accepted loading difference, percentage points.")
    p.add_argument("--out", default=str(ROOT / "reports" / "validation"))
    args = p.parse_args(argv)
    if not opendss_available():
        raise SystemExit(INSTALL_HINT)
    from importlib import metadata

    rows = [check(n, f, args.load_scale, args.tolerance, args.max_dv, args.max_dloading)
            for n in args.networks for f in args.frameworks]
    report = {"provenance": collect_provenance().to_dict(),
              "opendssdirect": metadata.version("opendssdirect.py"),
              "acceptance": {"max_dv_pu": args.max_dv, "max_dloading_percent": args.max_dloading,
                             "tolerance": args.tolerance},
              "rows": rows}
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "opendss.json").write_text(json.dumps(report, indent=1) + "\n", encoding="utf-8")
    text = render(report)
    (out / "opendss.md").write_text(text, encoding="utf-8")
    print(text)
    return 1 if any(r["status"] == "DIFFERENT" for r in rows) else 0


if __name__ == "__main__":
    raise SystemExit(main())
