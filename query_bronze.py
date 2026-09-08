#!/usr/bin/env python
import sys
try:
    import duckdb
except ImportError:
    print("Error: duckdb not installed")
    sys.exit(1)

con = duckdb.connect()
result = con.execute("""
    SELECT topic, count(*) as n_records
    FROM read_parquet('data/bronze/**/*.parquet')
    GROUP BY topic
""").fetchall()

print("\nRecords by topic:")
for topic, count in result:
    print(f"  {topic}: {count} records")

total = sum(c[1] for c in result)
print(f"\nTotal: {total} records in Parquet!")
print("\nPhase 2 SUCCESS! ✓")
