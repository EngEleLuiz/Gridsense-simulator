"""Runs one or more hosting-capacity methodologies against a network
and writes the results as partitioned Parquet files, following the
same schema-on-read Bronze pattern as ingestion/bronze_consumer.py:
raw JSON payload + run metadata now, typed parsing deferred to a dbt
staging model later.

Each method runs on its OWN fresh network instance, so one method's
sgens (e.g. deterministic's hosting_capacity_pv_* or stochastic's
hosting_capacity_mc_pv_*) never leak into another method's power flow.

Run with:
    python scripts/run_hosting_capacity_study.py --network cigre_lv \
        --methods deterministic stochastic qsts -v
"""

from __future__ import annotations

import argparse
import json
import logging
import uuid
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

import pandapower.networks as pn
import pyarrow as pa
import pyarrow.parquet as pq

from gridsense_sim.hosting_capacity import (
    find_hosting_capacity_deterministic,
    find_hosting_capacity_qsts,
    run_monte_carlo,
)
from gridsense_sim.hosting_capacity.qsts import DEFAULT_STEPS_PER_DAY, DEFAULT_TOTAL_STEPS

logger = logging.getLogger("gridsense_sim.run_hosting_capacity_study")

HC_BRONZE_SCHEMA = pa.schema(
    [
        ("network", pa.string()),
        ("method", pa.string()),
        ("run_id", pa.string()),
        ("run_timestamp", pa.string()),
        ("raw_value", pa.string()),
    ]
)

SUPPORTED_NETWORKS = {
    "case14": pn.case14,
    "case39": pn.case39,
    "case57": pn.case57,
    "case118": pn.case118,
    "cigre_lv": pn.create_cigre_network_lv,
}


def _stochastic_summary(result, include_trials: bool = False) -> dict:
    """Stochastic returns a distribution, not one number -- summarize
    it to p50/p95 so the mart can compare it against the single-number
    deterministic/QSTS results without the row size exploding (500
    trials x N buses each is large). Full trials are opt-in.
    """
    summary = {
        "network_name": result.network_name,
        "n_trials": len(result.trials),
        "violation_rate": result.violation_rate,
        "hosting_capacity_mw_p50": result.hosting_capacity_mw(0.5),
        "hosting_capacity_mw_p95": result.hosting_capacity_mw(0.95),
    }
    if include_trials:
        summary["trials"] = [asdict(t) for t in result.trials]
    return summary


def build_records(
    network_name: str,
    methods: list[str],
    run_id: str,
    run_timestamp: str,
    deterministic_kwargs: dict,
    stochastic_kwargs: dict,
    qsts_kwargs: dict,
    include_stochastic_trials: bool,
) -> list[dict]:
    """Runs the requested methods and returns Bronze-shaped rows ready
    for write_records_to_parquet.
    """
    records: list[dict] = []

    if "deterministic" in methods:
        net = SUPPORTED_NETWORKS[network_name]()
        result = find_hosting_capacity_deterministic(net, network_name, **deterministic_kwargs)
        records.append(
            {
                "network": network_name,
                "method": "deterministic",
                "run_id": run_id,
                "run_timestamp": run_timestamp,
                "raw_value": json.dumps(asdict(result)),
            }
        )
        logger.info(
            "deterministic: lambda_max=%.4f total_pv_mw=%.4f binding=%s",
            result.lambda_max,
            result.total_pv_mw,
            result.binding_constraint,
        )

    if "stochastic" in methods:
        net = SUPPORTED_NETWORKS[network_name]()
        result = run_monte_carlo(net, network_name, **stochastic_kwargs)
        summary = _stochastic_summary(result, include_trials=include_stochastic_trials)
        records.append(
            {
                "network": network_name,
                "method": "stochastic",
                "run_id": run_id,
                "run_timestamp": run_timestamp,
                "raw_value": json.dumps(summary),
            }
        )
        logger.info(
            "stochastic: p50=%.4f p95=%.4f violation_rate=%.2f",
            summary["hosting_capacity_mw_p50"],
            summary["hosting_capacity_mw_p95"],
            summary["violation_rate"],
        )

    if "qsts" in methods:
        net = SUPPORTED_NETWORKS[network_name]()
        result = find_hosting_capacity_qsts(net, network_name, **qsts_kwargs)
        records.append(
            {
                "network": network_name,
                "method": "qsts",
                "run_id": run_id,
                "run_timestamp": run_timestamp,
                "raw_value": json.dumps(asdict(result)),
            }
        )
        logger.info(
            "qsts: lambda_max=%.4f total_pv_mw=%.4f total_steps=%d binding=%s",
            result.lambda_max,
            result.total_pv_mw,
            result.total_steps,
            result.binding_constraint,
        )

    return records


def write_records_to_parquet(records: list[dict], output_dir: str | Path) -> Path | None:
    """Partition layout:
    {output_dir}/network={network}/method={method}/part-{ts_ms}.parquet

    Returns the path of the last file written, or None if `records`
    was empty.
    """
    if not records:
        return None

    output_dir = Path(output_dir)
    by_partition: dict[tuple[str, str], list[dict]] = {}
    for record in records:
        by_partition.setdefault((record["network"], record["method"]), []).append(record)

    last_path = None
    for (network, method), rows in by_partition.items():
        partition_dir = output_dir / f"network={network}" / f"method={method}"
        partition_dir.mkdir(parents=True, exist_ok=True)
        file_path = partition_dir / f"part-{int(datetime.now(timezone.utc).timestamp() * 1000)}.parquet"
        table = pa.Table.from_pylist(rows, schema=HC_BRONZE_SCHEMA)
        pq.write_table(table, file_path)
        last_path = file_path
        logger.info("Wrote %d record(s) to %s", len(rows), file_path)

    return last_path


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run hosting-capacity methodologies and write results as Bronze-pattern Parquet."
    )
    parser.add_argument("--network", required=True, choices=sorted(SUPPORTED_NETWORKS))
    parser.add_argument(
        "--methods",
        nargs="+",
        default=["deterministic", "stochastic", "qsts"],
        choices=["deterministic", "stochastic", "qsts"],
    )
    parser.add_argument("--output-dir", default="data/hosting_capacity")
    parser.add_argument(
        "--tolerance",
        type=float,
        default=0.01,
        help="Bisection tolerance for deterministic/qsts, in lambda_ units.",
    )
    parser.add_argument("--mc-n-trials", type=int, default=500)
    parser.add_argument(
    "--mc-max-pv-mw-per-bus",
    type=float,
    default=0.02,
    help=(
        "Upper bound of the per-bus uniform PV sampling range. WARNING: "
        "the default is not calibrated against any given network's real "
        "hosting-capacity ceiling -- a first cigre_lv run (Sept/2026) "
        "showed 0/500 trials violating anything at this default, meaning "
        "it sampled well below where the network actually breaks. Pass "
        "a value informed by a prior --methods deterministic run on the "
        "same network (see that run's total_pv_mw / number of load "
        "buses) if you want a stochastic result comparable to the "
        "other methods. See stochastic.py's module docstring."
    ),
)
    parser.add_argument(
        "--mc-include-trials",
        action="store_true",
        help="Store every Monte Carlo trial in raw_value, not just the p50/p95 "
        "summary (makes the row much larger; off by default).",
    )
    parser.add_argument(
        "--qsts-total-steps",
        type=int,
        default=DEFAULT_TOTAL_STEPS,
        help=(
            f"Default is {DEFAULT_TOTAL_STEPS} (60 days @ {DEFAULT_STEPS_PER_DAY} "
            f"steps/day). Pass 288*365 for the full annual horizon used in the "
            f"dissertation's final Chapter 5 numbers -- see qsts.py's module "
            f"docstring for the cost of doing that before running it."
        ),
    )
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    run_id = str(uuid.uuid4())
    run_timestamp = datetime.now(timezone.utc).isoformat()

    records = build_records(
        network_name=args.network,
        methods=args.methods,
        run_id=run_id,
        run_timestamp=run_timestamp,
        deterministic_kwargs={"tolerance": args.tolerance},
        stochastic_kwargs={
            "n_trials": args.mc_n_trials,
            "max_pv_mw_per_bus": args.mc_max_pv_mw_per_bus,
            "seed": args.seed,
        },
        qsts_kwargs={
            "total_steps": args.qsts_total_steps,
            "tolerance": args.tolerance,
            "profile_seed": args.seed,
        },
        include_stochastic_trials=args.mc_include_trials,
    )
    write_records_to_parquet(records, args.output_dir)


if __name__ == "__main__":
    main()
