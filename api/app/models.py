"""Response models. Field names mirror the Gold mart columns exactly,
so the API is a thin, typed, documented window onto
`transform/models/gold/` -- no relabeling or reshaping happens here.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class HealthStatus(BaseModel):
    status: str
    database: str


class DailyKPI(BaseModel):
    network: str
    day_bucket: datetime
    avg_load_mw: float
    peak_load_mw: float
    min_load_mw: float
    avg_generation_mw: float
    load_factor: float | None = None
    n_contingency_steps: int
    n_steps: int


class HourlyVoltageQuality(BaseModel):
    network: str
    bus_id: int
    hour_bucket: datetime
    avg_voltage_pu: float
    min_voltage_pu: float
    max_voltage_pu: float
    stddev_voltage_pu: float | None = None
    n_readings: int
    n_violations: int
    violation_rate_pct: float | None = None


class HourlyLineLoading(BaseModel):
    network: str
    line_id: int
    hour_bucket: datetime
    avg_loading_percent: float
    max_loading_percent: float
    n_readings: int
    n_overload_events: int
    overload_rate_pct: float | None = None
