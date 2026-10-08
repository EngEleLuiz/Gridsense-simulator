"""PVGIS (EU JRC) hourly PV output per kWp at any location.

``seriescalc`` runs the JRC's full PV model (plane-of-array transposition,
module temperature, angular/spectral losses, system losses) over
satellite irradiance (SARAH where covered, ERA5 elsewhere). It is the
tilted-plane complement to the horizontal model in :mod:`.solar`.

Response format (verified on a real ``seriescalc`` JSON): records in
``outputs.hourly`` with ``time`` = ``YYYYMMDD:HHMM`` (UTC), ``P`` in W for
``inputs.pv_module.peak_power`` kWp, ``G(i)`` plane-of-array W/m2,
``H_sun`` sun height (deg), ``T2m`` degC. With SARAH the minute is
``:10`` (satellite scan time).

Timing: ``H_sun`` is the sun height *at the stamp* (our solar geometry
reproduces it within 0.1 deg). The loader puts each record in the hourly
interval that contains its stamp (``floor``), which leaves an offset of at
most 10-20 min, inside the alignment tolerance used for this source; the
measured offset is recorded in the quality stats.
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from .base import DatasetError, DatasetInfo, ProfileKind, ProfileSet, Site, SourceFile
from .cache import DataCache, build_url
from .quality import QualityReport, check_solar_alignment, fill_short_gaps, regularize

INFO = DatasetInfo(
    key="pvgis",
    title="PVGIS hourly radiation and PV output (seriescalc)",
    publisher="European Commission, Joint Research Centre (JRC)",
    provides=("pv",),
    coverage="Europe, Africa, most of Asia and the Americas (SARAH-3 / ERA5), 2005-2023",
    resolution="1 h",
    license="Free reuse with attribution (Commission Decision 2011/833/EU)",
    citation="Huld, T., Mueller, R., Gambardella, A. (2012). A new solar radiation database "
             "for estimating PV performance in Europe and Africa. Solar Energy 86, 1803-1815; "
             "PVGIS https://joint-research-centre.ec.europa.eu/pvgis",
    homepage="https://joint-research-centre.ec.europa.eu/photovoltaic-geographical-information-system-pvgis_en",
    access="REST API (no key), JSON response cached per site, period and system",
    notes=("Satellite-derived: no local shading; 14 % default system losses.",),
)

API_URLS = (
    "https://re.jrc.ec.europa.eu/api/v5_3/seriescalc",
    "https://re.jrc.ec.europa.eu/api/v5_2/seriescalc",
)
RESOLUTION = pd.Timedelta("1h")
ALIGNMENT_TOLERANCE_MIN = 35.0


def request_urls(latitude, longitude, startyear, endyear, angle, aspect, loss) -> list[str]:
    params = {
        "lat": f"{latitude:.4f}", "lon": f"{longitude:.4f}", "startyear": int(startyear),
        "endyear": int(endyear), "pvcalculation": 1, "peakpower": 1, "loss": loss,
        "angle": angle, "aspect": aspect, "outputformat": "json",
    }
    return [build_url(u, params) for u in API_URLS]


def parse_pvgis_json(
    payload: dict | str | Path, source: SourceFile | None = None,
    utc_offset_hours: float | None = None, tz_name: str | None = None,
) -> ProfileSet:
    if isinstance(payload, (str, Path)):
        path = Path(payload)
        source = source or SourceFile.from_path(path)
        payload = json.loads(path.read_text(encoding="utf-8"))
    try:
        inputs = payload["inputs"]
        rows = payload["outputs"]["hourly"]
        lat = float(inputs["location"]["latitude"])
        lon = float(inputs["location"]["longitude"])
        peak_kw = float(inputs["pv_module"]["peak_power"])
    except (KeyError, TypeError) as exc:
        raise DatasetError(f"PVGIS: unexpected response layout ({exc}); was pvcalculation=1?") from exc
    df = pd.DataFrame(rows)
    if "P" not in df:
        raise DatasetError("PVGIS: no 'P' column; request pvcalculation=1.")
    stamps = pd.to_datetime(df["time"], format="%Y%m%d:%H%M", utc=True)
    report = QualityReport("pvgis")
    frame = pd.DataFrame(
        {"pv_pu": (df["P"].astype(float) / (peak_kw * 1000.0)).to_numpy(),
         "poa_wm2": df.get("G(i)", pd.Series(dtype=float)).astype(float).to_numpy()
         if "G(i)" in df else float("nan"),
         "temp_air_c": df["T2m"].astype(float).to_numpy() if "T2m" in df else float("nan")},
        index=pd.DatetimeIndex(stamps).floor("h"),
    )
    minutes = sorted(set(stamps.dt.minute))
    report.add("stamp_to_interval", "info",
               f"records stamped at minute(s) {minutes} placed in the containing hour")
    report.add("unit_conversion", "info", f"P (W for {peak_kw:g} kWp) -> per unit of rating")
    reconstructed = int(df["Int"].astype(float).gt(0).sum()) if "Int" in df else 0
    if reconstructed:
        report.add("reconstructed_irradiance", "info",
                   "records flagged Int=1 (radiation reconstructed by PVGIS)", reconstructed)
    over = frame["pv_pu"] > 1.0
    if over.any():
        report.add("pv_above_rating_clipped", "warning", "pv_pu > 1 clipped to 1", int(over.sum()))
        frame.loc[over, "pv_pu"] = 1.0
    offset = utc_offset_hours if utc_offset_hours is not None else round(lon / 15.0)
    site = Site(f"PVGIS {lat:.3f},{lon:.3f}", lat, lon, float(offset), tz_name)
    frame = regularize(frame, RESOLUTION, report)
    frame = fill_short_gaps(frame, 2, report)
    check_solar_alignment(frame["pv_pu"], RESOLUTION, lon, report, ALIGNMENT_TOLERANCE_MIN)
    meteo = inputs.get("meteo_data", {})
    mounting = inputs.get("mounting_system", {})
    return ProfileSet(
        "pvgis", ProfileKind.PV_PU, frame[["pv_pu"]], RESOLUTION, site,
        (source,) if source else (),
        {"radiation_db": meteo.get("radiation_db"), "mounting_system": mounting,
         "pv_module": inputs.get("pv_module")},
        report,
    )


def load(
    latitude: float = -27.5954,
    longitude: float = -48.5480,
    startyear: int = 2020,
    endyear: int = 2020,
    angle: float = 20.0,
    aspect: float = 180.0,
    loss: float = 14.0,
    cache: DataCache | None = None,
    source_file: str | Path | None = None,
    utc_offset_hours: float | None = None,
    tz_name: str | None = None,
) -> ProfileSet:
    """Hourly PV per kWp. ``aspect`` follows PVGIS: 0 = south, 90 = west, 180 = north.

    Defaults face north (aspect 180) at a 20 deg tilt, the usual rooftop
    orientation in southern Brazil.
    """
    if source_file is None:
        cache = cache or DataCache()
        fname = (f"pvgis_{latitude:.4f}_{longitude:.4f}_{startyear}_{endyear}"
                 f"_t{angle:g}_a{aspect:g}_l{loss:g}.json")
        src = cache.fetch("pvgis", fname, request_urls(latitude, longitude, startyear, endyear,
                                                       angle, aspect, loss))
    else:
        src = SourceFile.from_path(source_file)
    return parse_pvgis_json(Path(src.path), src, utc_offset_hours, tz_name)


__all__ = ["INFO", "load", "parse_pvgis_json", "request_urls"]
