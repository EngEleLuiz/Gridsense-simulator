"""Analysis of hosting-capacity results: loading, statistics, tables.

The studies write one JSON payload per (run, method). This package turns a
results directory into the numbers a results chapter cites, with the
uncertainty each number carries:

* :mod:`.results` -- load a results directory into flat records;
* :mod:`.stats`   -- measurement resolution, effect sizes, the weekly
  distribution of a QSTS capacity, paired comparisons across networks,
  resolution bias.

Nothing here runs a power flow.
"""

from .results import ResultRecord, load_results
from .stats import (
    Effect,
    PairedSummary,
    WeeklySummary,
    block_bootstrap_quantile,
    paired_summary,
    relative_effect,
    resolution_bias,
    weekly_summary,
)

__all__ = [
    "Effect", "PairedSummary", "ResultRecord", "WeeklySummary", "block_bootstrap_quantile",
    "load_results", "paired_summary", "relative_effect", "resolution_bias", "weekly_summary",
]
