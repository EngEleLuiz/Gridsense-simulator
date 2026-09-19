from __future__ import annotations

from datetime import datetime, timezone


def _sample_kpi_row() -> dict:
    return {
        "network": "case14",
        "day_bucket": datetime(2026, 9, 9, tzinfo=timezone.utc),
        "avg_load_mw": 236.19,
        "peak_load_mw": 299.57,
        "min_load_mw": 161.51,
        "avg_generation_mw": 40.0,
        "load_factor": 0.7884,
        "n_contingency_steps": 0,
        "n_steps": 200,
    }


def test_get_daily_kpis_returns_rows(client_factory) -> None:
    client, _ = client_factory([[_sample_kpi_row()]])
    resp = client.get("/api/v1/kpis/daily")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["network"] == "case14"
    assert body[0]["load_factor"] == 0.7884


def test_get_daily_kpis_passes_network_filter_to_query(client_factory) -> None:
    client, fake = client_factory([[_sample_kpi_row()]])
    resp = client.get("/api/v1/kpis/daily", params={"network": "case14"})
    assert resp.status_code == 200
    _, params = fake.calls[0]
    assert params["network"] == "case14"


def test_get_daily_kpis_respects_limit_param(client_factory) -> None:
    client, fake = client_factory([[_sample_kpi_row()]])
    client.get("/api/v1/kpis/daily", params={"limit": 5})
    _, params = fake.calls[0]
    assert params["limit"] == 5


def test_get_daily_kpis_empty_result(client_factory) -> None:
    client, _ = client_factory([[]])
    resp = client.get("/api/v1/kpis/daily")
    assert resp.status_code == 200
    assert resp.json() == []


def test_get_daily_kpis_rejects_limit_out_of_range(client_factory) -> None:
    client, _ = client_factory([[]])
    resp = client.get("/api/v1/kpis/daily", params={"limit": 5000})
    assert resp.status_code == 422
