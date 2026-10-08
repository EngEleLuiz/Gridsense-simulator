"""Core types shared by every real-data loader.

Conventions (enforced by :class:`ProfileSet`):

* The index is a **tz-aware UTC** ``DatetimeIndex`` marking the **start**
  of each interval, strictly increasing, without duplicates, on a
  regular grid of ``resolution``. Loaders convert whatever the source
  uses (interval end, local wall clock, DST) into this before returning;
  the conversions are measured, not assumed (see ``quality.py``).
* Values are **interval means** in the unit named by ``kind``.
* Columns are series identifiers (customers, stations, channels).

A :class:`ProfileSet` always carries the files it came from (path, URL,
SHA-256) so that a hosting-capacity number can be traced back to the
exact bytes that produced it.
"""

from __future__ import annotations

import enum
import hashlib
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pandas as pd

if TYPE_CHECKING:  # pragma: no cover
    from .quality import QualityReport


class DatasetError(RuntimeError):
    """A dataset could not be obtained or parsed."""


class DataNotAvailableError(DatasetError):
    """The source file is not cached and cannot be downloaded (offline/blocked)."""


class ProfileKind(str, enum.Enum):
    LOAD_KW = "load_kw"          # active-power demand per series, kW
    PV_KW = "pv_kw"              # PV output per series, kW
    PV_PU = "pv_pu"              # PV output per unit of rating, [0, 1]
    LOAD_PU = "load_pu"          # demand per unit of a reference (benchmark profiles)
    IRRADIANCE = "irradiance"    # columns: ghi_wm2 [, temp_air_c], W/m2 and degC


@dataclass(frozen=True)
class Site:
    """Where a series was measured (or where it is projected to).

    Attributes:
        utc_offset_hours: Local *standard* time offset (no DST).
        tz_name: IANA zone of the local wall clock; drives human behaviour
            (load) and may include DST. ``None`` -> fixed standard offset.
    """

    name: str
    latitude: float
    longitude: float
    utc_offset_hours: float
    tz_name: str | None = None

    @property
    def hemisphere(self) -> str:
        return "south" if self.latitude < 0 else "north"

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class SourceFile:
    path: str
    sha256: str
    size_bytes: int
    url: str | None = None

    @classmethod
    def from_path(cls, path: str | Path, url: str | None = None) -> SourceFile:
        p = Path(path)
        return cls(path=str(p), sha256=sha256_file(p), size_bytes=p.stat().st_size, url=url)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class DatasetInfo:
    """Static description of a dataset, used by docs, the CLI and manifests."""

    key: str
    title: str
    publisher: str
    provides: tuple[str, ...]          # e.g. ("load",), ("irradiance",), ("load", "pv")
    coverage: str
    resolution: str
    license: str
    citation: str
    homepage: str
    access: str                        # how the raw file is obtained
    notes: tuple[str, ...] = ()

    def to_dict(self) -> dict:
        d = asdict(self)
        d["provides"] = list(self.provides)
        d["notes"] = list(self.notes)
        return d


@dataclass
class ProfileSet:
    """A cleaned, regular, UTC-indexed set of profiles from one dataset."""

    dataset: str
    kind: ProfileKind
    data: pd.DataFrame
    resolution: pd.Timedelta
    site: Site | None = None
    sources: tuple[SourceFile, ...] = ()
    meta: dict[str, Any] = field(default_factory=dict)
    quality: QualityReport | None = None

    def __post_init__(self) -> None:
        idx = self.data.index
        if not isinstance(idx, pd.DatetimeIndex) or idx.tz is None:
            raise ValueError(f"{self.dataset}: index must be a tz-aware DatetimeIndex.")
        if str(idx.tz) != "UTC":
            raise ValueError(f"{self.dataset}: index must be in UTC, got {idx.tz}.")
        if idx.has_duplicates:
            raise ValueError(f"{self.dataset}: duplicate timestamps in a ProfileSet.")
        if not idx.is_monotonic_increasing:
            raise ValueError(f"{self.dataset}: index must be increasing.")
        if len(idx) > 1:
            steps = idx[1:] - idx[:-1]
            if not (steps == self.resolution).all():
                raise ValueError(f"{self.dataset}: index is not a regular {self.resolution} grid.")
        if self.data.shape[1] == 0:
            raise ValueError(f"{self.dataset}: no series (columns).")

    @property
    def start(self) -> pd.Timestamp:
        return self.data.index[0]

    @property
    def end(self) -> pd.Timestamp:
        """Exclusive end of the last interval."""
        return self.data.index[-1] + self.resolution

    def select(self, columns: list[str]) -> ProfileSet:
        missing = [c for c in columns if c not in self.data.columns]
        if missing:
            raise KeyError(f"{self.dataset}: unknown series {missing[:5]}")
        return ProfileSet(
            self.dataset, self.kind, self.data[columns].copy(), self.resolution,
            self.site, self.sources, dict(self.meta), self.quality,
        )

    def describe(self) -> dict:
        return {
            "dataset": self.dataset,
            "kind": self.kind.value,
            "n_series": int(self.data.shape[1]),
            "n_steps": int(self.data.shape[0]),
            "resolution": str(self.resolution),
            "start_utc": self.start.isoformat(),
            "end_utc": self.end.isoformat(),
            "site": self.site.to_dict() if self.site else None,
            "sources": [s.to_dict() for s in self.sources],
        }


def sha256_file(path: str | Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(chunk), b""):
            h.update(block)
    return h.hexdigest()


__all__ = [
    "DataNotAvailableError", "DatasetError", "DatasetInfo", "ProfileKind", "ProfileSet",
    "Site", "SourceFile", "sha256_file",
]
