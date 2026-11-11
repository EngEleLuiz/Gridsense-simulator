"""Database session management.

Reads connection details from the same environment variables used
across the project's ingestion and dbt tooling (TIMESCALE_HOST,
TIMESCALE_PORT, etc.), so one `.env` / one set of exported variables
configures every layer consistently. Falls back to the same localhost
defaults as the root `docker-compose.yml`.
"""

from __future__ import annotations

import os
from collections.abc import Generator

from sqlalchemy import URL, create_engine
from sqlalchemy.orm import Session, sessionmaker


def _database_url() -> URL:
    """Build the connection URL from the TIMESCALE_* environment variables.

    ``URL.create`` keeps each component separate, so a password (or user
    name) containing reserved characters such as ``@``, ``/`` or ``:`` is
    passed to the driver verbatim instead of corrupting the URL, which an
    f-string-built URL would do.

    There is deliberately no default password: it must come from
    ``TIMESCALE_PASSWORD`` (``docker-compose.yml``, the Makefile targets and
    the READMEs already set it). Without it the driver reports "no password
    supplied" instead of silently trying a well-known credential.
    """
    return URL.create(
        "postgresql+psycopg2",
        username=os.getenv("TIMESCALE_USER", "postgres"),
        password=os.getenv("TIMESCALE_PASSWORD"),
        host=os.getenv("TIMESCALE_HOST", "localhost"),
        port=int(os.getenv("TIMESCALE_PORT", "5432")),
        database=os.getenv("TIMESCALE_DB", "gridsense"),
    )


engine = create_engine(_database_url(), pool_pre_ping=True)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def get_db() -> Generator[Session, None, None]:
    """FastAPI dependency: yields a request-scoped DB session, always
    closed afterwards, even if the request handler raises.
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
