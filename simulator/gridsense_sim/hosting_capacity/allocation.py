"""Distribution of PV across buses for a penetration factor ``lambda_``.

Units convention (used by all three methods): ``lambda_`` is
**total PV / total nominal load**. :func:`proportional_to_load` puts
``lambda_ * p_load_nominal(b)`` on each bus, and the stochastic
estimator normalizes every random adoption pattern to the same total,
so a lambda from any method means the same MW.

The allocation basis must therefore be computed from the **nominal**
network, *before* any ``load_scale`` is applied -- otherwise lambda
would silently change meaning with the operating point.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pandapower as pp

HC_SGEN_PREFIX = "hosting_capacity_pv"


@dataclass(frozen=True)
class PVAllocation:
    """Per-bus PV sizing basis, in MW at ``lambda_ == 1.0``."""

    base_mw_per_bus: dict[int, float] = field(default_factory=dict)

    def mw_at(self, lambda_: float) -> dict[int, float]:
        """PV MW per bus at penetration ``lambda_``."""
        return {bus: base * lambda_ for bus, base in self.base_mw_per_bus.items()}

    @property
    def total_base_mw(self) -> float:
        """Total PV MW at ``lambda_ == 1.0``."""
        return float(sum(self.base_mw_per_bus.values()))

    def require_non_empty(self) -> None:
        """Raise if there is nothing to scale."""
        if not self.base_mw_per_bus or self.total_base_mw <= 0.0:
            raise ValueError(
                "Allocation has no bus with positive PV basis -- cannot search "
                "for a penetration factor with nothing to scale."
            )


def proportional_to_load(net: pp.pandapowerNet) -> PVAllocation:
    """PV basis proportional to each bus's nominal load (sums multiple loads)."""
    base: dict[int, float] = {}
    for bus, p in zip(net.load["bus"], net.load["p_mw"]):
        base[int(bus)] = base.get(int(bus), 0.0) + float(p)
    return PVAllocation(base_mw_per_bus=base)


def apply_allocation(net: pp.pandapowerNet, allocation: PVAllocation, lambda_: float) -> None:
    """Set the HC PV sgens of ``net`` to ``allocation.mw_at(lambda_)`` in place.

    Creates one unity-power-factor sgen per bus on first use (name
    prefixed :data:`HC_SGEN_PREFIX`), then only updates ``p_mw``.
    """
    existing = {
        int(net.sgen.at[idx, "bus"]): idx
        for idx in net.sgen.index
        if str(net.sgen.at[idx, "name"]).startswith(HC_SGEN_PREFIX)
    }
    for bus, mw in allocation.mw_at(lambda_).items():
        if bus in existing:
            net.sgen.at[existing[bus], "p_mw"] = mw
        else:
            pp.create_sgen(net, bus=bus, p_mw=mw, q_mvar=0.0, name=f"{HC_SGEN_PREFIX}_{bus}")
