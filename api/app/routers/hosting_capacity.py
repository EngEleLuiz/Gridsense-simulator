from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import text
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import HostingCapacityResult

router = APIRouter(prefix="/api/v1/hosting-capacity", tags=["hosting-capacity"])


@router.get("/compare", response_model=list[HostingCapacityResult])
def compare_hosting_capacity(
    network: str | None = Query(None, description="Filter by network, e.g. 'cigre_lv'."),
    limit: int = Query(100, ge=1, le=1000),
    db: Session = Depends(get_db),
) -> list[HostingCapacityResult]:
    """Side-by-side comparison of the deterministic, stochastic, and
    QSTS hosting-capacity methodologies, from
    `gold.mart_hosting_capacity`.

    Read the response's `total_pv_mw_comparable` field with care for
    `method="stochastic"` rows: at the default Monte Carlo sampling
    range, it is a lower bound under an arbitrary PV-size budget, not
    yet a ceiling comparable to the deterministic/qsts rows. See
    `simulator/gridsense_sim/hosting_capacity/stochastic.py`'s module
    docstring.
    """
    sql = text(
        """
        SELECT network, method, run_id, run_timestamp,
               total_pv_mw_comparable, total_pv_mw_p95, lambda_max,
               binding_constraint, violation_rate, n_trials,
               qsts_total_steps, qsts_steps_per_day
        FROM public_gold.mart_hosting_capacity
        WHERE (:network IS NULL OR network = :network)
        ORDER BY run_timestamp DESC, method
        LIMIT :limit
        """
    )
    rows = db.execute(sql, {"network": network, "limit": limit}).mappings().all()
    return [HostingCapacityResult(**row) for row in rows]
