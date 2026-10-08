"""Data-quality checks and cleaning, with an auditable report.

Every loader runs its raw frame through these steps and attaches the
resulting :class:`QualityReport` to the :class:`~.base.ProfileSet`. The
report travels into the series manifest, so a hosting-capacity result
records *what was done to the data*, not only where it came from.

Severity levels:

* ``info``    -- a deterministic transformation (e.g. unit conversion).
* ``warning`` -- data were altered or are suspicious, but usable
  (short gaps interpolated, implausible values removed, flat lines).
* ``error``   -- the data contradict a physical invariant (e.g. solar
  timestamps misaligned with the sun). :func:`~.series.build_series`
  refuses a set with errors unless explicitly overridden.

Design rule: checks that *can* be measured are measured. Timestamp
conventions are verified against solar geometry (energy-weighted
centroid vs solar noon) instead of trusting documentation -- the real
files showed that INMET labels the end of the hour and that Ausgrid
follows the Sydney wall clock, DST included.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from .solar import alignment_offset_minutes, bsrn_ghi_limits, interval_mean_cos_zenith

SEVERITIES = ("info", "warning", "error")


@dataclass(frozen=True)
class QualityIssue:
    code: str
    severity: str
    detail: str
    count: int = 0

    def __post_init__(self) -> None:
        if self.severity not in SEVERITIES:
            raise ValueError(f"severity must be one of {SEVERITIES}")


@dataclass
class QualityReport:
    dataset: str
    issues: list[QualityIssue] = field(default_factory=list)
    stats: dict[str, Any] = field(default_factory=dict)

    def add(self, code: str, severity: str, detail: str, count: int = 0) -> None:
        self.issues.append(QualityIssue(code, severity, detail, int(count)))

    @property
    def has_errors(self) -> bool:
        return any(i.severity == "error" for i in self.issues)

    def errors(self) -> list[QualityIssue]:
        return [i for i in self.issues if i.severity == "error"]

    def to_dict(self) -> dict:
        return {
            "dataset": self.dataset,
            "has_errors": self.has_errors,
            "issues": [asdict(i) for i in self.issues],
            "stats": _jsonable(self.stats),
        }

    def summary(self) -> str:
        lines = [f"[{self.dataset}] {len(self.issues)} issue(s)"]
        for i in self.issues:
            n = f" (n={i.count})" if i.count else ""
            lines.append(f"  {i.severity.upper():7s} {i.code}{n}: {i.detail}")
        for k, v in self.stats.items():
            lines.append(f"  stat    {k} = {v}")
        return "\n".join(lines)


def _jsonable(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {str(k): _jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_jsonable(v) for v in obj]
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (np.floating, float)):
        return None if not np.isfinite(obj) else round(float(obj), 6)
    if isinstance(obj, (pd.Timestamp, pd.Timedelta, pd.Period)):
        return str(obj)
    return obj


# --------------------------------------------------------------- structure
def regularize(df: pd.DataFrame, resolution: pd.Timedelta, report: QualityReport) -> pd.DataFrame:
    """Sort, de-duplicate and reindex onto a complete regular UTC grid.

    Exact duplicate rows are dropped silently-but-counted; duplicate
    timestamps with *different* values are averaged and reported as a
    warning (the LCL files contain both kinds).
    """
    out = df[~df.index.isna()].sort_index()
    if out.index.has_duplicates:
        dup_mask = out.index.duplicated(keep=False)
        dups = out[dup_mask]
        n_ts = int(dups.index.nunique())
        conflicting = int((dups.groupby(level=0).nunique(dropna=False) > 1).any(axis=1).sum())
        out = out.groupby(level=0).mean()
        report.add(
            "duplicate_timestamps", "warning" if conflicting else "info",
            f"{n_ts} duplicated timestamp(s); {conflicting} with conflicting values "
            "(averaged), the rest identical (dropped)", n_ts,
        )
    off_grid = (out.index - out.index[0]) % resolution != pd.Timedelta(0)
    if off_grid.any():
        report.add("off_grid_timestamps", "warning",
                   f"timestamps not on the {resolution} grid were dropped", int(off_grid.sum()))
        out = out[~off_grid]
    grid = pd.date_range(out.index[0], out.index[-1], freq=resolution)
    missing = len(grid) - len(out)
    if missing:
        report.add("missing_timestamps", "warning",
                   f"{missing} interval(s) absent from the source were inserted as NaN", missing)
    return out.reindex(grid)


def fill_short_gaps(
    df: pd.DataFrame, max_gap_steps: int, report: QualityReport, label: str = ""
) -> pd.DataFrame:
    """Linearly interpolate NaN runs of length <= ``max_gap_steps`` (interior only)."""
    if max_gap_steps <= 0:
        return df
    filled = df.copy()
    n_filled = 0
    for col in df.columns:
        s = df[col]
        isna = s.isna()
        if not isna.any():
            continue
        run_id = (isna != isna.shift()).cumsum()
        run_len = isna.groupby(run_id).transform("sum")
        short = isna & (run_len <= max_gap_steps)
        interp = s.interpolate(method="time", limit_area="inside")
        filled.loc[short, col] = interp[short]
        n_filled += int((short & filled[col].notna()).sum())
    if n_filled:
        report.add("short_gaps_interpolated", "warning",
                   f"{label}gaps of <= {max_gap_steps} step(s) linearly interpolated", n_filled)
    return filled


def clip_negative(df: pd.DataFrame, report: QualityReport, tolerance: float = 0.0) -> pd.DataFrame:
    neg = df < -abs(tolerance)
    n = int(neg.sum().sum())
    if n:
        report.add("negative_values", "warning", "negative values set to NaN", n)
    small = (df < 0) & ~neg
    return df.mask(neg).mask(small, 0.0)


def flag_flatlines(df: pd.DataFrame, min_run: int, report: QualityReport) -> None:
    """Warn about runs of >= ``min_run`` identical non-zero values (stuck/estimated reads)."""
    total = 0
    cols = 0
    for col in df.columns:
        s = df[col]
        same = (s == s.shift()) & s.notna() & (s != 0)
        run_id = (~same).cumsum()
        lengths = same.groupby(run_id).sum() + 1
        long_runs = lengths[lengths >= min_run]
        if len(long_runs):
            total += int(long_runs.sum())
            cols += 1
    if total:
        report.add("flat_lines", "warning",
                   f"{cols} series with constant non-zero runs >= {min_run} steps", total)


def completeness(df: pd.DataFrame) -> pd.Series:
    return df.notna().mean()


# ------------------------------------------------------------- irradiance
def check_irradiance(
    ghi: pd.Series,
    resolution: pd.Timedelta,
    latitude: float,
    longitude: float,
    report: QualityReport,
    night_tolerance_wm2: float = 20.0,
) -> pd.Series:
    """BSRN plausibility on GHI; returns the cleaned series.

    * above the physically possible limit -> NaN (reported, ``warning``);
    * above the extremely rare limit       -> kept, counted;
    * sun below the horizon for the whole interval but GHI > tolerance
      -> set to 0 (reported); small night values (sensor offset) -> 0.
    """
    g = ghi.astype(float).copy()
    # Night first: it is the more specific diagnosis (the BSRN limit is
    # only 100 W/m2 at night, so a night spike would otherwise be filed
    # as "physically impossible").
    mu0 = interval_mean_cos_zenith(g.index, resolution, latitude, longitude)
    night = mu0 <= 0.0
    bad_night = night & (g.to_numpy() > night_tolerance_wm2)
    if bad_night.any():
        report.add("irradiance_at_night", "warning",
                   f"GHI > {night_tolerance_wm2} W/m2 with the sun below the horizon set to 0",
                   int(bad_night.sum()))
    night_nan = night & g.isna().to_numpy()
    if night_nan.any():
        report.add("night_zero_filled", "info",
                   "missing GHI with the sun below the horizon set to 0 (known value)",
                   int(night_nan.sum()))
    g[night] = 0.0
    ppl, erl = bsrn_ghi_limits(g.index, resolution, latitude, longitude)
    above_ppl = g.to_numpy() > ppl
    if above_ppl.any():
        report.add("ghi_above_physical_limit", "warning",
                   "GHI above the BSRN physically possible limit set to NaN", int(above_ppl.sum()))
        g[above_ppl] = np.nan
    above_erl = (g.to_numpy() > erl) & ~above_ppl
    if above_erl.any():
        report.add("ghi_above_rare_limit", "info",
                   "GHI above the BSRN extremely rare limit (kept)", int(np.nansum(above_erl)))
    return g


def check_solar_alignment(
    values: pd.Series,
    resolution: pd.Timedelta,
    longitude: float,
    report: QualityReport,
    tolerance_min: float | None = None,
) -> pd.DataFrame:
    """Error if the solar-driven series is not centred on solar noon.

    Tolerance defaults to ``max(20 min, resolution / 3)``. A monthly
    spread above 45 min with a ~60 min step is reported as a DST-like
    shift (``error``: the loader's clock convention is wrong).
    """
    monthly = alignment_offset_minutes(values, resolution, longitude)
    if monthly.empty:
        report.add("alignment_not_measured", "warning", "not enough daylight energy to check timing")
        return monthly
    tol = tolerance_min if tolerance_min is not None else max(20.0, resolution / pd.Timedelta("1min") / 3)
    overall = float(np.average(monthly["offset_min"], weights=monthly["days"]))
    spread = float(monthly["offset_min"].max() - monthly["offset_min"].min())
    report.stats["solar_alignment_offset_min"] = round(overall, 1)
    report.stats["solar_alignment_monthly_spread_min"] = round(spread, 1)
    if abs(overall) > tol:
        report.add("timestamp_misaligned", "error",
                   f"energy centroid is {overall:+.0f} min from solar noon (tolerance {tol:.0f} min): "
                   "interval labelling or time zone is wrong")
    if len(monthly) >= 3 and spread > 45.0:
        report.add("dst_like_shift", "error",
                   f"monthly centroid offsets span {spread:.0f} min: a DST shift is not handled")
    return monthly


__all__ = [
    "QualityIssue", "QualityReport", "check_irradiance", "check_solar_alignment",
    "clip_negative", "completeness", "fill_short_gaps", "flag_flatlines", "regularize",
]
