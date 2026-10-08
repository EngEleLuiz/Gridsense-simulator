"""Real datasets for GridSense (Phase 7).

Six open, citable sources, each with a loader that fetches (or accepts a
manual copy), checksums, parses, quality-controls and normalises the data
into a :class:`ProfileSet` (UTC, interval start, regular grid):

==============  ===============================  ===================  =========
key             what                             where                step
==============  ===============================  ===================  =========
``inmet``       GHI + air temperature (ground)   Brazil, ~600 AWS     1 h
``nasa_power``  GHI + T2M (satellite/reanalysis) global               1 h
``pvgis``       PV per kWp, tilted plane (JRC)   global (SARAH/ERA5)  1 h
``ausgrid``     load (GC+CL) and gross PV        300 homes, Sydney    30 min
``lcl``         load                             5,567 homes, London  30 min
``simbench``    benchmark load/PV profiles       Germany (pip pkg)    15 min
==============  ===============================  ===================  =========

:func:`build_series` turns one load set and one PV/irradiance set into the
QSTS :class:`~gridsense_sim.hosting_capacity.timeseries.TimeSeries`, with a
manifest that records every source file's SHA-256, the quality report and
the transformations applied. See ``docs/DATASETS.md``.
"""

from .base import (
    DataNotAvailableError,
    DatasetError,
    DatasetInfo,
    ProfileKind,
    ProfileSet,
    Site,
    SourceFile,
)
from .cache import ChecksumMismatchError, DataCache
from .quality import QualityIssue, QualityReport
from .registry import DATASETS, get_loader, load_dataset, parse_options
from .series import PRESET_SITES, RealSeries, build_series, load_series, save_series
from .solar import PVModel, ghi_to_pv_pu

__all__ = [
    "DATASETS",
    "PRESET_SITES",
    "ChecksumMismatchError",
    "DataCache",
    "DataNotAvailableError",
    "DatasetError",
    "DatasetInfo",
    "PVModel",
    "ProfileKind",
    "ProfileSet",
    "QualityIssue",
    "QualityReport",
    "RealSeries",
    "Site",
    "SourceFile",
    "build_series",
    "get_loader",
    "ghi_to_pv_pu",
    "load_dataset",
    "load_series",
    "parse_options",
    "save_series",
]
