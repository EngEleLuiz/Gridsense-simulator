"""Contingency scenarios (N-1 / N-2 events) for the grid simulator.

A scenario temporarily takes an element (line, generator, transformer)
out of service to simulate a real-world contingency, then restores it.
This is deliberately simple for the local test build; more elaborate
scenario scheduling can be layered on later.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

import pandapower as pp


class ElementType(str, Enum):
    LINE = "line"
    GENERATOR = "gen"
    STATIC_GENERATOR = "sgen"
    TRANSFORMER = "trafo"


@dataclass
class Contingency:
    """A single contingency event.

    Attributes:
        element_type: The pandapower table affected (line, gen, etc.).
        element_index: Row index within that table.
        start_step: Simulation step at which the element goes out of service.
        duration_steps: How many steps the outage lasts.
        description: Human-readable description, useful for logs/alerts.
    """

    element_type: ElementType
    element_index: int
    start_step: int
    duration_steps: int
    description: str = ""

    def is_active(self, step: int) -> bool:
        return self.start_step <= step < self.start_step + self.duration_steps

    def apply(self, net: pp.pandapowerNet, active: bool) -> None:
        """Set the element's in_service flag according to `active`."""
        table = getattr(net, self.element_type.value)
        table.at[self.element_index, "in_service"] = active
