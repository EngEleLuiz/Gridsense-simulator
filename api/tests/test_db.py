"""Tests for the connection URL built in app.db (code review CR-14)."""

from __future__ import annotations

import secrets

import pytest
from sqlalchemy import make_url

from app.db import _database_url


def test_defaults_have_no_password(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in ("TIMESCALE_HOST", "TIMESCALE_PORT", "TIMESCALE_USER", "TIMESCALE_PASSWORD", "TIMESCALE_DB"):
        monkeypatch.delenv(var, raising=False)
    url = _database_url()
    assert url.drivername == "postgresql+psycopg2"
    assert (url.host, url.port, url.username, url.database) == (
        "localhost", 5432, "postgres", "gridsense")
    # No credential is baked into the code: it has to come from the environment.
    assert url.password is None


def test_password_with_reserved_characters_round_trips(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TIMESCALE_HOST", "db.internal")
    monkeypatch.setenv("TIMESCALE_PORT", "6543")
    monkeypatch.setenv("TIMESCALE_USER", "grid:user")
    # Generated per run (reserved characters + random suffix): no fixed
    # credential-shaped literal in the repository.
    reserved = "@/:"
    value = secrets.token_hex(4) + reserved
    monkeypatch.setenv("TIMESCALE_PASSWORD", value)
    monkeypatch.setenv("TIMESCALE_DB", "gridsense")
    url = _database_url()
    assert url.password == value
    # Rendering to a string and parsing it back must not lose or split anything.
    parsed = make_url(url.render_as_string(hide_password=False))
    assert parsed.password == value
    assert parsed.username == "grid:user"
    assert (parsed.host, parsed.port, parsed.database) == ("db.internal", 6543, "gridsense")


def test_non_numeric_port_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TIMESCALE_PORT", "not-a-port")
    with pytest.raises(ValueError):
        _database_url()
