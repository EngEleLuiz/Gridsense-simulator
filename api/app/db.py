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

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker


def _database_url() -> str:
    host = os.getenv("TIMESCALE_HOST", "localhost")
    port = os.getenv("TIMESCALE_PORT", "5432")
    user = os.getenv("TIMESCALE_USER", "postgres")
    password = os.getenv("TIMESCALE_PASSWORD", "postgres")
    db = os.getenv("TIMESCALE_DB", "gridsense")
    return f"postgresql+psycopg2://{user}:{password}@{host}:{port}/{db}"


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
