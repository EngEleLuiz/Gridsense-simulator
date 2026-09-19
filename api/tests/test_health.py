from __future__ import annotations


def test_health_reports_ok_and_connected(client_factory) -> None:
    client, _ = client_factory([[{"?column?": 1}]])
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["database"] == "connected"


def test_health_reports_unreachable_on_db_error(client_factory) -> None:
    client, fake = client_factory([])

    def _raise(*args, **kwargs):
        raise RuntimeError("connection refused")

    fake.execute = _raise  # type: ignore[method-assign]

    resp = client.get("/health")
    # A DB outage is still a successful API response -- monitoring
    # tools need to distinguish "API process is down" (no response)
    # from "API is up, DB is down" (this case).
    assert resp.status_code == 200
    assert resp.json()["database"] == "unreachable"
