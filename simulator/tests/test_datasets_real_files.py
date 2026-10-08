"""Opt-in checks on genuine raw files (never committed to the repository).

Set ``GRIDSENSE_REAL_DATA_DIR`` to a folder holding any of:

* INMET station CSVs: ``INMET_*.CSV`` or ``A###*.CSV`` (official or re-exported)
* Ausgrid: ``*Solar home electricity data*.csv`` or ``ausgrid*.csv``
* Low Carbon London: ``*LCL*.csv`` / ``lcl*.csv``
* NASA POWER responses: ``nasa*.json``; PVGIS responses: ``pvgis*.json``

Each test asserts what the loader promises on *real* data: it parses,
no quality *errors*, and solar timing within tolerance where it can be
measured. Skipped (not failed) when no such file is present, so CI stays
offline.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from gridsense_sim.datasets import ausgrid, inmet, lcl, nasa_power, pvgis

ROOT = os.environ.get("GRIDSENSE_REAL_DATA_DIR")
pytestmark = pytest.mark.skipif(not ROOT, reason="GRIDSENSE_REAL_DATA_DIR not set")


def _files(*patterns: str) -> list[Path]:
    if not ROOT:
        return []
    out: list[Path] = []
    for pat in patterns:
        out += sorted(Path(ROOT).glob(pat))
    return sorted(set(out))


def _need(files: list[Path]) -> list[Path]:
    if not files:
        pytest.skip("no matching real file")
    return files


def test_real_inmet_files() -> None:
    for f in _need(_files("INMET_*.CSV", "A[0-9][0-9][0-9]*.CSV")):
        station = next((p for p in f.stem.replace("-", "_").split("_") if p[:1] == "A" and p[1:4].isdigit()), None)
        ps = inmet.parse_inmet_csv(f, station=station)
        assert not ps.quality.has_errors, (f.name, ps.quality.summary())
        assert abs(ps.quality.stats["solar_alignment_offset_min"]) < 20, f.name
        assert 0 <= ps.data["ghi_wm2"].max() < 1400


def test_real_ausgrid_files() -> None:
    for f in _need(_files("*Solar home electricity data*.csv", "ausgrid*.csv")):
        long, meta = ausgrid.parse_ausgrid(f)
        for kind in ("load", "pv"):
            ps = ausgrid.build_profile_sets(long, meta, kind)
            assert not ps.quality.has_errors, (f.name, kind, ps.quality.summary())
            assert ps.data.shape[1] >= 250
        assert abs(ps.quality.stats["solar_alignment_offset_min"]) < 15


def test_real_lcl_files() -> None:
    for f in _need(_files("*LCL*.csv", "lcl*.csv")):
        ps = lcl.parse_lcl(f, max_households=50)
        assert ps.data.shape[1] >= 1 and not ps.quality.has_errors


def test_real_nasa_power_files() -> None:
    for f in _need(_files("nasa*.json")):
        ps = nasa_power.parse_nasa_power_json(f)
        assert not ps.quality.has_errors, ps.quality.summary()


def test_real_pvgis_files() -> None:
    for f in _need(_files("pvgis*.json")):
        ps = pvgis.parse_pvgis_json(f)
        assert not ps.quality.has_errors, ps.quality.summary()
        assert ps.data["pv_pu"].max() <= 1.0
