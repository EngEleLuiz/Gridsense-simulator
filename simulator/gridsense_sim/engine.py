"""Core simulation engine for the GridSense Simulator.

Loads an IEEE test network, steps through simulated time applying
load and renewable generation profiles plus scheduled contingencies,
runs an AC power flow at each step, and emits telemetry records via
a pluggable `TelemetryPublisher`.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import pandapower as pp
import pandapower.networks as pn

from .profiles import LoadProfile, ProfileConfig, SolarProfile, WindProfile
from .publisher import TelemetryPublisher
from .scenarios import Contingency

logger = logging.getLogger("gridsense_sim.engine")

SUPPORTED_NETWORKS = {
    "case14": pn.case14,
    "case39": pn.case39,
    "case57": pn.case57,
    "case118": pn.case118,
}


@dataclass
class SimulationConfig:
    """Configuration for a simulation run."""

    network_name: str = "case14"
    step_seconds: int = 5
    steps_per_day: int = 288  # used only for profile phase calculation
    total_steps: int = 100
    load_noise_std: float = 0.03
    seed: int | None = 42
    contingencies: list[Contingency] = field(default_factory=list)


class GridSimulationEngine:
    """Runs a time-stepped power-flow simulation and emits telemetry."""

    def __init__(self, config: SimulationConfig, publisher: TelemetryPublisher):
        if config.network_name not in SUPPORTED_NETWORKS:
            raise ValueError(
                f"Unsupported network '{config.network_name}'. "
                f"Choose one of: {list(SUPPORTED_NETWORKS)}"
            )
        self.config = config
        self.publisher = publisher
        self.net = SUPPORTED_NETWORKS[config.network_name]()

        profile_cfg = ProfileConfig(noise_std=config.load_noise_std, seed=config.seed)
        self.load_profile = LoadProfile(profile_cfg)
        self.solar_profile = SolarProfile(profile_cfg)
        self.wind_profile = WindProfile(profile_cfg)

        self._base_loads = self.net.load["p_mw"].copy()

    def _apply_load_profile(self, step: int) -> None:
        multiplier = self.load_profile.value_at(step, self.config.steps_per_day)
        self.net.load["p_mw"] = self._base_loads * multiplier

    def _apply_renewable_profile(self, step: int) -> None:
        """Scale any static generators (sgen) as renewable output.

        IEEE test cases don't ship with renewable sgens by default, so
        this is a no-op unless sgens have been added to the network
        (e.g. via a scenario setup script). It's left in place so the
        pipeline is ready for Phase 2 renewable-profile injection.
        """
        if self.net.sgen.empty:
            return
        solar_mult = self.solar_profile.value_at(step, self.config.steps_per_day)
        wind_mult = self.wind_profile.value_at(step, self.config.steps_per_day)
        for idx in self.net.sgen.index:
            gen_type = self.net.sgen.at[idx, "name"] if "name" in self.net.sgen.columns else ""
            mult = solar_mult if "solar" in str(gen_type).lower() else wind_mult
            max_p = self.net.sgen.at[idx, "sn_mva"] if "sn_mva" in self.net.sgen.columns else 1.0
            self.net.sgen.at[idx, "p_mw"] = max_p * mult

    def _apply_contingencies(self, step: int) -> list[str]:
        active_descriptions = []
        for contingency in self.config.contingencies:
            active = contingency.is_active(step)
            contingency.apply(self.net, active=not active)
            if active:
                active_descriptions.append(
                    contingency.description
                    or f"{contingency.element_type.value}[{contingency.element_index}] out of service"
                )
        return active_descriptions

    def _build_telemetry(self, step: int, active_contingencies: list[str]) -> dict:
        timestamp = datetime.now(timezone.utc) - timedelta(
            seconds=(self.config.total_steps - step) * self.config.step_seconds
        )
        return {
            "timestamp": timestamp.isoformat(),
            "step": step,
            "network": self.config.network_name,
            "bus_voltage_pu": self.net.res_bus["vm_pu"].round(4).to_dict(),
            "line_loading_percent": self.net.res_line["loading_percent"].round(2).to_dict(),
            "gen_power_mw": self.net.res_gen["p_mw"].round(3).to_dict() if not self.net.res_gen.empty else {},
            "total_load_mw": round(float(self.net.res_load["p_mw"].sum()), 3) if not self.net.res_load.empty else 0.0,
            "total_generation_mw": round(float(self.net.res_gen["p_mw"].sum()), 3) if not self.net.res_gen.empty else 0.0,
            "active_contingencies": active_contingencies,
        }

    def run(self) -> None:
        """Run the full simulation, publishing telemetry at every step."""
        logger.info(
            "Starting simulation: network=%s steps=%d step_seconds=%ds",
            self.config.network_name,
            self.config.total_steps,
            self.config.step_seconds,
        )
        for step in range(self.config.total_steps):
            self._apply_load_profile(step)
            self._apply_renewable_profile(step)
            active_contingencies = self._apply_contingencies(step)

            try:
                pp.runpp(self.net)
                converged = True
            except pp.LoadflowNotConverged:
                converged = False
                logger.warning("Power flow did not converge at step %d", step)

            if converged:
                record = self._build_telemetry(step, active_contingencies)
                self.publisher.publish("grid.telemetry.raw", record)

                if active_contingencies:
                    self.publisher.publish(
                        "grid.events.alerts",
                        {
                            "timestamp": record["timestamp"],
                            "step": step,
                            "events": active_contingencies,
                        },
                    )
            else:
                self.publisher.publish(
                    "grid.events.alerts",
                    {
                        "step": step,
                        "severity": "critical",
                        "message": "Power flow did not converge",
                    },
                )

        self.publisher.close()
        logger.info("Simulation finished.")
