"""Open Climate Fix ``uk_pv``: measured rooftop PV output across Great Britain.

Sheffield Solar's metered PV systems, published by Open Climate Fix on
Hugging Face (``openclimatefix/uk_pv``, CC-BY-4.0, doi:10.57967/hf/0878),
2010-2025. Two products:

* ``30_minutely`` -- every system; *energy* accumulated on the meter over
  each half hour: true interval means, low noise.
* ``5_minutely`` -- about 1,300 systems; *instantaneous* readings every
  5 minutes: the resolution a 10-minute regulatory criterion needs, but
  noisy (two samples are averaged per 10-minute reading).

Layout (from the dataset card): ``metadata.csv`` (``ss_id``,
``latitude_rounded``, ``longitude_rounded``, ``orientation``, ``tilt``,
``kWp``, ...); ``{5,30}_minutely/year=YYYY/month=MM/data.parquet`` with
``ss_id``, ``datetime_GMT`` and ``generation_Wh``; ``bad_data.csv``
(``ss_id``, ``start_datetime_GMT``, ``end_datetime_GMT``, ``reason``).
Each row covers the period **ending** at ``datetime_GMT`` (UTC).

The dataset is gated (accept the terms on Hugging Face). Download only
the months you need, e.g.::

    huggingface-cli download openclimatefix/uk_pv --repo-type dataset \\
        --include "metadata.csv" "bad_data.csv" "5_minutely/year=2013/*" \\
        --local-dir data/raw/uk_pv

This loader then selects the systems within ``radius_km`` of a site,
applies the cleaning the card recommends (negative values, output above
the physical bound, ``bad_data.csv`` periods, energy at night) and
returns per-system output in per unit of ``kWp`` together with each
system's capacity, so the series builder forms a capacity-weighted fleet
profile. Timing is re-verified against solar noon like every other source.
"""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pandas as pd

from .base import DatasetError, DatasetInfo, ProfileKind, ProfileSet, Site, SourceFile
from .quality import (
    QualityReport,
    check_solar_alignment,
    clip_negative,
    fill_short_gaps,
    regularize,
)
from .solar import interval_mean_cos_zenith

INFO = DatasetInfo(
    key="uk_pv",
    title="UK PV: metered domestic PV generation in Great Britain (Sheffield Solar / Open Climate Fix)",
    publisher="Open Climate Fix, with data from Sheffield Solar",
    provides=("pv",),
    coverage="30,000+ GB systems, 2010-2025 (about 1,300 with 5-minute readings)",
    resolution="5 min (instantaneous) / 30 min (energy)",
    license="CC-BY-4.0",
    citation="Open Climate Fix (2025). uk_pv [dataset]. Hugging Face. doi:10.57967/hf/0878",
    homepage="https://huggingface.co/datasets/openclimatefix/uk_pv",
    access="Gated Hugging Face dataset: accept the terms, download metadata.csv and the needed months",
    notes=(
        "Stamps are UTC and mark the end of each period.",
        "5-minute values are instantaneous readings (noisy); 30-minute values are metered energy.",
        "Locations rounded to about 1 km.",
    ),
)

RESOLUTIONS = {"5min": ("5_minutely", pd.Timedelta("5min")), "30min": ("30_minutely", pd.Timedelta("30min"))}
LONDON = (51.5074, -0.1278)


def _haversine_km(lat1, lon1, lat2, lon2) -> np.ndarray:
    lat1, lon1, lat2, lon2 = map(np.deg2rad, (lat1, lon1, lat2, lon2))
    h = np.sin((lat2 - lat1) / 2) ** 2 + np.cos(lat1) * np.cos(lat2) * np.sin((lon2 - lon1) / 2) ** 2
    return 2 * 6371.0 * np.arcsin(np.sqrt(h))


def select_systems(metadata: pd.DataFrame, latitude: float, longitude: float, radius_km: float,
                   min_kwp: float = 0.5, max_kwp: float = 10.0) -> pd.DataFrame:
    """Domestic-size systems within ``radius_km`` of the site."""
    need = {"ss_id", "latitude_rounded", "longitude_rounded", "kWp"}
    if not need.issubset(metadata.columns):
        raise DatasetError(f"uk_pv metadata: missing columns {need - set(metadata.columns)}.")
    d = _haversine_km(latitude, longitude, metadata["latitude_rounded"].to_numpy(float),
                      metadata["longitude_rounded"].to_numpy(float))
    sel = metadata.assign(distance_km=d)
    sel = sel[(sel["distance_km"] <= radius_km) & sel["kWp"].between(min_kwp, max_kwp)]
    return sel.sort_values("distance_km")


def _partition_files(root: Path, product: str, start: pd.Timestamp, end: pd.Timestamp) -> list[Path]:
    base = root / product
    files = []
    month = pd.Timestamp(year=start.year, month=start.month, day=1)
    last = end.tz_localize(None) if end.tzinfo is not None else end
    while month <= last:  # every month touched by [start, end]; the end stamp may fall in the next one
        for pattern in (f"year={month.year}/month={month.month}/*.parquet",
                        f"year={month.year}/month={month.month:02d}/*.parquet"):
            files.extend(sorted(base.glob(pattern)))
        month = (month + pd.Timedelta(days=32)).replace(day=1)
    return sorted(set(files))


def parse_uk_pv(
    root: str | Path,
    start: str,
    end: str,
    latitude: float = LONDON[0],
    longitude: float = LONDON[1],
    radius_km: float = 10.0,
    resolution: str = "5min",
    max_systems: int | None = 60,
    min_systems: int = 3,
    min_completeness: float = 0.9,
) -> ProfileSet:
    """Read, clean and per-unit the systems near a site for ``[start, end)``."""
    if resolution not in RESOLUTIONS:
        raise ValueError(f"resolution must be one of {sorted(RESOLUTIONS)}.")
    root = Path(root)
    meta_path = root / "metadata.csv"
    if not meta_path.exists():
        raise DatasetError(f"uk_pv: {meta_path} not found (download metadata.csv first).")
    product, step = RESOLUTIONS[resolution]
    t0, t1 = pd.Timestamp(start, tz="UTC"), pd.Timestamp(end, tz="UTC")
    meta = pd.read_csv(meta_path)
    chosen = select_systems(meta, latitude, longitude, radius_km)
    if chosen.empty:
        raise DatasetError(f"uk_pv: no domestic system within {radius_km} km of ({latitude}, {longitude}).")
    files = _partition_files(root, product, t0, t1)
    if not files:
        raise DatasetError(f"uk_pv: no {product} partitions for {start}..{end} under {root}.")
    ids = chosen["ss_id"].astype(int).tolist()
    import pyarrow.parquet as pq

    parts = []
    for f in files:
        tab = pq.read_table(f, columns=["ss_id", "datetime_GMT", "generation_Wh"],
                            filters=[("ss_id", "in", ids)])
        if tab.num_rows:
            parts.append(tab.to_pandas())
    if not parts:
        raise DatasetError("uk_pv: none of the selected systems reported in the period.")
    long = pd.concat(parts, ignore_index=True)
    report = QualityReport(f"uk_pv:{resolution}")
    stamp = pd.to_datetime(long["datetime_GMT"], utc=True)
    long["utc"] = stamp - step  # period-ending -> interval start
    long = long[(long["utc"] >= t0) & (long["utc"] < t1)]
    report.add("interval_end_to_start", "info", "datetime_GMT marks the end of the period; shifted back")
    kwp = chosen.set_index("ss_id")["kWp"].astype(float)
    long["kwp"] = long["ss_id"].map(kwp)
    hours = step / pd.Timedelta("1h")
    long["kw"] = long["generation_Wh"].astype(float) / 1000.0 / hours
    report.add("unit_conversion", "info", f"Wh per {step} -> mean kW")
    neg = long["kw"] < 0
    if neg.any():
        report.add("negative_values", "warning", "negative generation readings dropped", int(neg.sum()))
    over = long["kw"] > 1.5 * long["kwp"]
    if over.any():
        report.add("above_physical_bound", "warning", "readings above 1.5 x kWp dropped", int(over.sum()))
    long = long[~neg & ~over]
    bad_path = root / "bad_data.csv"
    if bad_path.exists():
        bad = pd.read_csv(bad_path)
        n_bad = 0
        for _, b in bad[bad["ss_id"].isin(ids)].iterrows():
            s = pd.Timestamp(b["start_datetime_GMT"], tz="UTC")
            e = pd.Timestamp(b["end_datetime_GMT"], tz="UTC") if isinstance(b["end_datetime_GMT"], str) \
                and b["end_datetime_GMT"] else pd.Timestamp.max.tz_localize("UTC")
            m = (long["ss_id"] == b["ss_id"]) & (long["utc"] + step >= s) & (long["utc"] + step <= e)
            n_bad += int(m.sum())
            long = long[~m]
        if n_bad:
            report.add("bad_data_periods", "warning", "readings inside bad_data.csv periods dropped", n_bad)
    wide = long.pivot_table(index="utc", columns="ss_id", values="kw", aggfunc="mean")
    wide.index = pd.DatetimeIndex(wide.index)
    wide = regularize(wide, step, report)
    full = pd.date_range(t0, t1 - step, freq=step)
    wide = wide.reindex(full)
    # Night: output must be zero; a system reporting energy at night is mis-timed or faulty.
    mu = interval_mean_cos_zenith(wide.index, step, latitude, longitude)
    night = mu <= 0.0
    night_energy = (wide[night] > 0.02 * kwp.reindex(wide.columns).to_numpy()).sum()
    faulty = night_energy[night_energy > 0.02 * night.sum()].index.tolist()
    if faulty:
        report.add("night_output_systems_dropped", "warning",
                   "systems with output at night on >2 % of night steps removed", len(faulty))
        wide = wide.drop(columns=faulty)
    wide.loc[night] = wide.loc[night].fillna(0.0)
    wide = clip_negative(wide, report)
    wide = fill_short_gaps(wide, 2, report)
    comp = wide.notna().mean()
    keep = comp[comp >= min_completeness].index
    dropped = len(comp) - len(keep)
    if dropped:
        report.add("incomplete_systems_dropped", "warning",
                   f"systems below {min_completeness:.0%} completeness removed", dropped)
    wide = wide[keep]
    if max_systems is not None and wide.shape[1] > max_systems:
        order = chosen.set_index("ss_id").loc[wide.columns, "distance_km"].sort_values()
        wide = wide[order.index[:max_systems]]
    if wide.shape[1] < min_systems:
        raise DatasetError(f"uk_pv: only {wide.shape[1]} usable system(s) (min {min_systems}); "
                           "widen radius_km or the period.")
    caps = kwp.reindex(wide.columns)
    pu = wide.divide(caps.to_numpy(), axis=1)
    over_pu = pu > 1.0
    if over_pu.any().any():
        report.add("pv_above_rating_clipped", "info", "output above kWp clipped to 1 pu",
                   int(over_pu.sum().sum()))
        pu = pu.clip(upper=1.0)
    pu.columns = [f"ss{int(c)}" for c in pu.columns]
    fleet = pu.mean(axis=1)
    check_solar_alignment(fleet, step, longitude, report, tolerance_min=max(20.0, step / pd.Timedelta("1min")))
    report.stats["n_systems"] = int(pu.shape[1])
    report.stats["median_distance_km"] = float(chosen.set_index("ss_id").loc[wide.columns, "distance_km"].median())
    sel = chosen.set_index("ss_id").loc[wide.columns]
    site = Site(f"uk_pv fleet near ({latitude:.3f}, {longitude:.3f})", float(sel["latitude_rounded"].mean()),
                float(sel["longitude_rounded"].mean()), 0.0, "Europe/London")
    sources = (SourceFile.from_path(meta_path),) + tuple(SourceFile.from_path(f) for f in files)
    return ProfileSet(
        "uk_pv", ProfileKind.PV_PU, pu, step, site, sources,
        {"capacity_kwp": {f"ss{int(c)}": float(v) for c, v in caps.items()},
         "resolution": resolution, "radius_km": radius_km, "period": [start, end]},
        report,
    )


def load(
    start: str = "2013-01-01",
    end: str = "2014-01-01",
    latitude: float = LONDON[0],
    longitude: float = LONDON[1],
    radius_km: float = 10.0,
    resolution: str = "5min",
    max_systems: int | None = 60,
    cache=None,  # uniform loader signature; the gated dataset is read from a local copy
    source_file: str | Path | None = None,
) -> ProfileSet:
    """``source_file`` is the local ``uk_pv`` folder (with ``metadata.csv``)."""
    if source_file is None:
        raise DatasetError(
            "uk_pv is gated on Hugging Face: accept the terms, download metadata.csv and the needed "
            "months (see this module's docstring), then pass the folder with --source-file/--pv-file.")
    _ = math, cache
    return parse_uk_pv(source_file, start, end, latitude, longitude, radius_km, resolution, max_systems)


__all__ = ["INFO", "LONDON", "load", "parse_uk_pv", "select_systems"]
