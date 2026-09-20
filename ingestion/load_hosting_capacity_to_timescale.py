"""Hosting-capacity results loader.

Reads the partitioned Parquet files produced by
scripts/run_hosting_capacity_study.py and bulk-loads them into a
TimescaleDB table (bronze.hosting_capacity_results). Deliberately NOT
a hypertable, unlike bronze.raw_events: hosting-capacity studies are
run a handful of times, not streamed continuously, so time-based
partitioning has no benefit here and would just add overhead.

Idempotent upsert keyed on (network, method, run_id), so re-running
the loader after a partial run never creates duplicate rows.

Run with:
    python ingestion/load_hosting_capacity_to_timescale.py \
        --results-dir data/hosting_capacity \
        --db-url postgresql://postgres:postgres@localhost:5432/gridsense -v
"""

from __future__ import annotations

import argparse
import csv
import io
import logging
from pathlib import Path

import psycopg2
import pyarrow.parquet as pq

logger = logging.getLogger("gridsense_ingestion.load_hosting_capacity_to_timescale")

DDL = """
CREATE SCHEMA IF NOT EXISTS bronze;

CREATE TABLE IF NOT EXISTS bronze.hosting_capacity_results (
    network       TEXT        NOT NULL,
    method        TEXT        NOT NULL,
    run_id        TEXT        NOT NULL,
    run_timestamp TIMESTAMPTZ NOT NULL,
    raw_value     JSONB       NOT NULL,
    PRIMARY KEY (network, method, run_id)
);

CREATE INDEX IF NOT EXISTS idx_hosting_capacity_results_network_time
    ON bronze.hosting_capacity_results (network, run_timestamp DESC);
"""

# Staging table has no primary key, so COPY can never violate a
# constraint; the upsert into the real table happens afterwards.
STAGING_DDL = """
CREATE TEMP TABLE staging_hosting_capacity_results (
    network       TEXT,
    method        TEXT,
    run_id        TEXT,
    run_timestamp TIMESTAMPTZ,
    raw_value     TEXT
) ON COMMIT DROP;
"""

UPSERT_SQL = """
INSERT INTO bronze.hosting_capacity_results
    (network, method, run_id, run_timestamp, raw_value)
SELECT
    network, method, run_id, run_timestamp, raw_value::jsonb
FROM staging_hosting_capacity_results
ON CONFLICT (network, method, run_id) DO NOTHING;
"""


def iter_parquet_rows(results_dir: Path):
    """Yields dict rows from every Parquet file under `results_dir`."""
    parquet_files = sorted(results_dir.rglob("*.parquet"))
    logger.info("Found %d Parquet file(s) under %s", len(parquet_files), results_dir)
    for path in parquet_files:
        table = pq.read_table(path)
        for row in table.to_pylist():
            yield row


def load(results_dir: Path, db_url: str, batch_size: int = 1000) -> int:
    """Loads all hosting-capacity Parquet rows into TimescaleDB.
    Returns the number of new rows loaded.
    """
    conn = psycopg2.connect(db_url)
    conn.autocommit = False
    total = 0
    try:
        with conn.cursor() as cur:
            cur.execute(DDL)
        conn.commit()

        def flush(rows: list[dict]) -> int:
            if not rows:
                return 0
            buf = io.StringIO()
            writer = csv.writer(buf)
            for row in rows:
                writer.writerow(
                    [
                        row["network"],
                        row["method"],
                        row["run_id"],
                        row["run_timestamp"],
                        row["raw_value"],
                    ]
                )
            buf.seek(0)
            with conn.cursor() as cur:
                cur.execute(STAGING_DDL)
                cur.copy_expert(
                    "COPY staging_hosting_capacity_results "
                    "(network, method, run_id, run_timestamp, raw_value) "
                    "FROM STDIN WITH (FORMAT csv)",
                    buf,
                )
                cur.execute(UPSERT_SQL)
                inserted = cur.rowcount
            conn.commit()
            return inserted

        batch: list[dict] = []
        for row in iter_parquet_rows(results_dir):
            batch.append(row)
            if len(batch) >= batch_size:
                total += flush(batch)
                batch = []
        total += flush(batch)

    finally:
        conn.close()

    logger.info("Loaded %d new row(s) into bronze.hosting_capacity_results", total)
    return total


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Load hosting-capacity study Parquet results into TimescaleDB."
    )
    parser.add_argument("--results-dir", default="data/hosting_capacity")
    parser.add_argument(
        "--db-url",
        default="postgresql://postgres:postgres@localhost:5432/gridsense",
        help="SQLAlchemy-style Postgres/TimescaleDB connection URL.",
    )
    parser.add_argument("--batch-size", type=int, default=1000)
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    load(Path(args.results_dir), args.db_url, args.batch_size)


if __name__ == "__main__":
    main()
