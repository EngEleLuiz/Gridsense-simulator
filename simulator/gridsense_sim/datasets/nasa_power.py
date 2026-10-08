"""NASA POWER hourly point data: all-sky GHI and 2 m air temperature.

Global coverage (satellite CERES SYN1deg + MERRA-2 reanalysis), hourly,
from 2001. Useful where no ground station exists and as an independent
cross-check of INMET at the same site.

Response format (verified on a real API v2.10 response):
``properties.parameter.<NAME>`` maps ``"YYYYMMDDHH"`` to a value;
``header.fill_value`` (-999) marks missing data; ``parameters.<NAME>.units``
is ``"Wh/m^2"`` for hourly ALLSKY_SFC_SW_DWN, i.e. the hourly mean in W/m2;
``header.time_standard`` must be ``"UTC"`` (we request it); the hour key
marks the interval start.
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from .base import DatasetError, DatasetInfo, ProfileKind, ProfileSet, Site, SourceFile
from .cache import DataCache, build_url
from .quality import QualityReport, check_irradiance, check_solar_alignment, fill_short_gaps, regularize

INFO = DatasetInfo(
    key="nasa_power",
    title="NASA POWER - hourly all-sky surface shortwave irradiance and temperature",
    publisher="NASA Langley Research Center (POWER project)",
    provides=("irradiance",),
    coverage="Global, 2001-present (0.5 x 0.625 deg meteorology, 1 deg radiation)",
    resolution="1 h",
    license="NASA open data; no restrictions, acknowledgement requested",
    citation="NASA Langley Research Center POWER Project, funded through the NASA Earth "
             "Science Applied Science Program. https://power.larc.nasa.gov",
    homepage="https://power.larc.nasa.gov",
    access="REST API (no key), JSON response cached per site and period",
    notes=("Coarse grid cell: smooths cloud variability compared with a ground station.",),
)

API_URL = "https://power.larc.nasa.gov/api/temporal/hourly/point"
PARAMETERS = ("ALLSKY_SFC_SW_DWN", "T2M")
RESOLUTION = pd.Timedelta("1h")


def request_url(latitude: float, longitude: float, start: str, end: str) -> str:
    return build_url(API_URL, {
        "parameters": ",".join(PARAMETERS), "community": "RE",
        "latitude": f"{latitude:.4f}", "longitude": f"{longitude:.4f}",
        "start": start, "end": end, "format": "JSON", "time-standard": "UTC",
    })


def parse_nasa_power_json(
    payload: dict | str | Path, source: SourceFile | None = None, site_name: str | None = None,
    utc_offset_hours: float | None = None, tz_name: str | None = None,
) -> ProfileSet:
    if isinstance(payload, (str, Path)):
        path = Path(payload)
        source = source or SourceFile.from_path(path)
        payload = json.loads(path.read_text(encoding="utf-8"))
    if "payload" in payload and "properties" not in payload:  # tolerate wrapped responses
        payload = payload["payload"]
    try:
        header = payload["header"]
        params = payload["properties"]["parameter"]
        lon, lat = payload["geometry"]["coordinates"][:2]
    except (KeyError, TypeError) as exc:
        raise DatasetError(f"NASA POWER: unexpected response layout ({exc}).") from exc
    if str(header.get("time_standard", "UTC")).upper() != "UTC":
        raise DatasetError("NASA POWER: response is not in UTC; request time-standard=UTC.")
    if "ALLSKY_SFC_SW_DWN" not in params:
        raise DatasetError("NASA POWER: ALLSKY_SFC_SW_DWN missing from the response.")
    units = payload.get("parameters", {}).get("ALLSKY_SFC_SW_DWN", {}).get("units", "Wh/m^2")
    if units.replace(" ", "") not in ("Wh/m^2", "W/m^2"):
        raise DatasetError(f"NASA POWER: unexpected GHI units {units!r} (expected Wh/m^2 hourly).")
    fill = float(header.get("fill_value", -999.0))
    df = pd.DataFrame(params)
    df.index = pd.to_datetime(df.index, format="%Y%m%d%H").tz_localize("UTC")
    df = df.mask(df <= fill).astype(float)
    frame = pd.DataFrame({"ghi_wm2": df["ALLSKY_SFC_SW_DWN"]})
    if "T2M" in df:
        frame["temp_air_c"] = df["T2M"]
    report = QualityReport("nasa_power")
    report.add("unit_conversion", "info", f"{units} per hour taken as mean W/m2")
    offset = utc_offset_hours if utc_offset_hours is not None else round(lon / 15.0)
    site = Site(site_name or f"NASA POWER {lat:.3f},{lon:.3f}", float(lat), float(lon), float(offset), tz_name)
    frame = regularize(frame, RESOLUTION, report)
    frame["ghi_wm2"] = check_irradiance(frame["ghi_wm2"], RESOLUTION, site.latitude, site.longitude, report)
    frame = fill_short_gaps(frame, 2, report)
    check_solar_alignment(frame["ghi_wm2"], RESOLUTION, site.longitude, report)
    report.stats["ghi_completeness"] = float(frame["ghi_wm2"].notna().mean())
    return ProfileSet(
        "nasa_power", ProfileKind.IRRADIANCE, frame, RESOLUTION, site,
        (source,) if source else (), {"sources": header.get("sources"),
                                      "api_version": header.get("api", {}).get("version")}, report,
    )


def load(
    latitude: float = -27.5954,
    longitude: float = -48.5480,
    start: str = "20230101",
    end: str = "20231231",
    cache: DataCache | None = None,
    source_file: str | Path | None = None,
    site_name: str | None = None,
    utc_offset_hours: float | None = None,
    tz_name: str | None = None,
) -> ProfileSet:
    """Load one point and period (``start``/``end`` as ``YYYYMMDD``, inclusive)."""
    if source_file is None:
        cache = cache or DataCache()
        fname = f"nasa_power_{latitude:.4f}_{longitude:.4f}_{start}_{end}.json"
        src = cache.fetch("nasa_power", fname, [request_url(latitude, longitude, str(start), str(end))])
    else:
        src = SourceFile.from_path(source_file)
    return parse_nasa_power_json(Path(src.path), src, site_name, utc_offset_hours, tz_name)


__all__ = ["INFO", "load", "parse_nasa_power_json", "request_url"]
