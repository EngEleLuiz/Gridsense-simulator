"""Ausgrid Solar Home Electricity Data: 300 metered homes, load and gross PV.

Half-hourly kWh for 300 randomly selected residential customers of
Ausgrid (Sydney/NSW) with gross-metered rooftop PV, July 2010 - June 2013,
one CSV per financial year. Channels: ``GC`` general consumption, ``CL``
controlled load (off-peak water heating), ``GG`` gross PV generation.
Household demand = GC + CL. Southern hemisphere, so its seasons line up
with Brazil without any shift.

Format (verified on the real ``2012-2013 Solar home electricity data
v2.csv``): one title line, then ``Customer, Generator Capacity, Postcode,
Consumption Category, date, 0:30 ... 23:30, 0:00[, Row Quality]``. The
48 slot labels are interval **end** times (``0:00`` closes the day).
Dates are ``d/mm/yyyy`` (other years use ``d-Mon-yy``). ``Row Quality``
is empty for actual reads and ``NA`` for estimated rows.

Clock (measured, not documented): the energy centroid of the fleet's
GG sits at solar noon in Apr-Sep but +55..+64 min in Oct-Mar when the
stamps are read as fixed AEST (UTC+10). The files therefore follow the
**Sydney wall clock with DST**. The loader localises to
``Australia/Sydney``; slots that do not exist (spring forward) are dropped
and ambiguous ones (fall back) become gaps that are interpolated; both
are counted in the quality report and the alignment check re-verifies
the result.
"""

from __future__ import annotations

import io
import re
import zipfile
from pathlib import Path

import numpy as np
import pandas as pd

from .base import DatasetError, DatasetInfo, ProfileKind, ProfileSet, Site, SourceFile
from .cache import DataCache
from .quality import (
    QualityReport,
    check_solar_alignment,
    clip_negative,
    fill_short_gaps,
    flag_flatlines,
    regularize,
)

INFO = DatasetInfo(
    key="ausgrid",
    title="Ausgrid Solar Home Electricity Data (half-hour, 300 homes)",
    publisher="Ausgrid (NSW, Australia)",
    provides=("load", "pv"),
    coverage="300 PV homes in Sydney/NSW, 2010-07-01 to 2013-06-30",
    resolution="30 min",
    license="CC BY (Ausgrid, via Data.NSW); read the Ausgrid data notes (Aug 2014)",
    citation="Ratnam, E. L., Weller, S. R., Kellett, C. M., Murray, A. T. (2017). Residential "
             "load and rooftop PV generation: an Australian distribution network dataset. "
             "International Journal of Sustainable Energy 36(8), 787-806.",
    homepage="https://www.ausgrid.com.au/Industry/Our-Research/Data-to-share/Solar-home-electricity-data",
    access="Yearly CSV (or ZIP); official links have moved over time, so a manual copy is accepted",
    notes=(
        "Stamps follow the Sydney wall clock including DST (measured).",
        "Customers with extreme consumption/generation were excluded by Ausgrid: not a "
        "representative sample of all households.",
    ),
)

YEAR_FILES = {
    "2010-2011": "2010-2011 Solar home electricity data.csv",
    "2011-2012": "2011-2012 Solar home electricity data v2.csv",
    "2012-2013": "2012-2013 Solar home electricity data v2.csv",
}
# The Ausgrid server URLs used by earlier drafts could not be verified and the
# portal is currently unavailable. No automatic download is attempted: download
# the yearly files from the official page and pass them with --source-file.
YEAR_URLS: dict[str, list[str]] = {
    "2010-2011": [],
    "2011-2012": [],
    "2012-2013": [],
}
RESOLUTION = pd.Timedelta("30min")
TZ = "Australia/Sydney"
SITE = Site("Ausgrid Sydney/NSW", -33.87, 151.21, 10.0, TZ)
MAX_GAP_STEPS = 4
MIN_COMPLETENESS = 0.95


def _slot_start_minutes(label: str) -> int:
    m = re.fullmatch(r"\s*(\d{1,2}):(\d{2})\s*", label)
    if not m:
        raise DatasetError(f"Ausgrid: unexpected slot label {label!r}.")
    end = int(m.group(1)) * 60 + int(m.group(2))
    return (end - 30) % 1440  # labels are interval end; 0:00 closes the day


def _read_raw(raw: bytes) -> pd.DataFrame:
    text = raw.decode("utf-8-sig", errors="replace")
    lines = text.splitlines()
    header = next((i for i, l in enumerate(lines[:10]) if l.lstrip('"').startswith("Customer")), None)
    if header is None:
        raise DatasetError("Ausgrid: header row starting with 'Customer' not found.")
    return pd.read_csv(io.StringIO("\n".join(lines[header:])), keep_default_na=False,
                       na_values=[""], low_memory=False)


def _parse_dates(s: pd.Series) -> pd.Series:
    out = pd.Series(pd.NaT, index=s.index, dtype="datetime64[ns]")
    for fmt in ("%d/%m/%Y", "%d-%b-%y", "%d/%m/%y", "%Y-%m-%d"):
        missing = out.isna()
        if not missing.any():
            break
        out[missing] = pd.to_datetime(s[missing], format=fmt, errors="coerce")
    return out


def parse_ausgrid(raw: bytes | str | Path, source: SourceFile | None = None) -> tuple[pd.DataFrame, dict]:
    """Raw file -> long frame (customer, channel, utc start, kW) plus metadata."""
    if isinstance(raw, (str, Path)):
        path = Path(raw)
        source = source or SourceFile.from_path(path)
        if path.suffix.lower() == ".zip":
            with zipfile.ZipFile(path) as zf:
                member = next(m for m in zf.namelist() if m.lower().endswith(".csv"))
                raw = zf.read(member)
        else:
            raw = path.read_bytes()
    df = _read_raw(raw)
    df.columns = [str(c).strip() for c in df.columns]
    required = ["Customer", "Generator Capacity", "Consumption Category", "date"]
    if any(c not in df.columns for c in required):
        raise DatasetError(f"Ausgrid: missing columns {set(required) - set(df.columns)}.")
    slots = [c for c in df.columns if re.fullmatch(r"\d{1,2}:\d{2}", c)]
    if len(slots) != 48:
        raise DatasetError(f"Ausgrid: expected 48 half-hour columns, found {len(slots)}.")
    dates = _parse_dates(df["date"].astype(str))
    if dates.isna().any():
        raise DatasetError(f"Ausgrid: {int(dates.isna().sum())} unparseable dates.")
    estimated = (df["Row Quality"].astype(str).str.strip().str.upper() == "NA") \
        if "Row Quality" in df else pd.Series(False, index=df.index)

    values = df[slots].apply(pd.to_numeric, errors="coerce").to_numpy(float) * 2.0  # kWh/30min -> kW
    offsets = np.array([_slot_start_minutes(s) for s in slots], dtype="int64")
    local = (dates.to_numpy("datetime64[ns]")[:, None]
             + offsets[None, :].astype("timedelta64[m]")).ravel()
    long = pd.DataFrame({
        "customer": np.repeat(df["Customer"].astype(int).to_numpy(), 48),
        "channel": np.repeat(df["Consumption Category"].astype(str).str.strip().to_numpy(), 48),
        "local": local,
        "kw": values.ravel(),
    })
    utc = pd.DatetimeIndex(long["local"]).tz_localize(TZ, ambiguous="NaT", nonexistent="NaT")
    long["utc"] = utc.tz_convert("UTC")
    meta = {
        "capacity_kwp": df.groupby("Customer")["Generator Capacity"].first().astype(float).to_dict(),
        "postcode": df.groupby("Customer")["Postcode"].first().to_dict() if "Postcode" in df else {},
        "estimated_rows": int(estimated.sum()),
        "dst_dropped_values": int(long["utc"].isna().sum()),
        "source": source,
    }
    return long.dropna(subset=["utc"]), meta


def _wide(long: pd.DataFrame, channels: tuple[str, ...]) -> pd.DataFrame:
    sub = long[long["channel"].isin(channels)]
    wide = sub.pivot_table(index="utc", columns="customer", values="kw", aggfunc="sum", dropna=False)
    wide.columns = [f"c{int(c):03d}" for c in wide.columns]
    wide.index = pd.DatetimeIndex(wide.index).tz_convert("UTC")
    return wide


def build_profile_sets(long: pd.DataFrame, meta: dict, kind: str = "load") -> ProfileSet:
    report = QualityReport(f"ausgrid:{kind}")
    report.add("interval_end_to_start", "info", "slot labels are interval ends; converted to starts")
    report.add("unit_conversion", "info", "kWh per half hour -> mean kW (x2)")
    report.add("wall_clock_with_dst", "info",
               f"stamps localised to {TZ} (DST); {meta['dst_dropped_values']} values at DST "
               "transitions dropped", meta["dst_dropped_values"])
    if meta["estimated_rows"]:
        report.add("estimated_rows", "info", "rows flagged 'NA' in Row Quality (Ausgrid estimates) kept",
                   meta["estimated_rows"])
    caps = meta["capacity_kwp"]
    if kind == "load":
        frame = _wide(long, ("GC", "CL"))
        profile_kind = ProfileKind.LOAD_KW
    elif kind == "pv":
        gg = _wide(long, ("GG",))
        frame = gg.copy()
        for col in frame.columns:
            cap = caps.get(int(col[1:]))
            frame[col] = frame[col] / cap if cap else np.nan
        profile_kind = ProfileKind.PV_PU
    else:
        raise ValueError("kind must be 'load' or 'pv'.")
    frame = regularize(frame, RESOLUTION, report)
    frame = clip_negative(frame, report)
    if kind == "pv":
        over = frame > 1.0
        if over.any().any():
            report.add("pv_above_rating_clipped", "warning",
                       "GG above the nameplate capacity clipped to 1 pu", int(over.sum().sum()))
            frame = frame.clip(upper=1.0)
    frame = fill_short_gaps(frame, MAX_GAP_STEPS, report)
    comp = frame.notna().mean()
    dropped = sorted(comp[comp < MIN_COMPLETENESS].index)
    if dropped:
        report.add("incomplete_customers_dropped", "warning",
                   f"customers below {MIN_COMPLETENESS:.0%} completeness removed", len(dropped))
        frame = frame.drop(columns=dropped)
    if frame.shape[1] == 0:
        raise DatasetError("Ausgrid: no customer passed the completeness threshold.")
    flag_flatlines(frame, min_run=12, report=report)
    if kind == "pv":
        check_solar_alignment(frame.mean(axis=1), RESOLUTION, SITE.longitude, report)
    else:  # verify the clock through the same homes' PV
        gg = _wide(long, ("GG",)).reindex(frame.index)
        check_solar_alignment(gg.sum(axis=1, min_count=1), RESOLUTION, SITE.longitude, report)
    report.stats["n_customers"] = int(frame.shape[1])
    report.stats["completeness_min"] = float(frame.notna().mean().min())
    src = meta.get("source")
    return ProfileSet(
        "ausgrid", profile_kind, frame, RESOLUTION, SITE, (src,) if src else (),
        {"capacity_kwp": {f"c{int(k):03d}": v for k, v in caps.items()}, "channels":
         ["GC", "CL"] if kind == "load" else ["GG"]}, report,
    )


def load(
    kind: str = "load",
    year: str = "2012-2013",
    cache: DataCache | None = None,
    source_file: str | Path | None = None,
) -> ProfileSet:
    """``kind='load'`` (GC+CL, kW per home) or ``'pv'`` (GG per unit of capacity)."""
    if year not in YEAR_FILES:
        raise ValueError(f"year must be one of {sorted(YEAR_FILES)}.")
    if source_file is not None:
        src = SourceFile.from_path(source_file)
    else:
        cache = cache or DataCache()
        hit = cache.cached("ausgrid", YEAR_FILES[year])
        src = hit or cache.fetch("ausgrid", Path(YEAR_URLS[year][0]).name, YEAR_URLS[year])
    long, meta = parse_ausgrid(Path(src.path), src)
    ps = build_profile_sets(long, meta, kind)
    ps.meta["year"] = year
    return ps


__all__ = ["INFO", "SITE", "build_profile_sets", "load", "parse_ausgrid"]
