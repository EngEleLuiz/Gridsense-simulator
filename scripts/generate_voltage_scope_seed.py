"""Generate the dbt seed of voltage-checked buses from the Python scope rule.

Review finding R07: the Silver layer flagged slack, generator and MV
buses as voltage violations, while the hosting-capacity code excludes
them (``hosting_capacity/scope.py``, finding C11). The same reading was
"a violation" on the dashboard and "out of scope" in the HC study.

This script is the single bridge between the two layers: it evaluates
``voltage_scope_buses()`` for every registered network and writes
``transform/seeds/network_voltage_scope.csv``. ``tests/test_hc_cross_layer.py``
regenerates the rows in memory and fails if the committed seed drifts.

Usage (from the repository root):
    python scripts/generate_voltage_scope_seed.py
"""

from __future__ import annotations

import argparse
import csv
import io
from pathlib import Path


from gridsense_sim.hosting_capacity import limits_for, voltage_scope_buses
from gridsense_sim.hosting_capacity.limits import NETWORK_DEFAULT_FRAMEWORK
from gridsense_sim.networks import NETWORKS, make_network

SEED_PATH = Path(__file__).resolve().parents[1] / "transform" / "seeds" / "network_voltage_scope.csv"

# Single source of truth: gridsense_sim.networks (Phase 7.3). Built with the
# default existing-PV policy; PV does not change which buses are LV loads.
NETWORK_FACTORIES = {key: (lambda k=key: make_network(k)) for key in NETWORKS}


def available_networks() -> list[str]:
    """Registered networks whose optional dependencies are installed."""
    import importlib.util

    return sorted(k for k, v in NETWORKS.items()
                  if k in NETWORK_DEFAULT_FRAMEWORK
                  and (v.requires is None or importlib.util.find_spec(v.requires) is not None))


def scope_rows(networks: list[str] | None = None) -> list[tuple[str, int]]:
    """``(network, bus_id)`` for every voltage-checked bus, sorted.

    ``networks=None`` means all registered networks (the committed seed is
    generated that way, with the optional 'data' extra installed).
    """
    missing = set(NETWORK_DEFAULT_FRAMEWORK) - set(NETWORK_FACTORIES)
    if missing:
        raise RuntimeError(f"No network factory for {sorted(missing)}; add it to NETWORK_FACTORIES.")
    rows: list[tuple[str, int]] = []
    for name in sorted(networks if networks is not None else NETWORK_DEFAULT_FRAMEWORK):
        net = NETWORK_FACTORIES[name]()
        rows.extend((name, bus) for bus in voltage_scope_buses(net, limits_for(name)))
    return rows


def render_csv(rows: list[tuple[str, int]]) -> str:
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(["network", "bus_id"])
    writer.writerows(rows)
    return buf.getvalue()


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--output", type=Path, default=SEED_PATH)
    args = p.parse_args()
    rows = scope_rows()
    args.output.write_text(render_csv(rows), encoding="utf-8", newline="\n")
    print(f"Wrote {len(rows)} in-scope buses to {args.output}")


if __name__ == "__main__":
    main()
