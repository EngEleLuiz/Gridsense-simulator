"""Tests for KafkaPublisher.

A fake confluent_kafka.Producer is injected so these tests run
without any real Kafka broker — they verify serialization and the
publisher's contract, not the broker itself. End-to-end testing
against a real broker happens via `docker-compose.yml` on your
machine (see README, Phase 2 section).
"""

from __future__ import annotations

import json

from gridsense_sim.kafka_publisher import KafkaPublisher


class FakeProducer:
    """Minimal stand-in for confluent_kafka.Producer."""

    def __init__(self) -> None:
        self.produced: list[tuple[str, bytes | None, bytes]] = []
        self.poll_calls = 0
        self.flushed = False

    def produce(self, topic, key, value, callback=None):
        self.produced.append((topic, key, value))
        if callback is not None:
            callback(None, _FakeMsg(topic))

    def poll(self, timeout=0):
        self.poll_calls += 1

    def flush(self, timeout=10):
        self.flushed = True


class _FakeMsg:
    def __init__(self, topic: str) -> None:
        self._topic = topic

    def topic(self) -> str:
        return self._topic


def test_publish_serializes_record_as_json_and_uses_step_as_key() -> None:
    fake_producer = FakeProducer()
    publisher = KafkaPublisher(producer=fake_producer)

    publisher.publish("grid.telemetry.raw", {"step": 7, "total_load_mw": 123.4})

    assert len(fake_producer.produced) == 1
    topic, key, value = fake_producer.produced[0]
    assert topic == "grid.telemetry.raw"
    assert key == b"7"
    assert json.loads(value) == {"step": 7, "total_load_mw": 123.4}
    assert fake_producer.poll_calls == 1


def test_publish_without_step_uses_no_key() -> None:
    fake_producer = FakeProducer()
    publisher = KafkaPublisher(producer=fake_producer)

    publisher.publish("grid.events.alerts", {"message": "test"})

    _, key, _ = fake_producer.produced[0]
    assert key is None


def test_close_flushes_the_underlying_producer() -> None:
    fake_producer = FakeProducer()
    publisher = KafkaPublisher(producer=fake_producer)

    publisher.close()

    assert fake_producer.flushed is True
