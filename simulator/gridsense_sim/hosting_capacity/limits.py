"""Voltage/thermal criteria that define a hosting-capacity violation.

Design (review finding C2)
--------------------------
The criterion is a *named regulatory framework*, never an anonymous
pair of numbers, because the frameworks are not interchangeable:

``ansi_c84_range_a`` (0.95-1.05 pu)
    ANSI C84.1 Range A, steady-state service voltage. Used for the
    transmission test cases (case14/39/57/118).
``prodist_m8_bt`` (0.92-1.05 pu)
    ANEEL PRODIST Modulo 8, "adequada" range for LV 220/127 V
    (202-231 V on a 220 V base). **Verify against the revision in force
    before citing** -- tracked in the dissertation reference checklist.
``en50160_envelope`` (0.90-1.10 pu)
    EN 50160 +-10%. NOTE: in the standard this is a *statistical*
    criterion (95% of 10-min mean values over one week). Applying it to
    a single power-flow snapshot is a category error that the code
    still permits (it is the historical default for ``cigre_lv``,
    pending the advisor's decision) but flags via
    :attr:`CriterionKind.STATISTICAL`, so every result carries the
    caveat. It only becomes methodologically coherent inside a QSTS
    duration criterion (tau_bar = 0.05), which is S1 work.

The per-network *default* framework below reproduces the behaviour of
the original module exactly (no silent change of published numbers);
pass ``framework=`` explicitly to switch.

Cross-layer consistency: ``transform/seeds/network_voltage_limits.csv``
must hold the same default limits; ``tests/test_hc_cross_layer.py``
fails if they drift apart.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class CriterionKind(str, Enum):
    """Whether a voltage band is meant for instantaneous or statistical use."""

    INSTANTANEOUS = "instantaneous"
    STATISTICAL = "statistical"


@dataclass(frozen=True)
class NetworkLimits:
    """Limits that define a violation for one study.

    Attributes:
        v_min_pu, v_max_pu: Voltage band applied to buses in scope.
        max_line_loading_percent, max_trafo_loading_percent: Thermal limits.
        framework: Name of the regulatory framework the band comes from.
        kind: Instantaneous vs statistical (see module docstring).
        voltage_levels_kv: If set, only buses at these nominal voltages
            are voltage-checked (e.g. ``(0.4,)`` for cigre_lv, so the
            20 kV MV buses -- not customer service points -- are not
            judged by an LV criterion). ``None`` means every level.
    """

    v_min_pu: float
    v_max_pu: float
    max_line_loading_percent: float = 100.0
    max_trafo_loading_percent: float = 100.0
    framework: str = "custom"
    kind: CriterionKind = CriterionKind.INSTANTANEOUS
    voltage_levels_kv: tuple[float, ...] | None = None

    def __post_init__(self) -> None:
        if not 0.0 < self.v_min_pu < 1.0 < self.v_max_pu:
            raise ValueError(
                f"Voltage band must satisfy 0 < v_min < 1 < v_max, got "
                f"[{self.v_min_pu}, {self.v_max_pu}]."
            )
        if self.max_line_loading_percent <= 0 or self.max_trafo_loading_percent <= 0:
            raise ValueError("Thermal limits must be positive.")


@dataclass(frozen=True)
class VoltageFramework:
    """A named voltage band from a standard or regulation."""

    name: str
    v_min_pu: float
    v_max_pu: float
    kind: CriterionKind
    reference: str


FRAMEWORKS: dict[str, VoltageFramework] = {
    "ansi_c84_range_a": VoltageFramework(
        "ansi_c84_range_a", 0.95, 1.05, CriterionKind.INSTANTANEOUS,
        "ANSI C84.1, Range A (service voltage)",
    ),
    "prodist_m8_bt": VoltageFramework(
        "prodist_m8_bt", 0.92, 1.05, CriterionKind.INSTANTANEOUS,
        "ANEEL PRODIST Modulo 8, faixa adequada BT 220/127 V [VERIFY revision]",
    ),
    "en50160_envelope": VoltageFramework(
        "en50160_envelope", 0.90, 1.10, CriterionKind.STATISTICAL,
        "EN 50160, +-10% (95% of 10-min means per week)",
    ),
}

NETWORK_DEFAULT_FRAMEWORK: dict[str, str] = {
    "case14": "ansi_c84_range_a",
    "case39": "ansi_c84_range_a",
    "case57": "ansi_c84_range_a",
    "case118": "ansi_c84_range_a",
    "cigre_lv": "en50160_envelope",
}

NETWORK_VOLTAGE_LEVELS_KV: dict[str, tuple[float, ...] | None] = {
    "cigre_lv": (0.4,),
}


def limits_for(network_name: str, framework: str | None = None) -> NetworkLimits:
    """Return the limits for ``network_name`` under ``framework``.

    Args:
        network_name: A registered network key.
        framework: A key of :data:`FRAMEWORKS`; ``None`` uses the
            network's default from :data:`NETWORK_DEFAULT_FRAMEWORK`.

    Raises:
        ValueError: unknown network or framework. Deliberately no
            fallback -- judging a new network by the wrong band
            silently is the bug this module exists to prevent.
    """
    if network_name not in NETWORK_DEFAULT_FRAMEWORK:
        raise ValueError(
            f"No hosting-capacity limits registered for network '{network_name}'. "
            f"Add it to NETWORK_DEFAULT_FRAMEWORK in hosting_capacity/limits.py "
            f"(and to transform/seeds/network_voltage_limits.csv)."
        )
    key = framework or NETWORK_DEFAULT_FRAMEWORK[network_name]
    try:
        fw = FRAMEWORKS[key]
    except KeyError as exc:
        raise ValueError(
            f"Unknown voltage framework '{key}'. Known: {sorted(FRAMEWORKS)}."
        ) from exc
    return NetworkLimits(
        v_min_pu=fw.v_min_pu,
        v_max_pu=fw.v_max_pu,
        framework=fw.name,
        kind=fw.kind,
        voltage_levels_kv=NETWORK_VOLTAGE_LEVELS_KV.get(network_name),
    )


# Backwards-compatible view: network -> default limits.
NETWORK_LIMITS: dict[str, NetworkLimits] = {
    name: limits_for(name) for name in NETWORK_DEFAULT_FRAMEWORK
}
