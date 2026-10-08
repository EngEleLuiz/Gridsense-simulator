"""Phase 7 series builder: clocks, seasons, normalisation, provenance, QSTS integration."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow.parquet as pq
import pytest

import _dataset_fixtures as fx
from gridsense_sim.datasets import (
    PRESET_SITES,
    DatasetError,
    ProfileKind,
    ProfileSet,
    Site,
    build_series,
    load_series,
    save_series,
)
from gridsense_sim.datasets import ausgrid, inmet
from gridsense_sim.datasets.quality import QualityReport
from gridsense_sim.hosting_capacity import find_hosting_capacity_qsts

FLO = PRESET_SITES["florianopolis"]
SOUTH_LOAD_SITE = Site("south homes", -33.9, 151.2, 10.0, "Australia/Sydney")
NORTH_LOAD_SITE = Site("north homes", 51.5, -0.1, 0.0, "Europe/London")


def _load_set(site: Site, start="2023-01-01", days=400, freq="30min", peak_hour=19.0,
              winter_boost=0.0) -> ProfileSet:
    """Two homes with an evening peak on the *local wall clock*."""
    utc = pd.date_range(f"{start} 00:00", periods=int(days * pd.Timedelta("1D") / pd.Timedelta(freq)),
                        freq=freq, tz="UTC")
    wall = utc.tz_convert(site.tz_name)
    h = wall.hour + wall.minute / 60
    month = wall.month.to_numpy()
    season = 1 + winter_boost * np.isin(month, [6, 7, 8])
    base = (0.3 + np.exp(-((h - peak_hour) ** 2) / 3)) * season
    df = pd.DataFrame({"h1": base, "h2": base * 1.5}, index=utc)
    return ProfileSet("demo_load", ProfileKind.LOAD_KW, df, pd.Timedelta(freq), site,
                      quality=QualityReport("demo_load"))


def _irr_set(start="2023-01-01", days=400) -> ProfileSet:
    ghi = fx.clear_sky_ghi(f"{start} 00:00", days * 24, "1h", FLO.latitude, FLO.longitude)
    df = pd.DataFrame({"ghi_wm2": ghi, "temp_air_c": 25.0})
    return ProfileSet("demo_irr", ProfileKind.IRRADIANCE, df, pd.Timedelta("1h"), FLO,
                      quality=QualityReport("demo_irr"))


def test_build_series_keeps_wall_clock_peak_and_solar_noon() -> None:
    real = build_series(_load_set(SOUTH_LOAD_SITE), _irr_set(), target=FLO, start="2023-03-01",
                        days=14, steps_per_day=96)
    ts, d = real.series, real.manifest["diagnostics"]
    assert ts.n_steps == 14 * 96 and ts.steps_per_day == 96 and ts.source == "real:demo_load+demo_irr"
    assert d["load_mean_peak_hour"] == 19  # behaviour follows the household's own clock
    assert d["pv_mean_peak_hour"] in (11, 12)  # sun follows standard time at the target
    assert real.manifest["load"]["season_shift_days"] == 0
    assert np.all(ts.load_mult > 0) and np.all((ts.pv_mult >= 0) & (ts.pv_mult <= 1))
    assert real.local_index[0] == pd.Timestamp("2023-03-01")


def test_hemisphere_shift_is_automatic_and_recorded() -> None:
    north = _load_set(NORTH_LOAD_SITE, winter_boost=0.0)
    real = build_series(north, _irr_set(), target=FLO, start="2023-07-01", days=7)
    assert real.manifest["load"]["season_shift_days"] == 182
    w0 = pd.Timestamp(real.manifest["load"]["source_window_local"][0])
    assert w0.month == 12 or (w0.month == 1 and w0.day < 5)  # Brazilian July <- northern winter
    pinned = build_series(north, _irr_set(), target=FLO, start="2023-07-01", days=7,
                          load_season_shift_days=0)
    assert pinned.manifest["load"]["source_window_local"][0] == "2023-07-01"


def test_source_peak_normalisation_keeps_seasons_window_peak_forces_one() -> None:
    load = _load_set(SOUTH_LOAD_SITE, winter_boost=0.5)
    summer = build_series(load, _irr_set(), target=FLO, start="2023-01-10", days=7)
    winter = build_series(load, _irr_set(), target=FLO, start="2023-07-10", days=7)
    assert winter.series.load_mult.max() == pytest.approx(1.0, abs=0.02)
    assert summer.series.load_mult.max() == pytest.approx(1 / 1.5, abs=0.02)
    forced = build_series(load, _irr_set(), target=FLO, start="2023-01-10", days=7, normalize="window_peak",
                          peak_load_mult=0.9)
    assert forced.series.load_mult.max() == pytest.approx(0.9)


def test_upsampling_hold_conserves_energy() -> None:
    a = build_series(_load_set(SOUTH_LOAD_SITE), _irr_set(), target=FLO, start="2023-02-01", days=3,
                     steps_per_day=48)
    b = build_series(_load_set(SOUTH_LOAD_SITE), _irr_set(), target=FLO, start="2023-02-01", days=3,
                     steps_per_day=288)
    assert b.series.pv_mult.mean() == pytest.approx(a.series.pv_mult.mean(), rel=1e-3)
    assert b.series.load_mult.mean() == pytest.approx(a.series.load_mult.mean(), rel=1e-3)


def test_window_outside_coverage_and_bad_arguments_fail_clearly() -> None:
    short = _load_set(SOUTH_LOAD_SITE, days=20)
    with pytest.raises(DatasetError, match="no 30-day window"):
        build_series(short, _irr_set(), target=FLO, start="2023-01-05", days=30)
    with pytest.raises(ValueError, match="divide 1440"):
        build_series(short, _irr_set(), target=FLO, start="2023-01-02", days=1, steps_per_day=7)


def test_quality_errors_block_unless_overridden() -> None:
    irr = _irr_set()
    irr.quality.add("timestamp_misaligned", "error", "demo")
    with pytest.raises(DatasetError, match="timestamp_misaligned"):
        build_series(_load_set(SOUTH_LOAD_SITE), irr, target=FLO, start="2023-02-01", days=2)
    build_series(_load_set(SOUTH_LOAD_SITE), irr, target=FLO, start="2023-02-01", days=2,
                 allow_quality_errors=True)


def test_long_gaps_are_filled_from_climatology_and_counted() -> None:
    irr = _irr_set()
    day = irr.data.index[(irr.data.index >= "2023-02-03 12:00") & (irr.data.index < "2023-02-03 18:00")]
    irr.data.loc[day, "ghi_wm2"] = np.nan
    real = build_series(_load_set(SOUTH_LOAD_SITE), irr, target=FLO, start="2023-02-01", days=5,
                        steps_per_day=24)
    assert real.manifest["pv"]["climatology_filled_steps"] == 6
    assert not np.isnan(real.series.pv_mult).any()


def test_save_load_roundtrip_and_tamper_detection(tmp_path) -> None:
    real = build_series(_load_set(SOUTH_LOAD_SITE), _irr_set(), target=FLO, start="2023-02-01", days=2)
    path = save_series(real, tmp_path / "s")
    assert path.suffix == ".parquet" and path.with_suffix(".manifest.json").exists()
    back = load_series(path)
    np.testing.assert_array_equal(back.series.load_mult, real.series.load_mult)
    assert back.manifest_sha256 == real.manifest_sha256 == back.series.manifest_sha256
    table = pq.read_table(path)
    arr = table.column("pv_mult").to_numpy().copy()
    arr[50] += 0.01
    tampered = table.set_column(table.schema.get_field_index("pv_mult"), "pv_mult", [arr])
    pq.write_table(tampered.replace_schema_metadata(table.schema.metadata), tmp_path / "t.parquet")
    with pytest.raises(DatasetError, match="do not match"):
        load_series(tmp_path / "t.parquet")


def test_manifest_records_sources_and_quality(tmp_path) -> None:
    path = fx.write_inmet_official(tmp_path / "A806.CSV", days=40)
    pv = inmet.parse_inmet_csv(path)
    load = ausgrid.load("load", source_file=fx.write_ausgrid(tmp_path / "a.csv", start="2012-12-20", days=30))
    real = build_series(load, pv, target=FLO, start="2024-01-02", days=7, steps_per_day=48)
    m = real.manifest
    assert m["load"]["dataset"] == "ausgrid" and m["pv"]["dataset"] == "inmet"
    assert len(m["pv"]["sources"][0]["sha256"]) == 64
    assert m["pv"]["quality"]["issues"] and m["pv"]["conversion"]["model"] == "horizontal_noct_v1"
    json.dumps(m)  # fully JSON-serialisable


def test_qsts_runs_on_a_real_series_and_reports_its_provenance(cigre) -> None:
    real = build_series(_load_set(SOUTH_LOAD_SITE), _irr_set(), target=FLO, start="2023-02-01",
                        days=1, steps_per_day=24, peak_load_mult=0.8)
    r = find_hosting_capacity_qsts(cigre, "cigre_lv", series=real.series, tolerance=0.1)
    assert r.bounded and r.lambda_max > 0
    assert r.series_source == "real:demo_load+demo_irr"
    assert r.series_manifest_sha256 == real.manifest_sha256


def test_runner_records_real_series_params(tmp_path, cigre) -> None:  # noqa: ARG001
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
    import run_hosting_capacity_study as study

    real = build_series(_load_set(SOUTH_LOAD_SITE), _irr_set(), target=FLO, start="2023-02-01",
                        days=1, steps_per_day=24, peak_load_mult=0.8)
    path = save_series(real, tmp_path / "series")
    with pytest.raises(SystemExit):
        study.parse_args(["--network", "cigre_lv", "--qsts-series", str(path), "--qsts-peak-load", "1.0"])
    with pytest.raises(SystemExit):
        study.parse_args(["--network", "cigre_lv", "--qsts-series", str(tmp_path / "missing.parquet")])
    out = tmp_path / "hc"
    study.main(["--network", "cigre_lv", "--methods", "qsts", "--qsts-series", str(path),
                "--tolerance", "0.1", "--output-dir", str(out)])
    rows = pq.read_table(next(out.rglob("*.parquet"))).to_pylist()
    payload = json.loads(rows[0]["raw_value"])
    assert payload["status"] == "ok"
    assert payload["params"]["series_manifest_sha256"] == real.manifest_sha256
    assert payload["series_source"] == "real:demo_load+demo_irr"
    assert "total_steps" not in payload["params"]  # synthetic-only knobs are not recorded
