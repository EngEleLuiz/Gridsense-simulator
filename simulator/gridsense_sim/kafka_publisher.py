"""Kafka-backed telemetry publisher.

Implements the same `TelemetryPublisher` interface used by the console
and JSONL publishers (see `publisher.py`), so the simulation engine
code does not change at all when switching to Kafka. Requires a
Kafka broker reachable at `bootstrap_servers` — see the root
`docker-compose.yml` for a free, fully local single-broker setup
(no cloud service, no cost).
"""

from __future__ import annotations

import json
import logging
from typing import Any

from .publisher import TelemetryPublisher

logger = logging.getLogger("gridsense_sim.kafka_publisher")


class KafkaPublisher(TelemetryPublisher):
    """Publishes telemetry records to Kafka topics via confluent-kafka.

    A `producer` instance can be injected (e.g. a test double), which
    keeps this class testable without requiring a real Kafka broker.
    """

    def __init__(
        self,
        bootstrap_servers: str = "localhost:9092",
        client_id: str = "gridsense-sim-producer",
        producer: Any = None,
        **extra_producer_config: Any,
    ) -> None:
        if producer is not None:
            self._producer = producer
        else:
            try:
                from confluent_kafka import Producer
            except ImportError as exc:  # pragma: no cover - environment guard
                raise ImportError(
                    "confluent-kafka is required for KafkaPublisher. "
                    "Install it with: pip install 'gridsense-sim[kafka]'"
                ) from exc

            config = {
                "bootstrap.servers": bootstrap_servers,
                "client.id": client_id,
                **extra_producer_config,
            }
            self._producer = Producer(config)

    def publish(self, topic: str, record: dict[str, Any]) -> None:
        payload = json.dumps(record, default=str).encode("utf-8")
        key = str(record["step"]).encode("utf-8") if "step" in record else None
        self._producer.produce(
            topic=topic,
            key=key,
            value=payload,
            callback=self._delivery_callback,
        )
        # Serves outstanding delivery-report callbacks without blocking.
        self._producer.poll(0)

    def _delivery_callback(self, err: Any, msg: Any) -> None:
        if err is not None:
            logger.error("Delivery failed for record on topic %s: %s", msg.topic(), err)

    def close(self) -> None:
        """Blocks until all outstanding messages are delivered."""
        self._producer.flush(timeout=10)
