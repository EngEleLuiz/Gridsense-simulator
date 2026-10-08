"""``gridsense-data``: list, quality-check and turn real datasets into QSTS series.

Examples::

    gridsense-data list
    gridsense-data qc inmet --opt station=A806 --opt year=2023
    gridsense-data qc ausgrid --source-file "data/raw/ausgrid/2012-2013 Solar home electricity data v2.csv"
    gridsense-data build-series --load ausgrid --pv inmet --pv-opt station=A806 --pv-opt year=2023 \\
        --site florianopolis --start 2023-01-09 --days 28 --steps-per-day 96 \\
        --out data/series/flo_ausgrid_inmet_2023-01.parquet

The series file is then passed to the hosting-capacity runner with
``--qsts-series``. Set ``GRIDSENSE_OFFLINE=1`` to forbid downloads and
``GRIDSENSE_DATA_DIR`` to relocate the raw-data cache.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys

from .base import DatasetError, Site
from .cache import DataCache
from .registry import DATASETS, load_dataset, parse_options
from .series import PRESET_SITES, build_series, save_series

logger = logging.getLogger("gridsense_sim.datasets")


def _site(value: str) -> Site:
    if value in PRESET_SITES:
        return PRESET_SITES[value]
    try:
        lat, lon, *rest = (float(x) for x in value.split(","))
    except ValueError:
        raise argparse.ArgumentTypeError(
            f"--site must be one of {sorted(PRESET_SITES)} or 'lat,lon[,utc_offset_h]'") from None
    offset = rest[0] if rest else round(lon / 15.0)
    return Site(f"site {lat:.4f},{lon:.4f}", lat, lon, offset, None)


def _cmd_list(_: argparse.Namespace) -> int:
    for key, info in DATASETS.items():
        print(f"{key:11s} {', '.join(info.provides):10s} {info.resolution:7s} {info.coverage}")
    return 0


def _cmd_info(args: argparse.Namespace) -> int:
    print(json.dumps(DATASETS[args.dataset].to_dict(), indent=2))
    return 0


def _cmd_qc(args: argparse.Namespace) -> int:
    ps = load_dataset(args.dataset, DataCache(offline=args.offline or None),
                      args.source_file, **parse_options(args.opt))
    if args.json:
        print(json.dumps({"profile_set": ps.describe(), "quality": ps.quality.to_dict()},
                         indent=2, default=str))
    else:
        d = ps.describe()
        print(f"{d['dataset']}: {d['kind']}, {d['n_series']} series x {d['n_steps']} steps "
              f"@ {d['resolution']}, {d['start_utc']} .. {d['end_utc']}")
        for s in d["sources"]:
            print(f"  source {s['path']} sha256={s['sha256'][:16]}...")
        print(ps.quality.summary())
    return 1 if ps.quality.has_errors else 0


def _cmd_build(args: argparse.Namespace) -> int:
    cache = DataCache(offline=args.offline or None)
    load = load_dataset(args.load, cache, args.load_file, **parse_options(args.load_opt))
    pv = load_dataset(args.pv, cache, args.pv_file, **parse_options(args.pv_opt))
    real = build_series(
        load, pv, target=args.site, start=args.start, days=args.days,
        steps_per_day=args.steps_per_day, peak_load_mult=args.peak_load,
        normalize=args.normalize, n_load_series=args.n_load_series, seed=args.seed,
        load_source_start=args.load_source_start, pv_source_start=args.pv_source_start,
        upsample=args.upsample, allow_quality_errors=args.allow_quality_errors,
    )
    path = save_series(real, args.out)
    diag = real.manifest["diagnostics"]
    print(f"wrote {path} ({real.series.n_steps} steps, source={real.series.source})")
    print(f"manifest sha256 {real.manifest_sha256}")
    print(f"load peak hour {diag['load_mean_peak_hour']}h, PV peak hour {diag['pv_mean_peak_hour']}h, "
          f"load at PV peak = {diag['load_at_pv_peak_hour_over_load_peak']:.2f} x load peak, "
          f"PV capacity factor {diag['pv_capacity_factor']:.3f}")
    for w in real.manifest["warnings"]:
        print(f"WARNING {w}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="gridsense-data", description=__doc__.split("\n\n")[0])
    p.add_argument("-v", "--verbose", action="store_true")
    p.add_argument("--offline", action="store_true", help="never download (same as GRIDSENSE_OFFLINE=1)")
    sub = p.add_subparsers(dest="command", required=True)

    sub.add_parser("list", help="list the registered datasets").set_defaults(func=_cmd_list)
    pi = sub.add_parser("info", help="citation, licence and notes of a dataset")
    pi.add_argument("dataset", choices=sorted(DATASETS))
    pi.set_defaults(func=_cmd_info)

    pq = sub.add_parser("qc", help="load a dataset and print its quality report")
    pq.add_argument("dataset", choices=sorted(DATASETS))
    pq.add_argument("--source-file", help="use a manually downloaded raw file")
    pq.add_argument("--opt", action="append", metavar="KEY=VALUE", help="loader option (repeatable)")
    pq.add_argument("--json", action="store_true")
    pq.set_defaults(func=_cmd_qc)

    pb = sub.add_parser("build-series", help="build a QSTS series (Parquet + manifest)")
    pb.add_argument("--load", required=True, choices=sorted(k for k, i in DATASETS.items() if "load" in i.provides))
    pb.add_argument("--load-file")
    pb.add_argument("--load-opt", action="append", metavar="KEY=VALUE")
    pb.add_argument("--pv", required=True,
                    choices=sorted(k for k, i in DATASETS.items() if {"pv", "irradiance"} & set(i.provides)))
    pb.add_argument("--pv-file")
    pb.add_argument("--pv-opt", action="append", metavar="KEY=VALUE")
    pb.add_argument("--site", type=_site, default=PRESET_SITES["florianopolis"],
                    help=f"study site: {', '.join(sorted(PRESET_SITES))} or 'lat,lon[,utc_offset_h]'")
    pb.add_argument("--start", required=True, help="first study day, YYYY-MM-DD (local)")
    pb.add_argument("--days", type=int, required=True)
    pb.add_argument("--steps-per-day", type=int, default=96)
    pb.add_argument("--peak-load", type=float, default=1.0, help="multiplier the normalisation peak maps to")
    pb.add_argument("--normalize", choices=("source_peak", "window_peak"), default="source_peak")
    pb.add_argument("--n-load-series", type=int, default=None, help="random subset of homes (default all)")
    pb.add_argument("--seed", type=int, default=42)
    pb.add_argument("--load-source-start")
    pb.add_argument("--pv-source-start")
    pb.add_argument("--upsample", choices=("hold", "linear"), default="hold")
    pb.add_argument("--allow-quality-errors", action="store_true")
    pb.add_argument("--out", required=True)
    pb.set_defaults(func=_cmd_build)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING,
                        format="%(levelname)s %(name)s: %(message)s")
    try:
        return args.func(args)
    except DatasetError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
