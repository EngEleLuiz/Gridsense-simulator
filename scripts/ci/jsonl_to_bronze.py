"""CI helper: turn simulator JSON Lines into Bronze Parquet without Kafka.

Uses the real ``record_from_message`` / ``write_batch_to_parquet`` from
``ingestion/bronze_consumer.py``, so the Parquet produced here has exactly
the schema the Kafka consumer writes. Lets CI exercise
simulator -> Bronze -> TimescaleDB -> dbt end to end with no broker.

Usage:
    python scripts/ci/jsonl_to_bronze.py telemetry.jsonl --output-dir /tmp/bronze
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "ingestion"))
from bronze_consumer import record_from_message, write_batch_to_parquet  # noqa: E402

TOPIC = "grid.telemetry.raw"


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("jsonl", nargs="+", type=Path)
    p.add_argument("--output-dir", required=True, type=Path)
    args = p.parse_args()

    offset = 0
    records = []
    for path in args.jsonl:
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                records.append(record_from_message(TOPIC, None, line.encode("utf-8"), 0, offset))
                offset += 1
    if not records:
        sys.exit("No telemetry lines found -- did the simulator run?")
    write_batch_to_parquet(records, args.output_dir)
    print(f"Wrote {len(records)} Bronze record(s) to {args.output_dir}")


if __name__ == "__main__":
    main()
