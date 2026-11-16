"""Load a hosting-capacity results directory into flat, typed records.

Rows are matched by ``params.run_label`` (``<protocol id>/<run name>``) and
method; when a run was executed more than once, the latest row wins -- the
same rule as ``scripts/compare_protocols.py``.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class ResultRecord:
    """One (run, method) result, with what is needed to interpret and cite it."""

    run: str                       # run name inside the protocol ("" for unlabelled rows)
    protocol: str                  # protocol id ("" for unlabelled rows)
    method: str
    network: str
    status: str
    criterion: str | None          # duration criterion, or the voltage framework of a snapshot
    lambda_max: float | None
    bounded: bool
    resolved: bool | None
    total_pv_mw: float | None
    binding: str | None
    tolerance: float | None
    ci_low: float | None = None    # stochastic: distribution-free CI of the quantile
    ci_high: float | None = None
    alpha: float | None = None
    weekly_lambdas: tuple[float, ...] = ()        # QSTS: capacity of each window with an exceedance
    weekly_censored: int = 0                      # windows without an exceedance (lower bounds only)
    critical_window: int | None = None
    pv_power_factor: float = 1.0
    source_vm_pu: float | None = None
    block_average_min: int | None = None
    adoption_fraction: tuple[float, float] | None = None
    n_scenarios: int | None = None
    resolution_matches_criterion: bool | None = None
    criterion_to_verify: bool | None = None
    backend: str = "pandapower"
    git_commit: str | None = None
    git_dirty: bool | None = None
    run_timestamp: str = ""
    extra: dict = field(default_factory=dict, compare=False)

    @property
    def citable(self) -> bool:
        """A finite capacity from a finished, bracketed search on a clean commit."""
        return (self.status == "ok" and self.bounded and self.lambda_max is not None
                and self.git_dirty is False and self.resolved is not False)

    def to_dict(self) -> dict:
        d = asdict(self)
        d.pop("extra")
        d["citable"] = self.citable
        return d


def _record(row: dict) -> ResultRecord:
    p = json.loads(row["raw_value"])
    params = p.get("params") or {}
    cond = p.get("conditions") or {}
    prov = p.get("provenance") or {}
    label = params.get("run_label") or ""
    protocol, _, run = label.partition("/") if "/" in label else ("", "", label)
    windows = p.get("windows") or []
    with_exceedance = [w for w in windows if w.get("bounded") and w.get("lambda_max") is not None]
    finite = [w for w in windows if w.get("lambda_max") is not None]
    adoption = (params.get("adoption_model") or p.get("adoption_model") or {}).get("adoption_fraction")
    spec = p.get("criterion_spec") or {}
    return ResultRecord(
        run=run, protocol=protocol, method=row["method"], network=row["network"],
        status=p.get("status", "ok"),
        criterion=p.get("criterion") if isinstance(p.get("criterion"), str) and p.get("criterion") != "zero_tolerance"
        else cond.get("framework"),
        lambda_max=p.get("lambda_max"), bounded=bool(p.get("bounded")), resolved=p.get("resolved"),
        total_pv_mw=p.get("total_pv_mw"), binding=p.get("binding_constraint"),
        tolerance=params.get("tolerance"),
        ci_low=p.get("hc_lambda_ci_low"), ci_high=p.get("hc_lambda_ci_high"), alpha=p.get("alpha"),
        weekly_lambdas=tuple(float(w["lambda_max"]) for w in with_exceedance),
        weekly_censored=len(finite) - len(with_exceedance),
        critical_window=p.get("critical_window"),
        pv_power_factor=float(cond.get("pv_power_factor", 1.0) or 1.0),
        source_vm_pu=p.get("source_vm_pu", params.get("source_vm_pu")),
        block_average_min=params.get("series_block_average_min"),
        adoption_fraction=tuple(adoption) if adoption else None,
        n_scenarios=p.get("n_scenarios"),
        resolution_matches_criterion=p.get("resolution_matches_criterion"),
        criterion_to_verify=spec.get("to_verify"),
        backend=p.get("backend", "pandapower"),
        git_commit=prov.get("git_commit"), git_dirty=prov.get("git_dirty"),
        run_timestamp=row.get("run_timestamp", ""),
        extra={"verification": p.get("verification") or {}, "notes": p.get("notes") or []},
    )


def load_results(directory: str | Path) -> dict[tuple[str, str], ResultRecord]:
    """``(run name, method) -> latest record`` under ``directory`` (recursive).

    Rows without a run label are keyed by ``("", method)`` per network and are
    of little use for a protocol analysis; they are kept under
    ``(f"<unlabelled:{network}>", method)`` so nothing is dropped silently.
    """
    import pyarrow.parquet as pq

    out: dict[tuple[str, str], ResultRecord] = {}
    for path in sorted(Path(directory).rglob("*.parquet")):
        for row in pq.read_table(path).to_pylist():
            rec = _record(row)
            key = (rec.run or f"<unlabelled:{rec.network}>", rec.method)
            if key not in out or rec.run_timestamp > out[key].run_timestamp:
                out[key] = rec
    return out


__all__ = ["ResultRecord", "load_results"]
