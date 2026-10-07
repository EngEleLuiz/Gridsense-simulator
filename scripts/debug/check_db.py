"""Ad-hoc sanity checks against the TimescaleDB warehouse.

Replaces the former root-level ``check_data.py``, ``check_gold.py`` and
``check_violation.py``. The connection URL comes from ``GRIDSENSE_DB_URL``
(or ``--db-url``), never from a literal in the code.

Usage (from the repository root):
    python scripts/debug/check_db.py kpis
    python scripts/debug/check_db.py tables
    python scripts/debug/check_db.py violations --network case14
"""

from __future__ import annotations

import argparse
import os

import psycopg2

DEFAULT_DB_URL = "postgresql://postgres:postgres@localhost:5432/gridsense"


def _kpis(cur, _args) -> None:
    cur.execute("SELECT count(*) FROM public_gold.mart_grid_kpis_daily")
    print("daily rows:", cur.fetchone()[0])
    cur.execute("SELECT DISTINCT network FROM public_gold.mart_grid_kpis_daily ORDER BY 1")
    print("networks:", [r[0] for r in cur.fetchall()])


def _tables(cur, _args) -> None:
    cur.execute(
        "SELECT schemaname, tablename FROM pg_tables "
        "WHERE schemaname LIKE '%%gold%%' OR schemaname LIKE '%%silver%%' ORDER BY 1, 2"
    )
    for schema, table in cur.fetchall():
        print(f"{schema}.{table}")


def _violations(cur, args) -> None:
    cur.execute(
        """
        SELECT bus_id, avg(violation_rate_pct) AS rate
        FROM public_gold.mart_voltage_quality_hourly
        WHERE network = %s AND is_in_scope
        GROUP BY bus_id
        ORDER BY rate DESC NULLS LAST
        """,
        (args.network,),
    )
    for bus, rate in cur.fetchall():
        print(f"bus {bus:>4}: {rate:6.2f}% in-scope readings out of band")


COMMANDS = {"kpis": _kpis, "tables": _tables, "violations": _violations}


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("command", choices=sorted(COMMANDS))
    p.add_argument("--network", default="case14")
    p.add_argument("--db-url", default=os.getenv("GRIDSENSE_DB_URL", DEFAULT_DB_URL))
    args = p.parse_args()
    with psycopg2.connect(args.db_url) as conn, conn.cursor() as cur:
        COMMANDS[args.command](cur, args)


if __name__ == "__main__":
    main()
