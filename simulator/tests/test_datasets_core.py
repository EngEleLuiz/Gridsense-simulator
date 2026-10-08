"""Phase 7 core: solar geometry, quality checks, download cache."""

from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pytest
from _dataset_fixtures import FLO, clear_sky_ghi
from gridsense_sim.datasets import (
    DataCache,
    DataNotAvailableError,
    ProfileKind,
    ProfileSet,
)
from gridsense_sim.datasets.cache import ChecksumMismatchError
from gridsense_sim.datasets.quality import (
    QualityReport,
    check_irradiance,
    check_solar_alignment,
    clip_negative,
    fill_short_gaps,
    flag_flatlines,
    regularize,
)
from gridsense_sim.datasets.solar import (
    PVModel,
    alignment_offset_minutes,
    bsrn_ghi_limits,
    ghi_to_pv_pu,
    solar_elevation_deg,
    solar_noon_utc_minutes,
)

H = pd.Timedelta("1h")


# ------------------------------------------------------------------ solar
def test_solar_elevation_matches_pvgis_sun_height() -> None:
    """Reference: H_sun from a real PVGIS response (45 N, 8 E, 2013-01-01)."""
    idx = pd.DatetimeIndex(["2013-01-01 08:10", "2013-01-01 09:10"], tz="UTC")
    np.testing.assert_allclose(solar_elevation_deg(idx, 45.0, 8.0), [8.06, 14.80], atol=0.15)


def test_solar_noon_tracks_longitude_and_equation_of_time() -> None:
    idx = pd.DatetimeIndex(["2024-02-11", "2024-11-03"], tz="UTC")
    noon = solar_noon_utc_minutes(idx, -48.62)
    # 12:00 + 4 min/deg * 48.62 deg = 15:14 UTC, -/+ ~14/16 min equation of time
    assert 15 * 60 + 14 + 10 < noon[0] < 15 * 60 + 14 + 18
    assert 15 * 60 + 14 - 19 < noon[1] < 15 * 60 + 14 - 12


def test_bsrn_limits_are_ordered_and_positive_at_night() -> None:
    idx = pd.date_range("2024-01-01", periods=48, freq="1h", tz="UTC")
    ppl, erl = bsrn_ghi_limits(idx, H, *FLO)
    assert (ppl >= erl).all() and (erl >= 50.0).all()
    assert ppl.max() > 1200


def test_pv_model_is_monotone_clipped_and_temperature_sensitive() -> None:
    g = pd.Series([0.0, 200.0, 600.0, 1000.0, 1400.0])
    pu = ghi_to_pv_pu(g)
    assert pu.iloc[0] == 0 and pu.is_monotonic_increasing and pu.max() <= 1.0
    hot = ghi_to_pv_pu(g, pd.Series(40.0, index=g.index))
    cold = ghi_to_pv_pu(g, pd.Series(5.0, index=g.index))
    assert (hot.iloc[1:4] < cold.iloc[1:4]).all()
    assert ghi_to_pv_pu(pd.Series([1300.0]), model=PVModel(system_losses=0.0, gamma_per_c=0.0)).iloc[0] == 1.0


def test_alignment_detects_interval_end_labels_and_dst() -> None:
    ghi = clear_sky_ghi("2024-01-01", 24 * 60, "1h", *FLO)
    ok = alignment_offset_minutes(ghi, H, FLO[1])
    assert abs(ok["offset_min"]).max() < 5
    shifted = ghi.copy()
    shifted.index = shifted.index + H  # labelled with interval END
    assert alignment_offset_minutes(shifted, H, FLO[1])["offset_min"].median() == pytest.approx(60, abs=5)
    partial = ghi[ghi.index.hour < 15]  # only mornings: must not be measured at all
    assert alignment_offset_minutes(partial, H, FLO[1]).empty


# ---------------------------------------------------------------- quality
def _frame(values, start="2024-01-01", freq="1h") -> pd.DataFrame:
    return pd.DataFrame({"a": values}, index=pd.date_range(start, periods=len(values), freq=freq, tz="UTC"))


def test_regularize_counts_duplicates_conflicts_and_missing() -> None:
    df = _frame([1.0, 2.0, 3.0, 4.0])
    df = pd.concat([df, df.iloc[[1]], df.iloc[[2]].assign(a=99.0)]).drop(df.index[3:3])
    df = df.drop(df.index[3])  # one missing interval
    df = pd.concat([df, _frame([5.0], start="2024-01-01 04:00")])
    rep = QualityReport("t")
    out = regularize(df, H, rep)
    codes = {i.code: i for i in rep.issues}
    assert codes["duplicate_timestamps"].severity == "warning"  # one conflicting pair
    assert codes["missing_timestamps"].count == 1
    assert len(out) == 5 and out["a"].isna().sum() == 1
    assert out["a"].iloc[2] == pytest.approx((3.0 + 99.0) / 2)


def test_fill_short_gaps_only_fills_short_interior_runs() -> None:
    v = [1.0, np.nan, 3.0, np.nan, np.nan, np.nan, 7.0, np.nan]
    rep = QualityReport("t")
    out = fill_short_gaps(_frame(v), 2, rep)
    assert out["a"].iloc[1] == 2.0
    assert out["a"].iloc[3:6].isna().all()  # run of 3 > 2
    assert np.isnan(out["a"].iloc[7])  # edge: never extrapolated
    assert rep.issues[0].count == 1


def test_negative_values_and_flatlines_are_reported() -> None:
    rep = QualityReport("t")
    out = clip_negative(_frame([1.0, -0.5, 2.0]), rep)
    assert np.isnan(out["a"].iloc[1])
    flag_flatlines(_frame([0.0] * 20 + [1.2] * 15 + [0.5]), 12, rep)
    assert {i.code for i in rep.issues} == {"negative_values", "flat_lines"}


def test_check_irradiance_zeroes_night_and_rejects_impossible_values() -> None:
    ghi = clear_sky_ghi("2024-01-01", 48, "1h", *FLO)
    ghi.iloc[3] = 150.0  # 03:00 UTC = night in Florianopolis
    noon = int(np.argmax(ghi.to_numpy()))
    ghi.iloc[noon] = 2500.0  # above the physically possible limit
    ghi.iloc[5] = np.nan  # missing at night -> known zero
    rep = QualityReport("t")
    out = check_irradiance(ghi, H, *FLO, rep)
    assert out.iloc[3] == 0.0 and out.iloc[5] == 0.0 and np.isnan(out.iloc[noon])
    assert {"irradiance_at_night", "ghi_above_physical_limit", "night_zero_filled"} <= {i.code for i in rep.issues}


def test_misaligned_solar_series_is_an_error() -> None:
    ghi = clear_sky_ghi("2024-01-01", 24 * 30, "1h", *FLO)
    ghi.index = ghi.index - pd.Timedelta("2h")
    rep = QualityReport("t")
    check_solar_alignment(ghi, H, FLO[1], rep)
    assert rep.has_errors and rep.errors()[0].code == "timestamp_misaligned"


def test_profileset_rejects_non_utc_irregular_or_duplicated_indexes() -> None:
    df = _frame([1.0, 2.0, 3.0])
    ProfileSet("t", ProfileKind.LOAD_KW, df, H)
    with pytest.raises(ValueError):
        ProfileSet("t", ProfileKind.LOAD_KW, df.tz_convert("America/Sao_Paulo"), H)
    with pytest.raises(ValueError):
        ProfileSet("t", ProfileKind.LOAD_KW, df.iloc[[0, 2]], H)
    with pytest.raises(ValueError):
        ProfileSet("t", ProfileKind.LOAD_KW, pd.concat([df, df.iloc[[0]]]).sort_index(), H)


# ------------------------------------------------------------------ cache
def test_cache_fetches_records_sidecar_and_detects_tampering(tmp_path) -> None:
    src = tmp_path / "remote.csv"
    src.write_text("a,b\n1,2\n")
    cache = DataCache(root=tmp_path / "cache", offline=False, retries=1, backoff_s=0)
    bad = (tmp_path / "missing.csv").as_uri()
    sf = cache.fetch("demo", "file.csv", [bad, src.as_uri()])  # first mirror fails
    assert sf.url == src.as_uri() and len(sf.sha256) == 64
    sidecar = json.loads((tmp_path / "cache/demo/file.csv.source.json").read_text())
    assert sidecar["sha256"] == sf.sha256
    assert cache.fetch("demo", "file.csv", ["file:///nowhere"]).sha256 == sf.sha256  # cache hit
    (tmp_path / "cache/demo/file.csv").write_text("a,b\n1,3\n")
    with pytest.raises(ChecksumMismatchError):
        cache.cached("demo", "file.csv")


def test_cache_offline_and_expected_hash(tmp_path) -> None:
    src = tmp_path / "remote.bin"
    src.write_bytes(b"x" * 10)
    offline = DataCache(root=tmp_path / "c1", offline=True)
    with pytest.raises(DataNotAvailableError, match="save it as"):
        offline.fetch("demo", "f.bin", [src.as_uri()])
    online = DataCache(root=tmp_path / "c2", offline=False, retries=1, backoff_s=0)
    with pytest.raises(DataNotAvailableError, match="sha256"):
        online.fetch("demo", "f.bin", [src.as_uri()], expected_sha256="0" * 64)
    assert not (tmp_path / "c2/demo/f.bin").exists()
    with pytest.raises(ValueError):
        online.path_for("demo", "../escape.bin")


def test_manual_copy_is_accepted_and_hashed(tmp_path) -> None:
    cache = DataCache(root=tmp_path, offline=True)
    target = cache.path_for("inmet", "2023.zip")
    target.parent.mkdir(parents=True)
    target.write_bytes(b"zip")
    sf = cache.fetch("inmet", "2023.zip", ["https://unreachable.invalid/2023.zip"])
    assert sf.url is None and sf.size_bytes == 3
