from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import text
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import DailyKPI

router = APIRouter(prefix="/api/v1/kpis", tags=["kpis"])


@router.get("/daily", response_model=list[DailyKPI])
def get_daily_kpis(
    network: str | None = Query(None, description="Filter by network, e.g. 'case14'."),
    limit: int = Query(100, ge=1, le=1000),
    db: Session = Depends(get_db),
) -> list[DailyKPI]:
    """Daily system-level KPIs: peak/average load, load factor, and
    contingency-step counts. Reads directly from
    `gold.mart_grid_kpis_daily`.
    """
    sql = text(
        """
        SELECT network, day_bucket, avg_load_mw, peak_load_mw, min_load_mw,
               avg_generation_mw, load_factor, n_contingency_steps, n_steps
        FROM public_gold.mart_grid_kpis_daily
        WHERE (:network IS NULL OR network = :network)
        ORDER BY day_bucket DESC
        LIMIT :limit
        """
    )
    rows = db.execute(sql, {"network": network, "limit": limit}).mappings().all()
    return [DailyKPI(**row) for row in rows]
