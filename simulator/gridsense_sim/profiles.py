"""Synthetic load and renewable generation profiles.

In later phases these will be replaced/augmented with real data
(ONS load curves, NSRDB solar irradiance, WIND Toolkit wind speed).
For local testing, we generate realistic-looking synthetic curves
so the simulator can run without any external dataset or network
access.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass


@dataclass
class ProfileConfig:
    """Configuration for synthetic profile generation."""

    noise_std: float = 0.03
    seed: int | None = None


class LoadProfile:
    """Generates a smooth daily load curve with random noise.

    The curve peaks around midday/evening and dips at night, loosely
    modeled as a sum of sinusoids, scaled to a base per-unit factor.
    """

    def __init__(self, config: ProfileConfig | None = None) -> None:
        self.config = config or ProfileConfig()
        self._rng = random.Random(self.config.seed)

    def value_at(self, step: int, steps_per_day: int = 288) -> float:
        """Return a per-unit load multiplier (roughly 0.6 - 1.15) for a step.

        Args:
            step: Absolute simulation step index.
            steps_per_day: Number of steps representing one full day
                (default 288 == one sample every 5 minutes).
        """
        phase = 2 * math.pi * (step % steps_per_day) / steps_per_day
        base = 0.85 + 0.2 * math.sin(phase - math.pi / 2) + 0.08 * math.sin(2 * phase)
        noise = self._rng.gauss(0, self.config.noise_std)
        return max(0.3, base + noise)


class SolarProfile:
    """Generates a synthetic solar irradiance-driven output curve.

    Output is zero at night and follows a bell curve during daylight
    hours, with random cloud-cover noise.
    """

    def __init__(self, config: ProfileConfig | None = None) -> None:
        self.config = config or ProfileConfig()
        self._rng = random.Random(
            (self.config.seed + 1) if self.config.seed is not None else None
        )

    def value_at(self, step: int, steps_per_day: int = 288) -> float:
        """Return a per-unit output multiplier (0.0 - 1.0) for a step."""
        hour = (step % steps_per_day) / steps_per_day * 24
        if hour < 6 or hour > 18:
            return 0.0
        # Bell curve centered at midday (hour 12), width tuned for a
        # ~12h daylight window.
        base = math.exp(-((hour - 12) ** 2) / (2 * 3.2**2))
        cloud_noise = max(0.0, self._rng.gauss(0, self.config.noise_std * 2))
        return max(0.0, min(1.0, base - cloud_noise))


class WindProfile:
    """Generates a synthetic wind-power output curve.

    Wind is modeled as a slowly varying random walk clipped to
    [0, 1], which is a rough but reasonable approximation for
    short local test runs.
    """

    def __init__(self, config: ProfileConfig | None = None) -> None:
        self.config = config or ProfileConfig()
        self._rng = random.Random(
            (self.config.seed + 2) if self.config.seed is not None else None
        )
        self._current = self._rng.uniform(0.2, 0.6)

    def value_at(self, step: int, steps_per_day: int = 288) -> float:
        """Return a per-unit output multiplier (0.0 - 1.0) for a step."""
        drift = self._rng.gauss(0, 0.03)
        self._current = min(1.0, max(0.0, self._current + drift))
        return self._current
