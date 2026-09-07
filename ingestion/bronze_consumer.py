"""Bronze-layer Kafka consumer for GridSense Simulator.

Consumes the raw telemetry/event topics from Kafka and lands them,
unmodified, as partitioned Parquet files on local disk — this plays
the role of an S3/MinIO landing zone, but at zero infrastructure cost
for local development. Schema-on-read: each row stores the raw JSON
payload plus ingestion metadata; typed parsing/validation happens
later in the Silver layer (dbt), a future phase of this project.

Run with:
    python bronze_consumer.py --bootstrap-servers localhost:9092 -v
"""

from __future__ import annotations

import argparse
import logging
import signal
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

logger = logging.getLogger("gridsense_ingestion.bronze_consumer")

# Schema-on-read: raw_value keeps the untouched JSON payload as a string.
# Typed columns are only added downstream, in the Silver layer.
BRONZE_SCHEMA = pa.schema(
    [
        ("topic", pa.string()),
        ("kafka_key", pa.string()),
        ("kafka_partition", pa.int32()),
        ("kafka_offset", pa.int64()),
        ("ingestion_timestamp", pa.string()),
        ("raw_value", pa.string()),
    ]
)

DEFAULT_TOPICS = ["grid.telemetry.raw", "grid.events.alerts"]


@dataclass
class BronzeConsumerConfig:
    bootstrap_servers: str = "localhost:9092"
    group_id: str = "gridsense-bronze-consumer"
    topics: list[str] = field(default_factory=lambda: list(DEFAULT_TOPICS))
    output_dir: str = "data/bronze"
    batch_size: int = 50
    flush_interval_seconds: float = 10.0


def record_from_message(
    topic: str,
    key: bytes | None,
    value: bytes,
    partition: int,
    offset: int,
) -> dict[str, Any]:
    """Builds a bronze row dict from raw Kafka message fields.

    Kept as a pure function (no Kafka client dependency) so it can be
    unit tested without a real broker.
    """
    return {
        "topic": topic,
        "kafka_key": key.decode("utf-8") if key is not None else None,
        "kafka_partition": partition,
        "kafka_offset": offset,
        "ingestion_timestamp": datetime.now(timezone.utc).isoformat(),
        "raw_value": value.decode("utf-8"),
    }


def write_batch_to_parquet(records: list[dict[str, Any]], output_dir: str | Path) -> Path | None:
    """Writes a batch of bronze rows to partitioned Parquet files.

    Partition layout: {output_dir}/topic={topic}/date={YYYY-MM-DD}/part-{ts_ms}.parquet
    Records for different topics/dates within the same batch are
    split into separate files automatically.

    Returns the path of the last file written, or None if `records`
    was empty (so callers can safely no-op on empty batches).
    """
    if not records:
        return None

    output_dir = Path(output_dir)
    by_partition: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for record in records:
        date_str = record["ingestion_timestamp"][:10]
        by_partition.setdefault((record["topic"], date_str), []).append(record)

    last_path = None
    for (topic, date_str), rows in by_partition.items():
        partition_dir = output_dir / f"topic={topic}" / f"date={date_str}"
        partition_dir.mkdir(parents=True, exist_ok=True)
        file_path = partition_dir / f"part-{int(time.time() * 1000)}.parquet"

        table = pa.Table.from_pylist(rows, schema=BRONZE_SCHEMA)
        pq.write_table(table, file_path)
        last_path = file_path
        logger.info("Wrote %d record(s) to %s", len(rows), file_path)

    return last_path


class BronzeConsumer:
    """Consumes Kafka topics and periodically flushes batches to Parquet.

    A `consumer` instance can be injected (e.g. a test double), which
    keeps this class testable without a real Kafka broker.
    """

    def __init__(self, config: BronzeConsumerConfig, consumer: Any = None):
        self.config = config
        if consumer is not None:
            self._consumer = consumer
        else:
            try:
                from confluent_kafka import Consumer
            except ImportError as exc:  # pragma: no cover - environment guard
                raise ImportError(
                    "confluent-kafka is required to run BronzeConsumer against "
                    "a real broker. Install it with: pip install confluent-kafka"
                ) from exc

            self._consumer = Consumer(
                {
                    "bootstrap.servers": config.bootstrap_servers,
                    "group.id": config.group_id,
                    "auto.offset.reset": "earliest",
                    "enable.auto.commit": True,
                }
            )
        self._running = False

    def run(self) -> None:
        from confluent_kafka import KafkaError, KafkaException  # local import: optional dep

        self._consumer.subscribe(self.config.topics)
        self._running = True
        buffer: list[dict[str, Any]] = []
        last_flush = time.monotonic()

        logger.info(
            "Bronze consumer started. topics=%s output_dir=%s batch_size=%d",
            self.config.topics,
            self.config.output_dir,
            self.config.batch_size,
        )

        def _handle_shutdown(signum, frame):
            logger.info("Shutdown signal received, flushing remaining records...")
            self._running = False

        signal.signal(signal.SIGINT, _handle_shutdown)
        signal.signal(signal.SIGTERM, _handle_shutdown)

        try:
            while self._running:
                msg = self._consumer.poll(timeout=1.0)
                if msg is not None:
                    if msg.error():
                        if msg.error().code() == KafkaError._PARTITION_EOF:
                            pass
                        else:
                            raise KafkaException(msg.error())
                    else:
                        buffer.append(
                            record_from_message(
                                topic=msg.topic(),
                                key=msg.key(),
                                value=msg.value(),
                                partition=msg.partition(),
                                offset=msg.offset(),
                            )
                        )

                should_flush = len(buffer) >= self.config.batch_size or (
                    buffer and (time.monotonic() - last_flush) >= self.config.flush_interval_seconds
                )
                if should_flush:
                    write_batch_to_parquet(buffer, self.config.output_dir)
                    buffer = []
                    last_flush = time.monotonic()
        finally:
            if buffer:
                write_batch_to_parquet(buffer, self.config.output_dir)
            self._consumer.close()
            logger.info("Bronze consumer stopped.")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Consume GridSense Kafka topics into a local Parquet bronze layer."
    )
    parser.add_argument("--bootstrap-servers", default="localhost:9092")
    parser.add_argument("--group-id", default="gridsense-bronze-consumer")
    parser.add_argument("--topics", nargs="+", default=DEFAULT_TOPICS)
    parser.add_argument("--output-dir", default="data/bronze")
    parser.add_argument("--batch-size", type=int, default=50)
    parser.add_argument("--flush-interval", type=float, default=10.0)
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    config = BronzeConsumerConfig(
        bootstrap_servers=args.bootstrap_servers,
        group_id=args.group_id,
        topics=args.topics,
        output_dir=args.output_dir,
        batch_size=args.batch_size,
        flush_interval_seconds=args.flush_interval,
    )
    BronzeConsumer(config).run()


if __name__ == "__main__":
    main()
