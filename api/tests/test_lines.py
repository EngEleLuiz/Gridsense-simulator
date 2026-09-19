from __future__ import annotations

from datetime import datetime, timezone


def _sample_line_row(line_id: int = 0, n_overload_events: int = 3) -> dict:
    return {
        "network": "case14",
        "line_id": line_id,
        "hour_bucket": datetime(2026, 9, 9, 17, tzinfo=timezone.utc),
        "avg_loading_percent": 45.2,
        "max_loading_percent": 112.8,
        "n_readings": 200,
        "n_overload_events": n_overload_events,
        "overload_rate_pct": 1.5,
    }


def test_get_hourly_line_loading_returns_rows(client_factory) -> None:
    client, _ = client_factory([[_sample_line_row()]])
    resp = client.get("/api/v1/lines/hourly")
    assert resp.status_code == 200
    body = resp.json()
    assert body[0]["line_id"] == 0
    assert body[0]["max_loading_percent"] == 112.8


def test_get_hourly_line_loading_passes_line_id_filter(client_factory) -> None:
    client, fake = client_factory([[_sample_line_row(line_id=7)]])
    client.get("/api/v1/lines/hourly", params={"line_id": 7})
    _, params = fake.calls[0]
    assert params["line_id"] == 7


def test_get_overloaded_lines_returns_rows(client_factory) -> None:
    client, _ = client_factory([[_sample_line_row(n_overload_events=5)]])
    resp = client.get("/api/v1/lines/overloaded")
    assert resp.status_code == 200
    body = resp.json()
    assert body[0]["n_overload_events"] == 5


def test_get_overloaded_lines_empty_result(client_factory) -> None:
    client, _ = client_factory([[]])
    resp = client.get("/api/v1/lines/overloaded")
    assert resp.status_code == 200
    assert resp.json() == []
