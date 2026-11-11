"""Table and column names of the BDGD layers the converter reads.

The defaults follow the names used in the public BDGD releases (PRODIST
Module 10, "Manual de Instrucoes da BDGD") as far as the author could
confirm them without a release at hand:

* confirmed against the open-source BDGD-Tools converter: ``COD_ID``,
  ``PAC_1``, ``PAC_2``, ``PAC``, ``CTMT``, ``POT_NOM``, ``PER_FER``,
  ``PER_TOT`` and the layer names ``SSDBT``, ``UNTRMT``, ``SEGCON``,
  ``CRVCRG``;
* **to verify against the data dictionary of your release**: every other
  default below (``TIP_CND``, ``COMP``, ``FAS_CON``, ``UNI_TR_MT``,
  ``ENE_01``..``ENE_12``, ``TIP_CC``, ``POT_INST``, ``R1``, ``X1``, ``CNOM``,
  ``R``, ``XHL``, ``TIP_DIA``, ``POT_01``..``POT_96`` and the layer names
  ``UCBT_tab``, ``UGBT_tab``, ``RAMLIG``, ``EQTRMT``).

Releases differ (older ones call the transformer layer ``UNTRD`` and the
link column ``UNI_TR_D``), so every name can be overridden with a JSON file
(``gridsense-bdgd --schema my_schema.json``). ``gridsense-bdgd inspect``
prints which expected columns are present before anything is converted.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path


def _energy_columns() -> list[str]:
    return [f"ENE_{m:02d}" for m in range(1, 13)]


def _curve_columns() -> list[str]:
    return [f"POT_{q:02d}" for q in range(1, 97)]


@dataclass(frozen=True)
class BdgdSchema:
    # --- layers (file stem or layer name) ---
    lv_segments: str = "SSDBT"
    service_drops: str = "RAMLIG"
    transformers: str = "UNTRMT"
    transformer_equipment: str = "EQTRMT"
    conductors: str = "SEGCON"
    lv_consumers: str = "UCBT_tab"
    lv_generators: str = "UGBT_tab"
    load_curves: str = "CRVCRG"
    # --- shared columns ---
    id: str = "COD_ID"
    node_from: str = "PAC_1"
    node_to: str = "PAC_2"
    node: str = "PAC"
    phases: str = "FAS_CON"
    transformer_link: str = "UNI_TR_MT"
    # --- segments / service drops ---
    conductor_type: str = "TIP_CND"
    length_m: str = "COMP"
    # --- conductor catalogue ---
    r_ohm_per_km: str = "R1"
    x_ohm_per_km: str = "X1"
    rated_current_a: str = "CNOM"
    # --- transformers ---
    rated_kva: str = "POT_NOM"
    iron_losses_w: str = "PER_FER"
    total_losses_w: str = "PER_TOT"
    equipment_link: str = "UNI_TR_MT"       # column of EQTRMT pointing to UNTRMT.COD_ID
    r_percent: str = "R"
    x_percent: str = "XHL"
    # --- consumers / generators ---
    monthly_energy_kwh: list[str] = field(default_factory=_energy_columns)
    load_curve_type: str = "TIP_CC"
    installed_kw: str = "POT_INST"
    # --- typical load curves ---
    curve_day_type: str = "TIP_DIA"
    curve_values: list[str] = field(default_factory=_curve_columns)

    @classmethod
    def from_json(cls, path: str | Path) -> "BdgdSchema":
        spec = json.loads(Path(path).read_text(encoding="utf-8"))
        unknown = sorted(set(spec) - {f.name for f in fields(cls)})
        if unknown:
            raise ValueError(f"Unknown schema key(s) {unknown}.")
        return cls(**spec)

    def to_dict(self) -> dict:
        return asdict(self)

    def required_columns(self) -> dict[str, list[str]]:
        """Columns the converter cannot work without, per layer."""
        return {
            self.lv_segments: [self.node_from, self.node_to, self.conductor_type, self.length_m,
                               self.transformer_link],
            self.transformers: [self.id, self.node_from, self.node_to, self.rated_kva],
            self.conductors: [self.id, self.r_ohm_per_km, self.x_ohm_per_km, self.rated_current_a],
            self.lv_consumers: [self.node, self.transformer_link, *self.monthly_energy_kwh],
        }

    def optional_layers(self) -> list[str]:
        return [self.service_drops, self.transformer_equipment, self.lv_generators, self.load_curves]


__all__ = ["BdgdSchema"]
