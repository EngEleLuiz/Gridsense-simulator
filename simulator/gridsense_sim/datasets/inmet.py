"""INMET automatic weather stations (Brazil): hourly global horizontal irradiation.

Source: Instituto Nacional de Meteorologia, "Dados Historicos", one ZIP
per year with one CSV per automatic station
(``https://portal.inmet.gov.br/uploads/dadoshistoricos/{year}.zip``).

Format variants handled (all seen in real files):

* the official file: latin-1, ``;`` separator, decimal comma, 8 metadata
  lines (``LATITUDE:;-27,60250000``) before the column header;
* re-exports circulating in repositories: ``,`` separator with decimal
  point and accents stripped, or the metadata block removed and the text
  re-encoded with replacement characters;
* old (<= 2018) date/hour layout ``2018-01-01;00:00`` vs the current
  ``2024/01/01;0000 UTC``;
* missing values as ``-9999`` or empty fields (radiation is empty at night).

Units and timing (measured on the real A806 Florianopolis file, 2024):
radiation is kJ/m2 accumulated over the hour **ending** at the stamp
(UTC). Labelled as interval start, the energy centroid sits +45..+71 min
after solar noon; after shifting by -1 h it sits within +-15 min. The
loader therefore shifts by -1 h, and the solar-alignment check re-verifies
it on every load.
"""

from __future__ import annotations

import io
import re
import unicodedata
import zipfile
from pathlib import Path

import pandas as pd

from .base import DatasetError, DatasetInfo, ProfileKind, ProfileSet, Site, SourceFile
from .cache import DataCache
from .quality import (
    QualityReport,
    check_irradiance,
    check_solar_alignment,
    fill_short_gaps,
    regularize,
)

INFO = DatasetInfo(
    key="inmet",
    title="INMET automatic weather stations - hourly historical data",
    publisher="Instituto Nacional de Meteorologia (INMET), Brazil",
    provides=("irradiance",),
    coverage="~600 automatic stations in Brazil, 2000-present",
    resolution="1 h",
    license="Brazilian public open data (Lei 12.527/2011); cite INMET as the source",
    citation="INMET. Banco de Dados Meteorologicos - Dados Historicos. "
             "https://portal.inmet.gov.br/dadoshistoricos",
    homepage="https://portal.inmet.gov.br/dadoshistoricos",
    access="Yearly ZIP (all stations) downloaded and cached; the station CSV is read from it",
    notes=(
        "Radiation is kJ/m2 accumulated over the hour ending at the UTC stamp (verified).",
        "Only GHI is measured; no DNI/DHI.",
    ),
)

YEAR_ZIP_URL = "https://portal.inmet.gov.br/uploads/dadoshistoricos/{year}.zip"
RESOLUTION = pd.Timedelta("1h")
MAX_GAP_STEPS = 2

# Station metadata used only when a file has lost its header block.
KNOWN_STATIONS: dict[str, tuple[str, float, float]] = {
    "A806": ("FLORIANOPOLIS", -27.6025, -48.6200),
    "A701": ("SAO PAULO - MIRANTE", -23.4962, -46.6200),
    "A801": ("PORTO ALEGRE", -30.0535, -51.1748),
    "A304": ("NATAL", -5.8372, -35.2081),
    "A001": ("BRASILIA", -15.7894, -47.9258),
}

BRT = -3.0  # Brasilia standard time; no DST since 2019 (America/Sao_Paulo)


def _norm(text: str) -> str:
    """Upper-case ASCII, accents and replacement characters removed."""
    text = text.replace("\ufffd", "")
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    return text.upper().strip()


def _decode(raw: bytes) -> str:
    for enc in ("utf-8", "latin-1"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    raise DatasetError("INMET file is neither UTF-8 nor latin-1.")  # pragma: no cover


def _number(text: str, decimal: str) -> float | None:
    text = text.strip()
    if decimal == ",":
        text = text.replace(".", "").replace(",", ".") if text.count(",") else text
    try:
        value = float(text)
    except ValueError:
        return None
    return value


def _parse_header_block(lines: list[str], sep: str, decimal: str) -> dict:
    meta: dict[str, object] = {}
    for line in lines:
        parts = [p for p in line.split(sep)]
        if len(parts) < 2:
            continue
        key = _norm(parts[0]).rstrip(":")
        value = parts[1].strip()
        if key == "LATITUDE":
            meta["latitude"] = _number(value, decimal)
        elif key == "LONGITUDE":
            meta["longitude"] = _number(value, decimal)
        elif key.startswith("CODIGO"):
            meta["code"] = _norm(value)
        elif key in ("ESTACAO", "ESTAC"):
            meta["name"] = _norm(value)
        elif key == "ALTITUDE":
            meta["altitude"] = _number(value, decimal)
    return meta


def _find(columns: list[str], *needles: str) -> str | None:
    for col in columns:
        n = _norm(col)
        if all(k in n for k in needles):
            return col
    return None


def _parse_datetime(date: pd.Series, hour: pd.Series) -> pd.DatetimeIndex:
    h = hour.astype(str).str.upper().str.replace("UTC", "", regex=False).str.strip()
    digits = h.str.replace(r"[^0-9]", "", regex=True).str.zfill(4).str[:4]
    text = date.astype(str).str.strip() + " " + digits.str[:2] + ":" + digits.str[2:]
    out = pd.Series(pd.NaT, index=date.index, dtype="datetime64[ns]")
    for fmt in ("%Y/%m/%d %H:%M", "%Y-%m-%d %H:%M", "%d/%m/%Y %H:%M", "%d-%m-%Y %H:%M"):
        missing = out.isna()
        if not missing.any():
            break
        out[missing] = pd.to_datetime(text[missing], format=fmt, errors="coerce")
    return pd.DatetimeIndex(out).tz_localize("UTC")


def parse_inmet_csv(
    raw: bytes | str | Path,
    station: str | None = None,
    latitude: float | None = None,
    longitude: float | None = None,
    source: SourceFile | None = None,
) -> ProfileSet:
    """Parse one INMET station CSV into an ``irradiance`` ProfileSet.

    Coordinates come from the file header when present; otherwise from
    the arguments or :data:`KNOWN_STATIONS`.
    """
    if isinstance(raw, (str, Path)):
        path = Path(raw)
        source = source or SourceFile.from_path(path)
        raw = path.read_bytes()
    text = _decode(raw)
    lines = text.splitlines()
    header_idx = None
    for i, line in enumerate(lines[:40]):
        n = _norm(line)
        if re.match(r"^\"?DATA", n) and "HORA" in n:
            header_idx = i
    if header_idx is None:
        raise DatasetError("INMET: column header (Data;Hora UTC;...) not found in the first 40 lines.")
    header_line = lines[header_idx]
    sep = ";" if header_line.count(";") >= header_line.count(",") else ","
    decimal = "," if sep == ";" else "."
    meta = _parse_header_block(lines[:header_idx], sep, decimal)

    df = pd.read_csv(
        io.StringIO("\n".join(lines[header_idx:])), sep=sep, dtype=str,
        keep_default_na=False, index_col=False,
    )
    df = df.loc[:, [c for c in df.columns if c.strip() and not c.startswith("Unnamed")]]
    cols = list(df.columns)
    c_date, c_hour = cols[0], cols[1]
    c_rad = _find(cols, "RADIA")
    c_temp = _find(cols, "BULBO SECO") or _find(cols, "TEMPERATURA DO AR")
    if c_rad is None:
        raise DatasetError("INMET: no RADIACAO GLOBAL column in this file.")

    def numeric(col: str) -> pd.Series:
        s = df[col].str.strip()
        if decimal == ",":
            s = s.str.replace(",", ".", regex=False)
        v = pd.to_numeric(s, errors="coerce")
        return v.mask(v <= -9999)

    stamp_end = _parse_datetime(df[c_date], df[c_hour])
    report = QualityReport("inmet")
    bad = int(stamp_end.isna().sum())
    if bad:
        report.add("unparseable_timestamps", "warning", "rows with an invalid date/hour dropped", bad)
    frame = pd.DataFrame(
        {
            "ghi_wm2": (numeric(c_rad) / 3.6).to_numpy(),  # kJ/m2 per hour -> W/m2 mean
            **({"temp_air_c": numeric(c_temp).to_numpy()} if c_temp else {}),
        },
        index=stamp_end - RESOLUTION,  # hour-ending -> interval start (measured)
    )
    frame = frame[~frame.index.isna()]
    report.add("interval_end_to_start", "info", "INMET stamps mark the end of the hour; shifted by -1 h")
    report.add("unit_conversion", "info", "radiation kJ/m2 per hour -> mean W/m2 (/3.6)")

    code = (meta.get("code") or station or "").upper() or None
    lat = meta.get("latitude") if meta.get("latitude") is not None else latitude
    lon = meta.get("longitude") if meta.get("longitude") is not None else longitude
    if (lat is None or lon is None) and code in KNOWN_STATIONS:
        _, lat, lon = KNOWN_STATIONS[code]
        report.add("coordinates_from_registry", "info",
                   f"file has no header block; coordinates of {code} taken from KNOWN_STATIONS")
    if lat is None or lon is None:
        raise DatasetError("INMET: station coordinates unknown; pass latitude/longitude.")
    name = str(meta.get("name") or (KNOWN_STATIONS.get(code or "", ("?",))[0]))
    site = Site(f"INMET {code} {name}".strip(), float(lat), float(lon), BRT, "America/Sao_Paulo")

    frame = regularize(frame, RESOLUTION, report)
    frame["ghi_wm2"] = check_irradiance(frame["ghi_wm2"], RESOLUTION, site.latitude, site.longitude, report)
    frame = fill_short_gaps(frame, MAX_GAP_STEPS, report)
    check_solar_alignment(frame["ghi_wm2"], RESOLUTION, site.longitude, report)
    report.stats["ghi_completeness"] = float(frame["ghi_wm2"].notna().mean())
    if "temp_air_c" in frame:
        report.stats["temp_completeness"] = float(frame["temp_air_c"].notna().mean())
    return ProfileSet(
        "inmet", ProfileKind.IRRADIANCE, frame, RESOLUTION, site,
        (source,) if source else (), {"station": code, "altitude_m": meta.get("altitude")}, report,
    )


def extract_station_csv(zip_path: str | Path, station: str) -> tuple[str, bytes]:
    """Return ``(member_name, bytes)`` of a station CSV inside an INMET yearly ZIP."""
    station = station.upper()
    with zipfile.ZipFile(zip_path) as zf:
        members = [m for m in zf.namelist() if m.upper().endswith(".CSV")]
        hits = [m for m in members if f"_{station}_" in Path(m).name.upper()]
        if not hits:
            raise DatasetError(f"INMET: station {station} not found in {Path(zip_path).name} "
                               f"({len(members)} station files).")
        return hits[0], zf.read(hits[0])


def load(
    station: str = "A806",
    year: int = 2023,
    cache: DataCache | None = None,
    source_file: str | Path | None = None,
) -> ProfileSet:
    """Load one station-year. ``source_file`` may be a station CSV or a yearly ZIP."""
    cache = cache or DataCache()
    if source_file is not None:
        src = SourceFile.from_path(source_file)
    else:
        url = YEAR_ZIP_URL.format(year=int(year))
        src = cache.fetch("inmet", f"{int(year)}.zip", [url])
    path = Path(src.path)
    if path.suffix.lower() == ".zip":
        member, raw = extract_station_csv(path, station)
        ps = parse_inmet_csv(raw, station=station, source=src)
        ps.meta["zip_member"] = member
    else:
        ps = parse_inmet_csv(path, station=station, source=src)
    ps.meta["year"] = int(year)
    return ps


__all__ = ["INFO", "KNOWN_STATIONS", "extract_station_csv", "load", "parse_inmet_csv"]
