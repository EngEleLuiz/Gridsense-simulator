"""Typed exceptions for the hosting-capacity package.

Every failure mode that would otherwise produce a *silently wrong*
number is raised as one of these instead. Callers that want to record
the failure as a study outcome (e.g. ``scripts/run_hosting_capacity_study.py``,
which writes a ``status`` row rather than crashing) catch
:class:`HostingCapacityError` and serialize ``exc.status``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - import only for type hints
    from .violations import ViolationReport


class HostingCapacityError(RuntimeError):
    """Base class. ``status`` is a stable machine-readable code."""

    status: str = "error"


class BaselineInfeasibleError(HostingCapacityError):
    """The network already violates its limits with **zero** PV.

    Why this must be an error (review finding C3): bisection assumes the
    feasible set of penetration factors is ``[0, lambda*]``. If PV = 0 is
    already infeasible because of *load*-driven undervoltage, adding PV
    can first *fix* the violation and later cause a new one, so the
    feasible set becomes an interval ``[a, b]`` with ``a > 0`` and the
    bisection silently returns a meaningless number. CIGRE LV under
    PRODIST M8 at nominal load (bus 35 at ~0.912 pu) is the concrete case.
    """

    status = "baseline_infeasible"

    def __init__(self, message: str, report: "ViolationReport", step: int | None = None) -> None:
        super().__init__(message)
        self.report = report
        self.step = step


class NoDaylightError(HostingCapacityError):
    """A QSTS time window contains no step with PV output > 0.

    Review finding C5: the old 24-step test window (steps 0-23 at
    288 steps/day = 00:00-01:55) had no sun, so every candidate passed
    and the "hosting capacity" grew until the expansion cap
    (lambda ~ 1e6). Such a window cannot say anything about PV.
    """

    status = "no_daylight"
