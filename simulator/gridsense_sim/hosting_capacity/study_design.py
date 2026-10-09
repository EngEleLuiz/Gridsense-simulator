"""One dataset, three aggregations: deterministic, stochastic and QSTS from the same series.

The controlled comparison at the core of the dissertation requires every
method to read the *same* data. Here the snapshot methods take their
operating point from the QSTS series instead of a literature convention
(the fixed ``load_scale = 0.25``):

* **critical point** -- among the steps whose PV output is at least
  ``pv_fraction`` of the series' maximum, the one with the lowest load
  (maximum PV with minimum load, Eq. 3.15 of the draft; the same
  construction as Lima & Freitas, 2021, who take the critical scenario
  from the measured series);
* **deterministic** -- bisection at that point with ``load_scale = load``
  and ``pv_scale = pv``, so ``lambda`` is installed capacity;
* **stochastic** -- the same point, random adoption on top.

Because the zero-tolerance QSTS takes the minimum of the per-step
capacities over the whole series (the critical step included), with the
same allocation and voltage framework::

    HC_qsts(tau=0) <= HC_deterministic(critical point)

and a duration criterion can only raise the QSTS value. The gaps measure
two different things: the cost of choosing the critical point by proxy,
and the value of the duration allowance.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np

from .timeseries import TimeSeries

RULES = ("min_load_high_pv", "max_pv_to_load")


@dataclass(frozen=True)
class CriticalPoint:
    step: int              # index within the series
    absolute_step: int     # series.start_step + step
    load_mult: float
    pv_mult: float
    rule: str
    pv_fraction: float

    def to_dict(self) -> dict:
        return asdict(self)


def critical_point(ts: TimeSeries, rule: str = "min_load_high_pv", pv_fraction: float = 0.9) -> CriticalPoint:
    """Pick the snapshot operating point from a series.

    ``min_load_high_pv``: lowest load among steps with ``pv >= pv_fraction * max(pv)``
    (ties broken by the larger PV). ``max_pv_to_load``: largest PV/load ratio.
    """
    if rule not in RULES:
        raise ValueError(f"rule must be one of {RULES}.")
    if not 0.0 < pv_fraction <= 1.0:
        raise ValueError("pv_fraction must be in (0, 1].")
    pv, load = np.asarray(ts.pv_mult), np.asarray(ts.load_mult)
    if pv.max() <= 0:
        raise ValueError("The series has no PV output.")
    if rule == "min_load_high_pv":
        cand = np.flatnonzero(pv >= pv_fraction * pv.max())
        order = np.lexsort((-pv[cand], load[cand]))  # load ascending, then pv descending
        t = int(cand[order[0]])
    else:
        day = np.flatnonzero(pv > 0)
        t = int(day[np.argmax(pv[day] / load[day])])
    return CriticalPoint(t, ts.start_step + t, float(load[t]), float(pv[t]), rule, pv_fraction)


__all__ = ["RULES", "CriticalPoint", "critical_point"]
