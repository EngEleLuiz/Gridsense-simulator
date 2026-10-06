"""Hosting-capacity comparison endpoint (reads gold.mart_hosting_capacity)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import text
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import HostingCapacityResult

router = APIRouter(prefix="/api/v1/hosting-capacity", tags=["hosting-capacity"])

_COLUMNS = ", ".join(HostingCapacityResult.model_fields)


@router.get("/compare", response_model=list[HostingCapacityResult])
def compare_hosting_capacity(
    network: str | None = Query(None, description="Filter by network, e.g. 'cigre_lv'."),
    framework: str | None = Query(
        None, description="Filter by voltage framework, e.g. 'prodist_m8_bt'."
    ),
    comparable_only: bool = Query(
        True,
        description="Return only rows valid for cross-method comparison "
        "(payload v2, status ok, bounded). Set false to audit legacy or failed runs.",
    ),
    limit: int = Query(100, ge=1, le=1000),
    db: Session = Depends(get_db),
) -> list[HostingCapacityResult]:
    """Side-by-side deterministic / stochastic / QSTS results.

    By default only comparable rows are returned, so a client can never
    mix pre-review (invalid) numbers with corrected ones. Compare rows
    only within the same ``criterion_framework`` (and ``load_scale``
    for snapshot methods).
    """
    sql = text(
        f"""
        SELECT {_COLUMNS}
        FROM public_gold.mart_hosting_capacity
        WHERE (:network IS NULL OR network = :network)
          AND (:framework IS NULL OR criterion_framework = :framework)
          AND (NOT :comparable_only OR is_comparable)
        ORDER BY run_timestamp DESC, method
        LIMIT :limit
        """
    )
    params = {
        "network": network,
        "framework": framework,
        "comparable_only": comparable_only,
        "limit": limit,
    }
    rows = db.execute(sql, params).mappings().all()
    return [HostingCapacityResult(**row) for row in rows]
