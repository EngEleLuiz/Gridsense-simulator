"""Basic tests for the GridSense simulation engine.

Run with: pytest -v
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from gridsense_sim.engine import GridSimulationEngine, SimulationConfig
from gridsense_sim.profiles import LoadProfile, ProfileConfig, SolarProfile
from gridsense_sim.publisher import JSONLFilePublisher, TelemetryPublisher
from gridsense_sim.scenarios import Contingency, ElementType


class RecordingPublisher(TelemetryPublisher):
    """Test double that stores every published record in memory."""

    def __init__(self) -> None:
        self.records: list[tuple[str, dict]] = []
        self.closed = False

    def publish(self, topic: str, record: dict) -> None:
        self.records.append((topic, record))

    def close(self) -> None:
        self.closed = True


def test_load_profile_is_bounded() -> None:
    profile = LoadProfile(ProfileConfig(seed=1))
    values = [profile.value_at(step) for step in range(288)]
    assert all(0.2 < v < 1.5 for v in values)


def test_solar_profile_is_zero_at_night() -> None:
    profile = SolarProfile(ProfileConfig(seed=1))
    midnight_step = 0  # hour 0
    assert profile.value_at(midnight_step, steps_per_day=288) == 0.0


def test_engine_runs_and_publishes_telemetry() -> None:
    config = SimulationConfig(network_name="case14", total_steps=5, seed=1)
    publisher = RecordingPublisher()
    engine = GridSimulationEngine(config, publisher)
    engine.run()

    telemetry_records = [r for topic, r in publisher.records if topic == "grid.telemetry.raw"]
    assert len(telemetry_records) == 5
    assert publisher.closed is True

    first = telemetry_records[0]
    assert "bus_voltage_pu" in first
    assert "line_loading_percent" in first
    assert first["network"] == "case14"


def test_engine_raises_on_unsupported_network() -> None:
    config = SimulationConfig(network_name="not_a_real_case", total_steps=1)
    with pytest.raises(ValueError):
        GridSimulationEngine(config, RecordingPublisher())


def test_contingency_takes_line_out_of_service() -> None:
    contingency = Contingency(
        element_type=ElementType.LINE,
        element_index=0,
        start_step=2,
        duration_steps=2,
        description="Test line outage",
    )
    config = SimulationConfig(
        network_name="case14", total_steps=5, seed=1, contingencies=[contingency]
    )
    publisher = RecordingPublisher()
    engine = GridSimulationEngine(config, publisher)
    engine.run()

    alert_records = [r for topic, r in publisher.records if topic == "grid.events.alerts"]
    assert any("Test line outage" in r.get("events", []) for r in alert_records)


def test_jsonl_file_publisher_writes_valid_json_lines(tmp_path: Path) -> None:
    output_path = tmp_path / "telemetry.jsonl"
    publisher = JSONLFilePublisher(output_path)
    publisher.publish("grid.telemetry.raw", {"step": 1, "value": 42})
    publisher.close()

    lines = output_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    parsed = json.loads(lines[0])
    assert parsed["step"] == 1
    assert parsed["topic"] == "grid.telemetry.raw"
