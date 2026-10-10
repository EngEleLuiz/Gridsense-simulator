"""Duration-based voltage criteria for QSTS hosting capacity (review finding C7).

A zero-tolerance QSTS only reports the worst instant of a series, which
is what a snapshot study already does. The regulations that define
voltage quality are *statistical over a week of 10-minute readings*:

* **ANEEL PRODIST, Module 8** -- each measurement uses 1008 valid
  10-minute readings (one week). A reading is *adequate*, *precarious*
  or *critical* according to bands around the reference voltage. The
  relative durations DRP (precarious) and DRC (critical) must stay at or
  below 3 % and 0.5 % (DRP_M, DRC_M).
* **EN 50160** -- in each week 95 % of the 10-minute means must lie in
  Un +-10 %, and *all* of them in Un +10 % / -15 %. The 5 % allowance
  therefore only relaxes the *lower* side: for overvoltage (the PV side)
  no 10-minute mean may exceed +10 %.

Attribution to PV
-----------------
Each constraint is split by the side that new PV can make worse:

* upper voltage and *PV-attributable* overload (loading above both the
  rating and the no-PV loading of that element, i.e. reverse-flow driven)
  grow with PV and determine the hosting capacity;
* lower voltage and forward overload are load-driven: PV can only
  improve them, so they are reported as the *baseline compliance* of the
  network and never block the search. Readings that already violate the
  upper side without PV are counted as baseline, and PV may not make the
  count worse than ``max(regulatory allowance, baseline count)`` (the
  base-relative rule of Jain et al., NREL 2019).

Both PV-side event families are monotone in the PV penetration
``lambda``: a step that violates at ``lambda`` violates at any larger
value. Each step therefore has thresholds ``lambda^P_t <= lambda^C_t``
(leaving the adequate band; becoming critical) and ``lambda^T_t``
(PV-attributable overload). The weekly hosting capacity is the
*first exceedance*::

    HC_w = sup { lambda : for every lambda' <= lambda,
                 #precarious(lambda') <= A_P, #critical(lambda') <= A_C,
                 #overload(lambda')   <= A_T }

which :func:`weekly_capacity` computes exactly from the thresholds by a
single sweep (counts only change at thresholds).

Band edges here are per unit of nominal voltage. The PRODIST values come
from the 220 V table of Module 8 (adequate 202-231 V, precarious
191-202 / 231-233 V, critical outside); they are marked ``to_verify``
until checked against the revision in force.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from typing import Literal

EventKind = Literal["precarious", "critical", "thermal"]


@dataclass(frozen=True)
class DurationCriterion:
    """A voltage-quality criterion over a window of fixed-length readings.

    Attributes:
        adequate_lo_pu, adequate_hi_pu: Adequate band.
        critical_lo_pu, critical_hi_pu: Beyond these a reading is critical;
            between them and the adequate band it is precarious. Equal
            edges mean there is no precarious band on that side.
        upper_precarious_max, upper_critical_max: Allowed fraction of the
            window's readings in the upper precarious / critical band.
        lower_precarious_max, lower_critical_max: Same for the lower side
            (baseline compliance only; PV never makes it worse).
        thermal_max: Allowed fraction of readings with a PV-attributable
            overload (0 = instantaneous thermal limit).
        reading_minutes: Length of one regulatory reading.
        window_days: Length of the compliance window.
    """

    name: str
    adequate_lo_pu: float
    adequate_hi_pu: float
    critical_lo_pu: float
    critical_hi_pu: float
    upper_precarious_max: float
    upper_critical_max: float
    lower_precarious_max: float
    lower_critical_max: float
    thermal_max: float = 0.0
    reading_minutes: int = 10
    window_days: int = 7
    reference: str = ""
    to_verify: bool = False

    def __post_init__(self) -> None:
        if not (self.critical_lo_pu <= self.adequate_lo_pu < self.adequate_hi_pu <= self.critical_hi_pu):
            raise ValueError(f"{self.name}: bands must satisfy crit_lo <= adeq_lo < adeq_hi <= crit_hi.")
        for f in (self.upper_precarious_max, self.upper_critical_max, self.lower_precarious_max,
                  self.lower_critical_max, self.thermal_max):
            if not 0.0 <= f < 1.0:
                raise ValueError(f"{self.name}: allowed fractions must be in [0, 1).")
        if self.reading_minutes <= 0 or self.window_days <= 0:
            raise ValueError(f"{self.name}: reading_minutes and window_days must be positive.")

    @property
    def has_upper_precarious_band(self) -> bool:
        return self.critical_hi_pu > self.adequate_hi_pu

    @staticmethod
    def allowed(fraction: float, n_readings: int) -> int:
        """Largest count that keeps ``count / n_readings <= fraction``."""
        return math.floor(fraction * n_readings + 1e-9)

    def readings_per_window(self) -> int:
        return self.window_days * 24 * 60 // self.reading_minutes

    def to_dict(self) -> dict:
        return asdict(self)


DURATION_CRITERIA: dict[str, DurationCriterion] = {
    "prodist_m8_drp_drc": DurationCriterion(
        name="prodist_m8_drp_drc",
        adequate_lo_pu=202 / 220, adequate_hi_pu=231 / 220,
        critical_lo_pu=191 / 220, critical_hi_pu=233 / 220,
        upper_precarious_max=0.03, upper_critical_max=0.005,
        lower_precarious_max=0.03, lower_critical_max=0.005,
        reference="ANEEL PRODIST Module 8: DRP <= 3 %, DRC <= 0.5 % over 1008 10-min readings; "
                  "bands from the 220 V table (202-231 V adequate; 191-202 / 231-233 V precarious)",
        to_verify=True,
    ),
    "en50160_weekly": DurationCriterion(
        name="en50160_weekly",
        adequate_lo_pu=0.90, adequate_hi_pu=1.10,
        critical_lo_pu=0.85, critical_hi_pu=1.10,
        upper_precarious_max=0.0, upper_critical_max=0.0,
        lower_precarious_max=0.05, lower_critical_max=0.0,
        reference="EN 50160: 95 % of weekly 10-min means within Un +-10 %, all within +10 %/-15 % "
                  "(upper side: no 10-min mean above +10 %)",
        to_verify=True,
    ),
    "prodist_m8_zero_tolerance": DurationCriterion(
        name="prodist_m8_zero_tolerance",
        adequate_lo_pu=202 / 220, adequate_hi_pu=231 / 220,
        critical_lo_pu=202 / 220, critical_hi_pu=231 / 220,
        upper_precarious_max=0.0, upper_critical_max=0.0,
        lower_precarious_max=0.0, lower_critical_max=0.0,
        reference="PRODIST adequate band with no duration allowance (tau_bar = 0): the "
                  "zero-tolerance reference for measuring what the duration criterion changes",
        to_verify=True,
    ),
}


def get_criterion(name: str | DurationCriterion) -> DurationCriterion:
    if isinstance(name, DurationCriterion):
        return name
    try:
        return DURATION_CRITERIA[name]
    except KeyError:
        raise KeyError(f"Unknown duration criterion {name!r}; known: {sorted(DURATION_CRITERIA)}") from None


@dataclass
class WeekThresholds:
    """PV-side thresholds collected for one compliance window.

    Lists hold the thresholds of steps whose event occurs below ``cap``
    (a threshold of 0 means the reading already violates without PV);
    every other step has its thresholds at or above ``cap``.
    """

    n_readings: int
    cap: float
    precarious: list[float] = field(default_factory=list)  # leaving the adequate band
    critical: list[float] = field(default_factory=list)
    thermal: list[float] = field(default_factory=list)


@dataclass(frozen=True)
class WeeklyOutcome:
    lambda_max: float
    bounded: bool
    binding: EventKind | None
    counts_at_exceedance: dict[str, int]
    allowances: dict[str, int]
    baseline_counts: dict[str, int]


def weekly_capacity(th: WeekThresholds, criterion: DurationCriterion) -> WeeklyOutcome:
    """Exact first-exceedance capacity of one window from its thresholds.

    ``bounded=False`` means no exceedance below ``th.cap`` (capacity >= cap).
    """
    n = th.n_readings
    base = {
        "precarious": sum(1 for x in th.precarious if x <= 0.0),
        "critical": sum(1 for x in th.critical if x <= 0.0),
        "thermal": sum(1 for x in th.thermal if x <= 0.0),
    }
    # Baseline readings that are already critical are not precarious.
    base_prec_only = max(0, base["precarious"] - base["critical"])
    allow = {
        "precarious": max(criterion.allowed(criterion.upper_precarious_max, n), base_prec_only),
        "critical": max(criterion.allowed(criterion.upper_critical_max, n), base["critical"]),
        "thermal": max(criterion.allowed(criterion.thermal_max, n), base["thermal"]),
    }
    events: list[tuple[float, str]] = (
        [(x, "precarious") for x in th.precarious]
        + [(x, "critical") for x in th.critical]
        + [(x, "thermal") for x in th.thermal]
    )
    events.sort(key=lambda e: e[0])
    n_any = n_crit = n_therm = 0
    i = 0
    while i < len(events):
        theta = events[i][0]
        while i < len(events) and events[i][0] == theta:
            kind = events[i][1]
            if kind == "precarious":
                n_any += 1
            elif kind == "critical":
                n_crit += 1
            else:
                n_therm += 1
            i += 1
        n_prec = max(0, n_any - n_crit)
        violated: EventKind | None = None
        if n_crit > allow["critical"]:
            violated = "critical"
        elif n_therm > allow["thermal"]:
            violated = "thermal"
        elif n_prec > allow["precarious"]:
            violated = "precarious"
        if violated is not None:
            return WeeklyOutcome(
                lambda_max=max(0.0, theta), bounded=True, binding=violated,
                counts_at_exceedance={"precarious": n_prec, "critical": n_crit, "thermal": n_therm},
                allowances=allow, baseline_counts=base,
            )
    return WeeklyOutcome(
        lambda_max=th.cap, bounded=False, binding=None,
        counts_at_exceedance={"precarious": max(0, n_any - n_crit), "critical": n_crit,
                              "thermal": n_therm},
        allowances=allow, baseline_counts=base,
    )


__all__ = [
    "DURATION_CRITERIA", "DurationCriterion", "EventKind", "WeekThresholds", "WeeklyOutcome",
    "get_criterion", "weekly_capacity",
]
