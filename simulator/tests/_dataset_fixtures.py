"""Format-faithful fixtures for the real-data loaders.

Each writer reproduces the *layout* of a real source file (separators,
encodings, header blocks, unit conventions, timing conventions, quirks
seen in the real data) with values generated here -- no third-party data
is copied into the repository. Irradiance/PV values come from the same
solar geometry the quality checks use, so a fixture written with the
source's true timing convention passes the alignment check and one with
the wrong convention fails it.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from gridsense_sim.datasets.solar import interval_mean_cos_zenith

FLO = (-27.6025, -48.62)
SYD = (-33.87, 151.21)


def clear_sky_ghi(start_utc: str, periods: int, freq: str, lat: float, lon: float,
                  cloud: float = 0.0, seed: int = 0) -> pd.Series:
    """Interval-mean 'clear-sky' GHI (W/m2) on an interval-start UTC grid."""
    idx = pd.date_range(start_utc, periods=periods, freq=freq, tz="UTC")
    mu = interval_mean_cos_zenith(idx, pd.Timedelta(freq), lat, lon)
    ghi = 1050.0 * mu**1.15
    if cloud:
        rng = np.random.default_rng(seed)
        ghi = ghi * (1 - cloud * rng.random(len(idx)))
    return pd.Series(ghi, index=idx)


# --------------------------------------------------------------- INMET
def write_inmet_official(path: Path, days: int = 10, start: str = "2024-01-01",
                         station: str = "A806", gap_hours: int = 0) -> Path:
    """Official layout: latin-1, ';', decimal comma, 8-line header, hour-ENDING stamps."""
    ghi = clear_sky_ghi(f"{start} 00:00", days * 24, "1h", *FLO, cloud=0.3, seed=1)
    lines = [
        "REGIAO:;S", "UF:;SC", "ESTACAO:;FLORIANOPOLIS", f"CODIGO (WMO):;{station}",
        "LATITUDE:;-27,60250000", "LONGITUDE:;-48,61999999", "ALTITUDE:;4,87",
        "DATA DE FUNDACAO:;22/01/03",
        "Data;Hora UTC;PRECIPITAÇÃO TOTAL, HORÁRIO (mm);RADIACAO GLOBAL (Kj/m²);"
        "TEMPERATURA DO AR - BULBO SECO, HORARIA (°C);UMIDADE RELATIVA DO AR, HORARIA (%);",
    ]
    for i, (t, g) in enumerate(ghi.items()):
        end = t + pd.Timedelta("1h")  # INMET labels the END of the hour
        kj = g * 3.6
        rad = "" if kj < 0.05 else f"{kj:.1f}".replace(".", ",")
        if gap_hours and 200 <= i < 200 + gap_hours:
            rad = "-9999"
        temp = f"{22 + 5 * np.sin(i / 24 * 2 * np.pi):.1f}".replace(".", ",")
        lines.append(f"{end:%Y/%m/%d};{end:%H%M} UTC;0;{rad};{temp};80;")
    path.write_bytes("\n".join(lines).encode("latin-1"))
    return path


def write_inmet_reexport(path: Path, days: int = 10) -> Path:
    """Re-exported variant: ',' separator, decimal point, accents stripped, UTF-8."""
    raw = write_inmet_official(path, days).read_bytes().decode("latin-1")
    out = []
    for line in raw.splitlines():
        cells = [c.replace(",", ".") for c in line.split(";")]
        out.append(",".join(cells).replace("Ç", "").replace("Ã", "").replace("Á", "").replace("°", "").replace("²", ""))
    path.write_text("\n".join(out), encoding="utf-8")
    return path


def write_inmet_headerless(path: Path, days: int = 10) -> Path:
    """Header block lost and accents turned into replacement characters (seen in the wild)."""
    raw = write_inmet_official(path, days).read_bytes().decode("latin-1")
    lines = raw.splitlines()[8:]
    lines[0] = lines[0].replace("Ç", "\ufffd").replace("Ã", "\ufffd").replace("²", "\ufffd")
    path.write_text("\n" + "\n".join(lines), encoding="utf-8")
    return path


# -------------------------------------------------------------- Ausgrid
def ausgrid_slot_labels() -> list[str]:
    labels = [f"{h}:{m:02d}" for h in range(0, 24) for m in (0, 30)][1:]
    return labels + ["0:00"]


def write_ausgrid(path: Path, start: str = "2012-07-01", days: int = 20, customers: int = 4,
                  dst: bool = True) -> Path:
    """Title line + header; slot labels are interval ENDS; Sydney wall clock (DST)."""
    labels = ausgrid_slot_labels()
    tz = "Australia/Sydney" if dst else "Etc/GMT-10"
    rows = [
        '"2012-2013 Solar home electricity data - Before using this data, please read the notes"'
        + "," * 53,
        "Customer,Generator Capacity,Postcode,Consumption Category,date," + ",".join(labels) + ",Row Quality",
    ]
    rng = np.random.default_rng(3)
    for c in range(1, customers + 1):
        cap = 1.5 + c
        for d in pd.date_range(start, periods=days, freq="D"):
            local = pd.DatetimeIndex([d + pd.Timedelta(minutes=30 * k) for k in range(48)])
            utc = local.tz_localize(tz, ambiguous=False, nonexistent="shift_forward").tz_convert("UTC")
            pv = interval_mean_cos_zenith(utc, pd.Timedelta("30min"), *SYD) * 0.85 * cap * 0.5  # kWh/30min
            hours = local.hour + local.minute / 60
            gc = (0.15 + 0.35 * np.exp(-((hours - 18.5) ** 2) / 4) + 0.1 * np.exp(-((hours - 7.5) ** 2) / 2)
                  + 0.02 * rng.random(48))
            cl = np.where(hours < 5, 0.6, 0.0)
            quality = "NA" if (c == 1 and d == pd.Timestamp(start)) else ""
            for cat, vals in (("CL", cl), ("GC", gc), ("GG", pv)):
                rows.append(f"{c},{cap},2076,{cat},{d.day}/{d:%m/%Y}," + ",".join(f"{v:.3f}" for v in vals)
                            + f",{quality}")
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")
    return path


# ------------------------------------------------------------------ LCL
def write_lcl(path: Path, households: int = 3, start: str = "2013-03-28", days: int = 6) -> Path:
    """UTC stamps (incl. 01:00 on the BST start day), duplicates, 'Null', Std/ToU mix."""
    rows = ["LCLid,stdorToU,DateTime,KWH/hh (per half hour) ,Acorn,Acorn_grouped"]
    idx = pd.date_range(start, periods=days * 48, freq="30min")
    for h in range(households):
        tariff = "ToU" if h == households - 1 else "Std"
        for i, t in enumerate(idx):
            v = 0.1 + 0.3 * np.exp(-(((t.hour + t.minute / 60) - 19) ** 2) / 6) + 0.01 * h
            val = "Null" if (h == 0 and i == 50) else f"{v:.3f}"
            rows.append(f"MAC{h:06d},{tariff},{t:%Y-%m-%d %H:%M:%S}.0000000,{val},ACORN-E,Affluent")
            if h == 0 and i == 10:  # exact duplicate record, as in the real file
                rows.append(rows[-1])
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")
    return path


# ------------------------------------------------------------ NASA POWER
def nasa_power_payload(start: str = "2024-01-01", days: int = 5, lat: float = -27.6,
                       lon: float = -48.55) -> dict:
    ghi = clear_sky_ghi(f"{start} 00:00", days * 24, "1h", lat, lon)
    keys = [f"{t:%Y%m%d%H}" for t in ghi.index]
    allsky = {k: round(float(v), 2) for k, v in zip(keys, ghi.to_numpy())}
    allsky[keys[30]] = -999.0
    return {
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": [lon, lat, 10.0]},
        "properties": {"parameter": {"ALLSKY_SFC_SW_DWN": allsky,
                                     "T2M": {k: 24.0 for k in keys}}},
        "header": {"title": "NASA/POWER Source Native Resolution Hourly Data",
                   "api": {"version": "v2.10.0", "name": "POWER Hourly API"},
                   "sources": ["SYN1DEG", "MERRA2"], "fill_value": -999.0, "time_standard": "UTC",
                   "start": start.replace("-", ""), "end": start.replace("-", "")},
        "messages": [],
        "parameters": {"ALLSKY_SFC_SW_DWN": {"units": "Wh/m^2", "longname": "All Sky"},
                       "T2M": {"units": "C", "longname": "Temperature at 2 Meters"}},
        "times": {"data": 0.1, "process": 0.01},
    }


# ----------------------------------------------------------------- PVGIS
def pvgis_payload(start: str = "2020-01-01", days: int = 5, lat: float = -27.6,
                  lon: float = -48.55, peak_kw: float = 1.0) -> dict:
    idx = pd.date_range(f"{start} 00:10", periods=days * 24, freq="1h", tz="UTC")
    mu = interval_mean_cos_zenith(idx - pd.Timedelta("10min"), pd.Timedelta("1h"), lat, lon)
    p = 1000.0 * peak_kw * 0.8 * mu
    rows = [{"time": f"{t:%Y%m%d:%H%M}", "P": round(float(v), 2), "G(i)": round(float(v) * 1.2, 2),
             "H_sun": 0.0, "T2m": 25.0, "WS10m": 2.0, "Int": 0.0} for t, v in zip(idx, p)]
    return {
        "inputs": {"location": {"latitude": lat, "longitude": lon, "elevation": 10.0},
                   "meteo_data": {"radiation_db": "PVGIS-SARAH3", "meteo_db": "ERA5"},
                   "mounting_system": {"fixed": {"slope": {"value": 20}, "azimuth": {"value": 180}}},
                   "pv_module": {"technology": "c-Si", "peak_power": peak_kw, "system_loss": 14.0}},
        "outputs": {"hourly": rows},
        "meta": {},
    }


def write_json(path: Path, payload: dict) -> Path:
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


# -------------------------------------------------------------- SimBench
def simbench_table(days: int = 400) -> pd.DataFrame:
    """SimBench-like table: CET/CEST wall clock with the 2016 DST transitions."""
    utc = pd.date_range("2015-12-31 23:00", periods=days * 96, freq="15min", tz="UTC")
    wall = utc.tz_convert("Europe/Berlin").tz_localize(None)
    h = wall.hour + wall.minute / 60
    table = pd.DataFrame({"time": wall.strftime("%d.%m.%Y %H:%M")})
    for name, peak in (("H0-A", 19.0), ("H0-B", 20.0), ("G1-A", 11.0)):
        table[f"{name}_pload"] = 0.2 + 0.8 * np.exp(-((h - peak) ** 2) / 8)
        table[f"{name}_qload"] = 0.1
    table["PV1"] = interval_mean_cos_zenith(utc, pd.Timedelta("15min"), 51.0, 10.0)
    return table.iloc[: 366 * 96 - 4]  # roughly one calendar year, like the real file
