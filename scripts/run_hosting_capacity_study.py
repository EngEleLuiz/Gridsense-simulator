"""Run hosting-capacity methodologies and write Bronze-pattern Parquet.

Output layout (unchanged)::

    {output_dir}/network={network}/method={method}/part-{ts_ms}.parquet

with columns ``network, method, run_id, run_timestamp, raw_value``
(``raw_value`` = JSON, schema-on-read by
``transform/models/staging/stg_hosting_capacity_results.sql``).

Payload schema v2 (this review)
-------------------------------
Every ``raw_value`` now carries the same envelope:

``schema_version`` (2), ``status`` (``"ok"`` or an error code such as
``"baseline_infeasible"`` / ``"no_daylight"``), ``error``,
``lambda_max``, ``lambda_fail``, ``bounded``, ``total_pv_mw``,
``binding_constraint``, ``conditions`` (criterion + operating point),
``params`` (every argument that shaped the number) and
``provenance`` (git commit, dirty flag, library versions).

A failed method is **recorded**, not crashed on: "PRODIST at nominal
load is infeasible before any PV" is a result the dissertation needs.
Any other exception in one method (review finding R08) is recorded as
``status = "error"`` with its type and message, so the methods that did
finish are still written; the script then exits with code 1.

Arguments are validated before any power flow runs (finding R09).

For ``stochastic``, ``lambda_max``/``total_pv_mw`` hold ``F^-1(alpha)``
(``alpha`` recorded), i.e. the conservative stochastic HC; the legacy
budget-bound estimator is no longer written by this script.

Run with::

    python scripts/run_hosting_capacity_study.py --network cigre_lv \\
        --framework prodist_m8_bt --methods deterministic stochastic -v
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import sys
import uuid
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import pandapower as pp
import pandapower.networks as pn
import pyarrow as pa
import pyarrow.parquet as pq

from gridsense_sim.hosting_capacity import (
    DEFAULT_CRITICAL_LOAD_SCALE,
    limits_for,
    FRAMEWORKS,
    AdoptionModel,
    HostingCapacityError,
    estimate_hosting_capacity_stochastic,
    min_samples_for_ci,
    find_hosting_capacity_deterministic,
    find_hosting_capacity_qsts,
)
from gridsense_sim.hosting_capacity.qsts import DEFAULT_STEPS_PER_DAY, DEFAULT_TOTAL_STEPS
from gridsense_sim.provenance import collect_provenance

logger = logging.getLogger("gridsense_sim.run_hosting_capacity_study")

PAYLOAD_SCHEMA_VERSION = 2
METHODS: tuple[str, ...] = ("deterministic", "stochastic", "qsts")

HC_BRONZE_SCHEMA = pa.schema(
    [
        ("network", pa.string()),
        ("method", pa.string()),
        ("run_id", pa.string()),
        ("run_timestamp", pa.string()),
        ("raw_value", pa.string()),
    ]
)

SUPPORTED_NETWORKS: dict[str, Callable[[], pp.pandapowerNet]] = {
    "case14": pn.case14,
    "case39": pn.case39,
    "case57": pn.case57,
    "case118": pn.case118,
    "cigre_lv": pn.create_cigre_network_lv,
}


def _finite(x: float | None) -> float | None:
    """JSON-safe float: ``inf``/``nan`` -> ``None``."""
    if x is None or not math.isfinite(x):
        return None
    return float(x)


def _envelope(method: str, network: str, params: dict[str, Any], provenance: dict) -> dict:
    return {
        "schema_version": PAYLOAD_SCHEMA_VERSION,
        "method": method,
        "network_name": network,
        "status": "ok",
        "error": None,
        "params": params,
        "provenance": provenance,
    }


def _run_deterministic(net: pp.pandapowerNet, network: str, kw: dict) -> dict:
    r = find_hosting_capacity_deterministic(net, network, **kw)
    return {
        "lambda_max": r.lambda_max,
        "lambda_fail": r.lambda_fail,
        "bounded": r.bounded,
        "total_pv_mw": r.total_pv_mw if r.bounded else None,
        "binding_constraint": r.binding_constraint,
        "conditions": asdict(r.conditions),
        "iterations": r.iterations,
        "reverse_power_flow_at_hc": r.reverse_power_flow_at_hc,
        "pv_mw_per_bus": r.pv_mw_per_bus,
    }


def _run_stochastic(
    net: pp.pandapowerNet, network: str, kw: dict, alpha: float, include_scenarios: bool
) -> dict:
    est = estimate_hosting_capacity_stochastic(net, network, **kw)
    q = est.hc_lambda(alpha)
    p50 = est.hc_lambda(0.5)
    lam = _finite(q.point)
    bindings: dict[str, int] = {}
    for s in est.scenarios:
        key = (s.binding_constraint or "unbounded").split(" (")[0]
        bindings[key] = bindings.get(key, 0) + 1
    out = {
        "lambda_max": lam,
        "lambda_fail": None,
        "bounded": lam is not None,
        "total_pv_mw": None if lam is None else round(lam * est.total_nominal_load_mw, 6),
        "binding_constraint": None,
        "conditions": asdict(est.conditions),
        "alpha": alpha,
        "hc_lambda_ci_low": _finite(q.low),
        "hc_lambda_ci_high": _finite(q.high),
        "ci_confidence": q.confidence,
        "ci_achieved_coverage": q.achieved_coverage,
        "hc_lambda_p50": _finite(p50.point),
        "hc_mw_p50": None if _finite(p50.point) is None
        else round(p50.point * est.total_nominal_load_mw, 6),
        "total_nominal_load_mw": est.total_nominal_load_mw,
        "n_scenarios": len(est.scenarios),
        "n_censored": est.n_censored,
        "power_flows": est.power_flows,
        "binding_constraint_counts": bindings,
        "adoption_model": asdict(est.adoption_model),
    }
    if include_scenarios:
        out["scenarios"] = [
            {**asdict(s), "lambda_critical": _finite(s.lambda_critical)} for s in est.scenarios
        ]
    return out


def _run_qsts(net: pp.pandapowerNet, network: str, kw: dict) -> dict:
    r = find_hosting_capacity_qsts(net, network, **kw)
    return {
        "lambda_max": r.lambda_max,
        "lambda_fail": r.lambda_fail,
        "bounded": r.bounded,
        "total_pv_mw": r.total_pv_mw if r.bounded else None,
        "binding_constraint": r.binding_constraint,
        "conditions": asdict(r.conditions),
        "criterion": r.criterion,
        "first_violating_step": r.first_violating_step,
        "total_steps": r.total_steps,
        "steps_per_day": r.steps_per_day,
        "daylight_steps": r.daylight_steps,
        "power_flows": r.power_flows,
    }


def build_records(
    network_name: str,
    methods: list[str],
    run_id: str,
    run_timestamp: str,
    deterministic_kwargs: dict,
    stochastic_kwargs: dict,
    qsts_kwargs: dict,
    stochastic_alpha: float = 0.10,
    include_stochastic_scenarios: bool = False,
    network_factory: Callable[[], pp.pandapowerNet] | None = None,
) -> list[dict]:
    """Run the requested methods; one Bronze row per method.

    Each method gets a fresh network instance (the estimators also never
    mutate their input, so this is belt-and-braces). A
    :class:`HostingCapacityError` is serialized as ``status``.
    """
    factory = network_factory or SUPPORTED_NETWORKS[network_name]
    provenance = collect_provenance().to_dict()
    runners: dict[str, tuple[dict, Callable[[pp.pandapowerNet], dict]]] = {
        "deterministic": (
            deterministic_kwargs,
            lambda n: _run_deterministic(n, network_name, deterministic_kwargs),
        ),
        "stochastic": (
            {**stochastic_kwargs, "alpha": stochastic_alpha},
            lambda n: _run_stochastic(
                n, network_name, stochastic_kwargs, stochastic_alpha, include_stochastic_scenarios
            ),
        ),
        "qsts": (qsts_kwargs, lambda n: _run_qsts(n, network_name, qsts_kwargs)),
    }

    records: list[dict] = []
    for method in methods:
        params, run = runners[method]
        payload = _envelope(method, network_name, _jsonable(params), provenance)
        try:
            payload.update(run(factory()))
        except HostingCapacityError as exc:
            payload.update(
                status=exc.status,
                error=str(exc),
                bounded=False,
                conditions=_failed_conditions(network_name, params, method),
            )
            logger.warning("%s: %s", method, exc)
        except Exception as exc:  # noqa: BLE001 -- record, never lose sibling results
            payload.update(
                status="error",
                error=f"{type(exc).__name__}: {exc}",
                bounded=False,
                conditions=_failed_conditions(network_name, params, method),
            )
            logger.exception("%s failed; recorded as status='error'", method)
        else:
            logger.info(
                "%s: lambda_max=%s total_pv_mw=%s binding=%s",
                method, payload.get("lambda_max"), payload.get("total_pv_mw"),
                payload.get("binding_constraint"),
            )
        records.append(
            {
                "network": network_name,
                "method": method,
                "run_id": run_id,
                "run_timestamp": run_timestamp,
                "raw_value": json.dumps(payload, allow_nan=False, default=_json_default),
            }
        )
    return records


def _failed_conditions(network: str, params: dict, method: str) -> dict:
    """Criterion and operating point of a failed run, so the failure is
    still attributable (e.g. "PRODIST at nominal load: infeasible")."""
    try:
        lim = limits_for(network, params.get("framework"))
    except ValueError:
        return {"network_name": network, "framework": params.get("framework")}
    return {
        "network_name": network,
        "framework": lim.framework,
        "criterion_kind": lim.kind.value,
        "v_min_pu": lim.v_min_pu,
        "v_max_pu": lim.v_max_pu,
        "load_scale": None if method == "qsts" else params.get("load_scale"),
    }


def _json_default(obj: Any) -> Any:
    if hasattr(obj, "value"):  # Enum
        return obj.value
    if hasattr(obj, "item"):  # numpy scalar
        return obj.item()
    raise TypeError(f"Not JSON serializable: {type(obj)!r}")


def _jsonable(params: dict) -> dict:
    out: dict[str, Any] = {}
    for k, v in params.items():
        if isinstance(v, AdoptionModel):
            out[k] = asdict(v)
        elif hasattr(v, "__dataclass_fields__"):
            out[k] = asdict(v)
        else:
            out[k] = v
    return out


def write_records_to_parquet(records: list[dict], output_dir: str | Path) -> Path | None:
    """Write records partitioned by network/method; return the last file path."""
    if not records:
        return None
    output_dir = Path(output_dir)
    by_partition: dict[tuple[str, str], list[dict]] = {}
    for record in records:
        by_partition.setdefault((record["network"], record["method"]), []).append(record)
    last_path: Path | None = None
    for (network, method), rows in by_partition.items():
        partition_dir = output_dir / f"network={network}" / f"method={method}"
        partition_dir.mkdir(parents=True, exist_ok=True)
        ts_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        file_path = partition_dir / f"part-{ts_ms}-{uuid.uuid4().hex[:8]}.parquet"
        pq.write_table(pa.Table.from_pylist(rows, schema=HC_BRONZE_SCHEMA), file_path)
        last_path = file_path
        logger.info("Wrote %d record(s) to %s", len(rows), file_path)
    return last_path


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--network", required=True, choices=sorted(SUPPORTED_NETWORKS))
    p.add_argument("--methods", nargs="+", default=list(METHODS), choices=METHODS)
    p.add_argument("--framework", default=None, choices=sorted(FRAMEWORKS),
                   help="Voltage framework; default = network default (see limits.py).")
    p.add_argument("--load-scale", type=float, default=DEFAULT_CRITICAL_LOAD_SCALE,
                   help="Load multiplier at the critical operating point "
                        "(deterministic/stochastic). 1.0 reproduces the old nominal-load number.")
    p.add_argument("--output-dir", default="data/hosting_capacity")
    p.add_argument("--tolerance", type=float, default=0.01)
    p.add_argument("--mc-n-scenarios", type=int, default=60)
    p.add_argument("--mc-alpha", type=float, default=0.10,
                   help="Risk level: stochastic HC = F^-1(alpha) of the critical penetration.")
    p.add_argument("--mc-adoption-min", type=float, default=0.3)
    p.add_argument("--mc-adoption-max", type=float, default=1.0)
    p.add_argument("--mc-size-dispersion", type=float, default=0.5)
    p.add_argument("--mc-include-scenarios", action="store_true")
    p.add_argument("--qsts-total-steps", type=int, default=DEFAULT_TOTAL_STEPS)
    p.add_argument("--qsts-steps-per-day", type=int, default=DEFAULT_STEPS_PER_DAY)
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args(argv)
    _validate_args(p, args)
    return args


def _validate_args(parser: argparse.ArgumentParser, args: argparse.Namespace) -> None:
    """Reject invalid arguments before any power flow runs (finding R09)."""
    checks = [
        (args.load_scale > 0, "--load-scale must be > 0"),
        (args.tolerance > 0, "--tolerance must be > 0"),
        (0 < args.mc_alpha < 1, "--mc-alpha must be in (0, 1)"),
        (args.mc_n_scenarios >= 1, "--mc-n-scenarios must be >= 1"),
        (0 < args.mc_adoption_min <= args.mc_adoption_max <= 1,
         "--mc-adoption-min/max must satisfy 0 < min <= max <= 1"),
        (0 <= args.mc_size_dispersion < 1, "--mc-size-dispersion must be in [0, 1)"),
        (args.qsts_total_steps >= 1, "--qsts-total-steps must be >= 1"),
        (args.qsts_steps_per_day >= 1, "--qsts-steps-per-day must be >= 1"),
    ]
    for ok, message in checks:
        if not ok:
            parser.error(message)


def _warn_underpowered(args: argparse.Namespace) -> None:
    if "stochastic" not in args.methods:
        return
    needed = min_samples_for_ci(args.mc_alpha)
    if args.mc_n_scenarios < needed:
        logger.warning(
            "--mc-n-scenarios %d < %d: no 95%% CI for the %.0f%% quantile; the "
            "stochastic row will be recorded but not comparable.",
            args.mc_n_scenarios, needed, 100 * args.mc_alpha,
        )


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    _warn_underpowered(args)
    common = {"framework": args.framework, "tolerance": args.tolerance}
    records = build_records(
        network_name=args.network,
        methods=args.methods,
        run_id=str(uuid.uuid4()),
        run_timestamp=datetime.now(timezone.utc).isoformat(),
        deterministic_kwargs={**common, "load_scale": args.load_scale},
        stochastic_kwargs={
            **common,
            "load_scale": args.load_scale,
            "n_scenarios": args.mc_n_scenarios,
            "seed": args.seed,
            "adoption_model": AdoptionModel(
                adoption_fraction=(args.mc_adoption_min, args.mc_adoption_max),
                size_dispersion=args.mc_size_dispersion,
            ),
        },
        qsts_kwargs={
            **common,
            "total_steps": args.qsts_total_steps,
            "steps_per_day": args.qsts_steps_per_day,
            "profile_seed": args.seed,
        },
        stochastic_alpha=args.mc_alpha,
        include_stochastic_scenarios=args.mc_include_scenarios,
    )
    write_records_to_parquet(records, args.output_dir)
    failed = [r["method"] for r in records if json.loads(r["raw_value"])["status"] == "error"]
    if failed:
        logger.error("Method(s) %s failed with an unexpected error (rows written).", failed)
        sys.exit(1)


if __name__ == "__main__":
    main()
