"""Bronze-to-TimescaleDB loader.

Reads the partitioned Parquet files produced by `bronze_consumer.py`
and bulk-loads them, unmodified, into a TimescaleDB hypertable
(`bronze.raw_events`). This is the boundary between the streaming
world (Kafka -> Parquet) and the warehouse world (TimescaleDB + dbt):
everything downstream — Silver and Gold models — reads from this one
table.

Loading strategy: idempotent upsert keyed on (topic, kafka_partition,
kafka_offset) so re-running the loader (e.g. after a partial Kafka
consumer run) never creates duplicate rows, and CSV COPY is used for
bulk throughput.

Run with:
    python load_bronze_to_timescale.py \
        --bronze-dir data/bronze \
        --db-url postgresql://postgres:postgres@localhost:5432/gridsense
"""

from __future__ import annotations

import argparse
import csv
import io
import logging
from pathlib import Path

import psycopg2
import pyarrow.parquet as pq

logger = logging.getLogger("gridsense_ingestion.load_bronze_to_timescale")

DDL = """
CREATE SCHEMA IF NOT EXISTS bronze;

CREATE TABLE IF NOT EXISTS bronze.raw_events (
    topic               TEXT        NOT NULL,
    kafka_key           TEXT,
    kafka_partition     INTEGER     NOT NULL,
    kafka_offset        BIGINT      NOT NULL,
    ingestion_timestamp TIMESTAMPTZ NOT NULL,
    raw_value           JSONB       NOT NULL,
    -- TimescaleDB requires the partitioning column (ingestion_timestamp)
    -- to be part of any primary/unique key, since rows for the same
    -- logical key can live in different time-based chunks.
    PRIMARY KEY (topic, kafka_partition, kafka_offset, ingestion_timestamp)
);

-- Turns the table into a TimescaleDB hypertable, partitioned by time,
-- for efficient time-range queries and compression at scale. This is
-- a no-op (with a notice) if the timescaledb extension isn't
-- installed, so the loader still works against plain Postgres for
-- local testing.
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'timescaledb') THEN
        PERFORM create_hypertable(
            'bronze.raw_events', 'ingestion_timestamp',
            if_not_exists => TRUE,
            migrate_data => TRUE
        );
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_raw_events_topic_time
    ON bronze.raw_events (topic, ingestion_timestamp DESC);
"""

# Staging table has no primary key, so COPY can never violate a
# constraint; the upsert into the real table happens afterwards.
STAGING_DDL = """
CREATE TEMP TABLE staging_raw_events (
    topic               TEXT,
    kafka_key           TEXT,
    kafka_partition     INTEGER,
    kafka_offset        BIGINT,
    ingestion_timestamp TIMESTAMPTZ,
    raw_value           TEXT
) ON COMMIT DROP;
"""

UPSERT_SQL = """
INSERT INTO bronze.raw_events
    (topic, kafka_key, kafka_partition, kafka_offset, ingestion_timestamp, raw_value)
SELECT
    topic, kafka_key, kafka_partition, kafka_offset, ingestion_timestamp, raw_value::jsonb
FROM staging_raw_events
ON CONFLICT (topic, kafka_partition, kafka_offset, ingestion_timestamp) DO NOTHING;
"""


def iter_parquet_rows(bronze_dir: Path):
    """Yields dict rows from every Parquet file under `bronze_dir`."""
    parquet_files = sorted(bronze_dir.rglob("*.parquet"))
    logger.info("Found %d Parquet file(s) under %s", len(parquet_files), bronze_dir)
    for path in parquet_files:
        table = pq.read_table(path)
        for row in table.to_pylist():
            yield row


def load(bronze_dir: Path, db_url: str, batch_size: int = 5000) -> int:
    """Loads all Bronze Parquet rows into TimescaleDB. Returns row count loaded."""
    conn = psycopg2.connect(db_url)
    conn.autocommit = False
    total = 0
    try:
        with conn.cursor() as cur:
            cur.execute(DDL)
        conn.commit()

        batch: list[dict] = []

        def flush(rows: list[dict]) -> int:
            if not rows:
                return 0
            buf = io.StringIO()
            writer = csv.writer(buf)
            for row in rows:
                writer.writerow(
                    [
                        row["topic"],
                        row["kafka_key"],
                        row["kafka_partition"],
                        row["kafka_offset"],
                        row["ingestion_timestamp"],
                        row["raw_value"],
                    ]
                )
            buf.seek(0)
            with conn.cursor() as cur:
                cur.execute(STAGING_DDL)
                cur.copy_expert(
                    "COPY staging_raw_events "
                    "(topic, kafka_key, kafka_partition, kafka_offset, ingestion_timestamp, raw_value) "
                    "FROM STDIN WITH (FORMAT csv)",
                    buf,
                )
                cur.execute(UPSERT_SQL)
                inserted = cur.rowcount
            conn.commit()
            return inserted

        for row in iter_parquet_rows(bronze_dir):
            batch.append(row)
            if len(batch) >= batch_size:
                total += flush(batch)
                batch = []
        total += flush(batch)

    finally:
        conn.close()

    logger.info("Loaded %d new row(s) into bronze.raw_events", total)
    return total


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Load Bronze Parquet files into a TimescaleDB hypertable."
    )
    parser.add_argument("--bronze-dir", default="data/bronze")
    parser.add_argument(
        "--db-url",
        default="postgresql://postgres:postgres@localhost:5432/gridsense",
        help="SQLAlchemy-style Postgres/TimescaleDB connection URL.",
    )
    parser.add_argument("--batch-size", type=int, default=5000)
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    load(Path(args.bronze_dir), args.db_url, args.batch_size)


if __name__ == "__main__":
    main()
