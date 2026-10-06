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

    Mirrors ``gold.mart_hosting_capacity``. Only rows with
    ``is_comparable = True`` carry ``total_pv_mw_comparable``; compare
    such rows only when ``criterion_framework`` (and, for snapshot
    methods, ``load_scale``) are equal. Method-specific fields are
    ``None`` when not applicable.
    """

    network: str
    method: str
    run_id: str
    run_timestamp: datetime
    schema_version: int = 1
    status: str = "legacy_invalid"
    error: str | None = None
    is_comparable: bool = False
    is_bounded: bool = False
    criterion_framework: str | None = None
    criterion_kind: str | None = None
    v_min_pu: float | None = None
    v_max_pu: float | None = None
    load_scale: float | None = None
    total_pv_mw_comparable: float | None = None
    total_pv_mw_reported: float | None = None
    total_pv_mw_p50: float | None = None
    lambda_max: float | None = None
    lambda_fail: float | None = None
    binding_constraint: str | None = None
    hc_alpha: float | None = None
    hc_lambda_ci_low: float | None = None
    hc_lambda_ci_high: float | None = None
    n_scenarios: int | None = None
    n_censored: int | None = None
    qsts_criterion: str | None = None
    qsts_total_steps: int | None = None
    qsts_steps_per_day: int | None = None
    qsts_first_violating_step: int | None = None
    git_commit: str | None = None
    git_dirty: bool | None = None

