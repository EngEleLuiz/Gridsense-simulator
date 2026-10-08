"""Turn real load and PV/irradiance data into a QSTS :class:`TimeSeries`.

Pipeline (each step recorded in the manifest)::

    load ProfileSet --select/aggregate--> one demand series (UTC)
    pv   ProfileSet --PV model / fleet mean--> one PV per-unit series (UTC)
         |  resample to the study resolution (mean down, hold/linear up)
         |  move onto the study clock
         |     load: the *source's wall clock* -- people follow the local clock, DST included
         |     PV:   the PV site's *standard* time -- the sun ignores DST
         |  pick a contiguous source window matching the study dates
         |     (+ ~6-month season shift when hemispheres differ)
         |  fill remaining gaps from same-time-of-day climatology (+-7 days), counted
         |  load_mult = demand / source peak * peak_load_mult   (nominal load = peak demand)
         v
    TimeSeries(load_mult, pv_mult) + manifest (sources, SHA-256, QC, diagnostics)

Why aggregate one load series instead of one per bus: the QSTS engine
applies a single multiplier to every load (that is what made Phase 6
comparable across methods). The aggregate of many real homes carries
the realistic *diversified* shape -- evening peak, midday valley -- that
the synthetic profile lacked (review finding C9). Per-bus assignment is
a later extension; the per-customer columns are kept in the ProfileSet
for it.

Normalisation: by default the multiplier is scaled so the **whole source
period's** peak equals ``peak_load_mult`` (1.0: CIGRE nominal load =
peak demand, the convention adopted for finding R22). A summer window
therefore sits below 1.0, which is physically meaningful; use
``normalize="window_peak"`` to force the window's own peak to 1.0.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from ..hosting_capacity.timeseries import TimeSeries
from .base import DatasetError, ProfileKind, ProfileSet, Site
from .solar import PVModel, ghi_to_pv_pu

SERIES_SCHEMA_VERSION = 1
PARQUET_META_KEY = b"gridsense_series_manifest"
CLIMATOLOGY_HALF_WINDOW_DAYS = 7
PV_ZERO_THRESHOLD = 1e-4

PRESET_SITES: dict[str, Site] = {
    "florianopolis": Site("Florianopolis/SC", -27.5954, -48.5480, -3.0, "America/Sao_Paulo"),
    "sao_paulo": Site("Sao Paulo/SP", -23.5505, -46.6333, -3.0, "America/Sao_Paulo"),
    "porto_alegre": Site("Porto Alegre/RS", -30.0346, -51.2177, -3.0, "America/Sao_Paulo"),
    "natal": Site("Natal/RN", -5.7945, -35.2110, -3.0, "America/Fortaleza"),
    "brasilia": Site("Brasilia/DF", -15.7939, -47.8828, -3.0, "America/Sao_Paulo"),
}


@dataclass
class RealSeries:
    """A built series: the engine input plus everything needed to audit it."""

    series: TimeSeries
    local_index: pd.DatetimeIndex  # naive, study clock (target local standard time)
    manifest: dict[str, Any]

    @property
    def manifest_sha256(self) -> str:
        return self.manifest["manifest_sha256"]

    def frame(self) -> pd.DataFrame:
        return pd.DataFrame(
            {"load_mult": self.series.load_mult, "pv_mult": self.series.pv_mult},
            index=self.local_index,
        )


# ---------------------------------------------------------------- helpers
def _distance_km(a: Site, b: Site) -> float:
    lat1, lon1, lat2, lon2 = map(np.deg2rad, (a.latitude, a.longitude, b.latitude, b.longitude))
    h = np.sin((lat2 - lat1) / 2) ** 2 + np.cos(lat1) * np.cos(lat2) * np.sin((lon2 - lon1) / 2) ** 2
    return float(2 * 6371.0 * np.arcsin(np.sqrt(h)))


def _resample(s: pd.Series, src: pd.Timedelta, dst: pd.Timedelta, upsample: str) -> pd.Series:
    """Interval means on a UTC grid of ``dst``."""
    if dst == src:
        return s
    if dst > src:
        if dst % src != pd.Timedelta(0):
            raise DatasetError(f"Study resolution {dst} is not a multiple of the source {src}.")
        out = s.resample(dst, origin="epoch", label="left", closed="left").mean()
        counts = s.resample(dst, origin="epoch", label="left", closed="left").count()
        return out.where(counts >= (dst / src) / 2)
    if src % dst != pd.Timedelta(0):
        raise DatasetError(f"Source resolution {src} is not a multiple of the study {dst}.")
    k = int(src / dst)
    fine = pd.date_range(s.index[0], s.index[-1] + src - dst, freq=dst)
    if upsample == "hold":  # energy-exact
        return s.reindex(fine, method="ffill", limit=k - 1)
    if upsample == "linear":  # interval means placed at midpoints, then interpolated
        mid = s.copy()
        mid.index = mid.index + src / 2
        both = mid.reindex(mid.index.union(fine + dst / 2)).interpolate(method="time", limit_area="inside")
        out = both.reindex(fine + dst / 2)
        out.index = fine
        return out.bfill(limit=k).ffill(limit=k)
    raise ValueError("upsample must be 'hold' or 'linear'.")


def _to_clock(s: pd.Series, res: pd.Timedelta, tz_name: str | None, offset_h: float) -> pd.Series:
    """UTC series -> naive regular series on a local clock.

    With a DST zone the wall clock repeats one hour (averaged) and skips
    one hour (interpolated): that is how a household experiences it.
    """
    if tz_name:
        local = s.tz_convert(tz_name)
        naive = local.copy()
        naive.index = local.index.tz_localize(None)
        naive = naive.groupby(level=0).mean()
    else:
        naive = s.copy()
        naive.index = s.index.tz_localize(None) + pd.Timedelta(hours=offset_h)
    grid = pd.date_range(naive.index[0].floor("D"), naive.index[-1].ceil("D"), freq=res, inclusive="left")
    out = naive.reindex(grid)
    # Interpolate only the slots the clock change created (spring-forward
    # hour); genuine data gaps stay NaN for the explicit gap policy.
    created = ~grid.isin(naive.index)
    created &= (grid >= naive.index[0]) & (grid <= naive.index[-1])
    if created.any():
        inner = out.interpolate(method="time", limit_area="inside")
        out[created] = inner[created]
    return out


def _season_shift(src: Site | None, target: Site, requested: int | None) -> int:
    if requested is not None:
        return int(requested)
    if src is None:
        return 0
    return 182 if src.hemisphere != target.hemisphere else 0


def _pick_window(
    s: pd.Series, target_start: pd.Timestamp, days: int, shift_days: int,
    source_start: str | None, label: str,
) -> pd.Timestamp:
    """Start (naive local midnight) of a fully covered contiguous source window."""
    first = s.first_valid_index()
    last = s.last_valid_index()
    if first is None:
        raise DatasetError(f"{label}: the series is empty.")
    lo, hi = first.normalize(), last.normalize() + pd.Timedelta("1D")
    length = pd.Timedelta(days=days)
    if source_start is not None:
        cand = pd.Timestamp(source_start).normalize()
        if cand < lo or cand + length > hi:
            raise DatasetError(f"{label}: source window {cand.date()} + {days} d outside "
                               f"coverage {lo.date()}..{hi.date()}.")
        return cand
    want = target_start - pd.Timedelta(days=shift_days)
    for year in range(lo.year, hi.year + 1):
        try:
            cand = want.replace(year=year)
        except ValueError:  # 29 Feb in a common year
            cand = pd.Timestamp(year=year, month=2, day=28)
        if cand >= lo and cand + length <= hi:
            return cand
    raise DatasetError(
        f"{label}: no {days}-day window starting on {want.strftime('%d %b')} fits the source "
        f"coverage {lo.date()}..{hi.date()} (season shift {shift_days} d). Choose another "
        f"--start/--days or pass an explicit source start."
    )


def _climatology_fill(full: pd.Series, window: pd.Series, res: pd.Timedelta) -> tuple[pd.Series, int]:
    """Fill NaN in ``window`` with the median of the same time of day, +-7 days, from ``full``."""
    missing = window.index[window.isna()]
    if len(missing) == 0:
        return window, 0
    out = window.copy()
    tod = full.index - full.index.normalize()
    for t in missing:
        lo, hi = t - pd.Timedelta(days=CLIMATOLOGY_HALF_WINDOW_DAYS), t + pd.Timedelta(days=CLIMATOLOGY_HALF_WINDOW_DAYS)
        mask = (full.index >= lo) & (full.index <= hi) & (tod == (t - t.normalize()))
        vals = full[mask].dropna()
        if len(vals):
            out[t] = float(vals.median())
    return out, int(len(missing) - out.isna().sum())


def _check_quality(ps: ProfileSet, allow: bool) -> None:
    if ps.quality is not None and ps.quality.has_errors and not allow:
        codes = ", ".join(i.code for i in ps.quality.errors())
        raise DatasetError(f"{ps.dataset}: quality errors ({codes}); pass allow_quality_errors=True "
                           "only if you understand why.")


def _aggregate_load(ps: ProfileSet, n_series: int | None, seed: int) -> tuple[pd.Series, list[str]]:
    if ps.kind not in (ProfileKind.LOAD_KW, ProfileKind.LOAD_PU):
        raise DatasetError(f"{ps.dataset}: {ps.kind.value} is not a load profile.")
    cols = sorted(ps.data.columns)
    if n_series is not None:
        if n_series < 1 or n_series > len(cols):
            raise DatasetError(f"{ps.dataset}: n_series must be in [1, {len(cols)}].")
        rng = np.random.default_rng(seed)
        cols = sorted(rng.choice(cols, size=n_series, replace=False).tolist())
    data = ps.data[cols]
    # mean x count keeps the aggregate unbiased when a home misses a step
    return data.mean(axis=1) * len(cols), cols


def _pv_per_unit(ps: ProfileSet, model: PVModel) -> tuple[pd.Series, dict]:
    if ps.kind is ProfileKind.IRRADIANCE:
        if "ghi_wm2" not in ps.data:
            raise DatasetError(f"{ps.dataset}: irradiance set without ghi_wm2.")
        temp = ps.data["temp_air_c"] if "temp_air_c" in ps.data else None
        pu = ghi_to_pv_pu(ps.data["ghi_wm2"], temp, model).where(ps.data["ghi_wm2"].notna())
        return pu, {"conversion": model.to_dict(), "temperature": temp is not None}
    if ps.kind is ProfileKind.PV_PU:
        caps = ps.meta.get("capacity_kwp") or {}
        cols = list(ps.data.columns)
        if caps and all(c in caps for c in cols):
            w = pd.Series({c: float(caps[c]) for c in cols})
            pu = (ps.data[cols] * w).sum(axis=1, min_count=1) / ps.data[cols].notna().mul(w).sum(axis=1)
            return pu, {"conversion": "capacity_weighted_fleet_mean", "n_series": len(cols)}
        return ps.data[cols].mean(axis=1), {"conversion": "mean_of_series", "n_series": len(cols)}
    raise DatasetError(f"{ps.dataset}: {ps.kind.value} cannot provide PV.")


def _digest_arrays(*arrays: np.ndarray) -> str:
    h = hashlib.sha256()
    for a in arrays:
        h.update(np.ascontiguousarray(a, dtype="<f8").tobytes())
    return h.hexdigest()


def _canonical_sha(obj: dict) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, default=str).encode()).hexdigest()


def _diagnostics(local: pd.DatetimeIndex, load: np.ndarray, pv: np.ndarray) -> dict:
    df = pd.DataFrame({"load": load, "pv": pv}, index=local)
    by_hour = df.groupby(df.index.hour).mean()
    day = pv > 0
    ratio = np.where(day, pv / load, 0.0)
    return {
        "load_mult_min": float(load.min()), "load_mult_max": float(load.max()),
        "load_mult_mean": float(load.mean()),
        "load_mean_peak_hour": int(by_hour["load"].idxmax()),
        "pv_mean_peak_hour": int(by_hour["pv"].idxmax()),
        "load_at_pv_peak_hour_over_load_peak": float(
            by_hour["load"].loc[by_hour["pv"].idxmax()] / by_hour["load"].max()),
        "pv_capacity_factor": float(pv.mean()),
        "pv_max": float(pv.max()),
        "daylight_steps": int(day.sum()),
        "max_pv_to_load_ratio": float(ratio.max()),
        "corr_load_pv_daylight": float(np.corrcoef(load[day], pv[day])[0, 1]) if day.sum() > 2 else None,
    }


# ----------------------------------------------------------------- public
def build_series(
    load: ProfileSet,
    pv: ProfileSet,
    *,
    target: Site,
    start: str,
    days: int,
    steps_per_day: int = 96,
    peak_load_mult: float = 1.0,
    normalize: str = "source_peak",
    load_season_shift_days: int | None = None,
    pv_season_shift_days: int | None = None,
    load_source_start: str | None = None,
    pv_source_start: str | None = None,
    n_load_series: int | None = None,
    seed: int = 42,
    pv_model: PVModel | None = None,
    upsample: str = "hold",
    climatology_fill: bool = True,
    allow_quality_errors: bool = False,
) -> RealSeries:
    """Build a QSTS series on ``target``'s local standard clock.

    Args:
        start: First study day (local), ``YYYY-MM-DD``. Its year labels the
            study calendar only; data come from the matching source window.
        days: Study length in days.
        steps_per_day: Study resolution (must divide 1440 min).
        peak_load_mult: Value the normalisation peak is mapped to.
        normalize: ``"source_peak"`` (default) or ``"window_peak"``.
        *_season_shift_days: ``None`` = automatic (182 d if the source is
            in the other hemisphere, else 0).
        *_source_start: Pin the source window start (``YYYY-MM-DD``).
        n_load_series: Random subset of homes (``seed``); ``None`` = all.
        upsample: ``"hold"`` (energy-exact) or ``"linear"`` when the study
            is finer than a source.
    """
    if days < 1:
        raise ValueError("days must be >= 1.")
    if steps_per_day < 1 or (1440 % steps_per_day):
        raise ValueError("steps_per_day must divide 1440 (minutes per day).")
    if peak_load_mult <= 0:
        raise ValueError("peak_load_mult must be > 0.")
    if normalize not in ("source_peak", "window_peak"):
        raise ValueError("normalize must be 'source_peak' or 'window_peak'.")
    for ps in (load, pv):
        _check_quality(ps, allow_quality_errors)
    res = pd.Timedelta(days=1) / steps_per_day
    model = pv_model or PVModel()
    t0 = pd.Timestamp(start).normalize()
    n = days * steps_per_day
    warnings: list[str] = []

    # ---- load
    load_utc, load_cols = _aggregate_load(load, n_load_series, seed)
    load_res = _resample(load_utc, load.resolution, res, upsample)
    ls = load.site or target
    load_local = _to_clock(load_res, res, ls.tz_name, ls.utc_offset_hours)
    l_shift = _season_shift(load.site, target, load_season_shift_days)
    l0 = _pick_window(load_local, t0, days, l_shift, load_source_start, load.dataset)
    l_win = load_local.loc[l0: l0 + pd.Timedelta(days=days) - res]
    l_filled = 0
    if climatology_fill:
        l_win, l_filled = _climatology_fill(load_local, l_win, res)
    if len(l_win) != n or l_win.isna().any():
        raise DatasetError(f"{load.dataset}: {int(l_win.isna().sum())} load step(s) still missing "
                           "in the window after gap filling.")
    peak = float(load_local.max() if normalize == "source_peak" else l_win.max())
    load_mult = l_win.to_numpy(float) / peak * peak_load_mult
    nonpos = int((load_mult <= 0).sum())
    if nonpos:
        load_mult = np.maximum(load_mult, 1e-3 * peak_load_mult)
        warnings.append(f"{nonpos} non-positive load step(s) floored at 1e-3 x peak")

    # ---- PV
    pv_utc, pv_conv = _pv_per_unit(pv, model)
    pv_res = _resample(pv_utc, pv.resolution, res, upsample)
    ps_site = pv.site or target
    pv_local = _to_clock(pv_res, res, None, ps_site.utc_offset_hours)  # standard time: sun has no DST
    p_shift = _season_shift(pv.site, target, pv_season_shift_days)
    p0 = _pick_window(pv_local, t0, days, p_shift, pv_source_start, pv.dataset)
    p_win = pv_local.loc[p0: p0 + pd.Timedelta(days=days) - res]
    p_filled = 0
    if climatology_fill:
        p_win, p_filled = _climatology_fill(pv_local, p_win, res)
    if len(p_win) != n or p_win.isna().any():
        raise DatasetError(f"{pv.dataset}: {int(p_win.isna().sum())} PV step(s) still missing "
                           "in the window after gap filling.")
    pv_mult = np.clip(p_win.to_numpy(float), 0.0, 1.0)
    pv_mult[pv_mult < PV_ZERO_THRESHOLD] = 0.0
    if pv.site is not None and _distance_km(pv.site, target) > 100.0:
        warnings.append(f"PV source site is {_distance_km(pv.site, target):.0f} km from the target")
    if pv.site is not None and abs(pv.site.utc_offset_hours - target.utc_offset_hours) > 0:
        warnings.append("PV source keeps its own standard time (solar timing), not the target's")

    local_index = pd.date_range(t0, periods=n, freq=res)
    digest = _digest_arrays(load_mult, pv_mult)

    def _src(ps: ProfileSet, shift: int, w0: pd.Timestamp, extra: dict) -> dict:
        return {
            "dataset": ps.dataset, "kind": ps.kind.value,
            "site": ps.site.to_dict() if ps.site else None,
            "sources": [s.to_dict() for s in ps.sources],
            "source_resolution": str(ps.resolution),
            "season_shift_days": shift,
            "source_window_local": [str(w0.date()), str((w0 + pd.Timedelta(days=days)).date())],
            "meta": {k: v for k, v in ps.meta.items() if isinstance(v, (str, int, float, bool, list, type(None)))},
            "quality": ps.quality.to_dict() if ps.quality else None,
            **extra,
        }

    manifest: dict[str, Any] = {
        "schema_version": SERIES_SCHEMA_VERSION,
        "created_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "target_site": target.to_dict(),
        "clock": f"local standard time UTC{target.utc_offset_hours:+g} (no DST)",
        "start_local": str(t0.date()),
        "days": days,
        "steps_per_day": steps_per_day,
        "n_steps": n,
        "upsample": upsample,
        "load": _src(load, l_shift, l0, {
            "n_series": len(load_cols),
            "series_ids_sha256": hashlib.sha256(",".join(load_cols).encode()).hexdigest(),
            "selection_seed": seed if n_load_series is not None else None,
            "normalize": normalize, "normalization_peak": peak, "peak_load_mult": peak_load_mult,
            "clock": f"source wall clock ({ls.tz_name or 'fixed offset'})",
            "climatology_filled_steps": l_filled,
        }),
        "pv": _src(pv, p_shift, p0, {
            **pv_conv, "clock": f"source standard time UTC{ps_site.utc_offset_hours:+g}",
            "climatology_filled_steps": p_filled,
        }),
        "diagnostics": _diagnostics(local_index, load_mult, pv_mult),
        "warnings": warnings,
        "arrays_sha256": digest,
    }
    manifest = json.loads(json.dumps(manifest, sort_keys=True, default=str))  # JSON-normal form
    manifest["manifest_sha256"] = _canonical_sha(manifest)
    ts = TimeSeries(
        load_mult=load_mult, pv_mult=pv_mult, steps_per_day=steps_per_day, start_step=0,
        source=f"real:{load.dataset}+{pv.dataset}", manifest_sha256=manifest["manifest_sha256"],
    )
    return RealSeries(ts, local_index, manifest)


def save_series(real: RealSeries, path: str | Path) -> Path:
    """Write ``<path>.parquet`` (manifest embedded) and ``<path>.manifest.json``."""
    try:
        import pyarrow as pa
        import pyarrow.parquet as pq
    except ImportError as exc:  # pragma: no cover
        raise DatasetError('Saving a series needs pyarrow: pip install -e "simulator[data]"') from exc
    path = Path(path)
    if path.suffix != ".parquet":
        path = path.with_suffix(".parquet")
    path.parent.mkdir(parents=True, exist_ok=True)
    table = pa.table({
        "local_time": pa.array(real.local_index.strftime("%Y-%m-%dT%H:%M:%S")),
        "load_mult": pa.array(np.asarray(real.series.load_mult, dtype="float64")),
        "pv_mult": pa.array(np.asarray(real.series.pv_mult, dtype="float64")),
    })
    blob = json.dumps(real.manifest, sort_keys=True, default=str).encode()
    table = table.replace_schema_metadata({PARQUET_META_KEY: blob})
    pq.write_table(table, path)
    path.with_suffix(".manifest.json").write_text(
        json.dumps(real.manifest, indent=2, sort_keys=True, default=str), encoding="utf-8")
    return path


def load_series(path: str | Path) -> RealSeries:
    """Read a saved series and verify that its arrays match the manifest."""
    try:
        import pyarrow.parquet as pq
    except ImportError as exc:  # pragma: no cover
        raise DatasetError('Reading a series needs pyarrow: pip install -e "simulator[data]"') from exc
    path = Path(path)
    table = pq.read_table(path)
    meta = (table.schema.metadata or {}).get(PARQUET_META_KEY)
    if meta is None:
        raise DatasetError(f"{path}: no GridSense series manifest in the Parquet metadata.")
    manifest = json.loads(meta)
    load_mult = table.column("load_mult").to_numpy()
    pv_mult = table.column("pv_mult").to_numpy()
    if _digest_arrays(load_mult, pv_mult) != manifest.get("arrays_sha256"):
        raise DatasetError(f"{path}: series values do not match the manifest (file was modified).")
    body = {k: v for k, v in manifest.items() if k != "manifest_sha256"}
    if _canonical_sha(body) != manifest.get("manifest_sha256"):
        raise DatasetError(f"{path}: manifest content does not match its own hash.")
    ts = TimeSeries(
        load_mult=load_mult, pv_mult=pv_mult, steps_per_day=int(manifest["steps_per_day"]),
        start_step=0, source=f"real:{manifest['load']['dataset']}+{manifest['pv']['dataset']}",
        manifest_sha256=manifest["manifest_sha256"],
    )
    idx = pd.DatetimeIndex(pd.to_datetime(table.column("local_time").to_pylist()))
    return RealSeries(ts, idx, manifest)


__all__ = ["PRESET_SITES", "RealSeries", "SERIES_SCHEMA_VERSION", "build_series", "load_series",
           "save_series"]
