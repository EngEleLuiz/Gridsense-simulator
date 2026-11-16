"""Analysis layer: loading results, statistics with their limits, tables and figures."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import analyze_protocol  # noqa: E402
from gridsense_sim.analysis import (  # noqa: E402
    block_bootstrap_quantile,
    load_results,
    paired_summary,
    relative_effect,
    resolution_bias,
    weekly_summary,
)

COMMIT = "a" * 40


# ------------------------------------------------------------------ statistics
def test_relative_effect_and_the_search_resolution() -> None:
    e = relative_effect(1.36, 1.30, 0.01)
    assert e.relative == pytest.approx(0.06 / 1.30) and e.distinguishable
    assert not relative_effect(1.305, 1.30, 0.01).distinguishable          # inside one bracket: a tie
    assert relative_effect(1.305, 1.30, 0.001).distinguishable
    assert relative_effect(1.0, 1.02, 0.01, 0.05).resolution == 0.05      # the coarser search decides
    with pytest.raises(ValueError):
        relative_effect(float("inf"), 1.0)
    with pytest.raises(ValueError):
        relative_effect(1.0, 0.0)


def test_weekly_summary_reports_how_much_the_minimum_hangs_on_one_week() -> None:
    s = weekly_summary([2.0, 1.5, 3.0, 1.2, 2.5], n_censored=2)
    assert s.minimum == 1.2 and s.second_lowest == 1.5 and s.critical_week == 3
    assert s.gap_to_second == pytest.approx(0.3) and s.relative_gap == pytest.approx(0.25)
    assert s.n_weeks == 5 and s.n_censored == 2
    one = weekly_summary([1.7])
    assert one.second_lowest is None and one.gap_to_second is None
    with pytest.raises(ValueError):
        weekly_summary([])
    with pytest.raises(ValueError):
        weekly_summary([1.0, float("inf")])


def test_block_bootstrap_is_reproducible_and_refuses_the_minimum() -> None:
    x = np.random.default_rng(1).normal(2.0, 0.3, 51)
    a = block_bootstrap_quantile(x, 0.10, seed=7)
    assert a == block_bootstrap_quantile(x, 0.10, seed=7)
    assert a["low"] <= a["point"] <= a["high"] and a["n"] == 51
    wide = block_bootstrap_quantile(x, 0.10, seed=7, confidence=0.99)
    assert wide["low"] <= a["low"] and wide["high"] >= a["high"]
    for bad_q in (0.0, 1.0):
        with pytest.raises(ValueError, match="minimum"):
            block_bootstrap_quantile(x, bad_q)
    with pytest.raises(ValueError):
        block_bootstrap_quantile(x[:5], 0.1)
    with pytest.raises(ValueError):
        block_bootstrap_quantile(x, 0.1, block=40)
    assert block_bootstrap_quantile(np.full(20, 1.5), 0.5)["low"] == 1.5      # no spread, no interval


def test_paired_summary_counts_ties_and_states_the_attainable_p() -> None:
    a = {"n1": 1.20, "n2": 2.00, "n3": 3.005, "n4": 0.90, "only_a": 9.0}
    b = {"n1": 1.00, "n2": 1.50, "n3": 3.000, "n4": 1.00, "only_b": 9.0}
    s = paired_summary(a, b, 0.01)
    assert s.n == 4 and s.n_ties == 1 and s.n_positive == 2 and s.n_negative == 1
    assert s.relative["n1"] == pytest.approx(0.2) and s.min_relative == pytest.approx(-0.1)
    assert s.smallest_attainable_p == pytest.approx(2 / 2 ** 3)           # three non-tied pairs
    assert s.wilcoxon_p is not None and s.wilcoxon_p >= s.smallest_attainable_p
    seven = paired_summary({f"n{i}": 1.1 + i for i in range(7)}, {f"n{i}": 1.0 + i for i in range(7)})
    assert seven.wilcoxon_p == pytest.approx(2 / 2 ** 7)
    with pytest.raises(ValueError):
        paired_summary({"x": 1.0}, {"y": 1.0})


def test_resolution_bias_is_relative_to_the_criterion_reading() -> None:
    b = resolution_bias({10: 1.00, 15: 1.02, 60: 1.20, 30: 1.005}, reference_minutes=10)
    assert list(b) == [15, 30, 60]
    assert b[60].relative == pytest.approx(0.20) and b[15].distinguishable and not b[30].distinguishable
    with pytest.raises(ValueError):
        resolution_bias({15: 1.0}, reference_minutes=10)


# ------------------------------------------------------------------ loading and tables
def _row(label: str, method: str, network: str, payload: dict, stamp: str = "2026-01-01", dirty: bool = False) -> dict:
    base = {"status": "ok", "bounded": True, "resolved": True,
            "params": {"run_label": label, "tolerance": 0.01, **payload.pop("params", {})},
            "provenance": {"git_commit": COMMIT, "git_dirty": dirty}}
    return {"network": network, "method": method, "run_id": label, "run_timestamp": stamp,
            "raw_value": json.dumps({**base, **payload})}


def _write(directory: Path, rows: list[dict]) -> None:
    import pyarrow as pa
    import pyarrow.parquet as pq

    directory.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.Table.from_pylist(rows), directory / "part.parquet")


def _weeks(values: list[float]) -> list[dict]:
    return [{"index": i, "start_step": i * 672, "lambda_max": v, "bounded": True} for i, v in enumerate(values)]


@pytest.fixture()
def study(tmp_path) -> tuple[Path, Path, dict, dict]:
    base_dir, sens_dir = tmp_path / "protocol-t", tmp_path / "protocol-ts"
    rows, runs = [], []

    def run(group: str, name: str) -> None:
        runs.append({"group": group, "name": name, "args": [], "estimate_min": 1})

    weeks = {"netA": [2.4, 2.1, 2.6, 2.05, 2.8, 2.5, 2.3, 2.2, 2.9, 2.7], "netB": [1.6, 1.4, 1.8, 1.5]}
    for net, (prodist, en) in {"netA": (1.20, 1.60), "netB": (2.00, 2.005)}.items():
        for fw, value, binding in (("prodist_m8_bt", prodist, "overvoltage@bus_3 (1.0501 pu)"),
                                   ("en50160_envelope", en, "trafo_loading@trafo_0 (100.1%)")):
            run("A_deterministic_panel", f"A-{net}-{fw}")
            rows.append(_row(f"protocol-t/A-{net}-{fw}", "deterministic", net,
                             {"lambda_max": value, "binding_constraint": binding if net == "netA"
                              else "overvoltage@bus_1 (1.05 pu)", "conditions": {"framework": fw}}))
        tau0 = min(weeks[net]) - 0.1
        run("D_simbench_annual", f"D-{net}-tau0")
        rows += [
            _row(f"protocol-t/D-{net}-tau0", "deterministic", net,
                 {"lambda_max": tau0 + 0.2, "conditions": {"framework": "prodist_m8_bt"}}),
            _row(f"protocol-t/D-{net}-tau0", "stochastic", net,
                 {"lambda_max": tau0 - 0.3, "hc_lambda_ci_low": tau0 - 0.5, "hc_lambda_ci_high": tau0 - 0.1,
                  "alpha": 0.1, "n_scenarios": 60, "conditions": {"framework": "prodist_m8_bt"},
                  "adoption_model": {"adoption_fraction": [0.3, 1.0]}}),
            _row(f"protocol-t/D-{net}-tau0", "qsts", net,
                 {"lambda_max": tau0, "criterion": "prodist_m8_zero_tolerance",
                  "windows": _weeks([w - 0.1 for w in weeks[net]]), "criterion_spec": {"to_verify": True},
                  "resolution_matches_criterion": False}),
        ]
        run("D_simbench_annual", f"D-{net}-drp-drc")
        rows.append(_row(f"protocol-t/D-{net}-drp-drc", "qsts", net,
                         {"lambda_max": min(weeks[net]), "criterion": "prodist_m8_drp_drc",
                          "binding_constraint": "upper_voltage_precarious", "windows": _weeks(weeks[net]),
                          "criterion_spec": {"to_verify": True}, "resolution_matches_criterion": False}))
        run("E_sensitivity_simbench", f"E-{net}-drp-drc-hourly")
        rows.append(_row(f"protocol-t/E-{net}-drp-drc-hourly", "qsts", net,
                         {"lambda_max": min(weeks[net]) * 1.05, "criterion": "prodist_m8_drp_drc",
                          "windows": _weeks([w * 1.05 for w in weeks[net]]),
                          "params": {"series_block_average_min": 60}}))
    # a stale row (older timestamp), a failed run, a censored run and a dirty run
    rows.append(_row("protocol-t/A-netA-prodist_m8_bt", "deterministic", "netA",
                     {"lambda_max": 9.99, "conditions": {"framework": "prodist_m8_bt"}}, stamp="2025-01-01"))
    for name, payload, dirty in (("X-failed", {"status": "baseline_infeasible", "bounded": False}, False),
                                 ("X-censored", {"lambda_max": 1048576.0, "bounded": False}, False),
                                 ("X-dirty", {"lambda_max": 1.0}, True)):
        run("X_other", name)
        rows.append(_row(f"protocol-t/{name}", "deterministic", "netA",
                         {**payload, "conditions": {"framework": "prodist_m8_bt"}}, dirty=dirty))
    _write(base_dir, rows)
    protocol = {"id": "protocol-t", "fixed_parameters": {"tolerance": 0.01}, "runs": runs}

    ref = {"protocol": "protocol-t", "run": "A-netA-prodist_m8_bt", "method": "deterministic"}
    sens_runs, sens_rows = [], []
    for pf, value in ((0.95, 1.32), (0.92, 1.38)):
        name = f"G-netA-pf{pf}"
        sens_runs.append({"group": "G_power_factor", "name": name, "args": [], "estimate_min": 1,
                          "factor": {"name": "pv_power_factor", "value": pf, "base_value": 1.0}, "baseline": ref})
        sens_rows.append(_row(f"protocol-ts/{name}", "deterministic", "netA",
                              {"lambda_max": value, "conditions": {"framework": "prodist_m8_bt",
                                                                   "pv_power_factor": pf}}))
    sens_runs.append({"group": "H_source_voltage", "name": "H-netA-src1.00", "args": [], "estimate_min": 1,
                      "factor": {"name": "source_vm_pu", "value": 1.0, "base_value": "network default"},
                      "baseline": ref})
    sens_rows.append(_row("protocol-ts/H-netA-src1.00", "deterministic", "netA",
                          {"lambda_max": 1.204, "source_vm_pu": 1.0, "conditions": {"framework": "prodist_m8_bt"}}))
    sens_runs.append({"group": "H_source_voltage", "name": "H-netA-missing", "args": [], "estimate_min": 1,
                      "factor": {"name": "source_vm_pu", "value": 1.04, "base_value": "network default"},
                      "baseline": ref})
    sens_runs.append({"group": "I_resolution", "name": "I-reference", "args": [], "estimate_min": 1,
                      "factor": {"name": "block_average_min", "value": 10, "base_value": 10}, "baseline": None})
    _write(sens_dir, sens_rows)
    return base_dir, sens_dir, protocol, {"id": "protocol-ts", "runs": sens_runs}


def test_load_results_keeps_the_latest_row_and_flags_what_cannot_be_cited(study) -> None:
    base_dir, _, _, _ = study
    rec = load_results(base_dir)
    assert rec[("A-netA-prodist_m8_bt", "deterministic")].lambda_max == 1.20        # not the stale 9.99
    drp = rec[("D-netA-drp-drc", "qsts")]
    assert drp.criterion == "prodist_m8_drp_drc" and len(drp.weekly_lambdas) == 10 and drp.criterion_to_verify
    assert drp.citable and drp.resolution_matches_criterion is False
    assert rec[("E-netA-drp-drc-hourly", "qsts")].block_average_min == 60
    assert rec[("D-netA-tau0", "stochastic")].adoption_fraction == (0.3, 1.0)
    assert not rec[("X-failed", "deterministic")].citable
    assert not rec[("X-censored", "deterministic")].citable
    assert not rec[("X-dirty", "deterministic")].citable
    assert rec[("X-dirty", "deterministic")].to_dict()["citable"] is False


def test_analysis_sections(study) -> None:
    base_dir, sens_dir, protocol, sens_protocol = study
    a, _ = analyze_protocol.analyze(base_dir, protocol, 0.01, 0, sens_dir, sens_protocol)
    inv = a["inventory"]
    assert {r["run"]: r["reason"] for r in inv["not_citable"]} == {
        "X-failed": "status baseline_infeasible",
        "X-censored": "censored (no violation up to the search cap)",
        "X-dirty": "produced on a dirty git tree"}
    assert inv["commits"] == [COMMIT] and len(a["source_files"]) == 1

    panel = {r["network"]: r for r in a["panel"]}
    assert panel["netA"]["binding_changes"] is True and panel["netB"]["binding_changes"] is False
    assert panel["netA"]["effect"]["relative"] == pytest.approx(0.4 / 1.2)
    assert panel["netB"]["contrast_vs_primary"].startswith("~")                     # 0.005 < resolution

    methods = {r["network"]: r for r in a["methods"]}
    assert methods["netA"]["qsts_drp_drc"] == 2.05 and methods["netA"]["qsts_tau0"] == pytest.approx(1.95)
    assert methods["netA"]["duration_gain_effect"]["distinguishable"]
    assert "criterion to_verify" in methods["netA"]["caveats"]

    weekly = {r["run"]: r for r in a["weekly"]}
    assert weekly["D-netA-drp-drc"]["minimum"] == 2.05 and weekly["D-netA-drp-drc"]["p10_ci"] is not None
    assert weekly["D-netB-drp-drc"]["p10_ci"] is None                               # 4 weeks: no bootstrap
    assert [w["run"] for w in a["weekly_series"]] == sorted(w["run"] for w in a["weekly_series"])
    assert not any("hourly" in w["run"] for w in a["weekly_series"])

    h = {x["id"]: x for x in a["hypotheses"]}
    assert h["H1"]["n_supporting"] == 2 and h["H3"]["n_supporting"] == 2 and h["H4"]["n_supporting"] == 2
    assert h["H2"]["status"] == "evaluated" and h["H5"]["n_supporting"] == 1 and h["H5"]["n_against"] == 1

    sens = {r["run"]: r for r in a["sensitivity"]}
    assert sens["G-netA-pf0.92"]["effect"]["relative"] == pytest.approx(0.15)
    assert sens["H-netA-src1.00"]["effect_cell"].startswith("~")
    assert sens["H-netA-missing"]["status"] == "missing" and sens["H-netA-missing"]["effect"] is None
    assert "I-reference" not in sens                                                # the reference has no baseline


def test_hypotheses_report_what_has_not_been_run(tmp_path) -> None:
    _write(tmp_path / "empty", [_row("p/A-x-prodist_m8_bt", "deterministic", "x",
                                     {"lambda_max": 1.0, "conditions": {"framework": "prodist_m8_bt"}})])
    protocol = {"id": "p", "runs": [{"group": "A_deterministic_panel", "name": "A-x-prodist_m8_bt"}]}
    a, _ = analyze_protocol.analyze(tmp_path / "empty", protocol, 0.01, 0)
    assert {x["id"]: x["status"] for x in a["hypotheses"]} == {
        "H1": "not run", "H2": "not run", "H3": "not run", "H4": "not run", "H5": "not run"}
    assert "sensitivity" not in a


def test_cli_writes_tables_csv_json_and_figures(study, tmp_path, capsys) -> None:
    base_dir, sens_dir, protocol, sens_protocol = study
    pfile, sfile = tmp_path / "p.json", tmp_path / "s.json"
    pfile.write_text(json.dumps(protocol), encoding="utf-8")
    sfile.write_text(json.dumps(sens_protocol), encoding="utf-8")
    out = tmp_path / "report"
    args = [str(base_dir), "--protocol", str(pfile), "--sensitivity", str(sens_dir),
            "--sensitivity-protocol", str(sfile), "--out", str(out)]
    assert analyze_protocol.main(args) == 0
    assert "not citable" in capsys.readouterr().out
    md = (out / "tables.md").read_text(encoding="utf-8")
    for heading in ("Rows that must not be cited", "Deterministic panel", "Methods on the same data",
                    "Weekly capacities", "Hypothesis checks", "Sensitivities"):
        assert heading in md
    assert "criterion to_verify" in md and "resolution differs" in md
    assert (out / "results.csv").read_text(encoding="utf-8").count("\n") == len(load_results(base_dir)) + 1
    first = json.loads((out / "analysis.json").read_text(encoding="utf-8"))
    assert analyze_protocol.main(args) == 0                                         # deterministic output
    assert json.loads((out / "analysis.json").read_text(encoding="utf-8")) == first

    pytest.importorskip("matplotlib")
    assert analyze_protocol.main([*args, "--figures"]) == 0
    written = json.loads((out / "analysis.json").read_text(encoding="utf-8"))["figures"]
    assert written == ["fig_panel", "fig_methods", "fig_weekly", "fig_sensitivity"]
    for name in written:
        assert (out / f"{name}.pdf").stat().st_size > 1000 and (out / f"{name}.png").stat().st_size > 1000


def test_cli_rejects_missing_inputs(tmp_path) -> None:
    with pytest.raises(SystemExit):
        analyze_protocol.main([str(tmp_path / "nope")])
    (tmp_path / "d").mkdir()
    with pytest.raises(SystemExit):
        analyze_protocol.main([str(tmp_path / "d"), "--protocol", str(tmp_path / "missing.json")])
