"""SimBench benchmark profiles: annual 15-min load and PV time series.

SimBench (Meinecke et al., 2020) publishes, with its benchmark grids, a
full year (2016) of 15-min relative profiles: household (``H0-*``),
commercial (``G*``), agricultural (``L*``) loads, PV (``PV1..PV8``) and
others. They ship inside the ``simbench`` pip package, so this is the
one source that works fully offline (``pip install -e "simulator[data]"``).

Values are per unit of each element's rated power (``LOAD_PU`` /
``PV_PU``); ``build_series`` re-normalises anyway.

Clock (measured on the package data): the ``time`` column skips
02:00-02:45 on 2016-03-27 and repeats 02:00-02:45 on 2016-10-30, i.e. it
is the **CET/CEST wall clock** (Europe/Berlin). The PV profiles have
different, undocumented orientations (their energy centroids differ by
~2 h from each other), so the solar-alignment check cannot validate them;
their mean peaks ~55 min before solar noon at 10 deg E on that clock. The
measured offset is recorded as a warning. For Brazilian studies use PV
from INMET, NASA POWER or PVGIS and SimBench only for load shapes.
"""

from __future__ import annotations

import pandas as pd

from .base import DatasetError, DatasetInfo, ProfileKind, ProfileSet, Site
from .quality import QualityReport, clip_negative, fill_short_gaps, regularize
from .solar import alignment_offset_minutes

INFO = DatasetInfo(
    key="simbench",
    title="SimBench benchmark profiles (2016, 15 min)",
    publisher="SimBench consortium (Fraunhofer IEE, Univ. Kassel, RWTH Aachen, TU Dortmund, et al.)",
    provides=("load", "pv"),
    coverage="Representative German profiles; one synthetic-calendar year (2016)",
    resolution="15 min",
    license="Open Database License (ODbL) for the data; simbench package under BSD-3 [VERIFY]",
    citation="Meinecke, S., Sarajlic, D., Drauz, S. R., et al. (2020). SimBench - A Benchmark "
             "Dataset of Electric Power Systems to Compare Innovative Solutions Based on Power "
             "Flow Analysis. Energies 13(12), 3290. doi:10.3390/en13123290",
    homepage="https://simbench.de/en/",
    access="Bundled in the 'simbench' pip package (optional extra 'data'); no download",
    notes=(
        "Time axis is CET/CEST wall clock (DST transitions present in the file).",
        "Profiles are representative/standardised, derived from measurements; not raw meters.",
    ),
)

RESOLUTION = pd.Timedelta("15min")
SITE = Site("SimBench Germany (representative)", 51.0, 10.0, 1.0, "Europe/Berlin")
DEFAULT_LOAD_PROFILES = ("H0-A", "H0-B", "H0-C")


def _require_simbench():
    try:
        import simbench  # noqa: F401
    except ImportError as exc:  # pragma: no cover - exercised only without the extra
        raise DatasetError(
            "SimBench profiles need the optional 'simbench' package: "
            'pip install -e "simulator[data]"'
        ) from exc
    import simbench as sb
    return sb


def _index(time: pd.Series) -> pd.DatetimeIndex:
    naive = pd.to_datetime(time, format="%d.%m.%Y %H:%M")
    return pd.DatetimeIndex(naive).tz_localize(SITE.tz_name, ambiguous="infer").tz_convert("UTC")


def from_profiles_table(
    table: pd.DataFrame, kind: str, profiles: list[str] | tuple[str, ...] | None
) -> ProfileSet:
    report = QualityReport(f"simbench:{kind}")
    report.add("wall_clock_with_dst", "info", "time axis localised to Europe/Berlin (DST in file)")
    idx = _index(table["time"])
    if kind == "load":
        names = list(profiles or DEFAULT_LOAD_PROFILES)
        cols = {f"{n}_pload": n for n in names}
        missing = [c for c in cols if c not in table]
        if missing:
            raise DatasetError(f"SimBench: unknown load profiles {[cols[c] for c in missing]}.")
        frame = table[list(cols)].rename(columns=cols)
        pkind = ProfileKind.LOAD_PU
    elif kind == "pv":
        names = list(profiles or [c for c in table.columns if c.startswith("PV")])
        missing = [n for n in names if n not in table]
        if missing:
            raise DatasetError(f"SimBench: unknown PV profiles {missing}.")
        frame = table[names]
        pkind = ProfileKind.PV_PU
    else:
        raise ValueError("kind must be 'load' or 'pv'.")
    frame = frame.astype(float)
    frame.index = idx
    frame = regularize(frame, RESOLUTION, report)
    frame = clip_negative(frame, report)
    frame = fill_short_gaps(frame, 4, report)
    if kind == "pv":
        monthly = alignment_offset_minutes(frame.mean(axis=1), RESOLUTION, SITE.longitude)
        if not monthly.empty:
            off = float((monthly["offset_min"] * monthly["days"]).sum() / monthly["days"].sum())
            report.stats["solar_alignment_offset_min"] = round(off, 1)
            report.add("pv_orientation_unknown", "warning",
                       f"mean PV centroid {off:+.0f} min from solar noon at 10 deg E; profiles have "
                       "undocumented orientations, so timing cannot be validated")
    report.stats["profiles"] = list(frame.columns)
    return ProfileSet("simbench", pkind, frame, RESOLUTION, SITE, (),
                      {"profiles": list(frame.columns), "relative": True}, report)


def load(
    kind: str = "load",
    profiles: list[str] | str | None = None,
    scenario: int = 0,
    cache=None,
    source_file=None,
) -> ProfileSet:
    """``kind='load'`` (default ``H0-A,H0-B,H0-C`` household profiles) or ``'pv'``."""
    sb = _require_simbench()
    if isinstance(profiles, str):
        profiles = [p.strip() for p in profiles.split(",") if p.strip()]
    tables = sb.get_all_simbench_profiles(int(scenario))
    table = tables["load" if kind == "load" else "renewables"]
    ps = from_profiles_table(table, kind, profiles)
    ps.meta["simbench_version"] = getattr(sb, "__version__", None)
    ps.meta["scenario"] = int(scenario)
    return ps


__all__ = ["DEFAULT_LOAD_PROFILES", "INFO", "SITE", "from_profiles_table", "load"]
