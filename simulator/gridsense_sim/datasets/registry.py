"""Registry of real datasets: one place that maps a key to its loader.

Every loader has the signature ``load(cache=None, source_file=None, **options)``
and returns a :class:`~.base.ProfileSet`. ``options`` are dataset-specific
(see each module) and are passed through from the CLI as ``key=value``.
"""

from __future__ import annotations

from typing import Any, Callable

from . import ausgrid, inmet, lcl, nasa_power, pvgis, simbench_profiles
from .base import DatasetInfo, ProfileSet
from .cache import DataCache

_MODULES = {
    "inmet": inmet,
    "nasa_power": nasa_power,
    "pvgis": pvgis,
    "ausgrid": ausgrid,
    "lcl": lcl,
    "simbench": simbench_profiles,
}

DATASETS: dict[str, DatasetInfo] = {k: m.INFO for k, m in _MODULES.items()}


def get_loader(key: str) -> Callable[..., ProfileSet]:
    try:
        return _MODULES[key].load
    except KeyError:
        raise KeyError(f"Unknown dataset {key!r}; available: {sorted(_MODULES)}") from None


def load_dataset(
    key: str,
    cache: DataCache | None = None,
    source_file: str | None = None,
    **options: Any,
) -> ProfileSet:
    """Load (fetching and caching if needed) a dataset by key."""
    return get_loader(key)(cache=cache, source_file=source_file, **options)


def parse_options(pairs: list[str] | None) -> dict[str, Any]:
    """``["year=2023", "station=A806", "kind=pv"]`` -> typed dict (int/float/bool/None/str)."""
    out: dict[str, Any] = {}
    for pair in pairs or []:
        if "=" not in pair:
            raise ValueError(f"Option {pair!r} is not key=value.")
        key, raw = pair.split("=", 1)
        key = key.strip().replace("-", "_")
        raw = raw.strip()
        value: Any
        if raw.lower() in ("none", "null"):
            value = None
        elif raw.lower() in ("true", "false"):
            value = raw.lower() == "true"
        else:
            try:
                value = int(raw)
            except ValueError:
                try:
                    value = float(raw)
                except ValueError:
                    value = raw
        out[key] = value
    return out


__all__ = ["DATASETS", "get_loader", "load_dataset", "parse_options"]
