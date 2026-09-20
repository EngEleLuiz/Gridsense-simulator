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


class HostingCapacityResult(BaseModel):
    """One method's result for one hosting-capacity study run.

    Method-specific fields are None when not applicable to that row's
    method (see transform/models/staging/stg_hosting_capacity_results.sql).
    total_pv_mw_comparable is the one field every method populates --
    read stochastic's value there with care, it is not yet calibrated
    to the same ceiling as the deterministic/qsts methods.
    """

    network: str
    method: str
    run_id: str
    run_timestamp: datetime
    total_pv_mw_comparable: float | None = None
    total_pv_mw_p95: float | None = None
    lambda_max: float | None = None
    binding_constraint: str | None = None
    violation_rate: float | None = None
    n_trials: int | None = None
    qsts_total_steps: int | None = None
    qsts_steps_per_day: int | None = None
