"""Precomputed, immutable time series for QSTS (review finding C6).

The old QSTS called ``LoadProfile.value_at`` inside the bisection,
and each call draws fresh Gaussian noise -- so every lambda candidate
was judged on a *different* series, and monotonicity in lambda was not
even well defined. A :class:`TimeSeries` is built **once** per study
and shared by every candidate.

Immutability is enforced on private copies (review finding R10): the
arrays passed in by the caller are copied, never frozen in place, and
equality/hashing are identity-based because element-wise ``==`` on
numpy arrays has no single truth value.

Known limitation (C9, fixed in S2/Phase 8, not here): the synthetic
``LoadProfile`` peaks at midday, coinciding with solar. With that
shape QSTS cannot differ much from the snapshot method.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from ..profiles import LoadProfile, ProfileConfig, SolarProfile


@dataclass(frozen=True, eq=False)
class TimeSeries:
    """Per-step multipliers on nominal load and on PV rating.

    Attributes:
        load_mult: Load multiplier per step (applied to P and Q).
        pv_mult: PV output per unit of rating per step, in [0, 1].
        steps_per_day: Temporal resolution.
        start_step: Absolute index of the first step (for reporting).
    """

    load_mult: np.ndarray
    pv_mult: np.ndarray
    steps_per_day: int
    start_step: int = 0

    def __post_init__(self) -> None:
        lm = np.array(self.load_mult, dtype=float, copy=True)
        pm = np.array(self.pv_mult, dtype=float, copy=True)
        if lm.ndim != 1 or lm.shape != pm.shape or lm.size == 0:
            raise ValueError("load_mult and pv_mult must be equal-length non-empty 1-D arrays.")
        if np.any(lm <= 0):
            raise ValueError("load_mult must be strictly positive.")
        if np.any(pm < 0) or np.any(pm > 1):
            raise ValueError("pv_mult must lie in [0, 1].")
        if self.steps_per_day < 1:
            raise ValueError("steps_per_day must be >= 1.")
        lm.setflags(write=False)
        pm.setflags(write=False)
        object.__setattr__(self, "load_mult", lm)
        object.__setattr__(self, "pv_mult", pm)

    @property
    def n_steps(self) -> int:
        return int(self.load_mult.size)

    @property
    def daylight_steps(self) -> np.ndarray:
        """Relative indices of steps with PV output > 0."""
        return np.flatnonzero(self.pv_mult > 0.0)


def build_synthetic_series(
    total_steps: int,
    steps_per_day: int,
    seed: int | None = 42,
    start_step: int = 0,
) -> TimeSeries:
    """Sample the synthetic profiles once into a :class:`TimeSeries`."""
    if total_steps < 1:
        raise ValueError("total_steps must be >= 1.")
    cfg = ProfileConfig(seed=seed)
    load, solar = LoadProfile(cfg), SolarProfile(cfg)
    steps = range(start_step, start_step + total_steps)
    return TimeSeries(
        load_mult=np.array([load.value_at(s, steps_per_day) for s in steps]),
        pv_mult=np.array([solar.value_at(s, steps_per_day) for s in steps]),
        steps_per_day=steps_per_day,
        start_step=start_step,
    )
