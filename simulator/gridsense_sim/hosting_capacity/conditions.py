"""Operating point and study conditions (review finding C1).

The worst case for PV-driven overvoltage and reverse flow is
**maximum PV with minimum load** (dissertation Eq. 3.15). The original
deterministic search ran at *nominal* load, which overestimated the
hosting capacity of cigre_lv by ~44% (lambda 2.289 at x1.0 vs 1.586
at x0.25, doc 06).

``DEFAULT_CRITICAL_LOAD_SCALE`` = 0.25 is the minimum-load factor used
in the doc-06 review. It is an **assumption** (no real minimum-load
data until Phase 8), so it is never applied invisibly: every result
records the factor it used in :class:`StudyConditions`.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandapower as pp

from .limits import NetworkLimits

DEFAULT_CRITICAL_LOAD_SCALE: float = 0.25


@dataclass(frozen=True)
class StudyConditions:
    """Everything needed to interpret (and reproduce) one HC number.

    Attributes:
        network_name: Registered network key.
        framework: Voltage framework name (see ``limits.FRAMEWORKS``).
        criterion_kind: ``"instantaneous"`` or ``"statistical"``.
        v_min_pu, v_max_pu: The band actually applied.
        load_scale: Multiplier on nominal load P and Q, or ``None`` when
            load is time-varying (QSTS).
        n_voltage_buses: Size of the voltage-check scope.
        pv_power_factor: PV reactive behaviour (1.0 = unity).
    """

    network_name: str
    framework: str
    criterion_kind: str
    v_min_pu: float
    v_max_pu: float
    load_scale: float | None
    n_voltage_buses: int
    pv_power_factor: float = 1.0

    @classmethod
    def build(
        cls,
        network_name: str,
        limits: NetworkLimits,
        load_scale: float | None,
        n_voltage_buses: int,
    ) -> "StudyConditions":
        return cls(
            network_name=network_name,
            framework=limits.framework,
            criterion_kind=limits.kind.value,
            v_min_pu=limits.v_min_pu,
            v_max_pu=limits.v_max_pu,
            load_scale=load_scale,
            n_voltage_buses=n_voltage_buses,
        )


def apply_load_scale(net: pp.pandapowerNet, scale: float) -> None:
    """Scale every load's P **and** Q in place (constant power factor)."""
    if scale <= 0:
        raise ValueError(f"load_scale must be > 0, got {scale}.")
    net.load["p_mw"] = net.load["p_mw"] * scale
    net.load["q_mvar"] = net.load["q_mvar"] * scale
