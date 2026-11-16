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

import math
from dataclasses import dataclass, field

import pandapower as pp

HC_SGEN_PREFIX = "hosting_capacity_pv"
MIN_PV_POWER_FACTOR = 0.8


def reactive_per_active(power_factor: float) -> float:
    """Q/P of the study PV for a fixed inductive power factor (generator convention).

    The inverters run in constant-power-factor mode and *absorb* reactive
    power in proportion to their active output::

        Q = -P * tan(arccos(power_factor))

    ``lambda_`` keeps measuring installed **active** power: the inverter is
    assumed to be rated ``S = P / power_factor`` (e.g. +8.7 % at 0.92), so the
    reactive support never curtails active power. That sizing rule is an
    assumption of the study and is recorded with every result through
    ``conditions.pv_power_factor``.

    Returns ``0.0`` for unity power factor (the default of every method).
    """
    if not MIN_PV_POWER_FACTOR <= power_factor <= 1.0:
        raise ValueError(
            f"pv_power_factor must be in [{MIN_PV_POWER_FACTOR}, 1.0] (inductive), got {power_factor}.")
    return -math.tan(math.acos(power_factor)) if power_factor < 1.0 else 0.0


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


def apply_allocation(net: pp.pandapowerNet, allocation: PVAllocation, lambda_: float,
                     q_per_p: float = 0.0) -> None:
    """Set the HC PV sgens of ``net`` to ``allocation.mw_at(lambda_)`` in place.

    Creates one sgen per bus on first use (name prefixed
    :data:`HC_SGEN_PREFIX`), then only updates ``p_mw`` and ``q_mvar``.
    ``q_per_p`` is the reactive power per unit of active power
    (:func:`reactive_per_active`); ``0.0`` is unity power factor.
    """
    existing = {
        int(net.sgen.at[idx, "bus"]): idx
        for idx in net.sgen.index
        if str(net.sgen.at[idx, "name"]).startswith(HC_SGEN_PREFIX)
    }
    for bus, mw in allocation.mw_at(lambda_).items():
        if bus in existing:
            net.sgen.at[existing[bus], "p_mw"] = mw
            net.sgen.at[existing[bus], "q_mvar"] = mw * q_per_p
        else:
            pp.create_sgen(net, bus=bus, p_mw=mw, q_mvar=mw * q_per_p, name=f"{HC_SGEN_PREFIX}_{bus}")
