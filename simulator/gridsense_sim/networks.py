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
``ieee_european_lv_offpeak`` UK       IEEE European LV test feeder, off-peak
``ieee_european_lv_onpeak``  UK       IEEE European LV test feeder, on-peak
==========================  ========  =========================================

The IEEE European LV feeder has 55 *single-phase* customers
(``asymmetric_load``): it runs through the three-phase path
(:mod:`.hosting_capacity.unbalanced`, Phase 7.4).

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

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import pandapower as pp
import pandapower.networks as pn

EXISTING_PV_POLICIES = ("remove", "keep")
KERBER_VORSTADT_SEED = 20261110


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
    source: str = "builtin"       # "builtin" or "file:<sha256 of the network file>"


def _kerber_vorstadt() -> pp.pandapowerNet:
    """Kerber suburban network with a fixed random seed.

    pandapower draws each service cable's type ("NAYY 4x50" or "NYY 4x35") with
    Python's global ``random`` module, so two builds of the same network differ
    (found in Phase 8 while checking the batched solver against pandapower).
    A fixed seed makes every build -- and every process of a protocol run --
    identical; the caller's random state is restored afterwards.
    """
    import random

    state = random.getstate()
    random.seed(KERBER_VORSTADT_SEED)
    try:
        return pn.create_kerber_vorstadtnetz_kabel_1()
    finally:
        random.setstate(state)


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

_IEEE_EU_CITE = ("IEEE PES Distribution System Analysis Subcommittee, European Low Voltage Test "
                 "Feeder (2015); as implemented in pandapower")

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
        "kerber_vorstadt", _kerber_vorstadt, "Germany", "reference",
        "Kerber suburban cable reference network, 146 loads (service-cable types drawn with a fixed seed)",
        _KERBER_CITE),
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
    "ieee_european_lv_offpeak": NetworkInfo(
        "ieee_european_lv_offpeak", lambda: pn.ieee_european_lv_asymmetric("off_peak_1440"), "UK", "benchmark",
        "IEEE European LV test feeder (906 LV buses, 55 single-phase customers), off-peak snapshot",
        _IEEE_EU_CITE),
    "ieee_european_lv_onpeak": NetworkInfo(
        "ieee_european_lv_onpeak", lambda: pn.ieee_european_lv_asymmetric("on_peak_566"), "UK", "benchmark",
        "IEEE European LV test feeder, on-peak snapshot (minute 566)", _IEEE_EU_CITE),
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


def register_network(
    key: str,
    factory: Callable[[], pp.pandapowerNet],
    *,
    default_framework: str,
    voltage_levels_kv: tuple[float, ...] | None,
    country: str = "unknown",
    kind: str = "custom",
    description: str = "",
    citation: str = "",
    source: str = "custom",
    hc_panel: bool = False,
) -> NetworkInfo:
    """Register a network that is not part of the built-in panel (Phase 8.3).

    This is how local networks (for example a feeder converted from the
    Brazilian BDGD) enter a study without being hard-coded in the repository:
    the study code only ever sees a registered key.

    ``default_framework`` and ``voltage_levels_kv`` are mandatory on purpose:
    judging a new network by a guessed voltage band is the error the limits
    module exists to prevent. Built-in keys cannot be overwritten.
    """
    from .hosting_capacity import limits as _limits

    if key in NETWORKS and NETWORKS[key].source == "builtin":
        raise ValueError(f"'{key}' is a built-in network and cannot be replaced.")
    if default_framework not in _limits.FRAMEWORKS:
        raise ValueError(f"Unknown voltage framework '{default_framework}'. Known: {sorted(_limits.FRAMEWORKS)}.")
    info = NetworkInfo(key, factory, country, kind, description, citation, hc_panel=hc_panel, source=source)
    NETWORKS[key] = info
    _limits.NETWORK_DEFAULT_FRAMEWORK[key] = default_framework
    _limits.NETWORK_VOLTAGE_LEVELS_KV[key] = tuple(voltage_levels_kv) if voltage_levels_kv else None
    _limits.NETWORK_LIMITS[key] = _limits.limits_for(key)
    return info


def unregister_network(key: str) -> None:
    """Remove a network added with :func:`register_network` (built-ins are kept)."""
    from .hosting_capacity import limits as _limits

    if key in NETWORKS and NETWORKS[key].source != "builtin":
        del NETWORKS[key]
        for table in (_limits.NETWORK_DEFAULT_FRAMEWORK, _limits.NETWORK_VOLTAGE_LEVELS_KV, _limits.NETWORK_LIMITS):
            table.pop(key, None)


def network_file_metadata(path: str | Path) -> dict:
    """Sidecar metadata of a network file (``<file>.meta.json``), or ``{}``."""
    meta = Path(str(path) + ".meta.json")
    return json.loads(meta.read_text(encoding="utf-8")) if meta.is_file() else {}


def register_network_file(
    key: str,
    path: str | Path,
    *,
    default_framework: str | None = None,
    voltage_levels_kv: tuple[float, ...] | None = None,
) -> NetworkInfo:
    """Register a pandapower JSON file as network ``key``.

    Metadata comes from the sidecar ``<file>.meta.json`` written by
    ``gridsense-bdgd build`` (framework, voltage level, origin); explicit
    arguments win. The file's SHA-256 becomes part of ``NetworkInfo.source``
    so a result can say exactly which file it was computed on.
    """
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f"Network file {path} does not exist.")
    meta = network_file_metadata(path)
    framework = default_framework or meta.get("default_framework")
    levels = voltage_levels_kv or meta.get("voltage_levels_kv")
    if not framework or not levels:
        raise ValueError(
            f"Network file {path} has no sidecar metadata: pass the default voltage framework and "
            "the voltage level(s) of the customer buses explicitly.")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return register_network(
        key, lambda: pp.from_json(str(path)), default_framework=framework,
        voltage_levels_kv=tuple(float(v) for v in levels), country=meta.get("country", "unknown"),
        kind=meta.get("kind", "custom"), description=meta.get("description", f"network file {path.name}"),
        citation=meta.get("citation", ""), source=f"file:{digest}")


__all__ = ["EXISTING_PV_POLICIES", "NETWORKS", "NetworkInfo", "hc_panel", "make_network",
           "network_file_metadata", "register_network", "register_network_file", "unregister_network"]
