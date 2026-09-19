from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import HealthStatus

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthStatus)
def health(db: Session = Depends(get_db)) -> HealthStatus:
    """Liveness/readiness check: confirms the API process is up and
    can reach the database. Never raises -- a DB outage is reported
    as a normal 200 response with `database: "unreachable"`, so
    monitoring tools can distinguish "API down" from "API up, DB down".
    """
    try:
        db.execute(text("SELECT 1"))
        db_status = "connected"
    except Exception:
        db_status = "unreachable"
    return HealthStatus(status="ok", database=db_status)
