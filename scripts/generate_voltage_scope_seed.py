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

import pandapower.networks as pn

from gridsense_sim.hosting_capacity import limits_for, voltage_scope_buses
from gridsense_sim.hosting_capacity.limits import NETWORK_DEFAULT_FRAMEWORK

SEED_PATH = Path(__file__).resolve().parents[1] / "transform" / "seeds" / "network_voltage_scope.csv"

NETWORK_FACTORIES = {
    "case14": pn.case14,
    "case39": pn.case39,
    "case57": pn.case57,
    "case118": pn.case118,
    "cigre_lv": pn.create_cigre_network_lv,
}


def scope_rows() -> list[tuple[str, int]]:
    """``(network, bus_id)`` for every voltage-checked bus, sorted."""
    missing = set(NETWORK_DEFAULT_FRAMEWORK) - set(NETWORK_FACTORIES)
    if missing:
        raise RuntimeError(f"No network factory for {sorted(missing)}; add it to NETWORK_FACTORIES.")
    rows: list[tuple[str, int]] = []
    for name in sorted(NETWORK_DEFAULT_FRAMEWORK):
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
