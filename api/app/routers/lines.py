from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import text
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import HourlyLineLoading

router = APIRouter(prefix="/api/v1/lines", tags=["lines"])


@router.get("/hourly", response_model=list[HourlyLineLoading])
def get_hourly_line_loading(
    network: str | None = Query(None),
    line_id: int | None = Query(None, description="Filter to a single line."),
    limit: int = Query(500, ge=1, le=5000),
    db: Session = Depends(get_db),
) -> list[HourlyLineLoading]:
    """Hourly loading KPIs per transmission line, from
    `gold.mart_line_loading_hourly`.
    """
    sql = text(
        """
        SELECT network, line_id, hour_bucket, avg_loading_percent,
               max_loading_percent, n_readings, n_overload_events,
               overload_rate_pct
        FROM public_gold.mart_line_loading_hourly
        WHERE (:network IS NULL OR network = :network)
          AND (:line_id IS NULL OR line_id = :line_id)
        ORDER BY hour_bucket DESC, line_id
        LIMIT :limit
        """
    )
    rows = db.execute(
        sql, {"network": network, "line_id": line_id, "limit": limit}
    ).mappings().all()
    return [HourlyLineLoading(**row) for row in rows]


@router.get("/overloaded", response_model=list[HourlyLineLoading])
def get_overloaded_lines(
    network: str | None = Query(None),
    limit: int = Query(20, ge=1, le=500),
    db: Session = Depends(get_db),
) -> list[HourlyLineLoading]:
    """Line/hour combinations with at least one overload event, worst
    first -- the "which lines are chronically stressed" endpoint.
    """
    sql = text(
        """
        SELECT network, line_id, hour_bucket, avg_loading_percent,
               max_loading_percent, n_readings, n_overload_events,
               overload_rate_pct
        FROM public_gold.mart_line_loading_hourly
        WHERE (:network IS NULL OR network = :network)
          AND n_overload_events > 0
        ORDER BY overload_rate_pct DESC, hour_bucket DESC
        LIMIT :limit
        """
    )
    rows = db.execute(sql, {"network": network, "limit": limit}).mappings().all()
    return [HourlyLineLoading(**row) for row in rows]
