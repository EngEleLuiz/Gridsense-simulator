"""``gridsense-bdgd``: inspect a BDGD export, list its transformers, convert one feeder.

    gridsense-bdgd inspect data/raw/bdgd
    gridsense-bdgd list    data/raw/bdgd --min-consumers 30 --top 20
    gridsense-bdgd build   data/raw/bdgd --transformer 123456 --mv-kv 13.8 --lv-kv 0.38 \\
        --load-factor 0.45 --vk-percent 4.5 --source-label "BDGD 2023, distributor X" \\
        --out data/networks/br_feeder_a.json

The converted file is used with ``run_hosting_capacity_study.py --network br_feeder_a
--network-file data/networks/br_feeder_a.json``.
"""

from __future__ import annotations

import argparse
import json
import sys

from .builder import BdgdBuildError, BuildOptions, build_lv_network, list_transformers, save_network
from .reader import BdgdReadError, inspect, read_tables
from .schema import BdgdSchema


def _schema(args: argparse.Namespace) -> BdgdSchema:
    return BdgdSchema.from_json(args.schema) if args.schema else BdgdSchema()


def _cmd_inspect(args: argparse.Namespace) -> int:
    rows = inspect(args.source, _schema(args))
    bad = False
    for r in rows:
        state = "ok" if r["found"] and not r["missing"] else ("MISSING" if r["required"] else "absent (optional)")
        if r["found"] and r["missing"]:
            state = f"missing columns {r['missing']}"
        bad |= r["required"] and (not r["found"] or bool(r["missing"]))
        print(f"{r['layer']:12s} {'required' if r['required'] else 'optional':8s} rows={r['rows']:>9d}  {state}")
        if args.columns and r["found"]:
            print("    " + ", ".join(r["columns"]))
    return 1 if bad else 0


def _cmd_list(args: argparse.Namespace) -> int:
    schema = _schema(args)
    df = list_transformers(read_tables(args.source, schema), schema)
    df = df[df["consumers"] >= args.min_consumers].head(args.top)
    print(df.to_string(index=False, float_format=lambda v: f"{v:.2f}"))
    return 0


def _cmd_build(args: argparse.Namespace) -> int:
    schema = _schema(args)
    options = BuildOptions(
        mv_kv=args.mv_kv, lv_kv=args.lv_kv, source_vm_pu=args.source_vm_pu, power_factor=args.power_factor,
        load_factor=args.load_factor, vk_percent=args.vk_percent, vkr_percent=args.vkr_percent,
        default_conductor=tuple(args.default_conductor) if args.default_conductor else None,
        include_service_drops=not args.no_service_drops,
        max_dropped_load_share=args.max_dropped_load_share, keep_ids=args.keep_ids)
    net, report = build_lv_network(read_tables(args.source, schema), args.transformer, options, schema)
    meta = save_network(net, report, args.out, source_label=args.source_label,
                        default_framework=args.default_framework)
    print(json.dumps({"counts": report.counts, "nominal_state": report.nominal_state,
                      "assumptions": report.assumptions, "warnings": report.warnings}, indent=2))
    print(f"wrote {args.out} and {meta}")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="gridsense-bdgd", description=__doc__.split("\n\n")[0])
    sub = p.add_subparsers(dest="command", required=True)

    def common(sp: argparse.ArgumentParser) -> None:
        sp.add_argument("source", help="Directory with one CSV/Parquet per BDGD layer, or a .gdb/.gpkg.")
        sp.add_argument("--schema", default=None, help="JSON file overriding layer/column names.")

    sp = sub.add_parser("inspect", help="Check that the layers and columns the converter needs exist.")
    common(sp)
    sp.add_argument("--columns", action="store_true", help="Also print each layer's columns.")
    sp.set_defaults(func=_cmd_inspect)

    sp = sub.add_parser("list", help="List distribution transformers with their LV network size.")
    common(sp)
    sp.add_argument("--min-consumers", type=int, default=10)
    sp.add_argument("--top", type=int, default=30)
    sp.set_defaults(func=_cmd_list)

    sp = sub.add_parser("build", help="Convert the LV network of one transformer.")
    common(sp)
    sp.add_argument("--transformer", required=True, help="Transformer id (COD_ID).")
    sp.add_argument("--out", required=True, help="Output pandapower JSON (keep it under data/, git-ignored).")
    sp.add_argument("--source-label", required=True,
                    help='Origin of the data for the citation, e.g. "BDGD 2023, distributor X".')
    sp.add_argument("--mv-kv", type=float, required=True, help="Primary voltage, kV line-to-line.")
    sp.add_argument("--lv-kv", type=float, default=0.38, help="Secondary voltage, kV line-to-line.")
    sp.add_argument("--source-vm-pu", type=float, default=1.0)
    sp.add_argument("--power-factor", type=float, default=0.92)
    sp.add_argument("--load-factor", type=float, default=None,
                    help="Mean / peak demand for consumers without a typical load curve (no default).")
    sp.add_argument("--vk-percent", type=float, default=None)
    sp.add_argument("--vkr-percent", type=float, default=None)
    sp.add_argument("--default-conductor", type=float, nargs=3, default=None, metavar=("R", "X", "AMPS"),
                    help="Used for branches whose conductor code is not in the catalogue.")
    sp.add_argument("--no-service-drops", action="store_true")
    sp.add_argument("--max-dropped-load-share", type=float, default=0.05)
    sp.add_argument("--default-framework", default="prodist_m8_bt")
    sp.add_argument("--keep-ids", action="store_true", help="Keep BDGD identifiers as bus names (local use only).")
    sp.set_defaults(func=_cmd_build)

    args = p.parse_args(argv)
    try:
        return args.func(args)
    except (BdgdReadError, BdgdBuildError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
