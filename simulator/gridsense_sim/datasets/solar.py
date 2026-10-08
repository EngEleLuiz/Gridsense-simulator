"""Solar geometry, irradiance plausibility limits and a simple PV model.

Dependency-free (numpy/pandas only) on purpose: the simulator must keep
running on any machine without pvlib. Accuracy targets are the ones the
data-quality checks need, not resource assessment:

* Solar position -- NOAA "General Solar Position Calculations" (Spencer
  1971 Fourier series for declination and equation of time). Error is a
  few tenths of a degree, ~1-2 min in solar noon: enough to detect a
  timestamp shifted by 30-60 min, which is what the checks look for.
  Cross-checked in the tests against the sun height PVGIS ships with
  every hourly record (``H_sun``).
* Plausibility limits -- BSRN recommended QC tests (Long & Shi, 2008,
  "An automated quality assessment and control algorithm for surface
  radiation measurements", The Open Atmospheric Science Journal 2,
  23-37): physically possible limit (PPL) and extremely rare limit (ERL)
  for global horizontal irradiance.
* PV model -- horizontal plane, NOCT cell temperature, linear power
  temperature coefficient, lumped system losses, AC clipping at the
  rating. It is a deliberate simplification (no transposition, no
  spectral or angle-of-incidence losses) and it is recorded as such in
  every series manifest. PVGIS provides a full model when a tilted plane
  matters.

All functions take **interval-start** UTC timestamps and work on
interval means, because every loader normalises to that convention.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np
import pandas as pd

SOLAR_CONSTANT_WM2 = 1361.0


def _utc_index(index: pd.DatetimeIndex) -> pd.DatetimeIndex:
    if index.tz is None:
        raise ValueError("Solar geometry needs a tz-aware (UTC) DatetimeIndex.")
    return index.tz_convert("UTC")


def _fractional_year(index: pd.DatetimeIndex) -> np.ndarray:
    """Spencer's day angle (radians), with the hour folded in."""
    idx = _utc_index(index)
    hours = idx.hour + idx.minute / 60.0 + idx.second / 3600.0
    days_in_year = np.where(idx.is_leap_year, 366.0, 365.0)
    return 2.0 * np.pi / days_in_year * (idx.dayofyear - 1 + (hours - 12.0) / 24.0)


def equation_of_time_minutes(index: pd.DatetimeIndex) -> np.ndarray:
    g = _fractional_year(index)
    return 229.18 * (
        0.000075 + 0.001868 * np.cos(g) - 0.032077 * np.sin(g)
        - 0.014615 * np.cos(2 * g) - 0.040849 * np.sin(2 * g)
    )


def declination_rad(index: pd.DatetimeIndex) -> np.ndarray:
    g = _fractional_year(index)
    return (
        0.006918 - 0.399912 * np.cos(g) + 0.070257 * np.sin(g)
        - 0.006758 * np.cos(2 * g) + 0.000907 * np.sin(2 * g)
        - 0.002697 * np.cos(3 * g) + 0.00148 * np.sin(3 * g)
    )


def extraterrestrial_normal_wm2(index: pd.DatetimeIndex) -> np.ndarray:
    g = _fractional_year(index)
    return SOLAR_CONSTANT_WM2 * (
        1.00011 + 0.034221 * np.cos(g) + 0.00128 * np.sin(g)
        + 0.000719 * np.cos(2 * g) + 0.000077 * np.sin(2 * g)
    )


def cos_zenith(index: pd.DatetimeIndex, latitude: float, longitude: float) -> np.ndarray:
    """Cosine of the solar zenith angle at each (instantaneous) timestamp."""
    idx = _utc_index(index)
    minutes = idx.hour * 60.0 + idx.minute + idx.second / 60.0
    true_solar_minutes = minutes + equation_of_time_minutes(idx) + 4.0 * longitude
    hour_angle = np.deg2rad(true_solar_minutes / 4.0 - 180.0)
    lat = np.deg2rad(latitude)
    dec = declination_rad(idx)
    return np.sin(lat) * np.sin(dec) + np.cos(lat) * np.cos(dec) * np.cos(hour_angle)


def solar_elevation_deg(index: pd.DatetimeIndex, latitude: float, longitude: float) -> np.ndarray:
    return np.rad2deg(np.arcsin(np.clip(cos_zenith(index, latitude, longitude), -1.0, 1.0)))


def interval_mean_cos_zenith(
    index: pd.DatetimeIndex,
    resolution: pd.Timedelta,
    latitude: float,
    longitude: float,
    n_sub: int = 12,
) -> np.ndarray:
    """Mean of ``max(cos Z, 0)`` over each interval ``[t, t + resolution)``.

    Sub-sampled at the midpoints of ``n_sub`` equal slices, so an hourly
    interval that contains sunrise gets a small positive value instead of
    the 0 an instantaneous evaluation at its start would give.
    """
    idx = _utc_index(index)
    acc = np.zeros(len(idx))
    for k in range(n_sub):
        offset = resolution * ((k + 0.5) / n_sub)
        acc += np.clip(cos_zenith(idx + offset, latitude, longitude), 0.0, None)
    return acc / n_sub


def solar_noon_utc_minutes(index: pd.DatetimeIndex, longitude: float) -> np.ndarray:
    """Minutes after 00:00 UTC of local solar noon, for each timestamp's date."""
    return 720.0 - 4.0 * longitude - equation_of_time_minutes(index)


def bsrn_ghi_limits(
    index: pd.DatetimeIndex, resolution: pd.Timedelta, latitude: float, longitude: float
) -> tuple[np.ndarray, np.ndarray]:
    """BSRN (Long & Shi 2008) upper limits for GHI: (physically possible, extremely rare).

    Evaluated with the interval-mean cos Z, which is the right quantity
    for interval-mean irradiance.
    """
    mu0 = interval_mean_cos_zenith(index, resolution, latitude, longitude)
    s0a = extraterrestrial_normal_wm2(index + resolution / 2)
    ppl = s0a * 1.5 * mu0**1.2 + 100.0
    erl = s0a * 1.2 * mu0**1.2 + 50.0
    return ppl, erl


@dataclass(frozen=True)
class PVModel:
    """Horizontal-plane PV with NOCT temperature and AC clipping.

    Attributes:
        gamma_per_c: Power temperature coefficient (1/degC), c-Si typical.
        noct_c: Nominal operating cell temperature (degC).
        system_losses: Lumped DC+AC losses (PVGIS default 14 %).
        clip_at: AC output cap, per unit of rating (DC/AC = 1 -> 1.0).
        default_temp_air_c: Used only when the source has no temperature.
    """

    gamma_per_c: float = -0.0037
    noct_c: float = 45.0
    system_losses: float = 0.14
    clip_at: float = 1.0
    default_temp_air_c: float = 20.0

    def to_dict(self) -> dict:
        return {"model": "horizontal_noct_v1", **asdict(self)}


def ghi_to_pv_pu(
    ghi_wm2: pd.Series, temp_air_c: pd.Series | None = None, model: PVModel | None = None
) -> pd.Series:
    """PV output per unit of rating from global horizontal irradiance.

    ``P/P_stc = G/1000 * (1 + gamma (T_cell - 25)) * (1 - losses)``, with
    ``T_cell = T_air + G (NOCT - 20) / 800``, clipped to ``[0, clip_at]``.
    """
    model = model or PVModel()
    g = ghi_wm2.clip(lower=0.0).fillna(0.0)
    if temp_air_c is None:
        t_air = pd.Series(model.default_temp_air_c, index=g.index)
    else:
        t_air = temp_air_c.reindex(g.index).interpolate(limit=3).fillna(model.default_temp_air_c)
    t_cell = t_air + g * (model.noct_c - 20.0) / 800.0
    pu = g / 1000.0 * (1.0 + model.gamma_per_c * (t_cell - 25.0)) * (1.0 - model.system_losses)
    return pu.clip(lower=0.0, upper=model.clip_at)


def alignment_offset_minutes(
    values: pd.Series,
    resolution: pd.Timedelta,
    longitude: float,
    min_daily_sum: float | None = None,
) -> pd.DataFrame:
    """Energy-weighted centroid of each day's solar-driven series vs solar noon.

    For a correctly labelled interval-start series the centroid of the
    interval *midpoints* sits at solar noon (offset ~0, cloud noise
    averages out over a month). A series labelled with interval *end*
    times shows ~ +1 interval; daylight-saving time shows ~ +60 min in
    the DST months only. Returns one row per month with the median
    daily offset and the number of days used.
    """
    s = values.dropna().clip(lower=0.0)
    if s.empty:
        return pd.DataFrame(columns=["offset_min", "days"])
    idx = _utc_index(s.index)
    mid = idx + resolution / 2
    minutes = mid.hour * 60.0 + mid.minute + mid.second / 60.0
    noon = solar_noon_utc_minutes(mid, longitude)
    rel = (minutes - noon + 720.0) % 1440.0 - 720.0  # minutes from solar noon, in [-720, 720)
    # Group by *solar* day so a day never straddles the UTC midnight.
    solar_day = (mid + pd.to_timedelta(4.0 * longitude, unit="min")).normalize()
    df = pd.DataFrame({"w": s.to_numpy(), "rel": rel, "day": solar_day.tz_localize(None)})
    steps_per_day = pd.Timedelta("1D") / resolution
    daily = df.groupby("day").apply(
        lambda d: pd.Series(
            {"n": len(d), "sum": d["w"].sum(),
             "offset": (d["w"] * d["rel"]).sum() / d["w"].sum() if d["w"].sum() > 0 else np.nan}
        ),
        include_groups=False,
    )
    # Only (nearly) complete days: a partial day biases the centroid toward
    # whichever half survived.
    daily = daily[daily["n"] >= 0.9 * steps_per_day]
    if daily.empty:
        return pd.DataFrame(columns=["offset_min", "days"])
    threshold = min_daily_sum if min_daily_sum is not None else 0.2 * daily["sum"].quantile(0.9)
    daily = daily[daily["sum"] >= threshold].dropna()
    if daily.empty:
        return pd.DataFrame(columns=["offset_min", "days"])
    monthly = daily.groupby(daily.index.to_period("M"))["offset"].agg(["median", "size"])
    monthly.columns = ["offset_min", "days"]
    return monthly


__all__ = [
    "SOLAR_CONSTANT_WM2",
    "PVModel",
    "alignment_offset_minutes",
    "bsrn_ghi_limits",
    "cos_zenith",
    "declination_rad",
    "equation_of_time_minutes",
    "extraterrestrial_normal_wm2",
    "ghi_to_pv_pu",
    "interval_mean_cos_zenith",
    "solar_elevation_deg",
    "solar_noon_utc_minutes",
]
