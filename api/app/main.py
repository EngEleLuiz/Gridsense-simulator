"""GridSense read API — a thin, typed HTTP layer over the Gold marts
built by the dbt transform project (Phase 3). Every endpoint maps to
exactly one Gold table; no business logic lives here, only query
parameters (filtering, pagination) and response typing.

Run locally with:
    uvicorn app.main:app --reload --port 8000

Interactive docs: http://localhost:8000/docs
"""

from __future__ import annotations

from fastapi import FastAPI

from .routers import health, hosting_capacity, kpis, lines, voltage

app = FastAPI(
    title="GridSense API",
    description=(
        "Read-only API over the GridSense Gold layer (TimescaleDB + dbt). "
        "Serves pre-aggregated grid KPIs, voltage-quality, line-loading, "
        "and hosting-capacity comparison metrics for dashboards and "
        "downstream tools."
    ),
    version="1.0.0",
)

app.include_router(health.router)
app.include_router(kpis.router)
app.include_router(voltage.router)
app.include_router(lines.router)
app.include_router(hosting_capacity.router)
