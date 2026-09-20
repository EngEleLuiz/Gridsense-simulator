"""Per-network physical limits used as the hosting-capacity violation
criterion.

IMPORTANT: this is deliberately NOT a single global constant. The
balanced transmission test cases (case14/39/57/118) use ANSI C84.1
Range A (0.95-1.05 pu), which is also what the existing dbt model
`transform/models/silver/fct_bus_voltage.sql` hardcodes today.

cigre_lv is a real low-voltage distribution feeder: at nominal load,
with zero PV, several of its buses already sit at 0.93-0.94 pu (see
the CLI run recorded when Phase 5 was validated). Using ANSI Range A
for this network would flag "violations" that exist at baseline, with
no PV added at all -- which makes a hosting-capacity search on top of
it meaningless (every bus would show ~zero or negative capacity).
CIGRE's own topology is designed against a wider tolerance; we use
+-10% (0.90-1.10 pu) for it instead, per the Sept/2026 decision
recorded when this module was created.

TODO (tracked, not fixed here): `fct_bus_voltage.sql` still hardcodes
0.95-1.05 for every network including cigre_lv. Once cigre_lv
telemetry starts flowing through the pipeline, that dashboard-level
flag will disagree with the hosting-capacity criterion used here. The
fix is to make that dbt model network-aware (e.g. a
`transform/seeds/network_voltage_limits.csv` seed joined by
`network`), using the same limits as this module, so the Silver layer
and the hosting-capacity code never diverge. Left as a separate,
small follow-up so it can be reviewed on its own.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class NetworkLimits:
    """Physical limits that define a hosting-capacity violation.

    Hosting capacity is bounded by whichever limit is hit first --
    voltage or thermal (line/transformer loading) -- not voltage
    alone.
    """

    v_min_pu: float
    v_max_pu: float
    max_line_loading_percent: float = 100.0
    max_trafo_loading_percent: float = 100.0


# ANSI C84.1 Range A -- standard normal-operation tolerance, used for
# the balanced transmission test cases. Matches the threshold already
# hardcoded in fct_bus_voltage.sql for these networks.
_ANSI_RANGE_A = NetworkLimits(v_min_pu=0.95, v_max_pu=1.05)

# CIGRE LV distribution feeder -- wider tolerance reflecting the
# network's own designed voltage drop along radial LV laterals.
_CIGRE_LV = NetworkLimits(v_min_pu=0.90, v_max_pu=1.10)

NETWORK_LIMITS: dict[str, NetworkLimits] = {
    "case14": _ANSI_RANGE_A,
    "case39": _ANSI_RANGE_A,
    "case57": _ANSI_RANGE_A,
    "case118": _ANSI_RANGE_A,
    "cigre_lv": _CIGRE_LV,
}


def limits_for(network_name: str) -> NetworkLimits:
    """Return the violation limits for a given network.

    Raises:
        ValueError: if the network has no registered limits. This is
            intentional -- silently falling back to ANSI Range A for
            an unrecognized network would risk repeating exactly the
            bug this module exists to avoid.
    """
    try:
        return NETWORK_LIMITS[network_name]
    except KeyError as exc:
        raise ValueError(
            f"No hosting-capacity voltage limits registered for network "
            f"'{network_name}'. Add an entry to NETWORK_LIMITS in "
            f"hosting_capacity/limits.py before using it in a hosting-"
            f"capacity study."
        ) from exc
