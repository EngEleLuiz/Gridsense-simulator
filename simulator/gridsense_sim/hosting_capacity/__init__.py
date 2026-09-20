"""Hosting capacity analysis for distributed PV, using three classic
methodologies: deterministic (bisection), stochastic (Monte Carlo),
and QSTS (quasi-static time series).

All three share the same violation physics (`violations.py`) and the
same per-network limit table (`limits.py`), so results are directly
comparable -- that controlled comparison is the dissertation's core
contribution (see 02/03-Project-Overview.md and
Analise-Comparativa-Trabalhos-Relacionados.md).

ASSUMPTION (not verified -- see NOTE TO AUTHOR in the dissertation
draft, Chapter 3): all three methods assume monotonicity, i.e. that
increasing PV penetration monotonically increases (or at worst does
not decrease) violation severity. This holds for simple P-only PV
injection without local Volt-VAr control. If Volt-VAr control is
added later, this assumption must be re-verified before reusing the
bisection search as-is.
"""

from .allocation import PVAllocation, proportional_to_load
from .deterministic import DeterministicHCResult, find_hosting_capacity as find_hosting_capacity_deterministic
from .limits import NETWORK_LIMITS, NetworkLimits, limits_for
from .qsts import QstsHCResult, find_hosting_capacity as find_hosting_capacity_qsts
from .stochastic import StochasticHCResult, run_monte_carlo
from .violations import ViolationReport, check_violations

__all__ = [
    "PVAllocation",
    "proportional_to_load",
    "DeterministicHCResult",
    "find_hosting_capacity_deterministic",
    "NETWORK_LIMITS",
    "NetworkLimits",
    "limits_for",
    "QstsHCResult",
    "find_hosting_capacity_qsts",
    "StochasticHCResult",
    "run_monte_carlo",
    "ViolationReport",
    "check_violations",
]
