"""Tests for the Bronze-layer Kafka consumer.

A fake confluent_kafka.Consumer is injected so these tests run
without any real Kafka broker. End-to-end testing against a real
broker happens via `docker-compose.yml` on your machine (see the
project README, Phase 2 section).
"""

from __future__ import annotations

import json

import pyarrow.parquet as pq

from bronze_consumer import (
    BronzeConsumer,
    BronzeConsumerConfig,
    record_from_message,
    write_batch_to_parquet,
)


def test_record_from_message_builds_expected_fields() -> None:
    record = record_from_message(
        topic="grid.telemetry.raw", key=b"5", value=b'{"step": 5}', partition=0, offset=123
    )

    assert record["topic"] == "grid.telemetry.raw"
    assert record["kafka_key"] == "5"
    assert record["kafka_partition"] == 0
    assert record["kafka_offset"] == 123
    assert json.loads(record["raw_value"]) == {"step": 5}
    assert "ingestion_timestamp" in record


def test_record_from_message_handles_missing_key() -> None:
    record = record_from_message(topic="t", key=None, value=b"{}", partition=0, offset=0)
    assert record["kafka_key"] is None


def test_write_batch_to_parquet_partitions_by_topic_and_date(tmp_path) -> None:
    records = [
        record_from_message("grid.telemetry.raw", b"1", b"{}", 0, 1),
        record_from_message("grid.events.alerts", b"1", b"{}", 0, 2),
    ]

    write_batch_to_parquet(records, tmp_path)

    parquet_files = list(tmp_path.rglob("*.parquet"))
    assert len(parquet_files) == 2
    topic_dirs = {p.parent.parent.name for p in parquet_files}
    assert topic_dirs == {"topic=grid.telemetry.raw", "topic=grid.events.alerts"}


def test_write_batch_to_parquet_returns_none_for_empty_batch(tmp_path) -> None:
    assert write_batch_to_parquet([], tmp_path) is None


def test_write_batch_to_parquet_produces_readable_rows(tmp_path) -> None:
    records = [record_from_message("grid.telemetry.raw", b"k", b'{"step": 0}', 0, i) for i in range(3)]

    write_batch_to_parquet(records, tmp_path)

    parquet_files = list(tmp_path.rglob("*.parquet"))
    table = pq.read_table(parquet_files[0])
    assert table.num_rows == 3
    assert table.column_names == [
        "topic",
        "kafka_key",
        "kafka_partition",
        "kafka_offset",
        "ingestion_timestamp",
        "raw_value",
    ]


class FakeMessage:
    """Minimal stand-in for a confluent_kafka Message."""

    def __init__(self, topic, key, value, partition, offset, err=None):
        self._topic = topic
        self._key = key
        self._value = value
        self._partition = partition
        self._offset = offset
        self._err = err

    def topic(self):
        return self._topic

    def key(self):
        return self._key

    def value(self):
        return self._value

    def partition(self):
        return self._partition

    def offset(self):
        return self._offset

    def error(self):
        return self._err


class FakeConsumer:
    """Minimal stand-in for confluent_kafka.Consumer."""

    def __init__(self, messages):
        self._messages = list(messages)
        self.subscribed_topics = None
        self.closed = False

    def subscribe(self, topics):
        self.subscribed_topics = topics

    def poll(self, timeout=1.0):
        if self._messages:
            return self._messages.pop(0)
        return None

    def close(self):
        self.closed = True


def test_bronze_consumer_flushes_when_batch_size_reached(tmp_path) -> None:
    messages = [
        FakeMessage("grid.telemetry.raw", b"k", f'{{"step": {i}}}'.encode(), 0, i) for i in range(3)
    ]
    fake_consumer = FakeConsumer(messages)
    config = BronzeConsumerConfig(
        topics=["grid.telemetry.raw"],
        output_dir=str(tmp_path),
        batch_size=3,
        flush_interval_seconds=999,
    )
    bronze_consumer = BronzeConsumer(config, consumer=fake_consumer)

    original_poll = fake_consumer.poll

    def poll_then_stop(timeout=1.0):
        msg = original_poll(timeout)
        if msg is None and not fake_consumer._messages:
            bronze_consumer._running = False
        return msg

    fake_consumer.poll = poll_then_stop

    bronze_consumer.run()

    parquet_files = list(tmp_path.rglob("*.parquet"))
    assert len(parquet_files) == 1
    table = pq.read_table(parquet_files[0])
    assert table.num_rows == 3
    assert fake_consumer.closed is True
    assert fake_consumer.subscribed_topics == ["grid.telemetry.raw"]


def test_bronze_consumer_flushes_remaining_buffer_on_shutdown(tmp_path) -> None:
    # Only 2 messages, batch_size is 10: consumer must still flush the
    # partial buffer once it stops, instead of dropping the records.
    messages = [FakeMessage("grid.telemetry.raw", b"k", b'{"step": 0}', 0, 0)]
    fake_consumer = FakeConsumer(messages)
    config = BronzeConsumerConfig(
        topics=["grid.telemetry.raw"],
        output_dir=str(tmp_path),
        batch_size=10,
        flush_interval_seconds=999,
    )
    bronze_consumer = BronzeConsumer(config, consumer=fake_consumer)

    original_poll = fake_consumer.poll

    def poll_then_stop(timeout=1.0):
        msg = original_poll(timeout)
        if msg is None and not fake_consumer._messages:
            bronze_consumer._running = False
        return msg

    fake_consumer.poll = poll_then_stop

    bronze_consumer.run()

    parquet_files = list(tmp_path.rglob("*.parquet"))
    assert len(parquet_files) == 1
    table = pq.read_table(parquet_files[0])
    assert table.num_rows == 1
