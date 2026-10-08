"""Phase 7 loaders: each source's real layout, units, clock and quirks."""

from __future__ import annotations

import zipfile

import _dataset_fixtures as fx
import pandas as pd
import pytest
from gridsense_sim.datasets import (
    DATASETS,
    DatasetError,
    ProfileKind,
    ausgrid,
    inmet,
    lcl,
    load_dataset,
    nasa_power,
    parse_options,
    pvgis,
    simbench_profiles,
)
from gridsense_sim.datasets.quality import QualityReport, check_solar_alignment


# ------------------------------------------------------------------ INMET
@pytest.mark.parametrize("writer", [fx.write_inmet_official, fx.write_inmet_reexport, fx.write_inmet_headerless])
def test_inmet_variants_parse_to_the_same_series(tmp_path, writer) -> None:
    ps = inmet.parse_inmet_csv(writer(tmp_path / "A806.CSV"), station="A806")
    assert ps.kind is ProfileKind.IRRADIANCE and ps.resolution == pd.Timedelta("1h")
    assert ps.site.latitude == pytest.approx(-27.6025, abs=1e-3)
    assert not ps.quality.has_errors  # hour-ending stamps shifted correctly
    assert abs(ps.quality.stats["solar_alignment_offset_min"]) < 15
    assert ps.data["ghi_wm2"].max() == pytest.approx(1050, rel=0.1)  # kJ/m2 -> W/m2
    assert ps.start == pd.Timestamp("2024-01-01 00:00", tz="UTC")  # first stamp 01:00 UTC = end of hour 0
    assert ps.data["temp_air_c"].notna().all()


def test_inmet_without_the_hour_shift_would_be_flagged(tmp_path) -> None:
    """The -1 h shift is necessary: undoing it trips the solar-alignment check."""
    ps = inmet.parse_inmet_csv(fx.write_inmet_official(tmp_path / "A806.CSV", days=20))
    shifted = ps.data["ghi_wm2"].copy()
    shifted.index = shifted.index + pd.Timedelta("1h")
    rep = QualityReport("t")
    check_solar_alignment(shifted, pd.Timedelta("1h"), ps.site.longitude, rep)
    assert rep.has_errors


def test_inmet_missing_values_and_daytime_gaps(tmp_path) -> None:
    ps = inmet.parse_inmet_csv(fx.write_inmet_official(tmp_path / "x.CSV", days=12, gap_hours=6))
    codes = {i.code for i in ps.quality.issues}
    assert "night_zero_filled" in codes
    assert ps.data["ghi_wm2"].isna().sum() > 0  # a 6 h -9999 run is longer than the 2 h fill limit


def test_inmet_station_is_read_from_the_yearly_zip(tmp_path) -> None:
    csv = fx.write_inmet_official(tmp_path / "INMET_S_SC_A806_FLORIANOPOLIS_01-01-2024_A_31-12-2024.CSV")
    other = fx.write_inmet_official(tmp_path / "INMET_S_SC_A841_JOACABA_01-01-2024_A_31-12-2024.CSV",
                                    station="A841")
    zpath = tmp_path / "2024.zip"
    with zipfile.ZipFile(zpath, "w") as zf:
        zf.write(other, f"2024/{other.name}")
        zf.write(csv, f"2024/{csv.name}")
    ps = inmet.load(station="A806", year=2024, source_file=zpath)
    assert ps.meta["station"] == "A806" and ps.meta["zip_member"].endswith(csv.name)
    with pytest.raises(DatasetError, match="A999"):
        inmet.load(station="A999", year=2024, source_file=zpath)


def test_inmet_without_header_or_coordinates_fails_clearly(tmp_path) -> None:
    path = fx.write_inmet_headerless(tmp_path / "x.CSV")
    with pytest.raises(DatasetError, match="coordinates"):
        inmet.parse_inmet_csv(path, station="Z999")


# ---------------------------------------------------------------- Ausgrid
def test_ausgrid_load_and_pv_units_clock_and_flags(tmp_path) -> None:
    path = fx.write_ausgrid(tmp_path / "solar.csv", start="2012-09-20", days=30)  # spans DST start
    load = ausgrid.load("load", source_file=path)
    pv = ausgrid.load("pv", source_file=path)
    assert load.kind is ProfileKind.LOAD_KW and pv.kind is ProfileKind.PV_PU
    assert list(load.data.columns) == ["c001", "c002", "c003", "c004"]
    assert not load.quality.has_errors and not pv.quality.has_errors  # DST handled
    assert abs(pv.quality.stats["solar_alignment_offset_min"]) < 15
    codes = {i.code for i in load.quality.issues}
    assert {"wall_clock_with_dst", "estimated_rows", "interval_end_to_start"} <= codes
    local = load.data["c001"].tz_convert("Australia/Sydney")
    by_hour = local.groupby(local.index.hour).mean()
    assert by_hour.idxmax() in (0, 1, 2, 3, 4, 18, 19)  # off-peak CL at night or evening GC peak
    assert pv.data.max().max() <= 1.0 and load.meta["capacity_kwp"]["c001"] == 2.5


def test_ausgrid_read_as_fixed_aest_would_fail_alignment(tmp_path) -> None:
    path = fx.write_ausgrid(tmp_path / "solar.csv", start="2012-10-10", days=40)
    long, meta = ausgrid.parse_ausgrid(path)
    wrong = long.copy()
    wrong["utc"] = (pd.DatetimeIndex(wrong["local"]).tz_localize("Etc/GMT-10").tz_convert("UTC"))
    ps = ausgrid.build_profile_sets(wrong, meta, "pv")
    assert ps.quality.has_errors  # +60 min in DST months


def test_ausgrid_rejects_bad_layout(tmp_path) -> None:
    (tmp_path / "bad.csv").write_text("foo,bar\n1,2\n")
    with pytest.raises(DatasetError, match="Customer"):
        ausgrid.load(source_file=tmp_path / "bad.csv")


# -------------------------------------------------------------------- LCL
def test_lcl_utc_duplicates_nulls_and_tariff_filter(tmp_path) -> None:
    path = fx.write_lcl(tmp_path / "lcl.csv")
    ps = lcl.load(start=None, end=None, source_file=path)
    assert list(ps.data.columns) == ["MAC000000", "MAC000001"]  # ToU household excluded
    codes = {i.code for i in ps.quality.issues}
    assert {"duplicate_records", "null_reads", "clock_utc"} <= codes
    # 2013-03-31 01:00 exists: stamps are UTC, not London wall clock
    assert pd.Timestamp("2013-03-31 01:00", tz="UTC") in ps.data.index
    assert ps.data.notna().all().all()
    both = lcl.load(start=None, end=None, tariff=None, source_file=path)
    assert both.data.shape[1] == 3


def test_lcl_reads_partitioned_zip_and_period(tmp_path) -> None:
    a = fx.write_lcl(tmp_path / "block_0.csv", households=2)
    zpath = tmp_path / "Partitioned LCL Data.zip"
    with zipfile.ZipFile(zpath, "w") as zf:
        zf.write(a, "Small LCL Data/LCL-June2015v2_0.csv")
    ps = lcl.load(start="2013-03-29", end="2013-03-31", source_file=zpath, max_households=1)
    assert ps.data.shape == (96, 1)
    with pytest.raises(DatasetError, match="no rows"):
        lcl.load(start="2020-01-01", end="2020-02-01", source_file=zpath)


# ------------------------------------------------------------- NASA POWER
def test_nasa_power_units_fill_value_and_timing(tmp_path) -> None:
    ps = nasa_power.load(source_file=fx.write_json(tmp_path / "np.json", fx.nasa_power_payload()))
    assert ps.kind is ProfileKind.IRRADIANCE and not ps.quality.has_errors
    assert ps.site.utc_offset_hours == -3.0
    assert ps.data["temp_air_c"].eq(24.0).all()
    # the -999 fill value is gone (interpolated: single interior gap)
    assert ps.data["ghi_wm2"].min() >= 0


def test_nasa_power_rejects_lst_and_wrong_units(tmp_path) -> None:
    p = fx.nasa_power_payload()
    p["header"]["time_standard"] = "LST"
    with pytest.raises(DatasetError, match="UTC"):
        nasa_power.parse_nasa_power_json(p)
    p = fx.nasa_power_payload()
    p["parameters"]["ALLSKY_SFC_SW_DWN"]["units"] = "MJ/m^2/day"
    with pytest.raises(DatasetError, match="units"):
        nasa_power.parse_nasa_power_json(p)


def test_nasa_power_request_url_is_stable() -> None:
    url = nasa_power.request_url(-27.5954, -48.548, "20230101", "20231231")
    assert url.startswith(nasa_power.API_URL) and "time-standard=UTC" in url and "community=RE" in url


# ------------------------------------------------------------------ PVGIS
def test_pvgis_per_unit_and_timing(tmp_path) -> None:
    ps = pvgis.load(source_file=fx.write_json(tmp_path / "pv.json", fx.pvgis_payload(peak_kw=4.0)))
    assert ps.kind is ProfileKind.PV_PU and 0.7 < ps.data["pv_pu"].max() <= 0.81
    assert not ps.quality.has_errors
    assert ps.meta["radiation_db"] == "PVGIS-SARAH3"


def test_pvgis_requires_pv_calculation() -> None:
    p = fx.pvgis_payload()
    for r in p["outputs"]["hourly"]:
        r.pop("P")
    with pytest.raises(DatasetError, match="pvcalculation"):
        pvgis.parse_pvgis_json(p)


# --------------------------------------------------------------- SimBench
def test_simbench_table_wall_clock_and_profiles() -> None:
    ps = simbench_profiles.from_profiles_table(fx.simbench_table(), "load", ["H0-A", "G1-A"])
    assert ps.kind is ProfileKind.LOAD_PU and list(ps.data.columns) == ["H0-A", "G1-A"]
    local = ps.data["H0-A"].tz_convert("Europe/Berlin")
    assert local.groupby(local.index.hour).mean().idxmax() == 19  # wall-clock peak preserved
    with pytest.raises(DatasetError, match="unknown"):
        simbench_profiles.from_profiles_table(fx.simbench_table(), "load", ["X0-Z"])


def test_simbench_package_data_if_installed() -> None:
    pytest.importorskip("simbench")
    ps = simbench_profiles.load("load")
    assert ps.data.shape == (35136, 3) and ps.resolution == pd.Timedelta("15min")
    assert ps.data.notna().all().all()


# --------------------------------------------------------------- registry
def test_registry_covers_six_documented_datasets() -> None:
    assert set(DATASETS) == {"inmet", "nasa_power", "pvgis", "ausgrid", "lcl", "simbench"}
    for info in DATASETS.values():
        assert info.citation and info.license and info.homepage.startswith("https://")


def test_parse_options_types() -> None:
    assert parse_options(["year=2023", "station=A806", "kind=pv", "x=1.5", "y=none", "z=true",
                          "year-range=2012-2013"]) == {
        "year": 2023, "station": "A806", "kind": "pv", "x": 1.5, "y": None, "z": True,
        "year_range": "2012-2013"}
    with pytest.raises(ValueError):
        parse_options(["novalue"])


def test_load_dataset_dispatches_with_manual_file(tmp_path) -> None:
    path = fx.write_json(tmp_path / "pv.json", fx.pvgis_payload())
    assert load_dataset("pvgis", source_file=str(path)).dataset == "pvgis"
    with pytest.raises(KeyError):
        load_dataset("nope")
