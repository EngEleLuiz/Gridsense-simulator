"""uk_pv loader (London PV, Phase 7.1) and the same-site series clock."""

from __future__ import annotations

import _dataset_fixtures as fx
import numpy as np
import pandas as pd
import pytest
from gridsense_sim.datasets import (
    PRESET_SITES,
    DatasetError,
    ProfileKind,
    ProfileSet,
    build_series,
    uk_pv,
)
from gridsense_sim.datasets.quality import QualityReport

LONDON = PRESET_SITES["london"]


def test_selects_nearby_cleans_and_per_units(tmp_path) -> None:
    root = fx.write_uk_pv(tmp_path / "uk_pv")
    ps = uk_pv.load(start="2013-06-01", end="2013-06-08", source_file=root)
    assert ps.kind is ProfileKind.PV_PU and ps.resolution == pd.Timedelta("5min")
    assert set(ps.data.columns) == {"ss101", "ss102", "ss103", "ss106"}  # far and night-faulty removed
    codes = {i.code for i in ps.quality.issues}
    assert {"interval_end_to_start", "negative_values", "bad_data_periods", "night_output_systems_dropped"} <= codes
    assert not ps.quality.has_errors  # period-ending stamps shifted correctly
    assert ps.data.max().max() <= 1.0 and ps.meta["capacity_kwp"]["ss102"] == 4.0
    assert ps.start == pd.Timestamp("2013-06-01", tz="UTC")


def test_thirty_minute_product_and_errors(tmp_path) -> None:
    root = fx.write_uk_pv(tmp_path / "uk", product="30_minutely", step_min=30)
    ps = uk_pv.load(start="2013-06-01", end="2013-06-08", resolution="30min", source_file=root)
    assert ps.resolution == pd.Timedelta("30min")
    with pytest.raises(DatasetError, match="gated"):
        uk_pv.load()
    with pytest.raises(DatasetError, match="no 5_minutely"):
        uk_pv.load(start="2015-01-01", end="2015-02-01", source_file=root)
    with pytest.raises(DatasetError, match="no domestic system"):
        uk_pv.load(latitude=0.0, longitude=0.0, source_file=root, resolution="30min")


def _london_load(start="2013-06-01", days=10) -> ProfileSet:
    """Homes with an evening peak at 19:00 on the London *wall clock* (BST in June)."""
    utc = pd.date_range(f"{start}", periods=days * 48, freq="30min", tz="UTC")
    wall = utc.tz_convert("Europe/London")
    h = wall.hour + wall.minute / 60
    df = pd.DataFrame({"a": 0.3 + np.exp(-((h - 19) ** 2) / 2)}, index=utc)
    return ProfileSet("demo", ProfileKind.LOAD_KW, df, pd.Timedelta("30min"), fx_site(), quality=QualityReport("d"))


def fx_site():
    from gridsense_sim.datasets.lcl import SITE
    return SITE


def test_same_site_uses_physical_time_and_other_site_wall_clock(tmp_path) -> None:
    pv = uk_pv.load(start="2013-06-01", end="2013-06-09", source_file=fx.write_uk_pv(tmp_path / "u", days=9))
    load = _london_load()
    same = build_series(load, pv, target=LONDON, start="2013-06-02", days=5, steps_per_day=144)
    assert same.manifest["load"]["clock_mode"] == "physical"
    # 19:00 BST = 18:00 GMT: on the study clock (GMT) the load peaks at 18 h
    assert same.manifest["diagnostics"]["load_mean_peak_hour"] == 18
    wall = build_series(load, pv, target=LONDON, start="2013-06-02", days=5, steps_per_day=144,
                        load_clock="wall")
    assert wall.manifest["diagnostics"]["load_mean_peak_hour"] == 19
    with pytest.raises(ValueError, match="load_clock"):
        build_series(load, pv, target=LONDON, start="2013-06-02", days=2, load_clock="x")
