#!/usr/bin/env python
"""Ad-hoc sanity check of the Bronze Parquet lake (DuckDB, no server).

Replaces the former root-level ``check.py``, ``query_bronze.py`` and
``ingestion_query.py``, which all did the same count.

Usage (from the repository root):
    python scripts/debug/query_bronze.py [--bronze-dir data/bronze]
"""

import argparse
import sys
from pathlib import Path

try:
    import duckdb
except ImportError:
    print("Error: duckdb not installed")
    print("Install with: pip install duckdb")
    sys.exit(1)


def main():
    parser = argparse.ArgumentParser(description="Count Bronze records per topic.")
    parser.add_argument("--bronze-dir", default="data/bronze", type=Path)
    bronze_dir = parser.parse_args().bronze_dir
    glob = (bronze_dir / "**" / "*.parquet").as_posix()
    con = duckdb.connect()

    # Check if bronze data exists
    if not bronze_dir.exists():
        print(f"✗ No data found in {bronze_dir}/")
        print("  Run the consumer first: python ingestion/bronze_consumer.py ...")
        sys.exit(1)

    parquet_files = list(bronze_dir.rglob("*.parquet"))
    if not parquet_files:
        print(f"✗ No Parquet files found in {bronze_dir}/")
        sys.exit(1)

    print(f"✓ Found {len(parquet_files)} Parquet file(s)")
    print()

    # Query: record count by topic
    try:
        result = con.execute("""
            SELECT topic, count(*) as n_records
            FROM read_parquet(?)
            GROUP BY topic
            ORDER BY topic
        """, [glob]).fetchall()

        print("Records by topic:")
        for topic, count in result:
            print(f"  {topic}: {count} records")
        print()

        # Sample data
        total = sum(count for _, count in result)
        if total > 0:
            print(f"✓ Successfully stored {total} records in Parquet!")
            print()
            print("Sample telemetry records:")
            con.execute("""
                SELECT
                    raw_value::JSON->>'step' as step,
                    (raw_value::JSON->>'total_load_mw')::DOUBLE as total_load_mw,
                    (raw_value::JSON->>'total_generation_mw')::DOUBLE as total_gen_mw
                FROM read_parquet(?)
                WHERE topic = 'grid.telemetry.raw'
                ORDER BY step::INT
                LIMIT 10
            """, params=[glob]).show()

    except Exception as e:
        print(f"✗ Query failed: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
