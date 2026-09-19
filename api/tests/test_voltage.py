from __future__ import annotations

from datetime import datetime, timezone


def _sample_voltage_row(bus_id: int = 0, violation_rate_pct: float = 100.0) -> dict:
    return {
        "network": "case14",
        "bus_id": bus_id,
        "hour_bucket": datetime(2026, 9, 9, 17, tzinfo=timezone.utc),
        "avg_voltage_pu": 1.06,
        "min_voltage_pu": 1.06,
        "max_voltage_pu": 1.06,
        "stddev_voltage_pu": 0.0,
        "n_readings": 200,
        "n_violations": 200,
        "violation_rate_pct": violation_rate_pct,
    }


def test_get_hourly_voltage_returns_rows(client_factory) -> None:
    client, _ = client_factory([[_sample_voltage_row()]])
    resp = client.get("/api/v1/voltage/hourly")
    assert resp.status_code == 200
    body = resp.json()
    assert body[0]["bus_id"] == 0
    assert body[0]["violation_rate_pct"] == 100.0


def test_get_hourly_voltage_passes_bus_id_filter(client_factory) -> None:
    client, fake = client_factory([[_sample_voltage_row(bus_id=5)]])
    client.get("/api/v1/voltage/hourly", params={"bus_id": 5})
    _, params = fake.calls[0]
    assert params["bus_id"] == 5


def test_get_top_violations_passes_min_rate(client_factory) -> None:
    client, fake = client_factory([[_sample_voltage_row()]])
    client.get("/api/v1/voltage/violations", params={"min_rate": 50})
    _, params = fake.calls[0]
    assert params["min_rate"] == 50


def test_get_top_violations_rejects_rate_above_100(client_factory) -> None:
    client, _ = client_factory([[]])
    resp = client.get("/api/v1/voltage/violations", params={"min_rate": 150})
    assert resp.status_code == 422
