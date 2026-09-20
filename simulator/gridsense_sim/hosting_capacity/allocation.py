"""Distributes a global PV penetration factor across load buses.

The deterministic and QSTS methods search over a single scalar
penetration factor `lambda_` (coordinated adoption: all buses gain PV
simultaneously, scaled together). This module defines how much PV
each bus gets at a given `lambda_`, decoupled from the search itself
so the same allocation strategy can be reused by both methods (and
compared against the stochastic method's uncoordinated, per-bus
random allocation in stochastic.py).
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pandapower as pp


@dataclass(frozen=True)
class PVAllocation:
    """A fixed per-bus PV sizing basis (MW at lambda_ == 1.0).

    At a given penetration factor `lambda_`, bus `b` gets
    `base_mw_per_bus[b] * lambda_` MW of PV.
    """

    base_mw_per_bus: dict[int, float] = field(default_factory=dict)

    def mw_at(self, lambda_: float) -> dict[int, float]:
        return {bus: base * lambda_ for bus, base in self.base_mw_per_bus.items()}


def proportional_to_load(net: pp.pandapowerNet) -> PVAllocation:
    """Size each bus's PV basis proportional to its existing load.

    This is the default allocation: a bus that already draws more
    load gets proportionally more PV at any given lambda_, which is a
    reasonable first approximation for "how much rooftop PV could
    plausibly go where the houses/loads already are" -- as opposed to
    e.g. equal MW per bus, which would put unrealistically large PV
    on lightly loaded buses.

    Buses with zero load get zero PV basis (and therefore never gain
    PV under this allocation, at any lambda_) -- a bus with no load
    load is not represented in the load table at all, so this only
    ever concerns buses that do have a load row with p_mw == 0.
    """
    base_mw_per_bus: dict[int, float] = {}
    for _, row in net.load.iterrows():
        bus = int(row["bus"])
        base_mw_per_bus[bus] = base_mw_per_bus.get(bus, 0.0) + float(row["p_mw"])
    return PVAllocation(base_mw_per_bus=base_mw_per_bus)


def apply_allocation(net: pp.pandapowerNet, allocation: PVAllocation, lambda_: float) -> None:
    """Set net.sgen p_mw values in-place to allocation.mw_at(lambda_).

    Creates one sgen per bus in the allocation on first call (name
    prefixed "hosting_capacity_pv" so it never collides with any
    renewable sgens the engine's own `_apply_renewable_profile` may
    add for other purposes); subsequent calls just update p_mw.
    """
    mw_per_bus = allocation.mw_at(lambda_)
    existing_by_bus = {
        int(net.sgen.at[idx, "bus"]): idx
        for idx in net.sgen.index
        if str(net.sgen.at[idx, "name"]).startswith("hosting_capacity_pv")
    }
    for bus, mw in mw_per_bus.items():
        if bus in existing_by_bus:
            net.sgen.at[existing_by_bus[bus], "p_mw"] = mw
        else:
            pp.create_sgen(net, bus=bus, p_mw=mw, q_mvar=0.0, name=f"hosting_capacity_pv_{bus}")
