from __future__ import annotations

from datetime import datetime, timezone


def _sample_deterministic_row() -> dict:
    return {
        "network": "cigre_lv",
        "method": "deterministic",
        "run_id": "d1f2c3b4-0000-0000-0000-000000000001",
        "run_timestamp": datetime(2026, 9, 20, tzinfo=timezone.utc),
        "total_pv_mw_comparable": 1.5717,
        "total_pv_mw_p95": None,
        "lambda_max": 2.2891,
        "binding_constraint": "trafo_loading@trafo_0 (100.5%)",
        "violation_rate": None,
        "n_trials": None,
        "qsts_total_steps": None,
        "qsts_steps_per_day": None,
    }


def _sample_stochastic_row() -> dict:
    return {
        "network": "cigre_lv",
        "method": "stochastic",
        "run_id": "d1f2c3b4-0000-0000-0000-000000000002",
        "run_timestamp": datetime(2026, 9, 20, tzinfo=timezone.utc),
        "total_pv_mw_comparable": 0.1505,
        "total_pv_mw_p95": 0.1883,
        "lambda_max": None,
        "binding_constraint": None,
        "violation_rate": 0.0,
        "n_trials": 500,
        "qsts_total_steps": None,
        "qsts_steps_per_day": None,
    }


def test_compare_hosting_capacity_returns_rows(client_factory) -> None:
    client, _ = client_factory([[_sample_deterministic_row(), _sample_stochastic_row()]])
    resp = client.get("/api/v1/hosting-capacity/compare")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 2
    methods = {row["method"] for row in body}
    assert methods == {"deterministic", "stochastic"}


def test_compare_hosting_capacity_method_specific_fields_are_null_when_not_applicable(
    client_factory,
) -> None:
    client, _ = client_factory([[_sample_deterministic_row()]])
    resp = client.get("/api/v1/hosting-capacity/compare")
    body = resp.json()[0]
    assert body["lambda_max"] == 2.2891
    assert body["binding_constraint"] == "trafo_loading@trafo_0 (100.5%)"
    # Stochastic-only fields must not leak a default onto a deterministic row.
    assert body["violation_rate"] is None
    assert body["n_trials"] is None


def test_compare_hosting_capacity_passes_network_filter_to_query(client_factory) -> None:
    client, fake = client_factory([[_sample_deterministic_row()]])
    resp = client.get("/api/v1/hosting-capacity/compare", params={"network": "cigre_lv"})
    assert resp.status_code == 200
    _, params = fake.calls[0]
    assert params["network"] == "cigre_lv"


def test_compare_hosting_capacity_empty_result(client_factory) -> None:
    client, _ = client_factory([[]])
    resp = client.get("/api/v1/hosting-capacity/compare")
    assert resp.status_code == 200
    assert resp.json() == []


def test_compare_hosting_capacity_rejects_limit_out_of_range(client_factory) -> None:
    client, _ = client_factory([[]])
    resp = client.get("/api/v1/hosting-capacity/compare", params={"limit": 5000})
    assert resp.status_code == 422
