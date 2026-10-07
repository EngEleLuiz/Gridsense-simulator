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


SCOPE_SEED = SEED.with_name("network_voltage_scope.csv")
GENERATOR = Path(__file__).resolve().parents[2] / "scripts" / "generate_voltage_scope_seed.py"


@pytest.mark.skipif(not SCOPE_SEED.exists(), reason="dbt scope seed not present in this checkout")
def test_dbt_scope_seed_matches_python_voltage_scope() -> None:
    """R07: the Silver layer must judge exactly the buses the HC code judges.

    Regenerates the seed rows from hosting_capacity/scope.py in memory and
    compares them with the committed CSV. Fix a failure by running
    ``python scripts/generate_voltage_scope_seed.py``.
    """
    import importlib.util

    spec = importlib.util.spec_from_file_location("gen_scope", GENERATOR)
    gen = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gen)
    expected = gen.render_csv(gen.scope_rows())
    assert SCOPE_SEED.read_text(encoding="utf-8").replace("\r\n", "\n") == expected
