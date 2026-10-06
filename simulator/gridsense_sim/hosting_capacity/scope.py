"""Which buses are voltage-checked (review finding C11).

A hosting-capacity voltage criterion applies to *customer service
points*, not to every node of the model. Two classes of bus are
excluded by construction:

* buses with an ``ext_grid`` (slack): their voltage is an input
  setpoint, not a result. In case14 the slack sits at 1.06 pu, which
  made the base case "infeasible" by definition under ANSI Range A.
* buses with an in-service voltage-controlled ``gen``: same reason.

Optionally, only buses at given nominal voltages are kept
(``NetworkLimits.voltage_levels_kv``) -- in cigre_lv this removes the
20 kV MV buses, which an LV regulatory band does not apply to.
"""

from __future__ import annotations

import math

import pandapower as pp

from .limits import NetworkLimits


def voltage_scope_buses(net: pp.pandapowerNet, limits: NetworkLimits) -> tuple[int, ...]:
    """Return the sorted bus indices whose voltage is checked against ``limits``."""
    excluded: set[int] = set()
    if not net.ext_grid.empty:
        excluded |= {int(b) for b in net.ext_grid["bus"]}
    if not net.gen.empty:
        in_service = net.gen[net.gen["in_service"].astype(bool)]
        excluded |= {int(b) for b in in_service["bus"]}

    buses = net.bus[net.bus["in_service"].astype(bool)]
    if limits.voltage_levels_kv is not None:
        levels = limits.voltage_levels_kv
        mask = buses["vn_kv"].apply(
            lambda vn: any(math.isclose(float(vn), lvl, rel_tol=1e-6) for lvl in levels)
        )
        buses = buses[mask]

    scope = tuple(sorted(int(b) for b in buses.index if int(b) not in excluded))
    if not scope:
        raise ValueError(
            "Voltage scope is empty: no in-service bus remains after excluding "
            "slack/generator buses and filtering by voltage level."
        )
    return scope
