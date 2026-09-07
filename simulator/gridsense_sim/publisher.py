"""Telemetry publishers.

For local testing we don't require Kafka to be running. This module
provides simple, dependency-free publishers (console + JSON Lines
file) that implement the same interface a future Kafka producer will
use. Once the ingestion pipeline (Phase 2) is online, a
`KafkaPublisher` can be dropped in without changing the simulation
engine.
"""

from __future__ import annotations

import json
import sys
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any


class TelemetryPublisher(ABC):
    """Base interface for anything that can receive telemetry records."""

    @abstractmethod
    def publish(self, topic: str, record: dict[str, Any]) -> None:
        """Publish a single telemetry record under a logical topic name."""
        raise NotImplementedError

    def close(self) -> None:
        """Optional cleanup hook. Default is a no-op."""
        return None


class ConsolePublisher(TelemetryPublisher):
    """Prints each telemetry record as a JSON line to stdout."""

    def publish(self, topic: str, record: dict[str, Any]) -> None:
        payload = {"topic": topic, **record}
        sys.stdout.write(json.dumps(payload, default=str) + "\n")


class JSONLFilePublisher(TelemetryPublisher):
    """Appends each telemetry record as a JSON line to a local file.

    This is useful for local testing/analysis: you can inspect the
    output afterwards with tools like `pandas.read_json(path, lines=True)`
    or `jq`.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._file = self.path.open("a", encoding="utf-8")

    def publish(self, topic: str, record: dict[str, Any]) -> None:
        payload = {"topic": topic, **record}
        self._file.write(json.dumps(payload, default=str) + "\n")
        self._file.flush()

    def close(self) -> None:
        self._file.close()


class MultiPublisher(TelemetryPublisher):
    """Fans out each record to multiple publishers (e.g. console + file)."""

    def __init__(self, publishers: list[TelemetryPublisher]) -> None:
        self.publishers = publishers

    def publish(self, topic: str, record: dict[str, Any]) -> None:
        for pub in self.publishers:
            pub.publish(topic, record)

    def close(self) -> None:
        for pub in self.publishers:
            pub.close()
