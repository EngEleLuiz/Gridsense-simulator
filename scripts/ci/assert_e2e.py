"""CI helper: assert the end-to-end data contract after `dbt build`.

Checks things no unit test can see, because they only exist once the
real runner, loader and dbt models have run against a real database:

* Bronze telemetry and hosting-capacity rows actually landed;
* every HC method produced at least one comparable row;
* a failed study (PRODIST at nominal load) is kept as a status row and
  is never exposed as a capacity;
* an under-powered stochastic study (no finite CI) is kept for audit but
  never flagged comparable;
* Silver/Gold are populated for every simulated network;
* when TimescaleDB is present, the hypertables were really created.

Usage:
    python scripts/ci/assert_e2e.py --db-url postgresql://postgres:postgres@localhost:5432/gridsense
"""

from __future__ import annotations

import argparse
import sys
from typing import Callable

import psycopg2

CHECKS: list[tuple[str, str, Callable[[list], bool]]] = [
    (
        "bronze telemetry loaded",
        "select count(*) from bronze.raw_events",
        lambda r: r[0][0] > 0,
    ),
    (
        "every HC method has a comparable row",
        "select method from public_gold.mart_hosting_capacity "
        "where is_comparable group by method",
        lambda r: {m for (m,) in r} >= {"deterministic", "stochastic", "qsts"},
    ),
    (
        "baseline_infeasible is recorded and never shown as capacity",
        "select count(*), count(total_pv_mw_comparable) "
        "from public_gold.mart_hosting_capacity where status = 'baseline_infeasible'",
        lambda r: r[0][0] >= 1 and r[0][1] == 0,
    ),
    (
        "comparable capacities are positive and finite",
        "select count(*) from public_gold.mart_hosting_capacity where is_comparable "
        "and not (total_pv_mw_comparable > 0 and total_pv_mw_comparable < 'Infinity')",
        lambda r: r[0][0] == 0,
    ),
    (
        "stochastic rows are comparable only with a finite CI (R05)",
        "select count(*) filter (where is_comparable and hc_lambda_ci_low is null), "
        "count(*) filter (where not is_comparable and status = 'ok' and hc_lambda_ci_low is null) "
        "from public_gold.mart_hosting_capacity where method = 'stochastic'",
        lambda r: r[0][0] == 0 and r[0][1] >= 1,
    ),
    (
        "silver voltage facts exist for every simulated network",
        "select count(distinct network) from public_silver.fct_bus_voltage",
        lambda r: r[0][0] >= 2,
    ),
    (
        "no silver row with NULL voltage limits (seed covers every network)",
        "select count(*) from public_silver.fct_bus_voltage where network_v_min_pu is null",
        lambda r: r[0][0] == 0,
    ),
    (
        "slack/generator buses are out of scope and never flagged (R07)",
        "select count(*) filter (where is_in_scope), count(*) filter (where is_voltage_violation) "
        "from public_silver.fct_bus_voltage where network = 'case14' and bus_id in (0, 1, 2, 5, 7)",
        lambda r: r[0][0] == 0 and r[0][1] == 0,
    ),
    (
        "gold daily KPIs populated",
        "select count(*) from public_gold.mart_grid_kpis_daily",
        lambda r: r[0][0] > 0,
    ),
]

TIMESCALE_CHECK = (
    "hypertables created (TimescaleDB present)",
    "select hypertable_schema || '.' || hypertable_name "
    "from timescaledb_information.hypertables",
    lambda r: {"bronze.raw_events", "public_silver.fct_bus_voltage"} <= {h for (h,) in r},
)


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--db-url", required=True)
    p.add_argument("--require-timescale", action="store_true")
    args = p.parse_args()

    conn = psycopg2.connect(args.db_url)
    conn.autocommit = True
    failures = 0
    with conn.cursor() as cur:
        cur.execute("select 1 from pg_extension where extname = 'timescaledb'")
        has_ts = cur.fetchone() is not None
        checks = list(CHECKS)
        if has_ts:
            checks.append(TIMESCALE_CHECK)
        elif args.require_timescale:
            print("::error::TimescaleDB extension not installed but --require-timescale was set")
            failures += 1

        for name, sql, ok in checks:
            try:
                cur.execute(sql)
                rows = cur.fetchall()
                passed = bool(ok(rows))
            except psycopg2.Error as exc:
                rows, passed = str(exc).strip(), False
            print(f"{'PASS' if passed else 'FAIL'}  {name}  -> {rows}")
            if not passed:
                print(f"::error::E2E contract failed: {name}")
                failures += 1
    conn.close()
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
