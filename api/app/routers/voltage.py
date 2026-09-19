from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import text
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import HourlyVoltageQuality

router = APIRouter(prefix="/api/v1/voltage", tags=["voltage"])


@router.get("/hourly", response_model=list[HourlyVoltageQuality])
def get_hourly_voltage(
    network: str | None = Query(None),
    bus_id: int | None = Query(None, description="Filter to a single bus."),
    limit: int = Query(500, ge=1, le=5000),
    db: Session = Depends(get_db),
) -> list[HourlyVoltageQuality]:
    """Hourly voltage-quality KPIs per bus, from
    `gold.mart_voltage_quality_hourly`.
    """
    sql = text(
        """
        SELECT network, bus_id, hour_bucket, avg_voltage_pu, min_voltage_pu,
               max_voltage_pu, stddev_voltage_pu, n_readings, n_violations,
               violation_rate_pct
        FROM public_gold.mart_voltage_quality_hourly
        WHERE (:network IS NULL OR network = :network)
          AND (:bus_id IS NULL OR bus_id = :bus_id)
        ORDER BY hour_bucket DESC, bus_id
        LIMIT :limit
        """
    )
    rows = db.execute(
        sql, {"network": network, "bus_id": bus_id, "limit": limit}
    ).mappings().all()
    return [HourlyVoltageQuality(**row) for row in rows]


@router.get("/violations", response_model=list[HourlyVoltageQuality])
def get_top_violations(
    network: str | None = Query(None),
    min_rate: float = Query(0.0, ge=0, le=100, description="Minimum violation rate (%) to include."),
    limit: int = Query(20, ge=1, le=500),
    db: Session = Depends(get_db),
) -> list[HourlyVoltageQuality]:
    """Bus/hour combinations with the highest voltage-violation rate,
    worst first -- the "which buses need attention" endpoint.
    """
    sql = text(
        """
        SELECT network, bus_id, hour_bucket, avg_voltage_pu, min_voltage_pu,
               max_voltage_pu, stddev_voltage_pu, n_readings, n_violations,
               violation_rate_pct
        FROM public_gold.mart_voltage_quality_hourly
        WHERE (:network IS NULL OR network = :network)
          AND violation_rate_pct >= :min_rate
        ORDER BY violation_rate_pct DESC, hour_bucket DESC
        LIMIT :limit
        """
    )
    rows = db.execute(
        sql, {"network": network, "min_rate": min_rate, "limit": limit}
    ).mappings().all()
    return [HourlyVoltageQuality(**row) for row in rows]
