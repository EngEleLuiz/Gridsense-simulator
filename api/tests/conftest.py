"""Shared test fixtures.

A fake DB session stands in for SQLAlchemy's real Session, returning
pre-canned rows queued up per test. This keeps the API test suite fast
and dependency-free -- consistent with the rest of the project's
testing philosophy (see simulator/tests and ingestion/tests): no
Docker or live database required to run `pytest`.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.db import get_db
from app.main import app


class _FakeResult:
    """Minimal stand-in for SQLAlchemy's CursorResult."""

    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def mappings(self) -> "_FakeResult":
        return self

    def all(self) -> list[dict[str, Any]]:
        return self._rows


class FakeSession:
    """Stands in for a SQLAlchemy Session.

    `queue` is a list of canned result sets, returned in order, one
    per `.execute()` call -- so a test can pre-load exactly what each
    query in a multi-query endpoint should "find".
    """

    def __init__(self, queue: list[list[dict[str, Any]]]) -> None:
        self._queue = list(queue)
        self.calls: list[tuple[str, dict]] = []

    def execute(self, statement: Any, params: dict | None = None) -> _FakeResult:
        self.calls.append((str(statement), params or {}))
        rows = self._queue.pop(0) if self._queue else []
        return _FakeResult(rows)

    def close(self) -> None:
        pass


@pytest.fixture
def client_factory():
    """Yields a factory: call it with a list of canned result sets to
    get back (TestClient, FakeSession). Inspect `fake.calls` to assert
    on the SQL/parameters a route actually sent.
    """

    def _make(queue: list[list[dict[str, Any]]]) -> tuple[TestClient, FakeSession]:
        fake = FakeSession(queue)

        def _override_get_db():
            yield fake

        app.dependency_overrides[get_db] = _override_get_db
        client = TestClient(app)
        return client, fake

    yield _make
    app.dependency_overrides.clear()
