#!/usr/bin/env python
"""Query the Bronze layer Parquet files.

Usage:
  python ingestion_query.py
"""

import sys
from pathlib import Path

try:
    import duckdb
except ImportError:
    print("Error: duckdb not installed")
    print("Install with: pip install duckdb")
    sys.exit(1)


def main():
    con = duckdb.connect()
    
    # Check if bronze data exists
    bronze_dir = Path("data/bronze")
    if not bronze_dir.exists():
        print("✗ No data found in data/bronze/")
        print("  Run the consumer first: python ingestion/bronze_consumer.py ...")
        sys.exit(1)
    
    parquet_files = list(bronze_dir.rglob("*.parquet"))
    if not parquet_files:
        print("✗ No Parquet files found in data/bronze/")
        sys.exit(1)
    
    print(f"✓ Found {len(parquet_files)} Parquet file(s)")
    print()
    
    # Query: record count by topic
    try:
        result = con.execute("""
            SELECT topic, count(*) as n_records
            FROM read_parquet('data/bronze/**/*.parquet')
            GROUP BY topic
            ORDER BY topic
        """).fetchall()
        
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
                FROM read_parquet('data/bronze/**/*.parquet')
                WHERE topic = 'grid.telemetry.raw'
                ORDER BY step::INT
                LIMIT 10
            """).show()
        
    except Exception as e:
        print(f"✗ Query failed: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
