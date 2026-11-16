"""Build the sensitivity protocol (v3) as an addendum to a frozen base protocol.

    python scripts/build_sensitivity_protocol.py            # writes protocol/protocol-v3.json

The base protocol (v2) answers the main questions under four assumptions
that a reader will question: PV at unity power factor, a fixed source
voltage (taps are not controlled), the temporal resolution of the data, and
the adoption model of the stochastic method. Each group below varies one of
them and nothing else. Every run names the base run it is compared with
(``baseline``) and the single factor it changes (``factor``), so the analysis
never has to guess which two numbers form a pair.

Groups
------
``G_power_factor``     PV at a fixed inductive power factor (0.95, 0.92).
``H_source_voltage``   Source voltage 0.98 / 1.00 / 1.02 / 1.04 pu (fixed tap).
``I_resolution``       Capacity on block-averaged data; the London case starts
                       from 5-minute data, so 10 min (the criterion's reading)
                       is the reference and 15 / 30 / 60 min are measured against it.
``J_adoption``         Stochastic method: low and high adoption ranges, and 200
                       scenarios instead of 60 at the base range.

The base protocol is never modified and its runs are not repeated: results
go to ``data/protocol-v3`` and are compared with ``data/<base id>``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

PANEL = ("cigre_lv", "kerber_landnetz", "kerber_vorstadt", "simbench_lv_rural1", "simbench_lv_semiurb4",
         "simbench_lv_urban6", "lv_schutterwald")
SIMBENCH = ("simbench_lv_rural1", "simbench_lv_semiurb4", "simbench_lv_urban6")
ADOPTION_NETWORKS = ("cigre_lv", *SIMBENCH)
POWER_FACTORS = (0.95, 0.92)
SOURCE_VOLTAGES = (0.98, 1.00, 1.02, 1.04)
QSTS_SOURCE_VOLTAGES = (0.98, 1.02)
ADOPTION_RANGES = ((0.1, 0.4), (0.7, 1.0))
LONDON_5MIN = "data/series/london_2018_06_5min.parquet"
LONDON_BLOCKS = (10, 15, 30, 60)          # 10 = the criterion's reading length (reference)
LONDON_REFERENCE_BLOCK = 10


def _base_run(base: dict, name: str) -> dict:
    for r in base["runs"]:
        if r["name"] == name:
            return r
    raise KeyError(f"{base['id']} has no run named {name!r}")


def _run(group: str, name: str, args: list[str], estimate: int, factor: dict, baseline: dict | None,
         requires: list[str] | None = None) -> dict:
    run = {"group": group, "name": name, "args": args, "estimate_min": estimate,
           "backend": "torch" if "--qsts-backend" in args else "pandapower",
           "factor": factor, "baseline": baseline}
    if requires:
        run["requires"] = requires
    return run


def build(base: dict, base_sha256: str, new_id: str = "protocol-v3") -> dict:
    """The sensitivity protocol derived from ``base`` (a parsed protocol-v2)."""
    bid = base["id"]
    runs: list[dict] = []

    def det(net: str) -> dict:
        return _base_run(base, f"A-{net}-prodist_m8_bt")

    def drp(net: str) -> dict:
        return _base_run(base, f"D-{net}-drp-drc")

    # G -- power factor
    for net in PANEL:
        for pf in POWER_FACTORS:
            runs.append(_run(
                "G_power_factor", f"G-{net}-prodist-pf{pf:.2f}",
                [*det(net)["args"], "--pv-power-factor", f"{pf:.2f}"], 1,
                {"name": "pv_power_factor", "value": pf, "base_value": 1.0},
                {"protocol": bid, "run": det(net)["name"], "method": "deterministic"}))
    for net in SIMBENCH:
        pf = POWER_FACTORS[-1]
        runs.append(_run(
            "G_power_factor", f"G-{net}-drp-drc-pf{pf:.2f}",
            [*drp(net)["args"], "--pv-power-factor", f"{pf:.2f}"], drp(net)["estimate_min"],
            {"name": "pv_power_factor", "value": pf, "base_value": 1.0},
            {"protocol": bid, "run": drp(net)["name"], "method": "qsts"}))

    # H -- source voltage (fixed tap)
    for net in PANEL:
        for v in SOURCE_VOLTAGES:
            runs.append(_run(
                "H_source_voltage", f"H-{net}-prodist-src{v:.2f}",
                [*det(net)["args"], "--source-vm-pu", f"{v:.2f}"], 1,
                {"name": "source_vm_pu", "value": v, "base_value": "network default"},
                {"protocol": bid, "run": det(net)["name"], "method": "deterministic"}))
    for net in SIMBENCH:
        for v in QSTS_SOURCE_VOLTAGES:
            runs.append(_run(
                "H_source_voltage", f"H-{net}-drp-drc-src{v:.2f}",
                [*drp(net)["args"], "--source-vm-pu", f"{v:.2f}"], drp(net)["estimate_min"],
                {"name": "source_vm_pu", "value": v, "base_value": "network default"},
                {"protocol": bid, "run": drp(net)["name"], "method": "qsts"}))

    # I -- temporal resolution
    for net in SIMBENCH:      # native 15 min; hourly is run E of the base protocol
        runs.append(_run(
            "I_resolution", f"I-{net}-drp-drc-block30",
            [*drp(net)["args"], "--qsts-block-average", "30"], drp(net)["estimate_min"],
            {"name": "block_average_min", "value": 30, "base_value": 15},
            {"protocol": bid, "run": drp(net)["name"], "method": "qsts"}))
    london = ["--network", "cigre_lv", "--qsts-series", LONDON_5MIN, "--methods", "qsts",
              "--qsts-criterion", "prodist_m8_drp_drc", "--qsts-backend", "torch", "--device", "auto"]
    for block in LONDON_BLOCKS:
        name = f"I-london-5min-drp-drc-block{block}"
        reference = block == LONDON_REFERENCE_BLOCK      # the reference run has no baseline of its own
        runs.append(_run(
            "I_resolution", name, [*london, "--qsts-block-average", str(block)], 2,
            {"name": "block_average_min", "value": block, "base_value": LONDON_REFERENCE_BLOCK},
            None if reference else
            {"protocol": new_id, "run": f"I-london-5min-drp-drc-block{LONDON_REFERENCE_BLOCK}", "method": "qsts"},
            requires=[LONDON_5MIN]))

    # J -- adoption model of the stochastic method
    for net in ADOPTION_NETWORKS:
        base_run = _base_run(base, f"C-{net}-prodist")
        ref = {"protocol": bid, "run": base_run["name"], "method": "stochastic"}
        for lo, hi in ADOPTION_RANGES:
            runs.append(_run(
                "J_adoption", f"J-{net}-prodist-adopt{lo:.1f}-{hi:.1f}",
                [*base_run["args"], "--mc-adoption-min", f"{lo:.1f}", "--mc-adoption-max", f"{hi:.1f}"],
                base_run["estimate_min"],
                {"name": "adoption_fraction", "value": [lo, hi],
                 "base_value": base["fixed_parameters"]["adoption_fraction"]}, ref))
        args = list(base_run["args"])
        args[args.index("--mc-n-scenarios") + 1] = "200"
        runs.append(_run(
            "J_adoption", f"J-{net}-prodist-n200", args, round(base_run["estimate_min"] * 200 / 60),
            {"name": "n_scenarios", "value": 200, "base_value": base["fixed_parameters"]["stochastic_scenarios"]},
            ref))

    return {
        "id": new_id,
        "description": (f"Sensitivity addendum to {bid}: one assumption varied per group (PV power factor, "
                        "source voltage, temporal resolution, adoption model). Base cases are runs of "
                        f"{bid} and are not repeated here."),
        "output_dir": f"data/{new_id}",
        "base_protocol": {"id": bid, "sha256": base_sha256, "output_dir": base["output_dir"]},
        "fixed_parameters": {
            **base["fixed_parameters"],
            "power_factors": list(POWER_FACTORS),
            "source_voltages_pu": list(SOURCE_VOLTAGES),
            "london_block_minutes": list(LONDON_BLOCKS),
            "london_reference_block_minutes": LONDON_REFERENCE_BLOCK,
            "adoption_ranges": [list(r) for r in ADOPTION_RANGES],
        },
        "runs": runs,
    }


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--base", default=str(ROOT / "protocol" / "protocol-v2.json"))
    p.add_argument("--id", default="protocol-v3")
    p.add_argument("--out", default=None, help="Default: protocol/<id>.json")
    args = p.parse_args(argv)
    raw = Path(args.base).read_bytes()
    protocol = build(json.loads(raw), hashlib.sha256(raw).hexdigest(), args.id)
    out = Path(args.out or ROOT / "protocol" / f"{args.id}.json")
    out.write_text(json.dumps(protocol, indent=1) + "\n", encoding="utf-8")
    groups: dict[str, int] = {}
    for r in protocol["runs"]:
        groups[r["group"]] = groups.get(r["group"], 0) + 1
    total = sum(r["estimate_min"] for r in protocol["runs"])
    print(f"wrote {out}: {len(protocol['runs'])} run(s) {groups}, about {total / 60:.1f} h")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
