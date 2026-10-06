"""Python limits and the dbt seed must agree (cross-layer consistency)."""

from __future__ import annotations

import csv
from pathlib import Path

import pytest

from gridsense_sim.hosting_capacity import NETWORK_LIMITS

SEED = Path(__file__).resolve().parents[2] / "transform" / "seeds" / "network_voltage_limits.csv"


@pytest.mark.skipif(not SEED.exists(), reason="dbt seed not present in this checkout")
def test_dbt_seed_matches_python_default_limits() -> None:
    with SEED.open(newline="") as fh:
        rows = {r["network"]: r for r in csv.DictReader(fh)}
    assert set(rows) == set(NETWORK_LIMITS)
    for name, lim in NETWORK_LIMITS.items():
        assert float(rows[name]["v_min_pu"]) == pytest.approx(lim.v_min_pu), name
        assert float(rows[name]["v_max_pu"]) == pytest.approx(lim.v_max_pu), name
