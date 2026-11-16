"""Turn protocol results into the tables and figures of the results chapter.

    python scripts/analyze_protocol.py data/protocol-v2 --out reports/protocol-v2
    python scripts/analyze_protocol.py data/protocol-v2 --sensitivity data/protocol-v3 --figures

One command regenerates everything, so no number or figure is typed by hand:

``results.csv``    every (run, method) row, flat, with its commit and flags
``tables.md``      inventory, panel, method comparison, weekly distributions,
                   hypothesis checks and (with ``--sensitivity``) the
                   sensitivity effects, each with its resolution
``analysis.json``  the same content, machine-readable, with the SHA-256 of
                   every result file read
``fig_*.pdf/png``  with ``--figures`` (needs matplotlib: ``pip install -e
                   "simulator[plots]"``); every figure carries the protocol
                   id and the commits of the rows it shows

Reading the output
------------------
* A capacity is the lower end of a bracket of width ``tolerance``; differences
  at or below it are printed as ties (``~``).
* Rows that should not be cited are listed first with the reason: failed or
  censored searches, results produced on a dirty tree, criteria still marked
  ``to_verify``, a series resolution that differs from the criterion's reading.
* The hypothesis section is a *check against thresholds written before the
  runs* (``docs/HYPOTHESES.md``); it does not choose them.

Missing runs are reported as missing and never fail the script: a protocol
may be run in parts.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "simulator"))

from gridsense_sim.analysis import (  # noqa: E402
    ResultRecord,
    block_bootstrap_quantile,
    load_results,
    paired_summary,
    relative_effect,
    weekly_summary,
)

Records = dict[tuple[str, str], ResultRecord]
PRIMARY, TAU0, CONTRAST = "prodist_m8_drp_drc", "prodist_m8_zero_tolerance", "en50160_weekly"
PANEL_PRIMARY, PANEL_CONTRAST = "prodist_m8_bt", "en50160_envelope"
MIN_WEEKS_FOR_BOOTSTRAP = 8


# --------------------------------------------------------------------------- helpers
def fmt(x: float | None, digits: int = 3) -> str:
    return "-" if x is None else f"{x:.{digits}f}"


def pct(x: float | None) -> str:
    return "-" if x is None else f"{100 * x:+.1f} %"


def lam(rec: ResultRecord | None) -> float | None:
    """The capacity of a record, or None when it has none (failed or censored search)."""
    if rec is None or rec.status != "ok" or not rec.bounded or rec.lambda_max is None:
        return None
    return float(rec.lambda_max)


def binding_family(binding: str | None) -> str:
    """Collapse a binding constraint to voltage / thermal for cross-run comparison."""
    if not binding:
        return "-"
    b = binding.lower()
    if "trafo" in b or "line" in b or "overload" in b or "thermal" in b or "loading" in b:
        return "thermal"
    if "volt" in b:
        return "voltage"
    return b.split("@")[0]


def groups_of(protocol: dict | None) -> dict[str, str]:
    return {r["name"]: r["group"] for r in (protocol or {}).get("runs", [])}


def in_group(records: Records, groups: dict[str, str], prefix: str) -> list[ResultRecord]:
    return [rec for (run, _), rec in sorted(records.items()) if groups.get(run, "").startswith(prefix)]


def not_citable_reason(rec: ResultRecord) -> str | None:
    if rec.status != "ok":
        return f"status {rec.status}"
    if not rec.bounded or rec.lambda_max is None:
        return "censored (no violation up to the search cap)"
    if rec.resolved is False:
        return "bracket not closed within the tolerance"
    if rec.git_dirty is None:
        return "no git provenance"
    if rec.git_dirty:
        return "produced on a dirty git tree"
    return None


def caveats(rec: ResultRecord) -> list[str]:
    out = []
    if rec.criterion_to_verify:
        out.append("criterion to_verify")
    if rec.resolution_matches_criterion is False:
        out.append("resolution differs from the criterion's reading")
    return out


def effect_cell(a: float | None, b: float | None, tolerance: float) -> tuple[str, dict | None]:
    if a is None or b is None:
        return "-", None
    e = relative_effect(a, b, tolerance)
    return (pct(e.relative) if e.distinguishable else f"~ ({pct(e.relative)})"), e.to_dict()


# --------------------------------------------------------------------------- sections
def inventory(records: Records) -> dict:
    rows = list(records.values())
    blocked = [{"run": r.run, "method": r.method, "reason": not_citable_reason(r)}
               for r in rows if not_citable_reason(r)]
    flagged = [{"run": r.run, "method": r.method, "caveats": caveats(r)} for r in rows if caveats(r)]
    return {
        "n_rows": len(rows),
        "protocols": sorted({r.protocol for r in rows}),
        "commits": sorted({r.git_commit for r in rows if r.git_commit}),
        "n_citable": sum(not_citable_reason(r) is None for r in rows),
        "not_citable": blocked,
        "with_caveats": flagged,
    }


def panel_table(records: Records, groups: dict[str, str], tolerance: float) -> list[dict]:
    by_net: dict[str, dict[str, ResultRecord]] = {}
    for rec in in_group(records, groups, "A_"):
        if rec.method == "deterministic" and rec.criterion:
            by_net.setdefault(rec.network, {})[rec.criterion] = rec
    out = []
    for net, d in sorted(by_net.items()):
        p, c = d.get(PANEL_PRIMARY), d.get(PANEL_CONTRAST)
        cell, eff = effect_cell(lam(c), lam(p), tolerance)
        out.append({
            "network": net,
            "lambda_primary": lam(p), "status_primary": p.status if p else "missing",
            "binding_primary": p.binding if p else None,
            "lambda_contrast": lam(c), "status_contrast": c.status if c else "missing",
            "binding_contrast": c.binding if c else None,
            "contrast_vs_primary": cell, "effect": eff,
            "binding_changes": (binding_family(p.binding) != binding_family(c.binding))
            if p and c and lam(p) is not None and lam(c) is not None else None,
        })
    return out


def method_table(records: Records, groups: dict[str, str], tolerance: float) -> list[dict]:
    by_net: dict[str, dict[str, ResultRecord]] = {}
    for rec in in_group(records, groups, "D_"):
        key = rec.method if rec.method != "qsts" else f"qsts:{rec.criterion}"
        by_net.setdefault(rec.network, {})[key] = rec
    out = []
    for net, d in sorted(by_net.items()):
        det, sto = d.get("deterministic"), d.get("stochastic")
        tau0, drp, en = d.get(f"qsts:{TAU0}"), d.get(f"qsts:{PRIMARY}"), d.get(f"qsts:{CONTRAST}")
        gain_cell, gain = effect_cell(lam(drp), lam(tau0), tolerance)
        sto_cell, sto_eff = effect_cell(lam(sto), lam(det), tolerance)
        proxy_cell, proxy = effect_cell(lam(det), lam(tau0), tolerance)
        out.append({
            "network": net,
            "deterministic": lam(det), "stochastic": lam(sto),
            "stochastic_ci": [sto.ci_low, sto.ci_high] if sto else None,
            "qsts_tau0": lam(tau0), "qsts_drp_drc": lam(drp), "qsts_en50160": lam(en),
            "binding_drp_drc": drp.binding if drp else None,
            "duration_gain": gain_cell, "duration_gain_effect": gain,
            "stochastic_vs_deterministic": sto_cell, "stochastic_effect": sto_eff,
            "deterministic_vs_tau0": proxy_cell, "proxy_effect": proxy,
            "caveats": sorted({c for r in (tau0, drp, en) if r for c in caveats(r)}),
        })
    return out


def weekly_table(records: Records, seed: int) -> list[dict]:
    out = []
    for (run, method), rec in sorted(records.items()):
        if method != "qsts" or not rec.weekly_lambdas:
            continue
        s = weekly_summary(rec.weekly_lambdas, rec.weekly_censored)
        row = {"run": run, "network": rec.network, "criterion": rec.criterion, **s.to_dict(), "p10_ci": None}
        if s.n_weeks >= MIN_WEEKS_FOR_BOOTSTRAP:
            row["p10_ci"] = block_bootstrap_quantile(rec.weekly_lambdas, 0.10, seed=seed)
        out.append(row)
    return out


def _pairs(table: list[dict], a: str, b: str) -> tuple[dict[str, float], dict[str, float]]:
    xa = {r["network"]: r[a] for r in table if r.get(a) is not None and r.get(b) is not None}
    xb = {r["network"]: r[b] for r in table if r.get(a) is not None and r.get(b) is not None}
    return xa, xb


def hypotheses(panel: list[dict], methods: list[dict], records: Records, groups: dict[str, str],
               tolerance: float) -> list[dict]:
    """Evaluate H1-H5 of docs/HYPOTHESES.md on whatever has been run."""
    out = []

    def paired(hid: str, statement: str, a: str, b: str, table: list[dict], expect: str) -> None:
        xa, xb = _pairs(table, a, b)
        if not xa:
            out.append({"id": hid, "statement": statement, "status": "not run", "n": 0})
            return
        s = paired_summary(xa, xb, tolerance)
        holds = (s.n_positive if expect == "positive" else s.n_negative)
        out.append({"id": hid, "statement": statement, "status": "evaluated", "n": s.n,
                    "n_supporting": holds, "n_against": s.n_negative if expect == "positive" else s.n_positive,
                    "n_ties": s.n_ties, "median_relative": s.median_relative,
                    "range_relative": [s.min_relative, s.max_relative],
                    "wilcoxon_p": s.wilcoxon_p, "smallest_attainable_p": s.smallest_attainable_p,
                    "per_network": s.relative})

    paired("H1", "The duration allowance raises the QSTS capacity above the zero-tolerance capacity by "
                 "more than the search resolution", "qsts_drp_drc", "qsts_tau0", methods, "positive")
    # H2: the literature operating point (panel, load x0.25) vs the series-derived point (group D)
    lit = {r["network"]: r["lambda_primary"] for r in panel if r["lambda_primary"] is not None}
    ser = {r["network"]: r["deterministic"] for r in methods if r["deterministic"] is not None}
    common = sorted(set(lit) & set(ser))
    if common:
        s = paired_summary({k: lit[k] for k in common}, {k: ser[k] for k in common}, tolerance)
        out.append({"id": "H2", "statement": "The literature operating point and the series-derived point give "
                                             "different deterministic capacities",
                    "status": "evaluated", "n": s.n, "n_supporting": s.n_positive + s.n_negative,
                    "n_against": 0, "n_ties": s.n_ties, "median_relative": s.median_relative,
                    "range_relative": [s.min_relative, s.max_relative], "wilcoxon_p": s.wilcoxon_p,
                    "smallest_attainable_p": s.smallest_attainable_p, "per_network": s.relative})
    else:
        out.append({"id": "H2", "statement": "The literature operating point and the series-derived point give "
                                             "different deterministic capacities", "status": "not run", "n": 0})
    paired("H3", "Uncoordinated adoption (stochastic p10) hosts less than load-proportional allocation "
                 "(deterministic) at the same operating point", "stochastic", "deterministic", methods, "negative")
    # H4: hourly blocks vs the native series, same network and criterion
    native = {r.network: lam(r) for r in in_group(records, groups, "D_")
              if r.method == "qsts" and r.criterion == PRIMARY and lam(r) is not None}
    hourly = {r.network: lam(r) for r in in_group(records, groups, "E_")
              if r.method == "qsts" and r.block_average_min == 60 and lam(r) is not None}
    common = sorted(set(native) & set(hourly))
    if common:
        s = paired_summary({k: hourly[k] for k in common}, {k: native[k] for k in common}, tolerance)
        out.append({"id": "H4", "statement": "Hourly data overstate the capacity under a duration criterion",
                    "status": "evaluated", "n": s.n, "n_supporting": s.n_positive, "n_against": s.n_negative,
                    "n_ties": s.n_ties, "median_relative": s.median_relative,
                    "range_relative": [s.min_relative, s.max_relative], "wilcoxon_p": s.wilcoxon_p,
                    "smallest_attainable_p": s.smallest_attainable_p, "per_network": s.relative})
    else:
        out.append({"id": "H4", "statement": "Hourly data overstate the capacity under a duration criterion",
                    "status": "not run", "n": 0})
    known = [r for r in panel if r["binding_changes"] is not None]
    out.append({"id": "H5", "statement": "The binding constraint changes between PRODIST and EN 50160",
                "status": "evaluated" if known else "not run", "n": len(known),
                "n_supporting": sum(r["binding_changes"] for r in known),
                "n_against": sum(not r["binding_changes"] for r in known), "n_ties": 0,
                "per_network": {r["network"]: bool(r["binding_changes"]) for r in known}})
    return out


def sensitivity_table(protocol: dict, sens: Records, base: Records, tolerance: float) -> list[dict]:
    out = []
    for run in protocol.get("runs", []):
        ref = run.get("baseline")
        if not ref:
            continue
        method = ref["method"]
        rec = sens.get((run["name"], method))
        pool = sens if ref["protocol"] == protocol["id"] else base
        base_rec = pool.get((ref["run"], method))
        cell, eff = effect_cell(lam(rec), lam(base_rec), tolerance)
        out.append({
            "group": run["group"], "run": run["name"], "network": rec.network if rec else None,
            "method": method, "factor": run["factor"]["name"], "value": run["factor"]["value"],
            "base_value": run["factor"]["base_value"], "baseline_run": ref["run"],
            "lambda": lam(rec), "status": rec.status if rec else "missing",
            "lambda_baseline": lam(base_rec), "status_baseline": base_rec.status if base_rec else "missing",
            "binding": rec.binding if rec else None, "binding_baseline": base_rec.binding if base_rec else None,
            "effect_cell": cell, "effect": eff,
            "lower_side_compliant": (rec.extra["verification"].get("lower_side_compliant") if rec else None),
        })
    return out


# --------------------------------------------------------------------------- output
def md_table(headers: list[str], rows: list[list[str]]) -> str:
    lines = ["| " + " | ".join(headers) + " |", "|" + "|".join("---" for _ in headers) + "|"]
    lines += ["| " + " | ".join(str(c) for c in row) + " |" for row in rows]
    return "\n".join(lines)


def render_markdown(a: dict) -> str:
    inv = a["inventory"]
    out = [f"# Results of {', '.join(inv['protocols']) or 'unlabelled runs'}", "",
           f"{inv['n_rows']} row(s), {inv['n_citable']} citable. Commits: "
           f"{', '.join(c[:10] for c in inv['commits']) or 'none recorded'}. "
           f"Search resolution: {a['tolerance']} (differences at or below it are shown as `~`).", ""]
    if inv["not_citable"]:
        out += ["## Rows that must not be cited", "",
                md_table(["run", "method", "reason"],
                         [[r["run"], r["method"], r["reason"]] for r in inv["not_citable"]]), ""]
    if inv["with_caveats"]:
        kinds: dict[str, int] = {}
        for r in inv["with_caveats"]:
            for c in r["caveats"]:
                kinds[c] = kinds.get(c, 0) + 1
        out += ["## Caveats that go with the numbers", ""]
        out += [f"* {k}: {n} row(s)" for k, n in sorted(kinds.items())] + [""]
    if a["panel"]:
        out += ["## Deterministic panel (literature operating point)", "",
                md_table(["network", "PRODIST", "binding", "EN 50160", "binding", "EN vs PRODIST", "binding changes"],
                         [[r["network"], fmt(r["lambda_primary"]) if r["lambda_primary"] is not None
                           else r["status_primary"], r["binding_primary"] or "-",
                           fmt(r["lambda_contrast"]) if r["lambda_contrast"] is not None else r["status_contrast"],
                           r["binding_contrast"] or "-", r["contrast_vs_primary"],
                           "-" if r["binding_changes"] is None else ("yes" if r["binding_changes"] else "no")]
                          for r in a["panel"]]), ""]
    if a["methods"]:
        def ci(r: dict) -> str:
            c = r["stochastic_ci"]
            return "" if not c or c[0] is None or c[1] is None else f" [{fmt(c[0])}, {fmt(c[1])}]"
        out += ["## Methods on the same data (series-derived operating point)", "",
                md_table(["network", "deterministic", "stochastic p10 [95 % CI]", "QSTS tau = 0", "QSTS DRP/DRC",
                          "QSTS EN 50160", "duration gain", "stochastic vs deterministic"],
                         [[r["network"], fmt(r["deterministic"]), fmt(r["stochastic"]) + ci(r), fmt(r["qsts_tau0"]),
                           fmt(r["qsts_drp_drc"]), fmt(r["qsts_en50160"]), r["duration_gain"],
                           r["stochastic_vs_deterministic"]] for r in a["methods"]]), ""]
    if a["weekly"]:
        def p10(r: dict) -> str:
            c = r["p10_ci"]
            return fmt(r["p10"]) + (f" [{fmt(c['low'])}, {fmt(c['high'])}]" if c else "")
        out += ["## Weekly capacities (QSTS)", "",
                "The annual capacity is the minimum over weeks. `gap` is the distance to the second-lowest "
                "week: how much the result depends on one week. The p10 interval is a circular block "
                "bootstrap over weeks (within-year variability only).", "",
                md_table(["run", "weeks", "censored", "min", "gap", "gap / min", "p10 [95 %]", "p50"],
                         [[r["run"], r["n_weeks"], r["n_censored"], fmt(r["minimum"]), fmt(r["gap_to_second"]),
                           pct(r["relative_gap"]), p10(r), fmt(r["p50"])] for r in a["weekly"]]), ""]
    if a["hypotheses"]:
        rows = []
        for h in a["hypotheses"]:
            if h["status"] != "evaluated":
                rows.append([h["id"], h["statement"], "not run", "-", "-", "-"])
                continue
            med = pct(h["median_relative"]) if "median_relative" in h else "-"
            rng = (f"{pct(h['range_relative'][0])} to {pct(h['range_relative'][1])}"
                   if "range_relative" in h else "-")
            p = h.get("wilcoxon_p")
            ptxt = "-" if p is None else f"{p:.3f} (min attainable {h['smallest_attainable_p']:.3f})"
            rows.append([h["id"], h["statement"],
                         f"{h['n_supporting']} of {h['n']} (against {h['n_against']}, ties {h['n_ties']})",
                         med, rng, ptxt])
        out += ["## Hypothesis checks", "",
                "Counts are networks whose difference exceeds the search resolution in the stated direction. "
                "Thresholds and decision rules are those of `docs/HYPOTHESES.md`.", "",
                md_table(["id", "statement", "networks supporting", "median effect", "range", "Wilcoxon p"],
                         rows), ""]
    if a.get("sensitivity"):
        out += ["## Sensitivities (one assumption varied per run)", "",
                md_table(["run", "factor", "value", "lambda", "baseline", "effect", "binding"],
                         [[r["run"], r["factor"], r["value"],
                           fmt(r["lambda"]) if r["lambda"] is not None else r["status"],
                           fmt(r["lambda_baseline"]) if r["lambda_baseline"] is not None else r["status_baseline"],
                           r["effect_cell"], r["binding"] or "-"] for r in a["sensitivity"]]), ""]
    return "\n".join(out) + "\n"


def write_csv(path: Path, records: Records) -> None:
    rows = [r.to_dict() for r in records.values()]
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        for r in rows:
            w.writerow({k: json.dumps(v) if isinstance(v, (list, tuple, dict)) else v for k, v in r.items()})


def file_hashes(directory: Path) -> dict[str, str]:
    return {str(p.relative_to(directory)).replace("\\", "/"): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(directory.rglob("*.parquet"))}


# --------------------------------------------------------------------------- figures
OKABE_ITO = {"blue": "#0072B2", "orange": "#E69F00", "green": "#009E73", "vermillion": "#D55E00",
             "purple": "#CC79A7", "sky": "#56B4E9", "black": "#000000", "grey": "#7F7F7F"}


def make_figures(a: dict, out: Path) -> list[str]:
    try:
        import matplotlib
    except ImportError as exc:  # pragma: no cover - exercised only without the extra
        raise SystemExit('--figures needs matplotlib: pip install -e "simulator[plots]"') from exc
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np

    plt.rcParams.update({"font.size": 9, "axes.spines.top": False, "axes.spines.right": False,
                         "axes.grid": True, "grid.alpha": 0.3, "figure.dpi": 150, "savefig.bbox": "tight",
                         "pdf.fonttype": 42})
    inv = a["inventory"]
    footer = (f"{', '.join(inv['protocols'])} | commits {', '.join(c[:10] for c in inv['commits']) or 'n/a'}"
              + (" | includes rows that must not be cited" if inv["not_citable"] else ""))
    written: list[str] = []

    def save(fig, name: str) -> None:
        fig.text(0.01, -0.02, footer, fontsize=6, color=OKABE_ITO["grey"], ha="left", va="top")
        for ext in ("pdf", "png"):
            fig.savefig(out / f"{name}.{ext}")
        plt.close(fig)
        written.append(name)

    def short(net: str) -> str:
        return net.replace("simbench_lv_", "sb_").replace("kerber_", "k_")

    panel = [r for r in a["panel"] if r["lambda_primary"] is not None or r["lambda_contrast"] is not None]
    if panel:
        fig, ax = plt.subplots(figsize=(6.3, 0.42 * len(panel) + 1.2))
        y = np.arange(len(panel))
        for off, key, bkey, label, color in ((-0.18, "lambda_primary", "binding_primary", "PRODIST (adequate band)",
                                              OKABE_ITO["blue"]),
                                             (0.18, "lambda_contrast", "binding_contrast", "EN 50160 (envelope)",
                                              OKABE_ITO["orange"])):
            vals = [r[key] or 0.0 for r in panel]
            ax.barh(y + off, vals, height=0.34, color=color, label=label)
            for yi, r in zip(y, panel):
                if r[key] is not None:
                    ax.text(r[key], yi + off, f" {r[key]:.2f} ({binding_family(r[bkey])[0].upper()})",
                            va="center", fontsize=7)
        ax.set_yticks(y, [short(r["network"]) for r in panel])
        ax.invert_yaxis()
        ax.set_xlabel("Hosting capacity, lambda (PV / nominal load)")
        ax.set_title("Deterministic capacity by criterion (V = voltage-bound, T = thermally bound)",
                     fontsize=9, pad=22)
        ax.set_xlim(0, 1.15 * max(max(r["lambda_primary"] or 0.0, r["lambda_contrast"] or 0.0) for r in panel))
        ax.legend(frameon=False, loc="lower center", bbox_to_anchor=(0.5, 1.0), ncol=2, fontsize=8)
        ax.grid(axis="y", visible=False)
        save(fig, "fig_panel")

    methods = [r for r in a["methods"] if any(r[k] is not None for k in
                                              ("deterministic", "stochastic", "qsts_tau0", "qsts_drp_drc"))]
    if methods:
        fig, ax = plt.subplots(figsize=(6.3, 0.6 * len(methods) + 1.4))
        series = (("deterministic", "Deterministic (series point)", "s", OKABE_ITO["black"]),
                  ("stochastic", "Stochastic p10 (95 % CI)", "o", OKABE_ITO["vermillion"]),
                  ("qsts_tau0", "QSTS, zero tolerance", "v", OKABE_ITO["sky"]),
                  ("qsts_drp_drc", "QSTS, PRODIST DRP/DRC", "D", OKABE_ITO["blue"]),
                  ("qsts_en50160", "QSTS, EN 50160 weekly", "^", OKABE_ITO["orange"]))
        for i, r in enumerate(methods):
            for j, (key, label, marker, color) in enumerate(series):
                if r[key] is None:
                    continue
                yy = i + (j - 2) * 0.13
                ax.plot(r[key], yy, marker=marker, color=color, linestyle="none", markersize=5,
                        label=label if i == 0 else None)
                c = r["stochastic_ci"]
                if key == "stochastic" and c and c[0] is not None and c[1] is not None:
                    ax.plot([c[0], c[1]], [yy, yy], color=color, linewidth=1)
        ax.set_yticks(range(len(methods)), [short(r["network"]) for r in methods])
        ax.invert_yaxis()
        ax.set_xlabel("Hosting capacity, lambda (PV / nominal load)")
        ax.set_title("Three methods on the same data", fontsize=9)
        handles, labels = ax.get_legend_handles_labels()
        seen = dict(zip(labels, handles))
        ax.legend(seen.values(), seen.keys(), frameon=False, fontsize=7, loc="best")
        ax.grid(axis="y", visible=False)
        save(fig, "fig_methods")

    weekly = [r for r in a["weekly_series"] if r["criterion"] == PRIMARY and len(r["lambdas"]) >= 2]
    if weekly:
        fig, ax = plt.subplots(figsize=(6.3, 3.0))
        colors = [OKABE_ITO[c] for c in ("blue", "orange", "green", "vermillion", "purple", "sky", "black")]
        for r, color in zip(weekly, colors * 3):
            x = np.arange(1, len(r["lambdas"]) + 1)
            ax.plot(x, r["lambdas"], color=color, linewidth=1, marker=".", markersize=3, label=short(r["run"]))
            k = int(np.argmin(r["lambdas"]))
            ax.plot(x[k], r["lambdas"][k], marker="o", markersize=7, markerfacecolor="none", color=color)
        ax.set_xlabel("Compliance window with an exceedance (chronological)")
        ax.set_ylabel("Weekly capacity, lambda")
        ax.set_title("Weekly capacities under PRODIST DRP/DRC (circle = the week that sets the capacity)",
                     fontsize=9)
        ax.legend(frameon=False, fontsize=7, ncol=2)
        save(fig, "fig_weekly")

    sens = [r for r in a.get("sensitivity") or [] if r["effect"] is not None]
    factors = [f for f in ("pv_power_factor", "source_vm_pu", "block_average_min") if any(r["factor"] == f for r in sens)]
    if factors:
        titles = {"pv_power_factor": "PV power factor (inductive)", "source_vm_pu": "Source voltage (pu)",
                  "block_average_min": "Block average (min)"}
        fig, axes = plt.subplots(1, len(factors), figsize=(3.2 * len(factors), 2.9), squeeze=False)
        palette = [OKABE_ITO[c] for c in ("blue", "orange", "green", "vermillion", "purple", "sky", "black")]
        networks = sorted({r["network"] for r in sens if r["factor"] in factors})
        color_of = {net: palette[i % len(palette)] for i, net in enumerate(networks)}   # one colour per network
        handles: dict[str, object] = {}
        for ax, factor in zip(axes[0], factors):
            lines: dict[tuple[str, str], list[tuple[float, float]]] = {}
            for r in sens:
                if r["factor"] == factor:
                    lines.setdefault((r["network"], r["method"]), []).append(
                        (float(r["value"]), 100 * r["effect"]["relative"]))
            for (net, method), pts in sorted(lines.items()):
                pts.sort()
                label = f"{short(net)}, {'QSTS DRP/DRC' if method == 'qsts' else method}"
                (line,) = ax.plot([p[0] for p in pts], [p[1] for p in pts], "--" if method == "qsts" else "-",
                                  marker="D" if method == "qsts" else "o", markersize=3, color=color_of[net],
                                  linewidth=1)
                handles.setdefault(label, line)
            ax.axhline(0, color=OKABE_ITO["grey"], linewidth=0.8)
            ax.set_xlabel(titles[factor])
        axes[0][0].set_ylabel("Change of capacity vs base run (%)")
        fig.legend(handles.values(), handles.keys(), frameon=False, fontsize=6, loc="lower center",
                   bbox_to_anchor=(0.5, 1.0), ncol=min(4, len(handles)))
        save(fig, "fig_sensitivity")
    return written


# --------------------------------------------------------------------------- main
def analyze(results_dir: Path, protocol: dict | None, tolerance: float, seed: int,
            sensitivity_dir: Path | None = None, sensitivity_protocol: dict | None = None) -> tuple[dict, Records]:
    records = load_results(results_dir)
    groups = groups_of(protocol)
    panel = panel_table(records, groups, tolerance)
    methods = method_table(records, groups, tolerance)
    a = {
        "results_dir": str(results_dir), "tolerance": tolerance, "bootstrap_seed": seed,
        "inventory": inventory(records), "panel": panel, "methods": methods,
        "weekly": weekly_table(records, seed),
        "weekly_series": [{"run": run, "criterion": rec.criterion, "lambdas": list(rec.weekly_lambdas)}
                          for (run, method), rec in sorted(records.items())
                          if method == "qsts" and rec.weekly_lambdas and rec.block_average_min is None],
        "hypotheses": hypotheses(panel, methods, records, groups, tolerance),
        "source_files": file_hashes(results_dir),
    }
    if sensitivity_dir is not None and sensitivity_protocol is not None:
        sens = load_results(sensitivity_dir)
        a["sensitivity"] = sensitivity_table(sensitivity_protocol, sens, records, tolerance)
        a["sensitivity_inventory"] = inventory(sens)
        a["sensitivity_files"] = file_hashes(sensitivity_dir)
    return a, records


def _protocol_for(results_dir: Path, explicit: str | None) -> dict | None:
    path = Path(explicit) if explicit else ROOT / "protocol" / f"{results_dir.name}.json"
    if not path.exists():
        if explicit:
            raise SystemExit(f"Protocol file {path} does not exist.")
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("results", help="Results directory of a protocol (e.g. data/protocol-v2).")
    p.add_argument("--protocol", default=None, help="Protocol file; default protocol/<results dir name>.json.")
    p.add_argument("--sensitivity", default=None, help="Results directory of the sensitivity protocol (v3).")
    p.add_argument("--sensitivity-protocol", default=None,
                   help="Its protocol file; default protocol/<sensitivity dir name>.json.")
    p.add_argument("--out", default=None, help="Output directory; default reports/<results dir name>.")
    p.add_argument("--tolerance", type=float, default=None,
                   help="Search resolution; default: the protocol's fixed_parameters.tolerance, else 0.01.")
    p.add_argument("--seed", type=int, default=0, help="Seed of the block bootstrap.")
    p.add_argument("--figures", action="store_true", help="Also write fig_*.pdf/png (needs matplotlib).")
    args = p.parse_args(argv)

    results_dir = Path(args.results)
    if not results_dir.is_dir():
        raise SystemExit(f"{results_dir} is not a directory.")
    protocol = _protocol_for(results_dir, args.protocol)
    tolerance = args.tolerance or float(((protocol or {}).get("fixed_parameters") or {}).get("tolerance", 0.01))
    sens_dir = Path(args.sensitivity) if args.sensitivity else None
    sens_protocol = _protocol_for(sens_dir, args.sensitivity_protocol) if sens_dir else None
    if sens_dir is not None and sens_protocol is None:
        raise SystemExit("--sensitivity needs its protocol file (--sensitivity-protocol).")
    a, records = analyze(results_dir, protocol, tolerance, args.seed, sens_dir, sens_protocol)

    out = Path(args.out) if args.out else ROOT / "reports" / results_dir.name
    out.mkdir(parents=True, exist_ok=True)
    write_csv(out / "results.csv", records)
    (out / "tables.md").write_text(render_markdown(a), encoding="utf-8")
    if args.figures:
        a["figures"] = make_figures(a, out)
    (out / "analysis.json").write_text(json.dumps(a, indent=1, default=str) + "\n", encoding="utf-8")
    inv = a["inventory"]
    print(f"{inv['n_rows']} row(s), {inv['n_citable']} citable, {len(inv['not_citable'])} not citable; "
          f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
