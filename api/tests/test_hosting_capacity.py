from __future__ import annotations

from datetime import datetime, timezone


def _deterministic_v2() -> dict:
    return {
        "network": "cigre_lv",
        "method": "deterministic",
        "run_id": "r-2",
        "run_timestamp": datetime(2026, 10, 5, tzinfo=timezone.utc),
        "schema_version": 2,
        "status": "ok",
        "is_comparable": True,
        "is_bounded": True,
        "criterion_framework": "prodist_m8_bt",
        "load_scale": 0.25,
        "total_pv_mw_comparable": 0.799,
        "total_pv_mw_reported": 0.799,
        "lambda_max": 1.164,
        "lambda_fail": 1.172,
        "binding_constraint": "overvoltage@bus_16 (1.0504 pu)",
    }


def _stochastic_v2() -> dict:
    return {
        "network": "cigre_lv",
        "method": "stochastic",
        "run_id": "r-2",
        "run_timestamp": datetime(2026, 10, 5, tzinfo=timezone.utc),
        "schema_version": 2,
        "status": "ok",
        "is_comparable": True,
        "is_bounded": True,
        "criterion_framework": "prodist_m8_bt",
        "load_scale": 0.25,
        "total_pv_mw_comparable": 0.311,
        "total_pv_mw_p50": 0.52,
        "lambda_max": 0.453,
        "hc_alpha": 0.10,
        "hc_lambda_ci_low": 0.359,
        "hc_lambda_ci_high": 0.539,
        "n_scenarios": 60,
        "n_censored": 0,
    }


def test_compare_returns_rows_with_conditions(client_factory) -> None:
    client, _ = client_factory([[_deterministic_v2(), _stochastic_v2()]])
    resp = client.get("/api/v1/hosting-capacity/compare")
    assert resp.status_code == 200
    body = resp.json()
    assert {r["method"] for r in body} == {"deterministic", "stochastic"}
    assert all(r["criterion_framework"] == "prodist_m8_bt" for r in body)


def test_method_specific_fields_are_null_when_not_applicable(client_factory) -> None:
    client, _ = client_factory([[_deterministic_v2()]])
    body = client.get("/api/v1/hosting-capacity/compare").json()[0]
    assert body["hc_alpha"] is None and body["n_scenarios"] is None
    assert body["lambda_fail"] == 1.172


def test_comparable_only_is_the_default(client_factory) -> None:
    client, fake = client_factory([[]])
    client.get("/api/v1/hosting-capacity/compare")
    sql, params = fake.calls[0]
    assert params["comparable_only"] is True
    assert "is_comparable" in sql


def test_filters_are_passed_to_query(client_factory) -> None:
    client, fake = client_factory([[]])
    client.get(
        "/api/v1/hosting-capacity/compare",
        params={"network": "cigre_lv", "framework": "prodist_m8_bt", "comparable_only": "false"},
    )
    _, params = fake.calls[0]
    assert params == {"network": "cigre_lv", "framework": "prodist_m8_bt",
                      "comparable_only": False, "limit": 100}


def test_legacy_row_defaults_to_not_comparable(client_factory) -> None:
    legacy = {
        "network": "cigre_lv", "method": "stochastic", "run_id": "r-1",
        "run_timestamp": datetime(2026, 9, 20, tzinfo=timezone.utc),
        "total_pv_mw_reported": 0.1505,
    }
    client, _ = client_factory([[legacy]])
    body = client.get("/api/v1/hosting-capacity/compare",
                      params={"comparable_only": "false"}).json()[0]
    assert body["is_comparable"] is False
    assert body["status"] == "legacy_invalid"
    assert body["total_pv_mw_comparable"] is None


def test_empty_result(client_factory) -> None:
    client, _ = client_factory([[]])
    assert client.get("/api/v1/hosting-capacity/compare").json() == []


def test_rejects_limit_out_of_range(client_factory) -> None:
    client, _ = client_factory([[]])
    assert client.get("/api/v1/hosting-capacity/compare", params={"limit": 5000}).status_code == 422
