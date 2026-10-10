"""Single registry of the networks the studies can run on (Phase 7.3).

Before this module the network factories were duplicated in the study
runner and in the dbt voltage-scope seed generator. Every network now
lives here once, with its provenance, so a result can say *what* grid it
came from.

Panel (low voltage, 0.4 kV):

==========================  ========  =========================================
key                         country   what
==========================  ========  =========================================
``cigre_lv``                Europe    CIGRE LV benchmark (TF C6.04.02)
``kerber_landnetz``         Germany   Kerber reference rural overhead network
``kerber_vorstadt``         Germany   Kerber reference suburban cable network
``simbench_lv_rural1``      Germany   SimBench LV rural 1 (from real grid data)
``simbench_lv_semiurb4``    Germany   SimBench LV semi-urban 4
``simbench_lv_urban6``      Germany   SimBench LV urban 6
``lv_schutterwald``         Germany   Real LV grid of Schutterwald, 14 feeders
==========================  ========  =========================================

SimBench grids ship with PV already installed. ``existing_pv`` decides
what a hosting-capacity study means for them:

* ``"remove"`` (default) -- capacity for PV on the network without any;
  comparable across networks;
* ``"keep"`` -- capacity for *additional* PV on top of what is installed.

The transmission cases (``case14``..``case118``) remain for the
telemetry simulator and regression tests; they are not distribution
grids and are not part of the hosting-capacity panel.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

import pandapower as pp
import pandapower.networks as pn

EXISTING_PV_POLICIES = ("remove", "keep")


@dataclass(frozen=True)
class NetworkInfo:
    key: str
    factory: Callable[[], pp.pandapowerNet]
    country: str
    kind: str                 # "benchmark", "reference", "real", "transmission_test"
    description: str
    citation: str
    requires: str | None = None   # optional package
    hc_panel: bool = True


def _simbench(code: str) -> Callable[[], pp.pandapowerNet]:
    def make() -> pp.pandapowerNet:
        try:
            import simbench as sb
        except ImportError as exc:  # pragma: no cover - exercised without the extra
            raise ImportError('SimBench networks need the optional package: pip install -e "simulator[data]"') from exc
        return sb.get_simbench_net(code)
    return make


_SIMBENCH_CITE = ("Meinecke, S. et al. (2020). SimBench - A Benchmark Dataset of Electric Power "
                  "Systems to Compare Innovative Solutions Based on Power Flow Analysis. Energies "
                  "13(12), 3290. doi:10.3390/en13123290")
_KERBER_CITE = ("Kerber, G. (2011). Aufnahmefaehigkeit von Niederspannungsverteilnetzen fuer die "
                "Einspeisung aus Photovoltaikkleinanlagen. Dissertation, TU Muenchen; as implemented "
                "in pandapower")

NETWORKS: dict[str, NetworkInfo] = {
    "case14": NetworkInfo("case14", pn.case14, "US", "transmission_test", "IEEE 14-bus", "IEEE", hc_panel=False),
    "case39": NetworkInfo("case39", pn.case39, "US", "transmission_test", "IEEE 39-bus", "IEEE", hc_panel=False),
    "case57": NetworkInfo("case57", pn.case57, "US", "transmission_test", "IEEE 57-bus", "IEEE", hc_panel=False),
    "case118": NetworkInfo("case118", pn.case118, "US", "transmission_test", "IEEE 118-bus", "IEEE",
                           hc_panel=False),
    "cigre_lv": NetworkInfo(
        "cigre_lv", pn.create_cigre_network_lv, "Europe", "benchmark",
        "CIGRE LV benchmark: residential, industrial and commercial feeders, 44 buses",
        "CIGRE TF C6.04.02 (2014). Benchmark Systems for Network Integration of Renewable and "
        "Distributed Energy Resources"),
    "kerber_landnetz": NetworkInfo(
        "kerber_landnetz", pn.create_kerber_landnetz_freileitung_1, "Germany", "reference",
        "Kerber rural overhead-line reference network, 13 loads", _KERBER_CITE),
    "kerber_vorstadt": NetworkInfo(
        "kerber_vorstadt", pn.create_kerber_vorstadtnetz_kabel_1, "Germany", "reference",
        "Kerber suburban cable reference network, 146 loads", _KERBER_CITE),
    "simbench_lv_rural1": NetworkInfo(
        "simbench_lv_rural1", _simbench("1-LV-rural1--0-sw"), "Germany", "benchmark",
        "SimBench LV rural 1 (derived from real grid data), 13 loads", _SIMBENCH_CITE, "simbench"),
    "simbench_lv_semiurb4": NetworkInfo(
        "simbench_lv_semiurb4", _simbench("1-LV-semiurb4--0-sw"), "Germany", "benchmark",
        "SimBench LV semi-urban 4, 41 loads", _SIMBENCH_CITE, "simbench"),
    "simbench_lv_urban6": NetworkInfo(
        "simbench_lv_urban6", _simbench("1-LV-urban6--0-sw"), "Germany", "benchmark",
        "SimBench LV urban 6, 111 loads", _SIMBENCH_CITE, "simbench"),
    "lv_schutterwald": NetworkInfo(
        "lv_schutterwald", pn.lv_schutterwald, "Germany", "real",
        "Real LV grid of Schutterwald (Baden-Wuerttemberg): 14 MV/LV substations, 1,506 loads",
        "pandapower network 'lv_schutterwald' (Thurner et al. 2018, pandapower: IEEE TPWRS 33(6)); "
        "original grid data from the municipality's utility [VERIFY source]"),
}


def hc_panel() -> list[str]:
    """Networks of the hosting-capacity panel (distribution grids)."""
    return [k for k, v in NETWORKS.items() if v.hc_panel]


def make_network(key: str, existing_pv: str = "remove") -> pp.pandapowerNet:
    """Build a registered network, applying the existing-PV policy."""
    try:
        info = NETWORKS[key]
    except KeyError:
        raise KeyError(f"Unknown network {key!r}; registered: {sorted(NETWORKS)}") from None
    if existing_pv not in EXISTING_PV_POLICIES:
        raise ValueError(f"existing_pv must be one of {EXISTING_PV_POLICIES}.")
    net = info.factory()
    if existing_pv == "remove" and len(net.sgen):
        net.sgen.drop(net.sgen.index, inplace=True)
    return net


__all__ = ["EXISTING_PV_POLICIES", "NETWORKS", "NetworkInfo", "hc_panel", "make_network"]
