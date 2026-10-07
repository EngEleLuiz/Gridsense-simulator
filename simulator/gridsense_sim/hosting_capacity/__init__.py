"""Hosting capacity of distributed PV by three methodologies.

* deterministic -- coordinated penetration, single snapshot at the
  critical operating point;
* stochastic   -- distribution of the critical penetration over random
  uncoordinated adoption scenarios;
* QSTS         -- coordinated penetration over a time series
  (zero-tolerance criterion; duration criterion is S1 work).

All three share ``limits`` (named regulatory frameworks), ``scope``
(which buses are judged), ``violations`` (one physics/judgement
implementation), ``baseline`` (PV = 0 gate) and ``search`` (one
monotone bisection), which is what makes the comparison controlled.

Units: ``lambda_`` is total PV / total nominal load in every method.

ASSUMPTION: monotonicity of violation in lambda. It holds for P-only
PV injection without Volt-VAr control; the baseline gate removes the
load-driven case where it fails. Re-verify if Volt-VAr is added.

No function in this package mutates the ``net`` passed by the caller.
"""

from .allocation import PVAllocation, apply_allocation, proportional_to_load
from .conditions import DEFAULT_CRITICAL_LOAD_SCALE, StudyConditions
from .deterministic import DeterministicHCResult
from .deterministic import find_hosting_capacity as find_hosting_capacity_deterministic
from .errors import BaselineInfeasibleError, HostingCapacityError, NoDaylightError
from .limits import (
    FRAMEWORKS,
    NETWORK_LIMITS,
    CriterionKind,
    NetworkLimits,
    VoltageFramework,
    limits_for,
)
from .qsts import QstsHCResult
from .qsts import find_hosting_capacity as find_hosting_capacity_qsts
from .quantiles import QuantileEstimate, min_samples_for_ci, quantile_with_ci
from .scope import voltage_scope_buses
from .search import SEARCH_DEFAULTS, BisectionOutcome, SearchDefaults, bisect_max_feasible, bisect_within
from .stochastic import (
    AdoptionModel,
    ScenarioResult,
    StochasticHCEstimate,
    StochasticHCResult,
    estimate_hosting_capacity as estimate_hosting_capacity_stochastic,
    run_monte_carlo,
)
from .timeseries import TimeSeries, build_synthetic_series
from .violations import ViolationReport, assess_violations, check_violations, run_power_flow

__all__ = [
    "AdoptionModel", "BaselineInfeasibleError", "BisectionOutcome", "CriterionKind",
    "DEFAULT_CRITICAL_LOAD_SCALE", "DeterministicHCResult", "FRAMEWORKS",
    "HostingCapacityError", "NETWORK_LIMITS", "NetworkLimits", "NoDaylightError",
    "PVAllocation", "QstsHCResult", "QuantileEstimate", "SEARCH_DEFAULTS", "ScenarioResult", "SearchDefaults",
    "StochasticHCEstimate", "StochasticHCResult", "StudyConditions", "TimeSeries",
    "ViolationReport", "VoltageFramework", "apply_allocation", "assess_violations",
    "bisect_max_feasible", "bisect_within", "build_synthetic_series", "check_violations",
    "estimate_hosting_capacity_stochastic", "find_hosting_capacity_deterministic",
    "find_hosting_capacity_qsts", "limits_for", "min_samples_for_ci", "proportional_to_load",
    "quantile_with_ci", "run_monte_carlo", "run_power_flow", "voltage_scope_buses",
]
