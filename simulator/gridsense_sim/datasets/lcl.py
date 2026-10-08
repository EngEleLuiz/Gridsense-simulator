"""Low Carbon London (UK Power Networks): half-hourly smart-meter demand.

5,567 London households, Nov 2011 - Feb 2014, kWh per half hour. The
largest open residential panel in this registry: it gives the series
builder a deep pool for realistic aggregation (diversity) of many homes.

Format (verified on a real household extract): ``LCLid, stdorToU,
DateTime, KWH/hh (per half hour) [, Acorn, Acorn_grouped]``; the value
column name may carry a trailing space; missing reads are the string
``Null``. The full release is one ~8 GB CSV (zipped ~0.7 GB); the
partitioned release splits it into block files. Both are read in chunks,
filtered by household and period, so memory stays bounded.

Clock (measured on the real file): stamps are **UTC/GMT**, not London
wall clock -- 2013-03-31 contains 01:00 and 01:30, which do not exist in
local time that day. The file also repeats some records (e.g. 12 extra
``00:00`` rows in 2013); exact duplicates are dropped and conflicting
ones averaged by :func:`~.quality.regularize`.

Assumption (cannot be measured on a load-only source): a stamp marks the
interval start. Its consequence is a possible 30 min shift of the load
shape, recorded in the quality report.

Only ``Std`` (flat-tariff) households are used by default: in 2013 the
``ToU`` group was on an experimental dynamic tariff that changed when
they consumed.
"""

from __future__ import annotations

import io
import zipfile
from pathlib import Path
from typing import Iterator

import pandas as pd

from .base import DatasetError, DatasetInfo, ProfileKind, ProfileSet, Site, SourceFile
from .cache import DataCache
from .quality import QualityReport, clip_negative, fill_short_gaps, flag_flatlines, regularize

INFO = DatasetInfo(
    key="lcl",
    title="SmartMeter Energy Consumption Data in London Households (Low Carbon London)",
    publisher="UK Power Networks, via London Datastore",
    provides=("load",),
    coverage="5,567 London households, 2011-11 to 2014-02",
    resolution="30 min",
    license="Open data published on the London Datastore; check the licence on the dataset "
            "page before redistribution [VERIFY]",
    citation="UK Power Networks (2015). SmartMeter Energy Consumption Data in London Households. "
             "London Datastore. Low Carbon London project.",
    homepage="https://data.london.gov.uk/dataset/smartmeter-energy-use-data-in-london-households",
    access="Large ZIP (full or partitioned CSV); read in chunks",
    notes=(
        "Stamps are UTC (measured); records are occasionally duplicated.",
        "Northern hemisphere: shift seasons by ~6 months for Brazilian studies.",
        "Interval-start labelling assumed (not verifiable from load alone).",
    ),
)

FULL_ZIP_URL = ("https://data.london.gov.uk/download/smartmeter-energy-use-data-in-london-households/"
                "3527bf39-d93e-4071-8451-df2ade1ea4f2/LCL-FullData.zip")
RESOLUTION = pd.Timedelta("30min")
SITE = Site("Low Carbon London", 51.507, -0.128, 0.0, "Europe/London")
MAX_GAP_STEPS = 4
MIN_COMPLETENESS = 0.95
CHUNK_ROWS = 2_000_000


def _iter_csv_streams(path: Path) -> Iterator[io.TextIOBase]:
    if path.suffix.lower() == ".zip":
        with zipfile.ZipFile(path) as zf:
            for member in sorted(m for m in zf.namelist() if m.lower().endswith(".csv")):
                with zf.open(member) as fh:
                    yield io.TextIOWrapper(fh, encoding="utf-8", errors="replace")
    else:
        with open(path, encoding="utf-8", errors="replace") as fh:
            yield fh


def _read_filtered(
    path: Path, start: pd.Timestamp | None, end: pd.Timestamp | None,
    tariff: str | None, households: set[str] | None, max_households: int | None,
) -> tuple[pd.DataFrame, list[str]]:
    parts: list[pd.DataFrame] = []
    seen: list[str] = []
    seen_set: set[str] = set()
    for stream in _iter_csv_streams(path):
        reader = pd.read_csv(stream, chunksize=CHUNK_ROWS, dtype=str, keep_default_na=False)
        for chunk in reader:
            chunk.columns = [c.strip() for c in chunk.columns]
            needed = {"LCLid", "DateTime", "KWH/hh (per half hour)"}
            if not needed.issubset(chunk.columns):
                raise DatasetError(f"LCL: missing columns {needed - set(chunk.columns)}.")
            if tariff and "stdorToU" in chunk:
                chunk = chunk[chunk["stdorToU"].str.strip() == tariff]
            ids = chunk["LCLid"].str.strip()
            if households is not None:
                chunk = chunk[ids.isin(households)]
            elif max_households is not None:
                for h in pd.unique(ids):
                    if h not in seen_set and len(seen) < max_households:
                        seen.append(h)
                        seen_set.add(h)
                chunk = chunk[ids.isin(seen_set)]
            if chunk.empty:
                continue
            ts = pd.to_datetime(chunk["DateTime"].str.strip(), errors="coerce", format="mixed")
            keep = ts.notna()
            if start is not None:
                keep &= ts >= start
            if end is not None:
                keep &= ts < end
            sub = chunk.loc[keep]
            if sub.empty:
                continue
            parts.append(pd.DataFrame({
                "id": sub["LCLid"].str.strip().to_numpy(),
                "utc": ts[keep].to_numpy(),
                "kwh": pd.to_numeric(sub["KWH/hh (per half hour)"].str.strip(), errors="coerce").to_numpy(),
            }))
    if not parts:
        raise DatasetError("LCL: no rows match the requested households/period/tariff.")
    return pd.concat(parts, ignore_index=True), seen


def parse_lcl(
    path: str | Path,
    start: str | None = None,
    end: str | None = None,
    tariff: str | None = "Std",
    households: list[str] | None = None,
    max_households: int | None = 300,
    source: SourceFile | None = None,
) -> ProfileSet:
    """Read households into a LOAD_KW ProfileSet (columns = LCLid)."""
    path = Path(path)
    source = source or SourceFile.from_path(path)
    t0 = pd.Timestamp(start) if start else None
    t1 = pd.Timestamp(end) if end else None
    hh = set(households) if households else None
    long, _ = _read_filtered(path, t0, t1, tariff, hh, None if hh else max_households)
    report = QualityReport("lcl")
    report.add("clock_utc", "info", "stamps read as UTC (verified: 01:00 exists on the BST start day)")
    report.add("interval_start_assumed", "info",
               "stamps taken as interval starts (unverifiable; <= 30 min shift of the load shape)")
    report.add("unit_conversion", "info", "kWh per half hour -> mean kW (x2)")
    null_reads = int(long["kwh"].isna().sum())
    if null_reads:
        report.add("null_reads", "warning", "'Null' or non-numeric reads", null_reads)
    dup = long.duplicated(subset=["id", "utc"], keep=False)
    if dup.any():
        exact = long[dup].duplicated(keep=False).sum()
        report.add("duplicate_records", "warning",
                   f"{int(dup.sum())} rows share (household, stamp); {int(exact)} are exact copies",
                   int(dup.sum()))
    wide = long.pivot_table(index="utc", columns="id", values="kwh", aggfunc="mean", dropna=False) * 2.0
    wide.index = pd.DatetimeIndex(wide.index).tz_localize("UTC")
    wide.columns = [str(c) for c in wide.columns]
    wide = regularize(wide, RESOLUTION, report)
    wide = clip_negative(wide, report)
    wide = fill_short_gaps(wide, MAX_GAP_STEPS, report)
    comp = wide.notna().mean()
    dropped = sorted(comp[comp < MIN_COMPLETENESS].index)
    if dropped:
        report.add("incomplete_households_dropped", "warning",
                   f"households below {MIN_COMPLETENESS:.0%} completeness in the period removed",
                   len(dropped))
        wide = wide.drop(columns=dropped)
    if wide.shape[1] == 0:
        raise DatasetError("LCL: no household passed the completeness threshold.")
    flag_flatlines(wide, min_run=24, report=report)
    report.stats["n_households"] = int(wide.shape[1])
    return ProfileSet("lcl", ProfileKind.LOAD_KW, wide, RESOLUTION, SITE, (source,),
                      {"tariff": tariff, "period": [start, end]}, report)


def load(
    start: str | None = "2013-01-01",
    end: str | None = "2014-01-01",
    tariff: str | None = "Std",
    max_households: int | None = 300,
    households: list[str] | None = None,
    cache: DataCache | None = None,
    source_file: str | Path | None = None,
) -> ProfileSet:
    if source_file is not None:
        src = SourceFile.from_path(source_file)
    else:
        cache = cache or DataCache()
        src = cache.fetch("lcl", "LCL-FullData.zip", [FULL_ZIP_URL])
    return parse_lcl(Path(src.path), start, end, tariff, households, max_households, src)


__all__ = ["INFO", "SITE", "load", "parse_lcl"]

