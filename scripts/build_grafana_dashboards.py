"""Generate the five GridSense Grafana dashboards (Phase 7.4).

One readable specification -> five provisioned JSON files in
``grafana/dashboards/``. Edit the SQL here and re-run::

    python scripts/build_grafana_dashboards.py

Dashboards:

1. ``gridsense-01-telemetry``      -- grid telemetry KPIs (Gold marts)
2. ``gridsense-02-voltage``        -- voltage quality on in-scope buses
3. ``gridsense-03-hc-methods``     -- hosting capacity: method comparison (Gold mart)
4. ``gridsense-04-hc-duration``    -- duration criteria: PRODIST DRP/DRC vs zero tolerance
5. ``gridsense-05-network-panel``  -- network panel, binding constraints, unbalance effect

Dashboards 4 and 5 read fields that are not typed in dbt staging yet
(criterion details, weekly windows, phase model, existing-PV policy)
directly from ``bronze.hosting_capacity_results.raw_value`` (JSONB).
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

DS = {"type": "postgres", "uid": "gridsense-timescaledb"}
OUT = Path(__file__).resolve().parents[1] / "grafana" / "dashboards"

# Latest successful QSTS run per (criterion, series) for the selected network.
QSTS_LATEST = """WITH q AS (
  SELECT DISTINCT ON (raw_value->>'criterion',
                      coalesce(raw_value->>'series_manifest_sha256', raw_value->>'series_source', 'synthetic'))
         network, run_timestamp, raw_value
  FROM bronze.hosting_capacity_results
  WHERE method = 'qsts' AND network = '$network' AND raw_value->>'status' = 'ok'
  ORDER BY raw_value->>'criterion',
           coalesce(raw_value->>'series_manifest_sha256', raw_value->>'series_source', 'synthetic'),
           run_timestamp DESC
)"""

# Latest successful snapshot run per network and study condition.
SNAPSHOT_LATEST = """WITH d AS (
  SELECT DISTINCT ON (network, method, raw_value->'conditions'->>'framework',
                      coalesce(raw_value->>'phase_model', 'balanced'),
                      coalesce(raw_value->>'source_vm_pu', ''),
                      coalesce(raw_value->'params'->>'existing_pv', ''),
                      coalesce(raw_value->'conditions'->>'load_scale', ''))
         network, method, run_timestamp, raw_value,
         raw_value->'conditions'->>'framework' AS framework,
         coalesce(raw_value->>'phase_model', 'balanced') AS phase_model
  FROM bronze.hosting_capacity_results
  WHERE method IN ('deterministic', 'stochastic') AND raw_value->>'status' = 'ok'
  ORDER BY network, method, raw_value->'conditions'->>'framework',
           coalesce(raw_value->>'phase_model', 'balanced'),
           coalesce(raw_value->>'source_vm_pu', ''),
           coalesce(raw_value->'params'->>'existing_pv', ''),
           coalesce(raw_value->'conditions'->>'load_scale', ''),
           run_timestamp DESC
)"""


def var(name: str, sql: str) -> dict:
    return {"name": name, "type": "query", "datasource": DS, "query": sql, "definition": sql,
            "refresh": 1, "includeAll": False, "multi": False, "sort": 1}


def panel(pid, title, ptype, sql, pos, fmt="table", unit=None, desc="", options=None, decimals=None):
    defaults: dict = {}
    if unit:
        defaults["unit"] = unit
    if decimals is not None:
        defaults["decimals"] = decimals
    return {
        "id": pid, "title": title, "type": ptype, "description": desc, "datasource": DS,
        "gridPos": dict(zip(("x", "y", "w", "h"), pos)),
        "fieldConfig": {"defaults": defaults, "overrides": []},
        "options": options or {},
        "targets": [{"refId": "A", "datasource": DS, "format": fmt, "rawQuery": True,
                     "editorMode": "code", "rawSql": sql}],
    }


def text(pid, title, content, pos) -> dict:
    return {"id": pid, "title": title, "type": "text", "gridPos": dict(zip(("x", "y", "w", "h"), pos)),
            "options": {"mode": "markdown", "content": content}}


def dashboard(uid, title, panels, variables=(), time_from="now-7d", tags=()) -> dict:
    return {"uid": uid, "title": title, "tags": ["gridsense", *tags], "timezone": "browser",
            "schemaVersion": 39, "version": 1, "editable": True,
            "time": {"from": time_from, "to": "now"}, "templating": {"list": list(variables)},
            "panels": panels}


STAT = {"reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False}, "colorMode": "value",
        "graphMode": "none", "textMode": "auto"}


def telemetry() -> dict:
    t = "public_gold.mart_grid_kpis_daily"
    ll = "public_gold.mart_line_loading_hourly"
    p = [
        panel(1, "Peak load", "stat", f"SELECT max(peak_load_mw) AS peak_load_mw FROM {t} WHERE network = '$network'",
              (0, 0, 6, 4), unit="MW", options=STAT, decimals=3),
        panel(2, "Mean load factor", "stat",
              f"SELECT round(avg(load_factor)::numeric, 3) AS load_factor FROM {t} WHERE network = '$network'",
              (6, 0, 6, 4), options=STAT,
              desc="Average over days of (mean load / peak load). 1.0 = flat load."),
        panel(3, "Steps with an active contingency", "stat",
              f"SELECT sum(n_contingency_steps) AS contingency_steps FROM {t} WHERE network = '$network'",
              (12, 0, 6, 4), options=STAT),
        panel(4, "Simulated steps", "stat", f"SELECT sum(n_steps) AS steps FROM {t} WHERE network = '$network'",
              (18, 0, 6, 4), options=STAT),
        panel(5, "Daily load and generation", "timeseries",
              f"SELECT day_bucket AS time, avg_load_mw, peak_load_mw, min_load_mw, avg_generation_mw FROM {t} "
              f"WHERE network = '$network' AND $__timeFilter(day_bucket) ORDER BY 1",
              (0, 4, 24, 8), fmt="time_series", unit="MW"),
        panel(6, "Line loading (hourly)", "timeseries",
              f"SELECT hour_bucket AS time, max(max_loading_percent) AS max_loading_percent, "
              f"avg(avg_loading_percent) AS mean_loading_percent FROM {ll} "
              f"WHERE network = '$network' AND $__timeFilter(hour_bucket) GROUP BY 1 ORDER BY 1",
              (0, 12, 14, 8), fmt="time_series", unit="percent"),
        panel(7, "Most loaded lines", "table",
              f"SELECT line_id, round(max(max_loading_percent)::numeric, 1) AS max_loading_percent, "
              f"sum(n_overload_events) AS overload_events, "
              f"round(100.0 * sum(n_overload_events) / nullif(sum(n_readings), 0), 2) AS overload_rate_pct "
              f"FROM {ll} WHERE network = '$network' GROUP BY line_id ORDER BY 2 DESC LIMIT 15",
              (14, 12, 10, 8)),
    ]
    return dashboard("gridsense-01-telemetry", "GridSense 1 - Grid telemetry", p,
                     [var("network", f"SELECT DISTINCT network FROM {t} ORDER BY 1")], tags=("telemetry",))


def voltage() -> dict:
    v = "public_gold.mart_voltage_quality_hourly"
    w = "network = '$network' AND is_in_scope"
    p = [
        panel(1, "Violation rate (in-scope buses)", "stat",
              f"SELECT round(100.0 * sum(n_violations) / nullif(sum(n_readings), 0), 2) AS violation_rate_pct "
              f"FROM {v} WHERE {w}", (0, 0, 8, 4), unit="percent", options=STAT),
        panel(2, "Lowest voltage", "stat", f"SELECT min(min_voltage_pu) AS min_voltage_pu FROM {v} WHERE {w}",
              (8, 0, 8, 4), options=STAT, decimals=4),
        panel(3, "Highest voltage", "stat", f"SELECT max(max_voltage_pu) AS max_voltage_pu FROM {v} WHERE {w}",
              (16, 0, 8, 4), options=STAT, decimals=4),
        panel(4, "Voltage envelope (hourly, in-scope buses)", "timeseries",
              f"SELECT hour_bucket AS time, min(min_voltage_pu) AS min_pu, avg(avg_voltage_pu) AS mean_pu, "
              f"max(max_voltage_pu) AS max_pu FROM {v} WHERE {w} AND $__timeFilter(hour_bucket) "
              f"GROUP BY 1 ORDER BY 1", (0, 4, 16, 8), fmt="time_series", decimals=4),
        panel(5, "Violation rate (hourly)", "timeseries",
              f"SELECT hour_bucket AS time, round(100.0 * sum(n_violations) / nullif(sum(n_readings), 0), 2) "
              f"AS violation_rate_pct FROM {v} WHERE {w} AND $__timeFilter(hour_bucket) GROUP BY 1 ORDER BY 1",
              (16, 4, 8, 8), fmt="time_series", unit="percent"),
        panel(6, "Buses by violation rate", "table",
              f"SELECT bus_id, round(min(min_voltage_pu)::numeric, 4) AS min_voltage_pu, "
              f"round(max(max_voltage_pu)::numeric, 4) AS max_voltage_pu, sum(n_violations) AS violations, "
              f"round(100.0 * sum(n_violations) / nullif(sum(n_readings), 0), 2) AS violation_rate_pct "
              f"FROM {v} WHERE {w} GROUP BY bus_id ORDER BY violation_rate_pct DESC NULLS LAST LIMIT 20",
              (0, 12, 16, 9)),
        text(7, "How to read",
             "Only **in-scope** buses (LV load buses) are judged; slack, generator and MV buses are excluded, "
             "exactly as in the hosting-capacity code (seed `network_voltage_scope`). The band comes from the "
             "network's default framework (seed `network_voltage_limits`). Meshed transmission test cases show "
             "high rates on buses without local voltage support: a property of the network, not a pipeline error.",
             (16, 12, 8, 9)),
    ]
    return dashboard("gridsense-02-voltage", "GridSense 2 - Voltage quality", p,
                     [var("network", f"SELECT DISTINCT network FROM {v} ORDER BY 1")], tags=("telemetry",))


def hc_methods() -> dict:
    m = "public_gold.mart_hosting_capacity"
    p = [
        panel(1, "Latest comparable result per method, criterion and operating point", "table",
              "SELECT DISTINCT ON (method, criterion_framework, load_scale, hc_alpha) "
              "method, criterion_framework, load_scale, hc_alpha, round(lambda_max::numeric, 3) AS lambda_max, "
              "round(total_pv_mw_comparable::numeric, 3) AS total_pv_mw, binding_constraint, "
              "round(hc_lambda_ci_low::numeric, 3) AS ci_low, round(hc_lambda_ci_high::numeric, 3) AS ci_high, "
              f"run_timestamp FROM {m} WHERE network = '$network' AND is_comparable "
              "ORDER BY method, criterion_framework, load_scale, hc_alpha, run_timestamp DESC",
              (0, 0, 24, 9),
              desc="Compare rows only within the same criterion_framework and load_scale. Stochastic rows are "
                   "the alpha quantile of the critical penetration with a distribution-free CI."),
        panel(2, "Hosting capacity by method and criterion (MW)", "bargauge",
              "SELECT DISTINCT ON (method, criterion_framework) method || ' / ' || criterion_framework AS label, "
              f"total_pv_mw_comparable AS mw FROM {m} WHERE network = '$network' AND is_comparable "
              "ORDER BY method, criterion_framework, run_timestamp DESC",
              (0, 9, 12, 9), unit="MW", decimals=3,
              options={"orientation": "horizontal", "displayMode": "gradient",
                       "reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": True}}),
        panel(3, "Binding constraint types (comparable runs)", "table",
              "SELECT method, criterion_framework, split_part(binding_constraint, '@', 1) AS constraint_type, "
              f"count(*) AS runs FROM {m} WHERE network = '$network' AND is_comparable "
              "AND binding_constraint IS NOT NULL GROUP BY 1, 2, 3 ORDER BY 1, 2, 4 DESC",
              (12, 9, 12, 9)),
        panel(4, "Runs that are not comparable (and why)", "table",
              "SELECT method, status, criterion_framework, is_bounded, is_resolved, "
              f"left(coalesce(error, ''), 140) AS error, run_timestamp FROM {m} "
              "WHERE network = '$network' AND NOT is_comparable ORDER BY run_timestamp DESC LIMIT 20",
              (0, 18, 24, 7),
              desc="baseline_infeasible = the network already violates the band without PV; legacy payloads and "
                   "stochastic rows without a CI are also excluded from comparison."),
    ]
    return dashboard("gridsense-03-hc-methods", "GridSense 3 - Hosting capacity: methods", p,
                     [var("network", f"SELECT DISTINCT network FROM {m} ORDER BY 1")], time_from="now-90d",
                     tags=("hosting-capacity",))


def hc_duration() -> dict:
    b = "bronze.hosting_capacity_results"
    p = [
        panel(1, "Latest QSTS result per criterion and series", "table",
              QSTS_LATEST + " SELECT raw_value->>'criterion' AS criterion, "
              "round((raw_value->>'lambda_max')::numeric, 3) AS lambda_max, "
              "round((raw_value->>'total_pv_mw')::numeric, 3) AS total_pv_mw, "
              "raw_value->>'binding_constraint' AS binding_constraint, "
              "coalesce(raw_value->>'series_source', 'synthetic') AS series, "
              "round((raw_value->'window_lambda_quantiles'->>'p10')::numeric, 3) AS week_p10, "
              "round((raw_value->'window_lambda_quantiles'->>'p50')::numeric, 3) AS week_p50, "
              "raw_value->>'baseline_lower_compliant' AS baseline_lower_ok, run_timestamp "
              "FROM q ORDER BY series, criterion",
              (0, 0, 24, 8),
              desc="zero_tolerance / prodist_m8_zero_tolerance: first violating step. prodist_m8_drp_drc: weekly "
                   "DRP <= 3 %, DRC <= 0.5 % over 10-min readings with PV attribution. week_p10/p50: spread of "
                   "the weekly capacities."),
        panel(2, "Effect of the duration criterion (per series)", "table",
              QSTS_LATEST + " SELECT coalesce(raw_value->>'series_source', 'synthetic') AS series, "
              "round(max((raw_value->>'lambda_max')::numeric) FILTER (WHERE raw_value->>'criterion' IN "
              "('zero_tolerance', 'prodist_m8_zero_tolerance')), 3) AS tau_zero, "
              "round(max((raw_value->>'lambda_max')::numeric) FILTER (WHERE raw_value->>'criterion' = "
              "'prodist_m8_drp_drc'), 3) AS prodist_drp_drc, "
              "round(max((raw_value->>'lambda_max')::numeric) FILTER (WHERE raw_value->>'criterion' = "
              "'en50160_weekly'), 3) AS en50160_weekly, "
              "round(100 * (max((raw_value->>'lambda_max')::numeric) FILTER (WHERE raw_value->>'criterion' = "
              "'prodist_m8_drp_drc') / nullif(max((raw_value->>'lambda_max')::numeric) FILTER (WHERE "
              "raw_value->>'criterion' IN ('zero_tolerance', 'prodist_m8_zero_tolerance')), 0) - 1), 1) "
              "AS duration_gain_pct FROM q GROUP BY 1 ORDER BY 1",
              (0, 8, 14, 8),
              desc="duration_gain_pct: how much the PRODIST weekly allowance raises the capacity over the "
                   "zero-tolerance QSTS on the same series. Expect >= 0 (tested invariant)."),
        panel(3, "Verification at the capacity (critical week, worst bus)", "table",
              QSTS_LATEST + " SELECT raw_value->>'criterion' AS criterion, raw_value->>'critical_window' AS "
              "critical_week, round(100 * (raw_value->'verification'->>'worst_bus_upper_drp')::numeric, 2) AS "
              "worst_bus_drp_pct, round(100 * (raw_value->'verification'->>'worst_bus_upper_drc')::numeric, 2) "
              "AS worst_bus_drc_pct, raw_value->'verification'->>'within_regulatory_allowance_per_bus' AS "
              "within_allowance FROM q WHERE raw_value ? 'verification' ORDER BY 1",
              (14, 8, 10, 8),
              desc="Exact per-bus recount at the reported capacity. PRODIST allows DRP 3 % and DRC 0.5 %."),
        panel(4, "Weekly capacities", "barchart",
              QSTS_LATEST + " SELECT 'week ' || lpad(w->>'index', 2, '0') AS week, raw_value->>'criterion' "
              "AS criterion, (w->>'lambda_max')::float AS lambda_max FROM q, "
              "jsonb_array_elements(q.raw_value->'windows') AS w WHERE w->>'lambda_max' IS NOT NULL "
              "ORDER BY 1, 2",
              (0, 16, 24, 8), options={"xField": "week", "orientation": "vertical", "groupWidth": 0.7},
              desc="One bar per week and criterion. The annual capacity is the minimum over weeks."),
    ]
    return dashboard("gridsense-04-hc-duration", "GridSense 4 - Hosting capacity: duration criteria", p,
                     [var("network", f"SELECT DISTINCT network FROM {b} WHERE method = 'qsts' ORDER BY 1")],
                     time_from="now-90d", tags=("hosting-capacity", "prodist"))


def network_panel() -> dict:
    b = "bronze.hosting_capacity_results"
    p = [
        panel(1, "Deterministic capacity across networks", "table",
              SNAPSHOT_LATEST + " SELECT network, raw_value->'params'->>'network_country' AS country, "
              "phase_model, round((raw_value->>'source_vm_pu')::numeric, 3) AS source_vm_pu, "
              "raw_value->'params'->>'existing_pv' AS existing_pv, "
              "raw_value->'conditions'->>'load_scale' AS load_scale, "
              "round((raw_value->>'lambda_max')::numeric, 3) AS lambda_max, "
              "coalesce(raw_value->>'lambda_unit', 'per_unit_of_nominal_load') AS lambda_unit, "
              "round((raw_value->>'total_pv_mw')::numeric, 3) AS total_pv_mw, "
              "raw_value->>'binding_constraint' AS binding_constraint "
              "FROM d WHERE method = 'deterministic' AND framework = '$framework' ORDER BY network, phase_model",
              (0, 0, 24, 10),
              desc="Latest deterministic run per network and condition. lambda is installed PV per unit of "
                   "nominal load (balanced networks) or kWp per customer (three-phase feeders): compare MW across "
                   "networks, lambda only within a network."),
        panel(2, "What limits each network (binding constraint type)", "table",
              SNAPSHOT_LATEST + " SELECT framework, split_part(raw_value->>'binding_constraint', '@', 1) AS "
              "constraint_type, count(DISTINCT network) AS networks, string_agg(DISTINCT network, ', ') AS which "
              "FROM d WHERE method = 'deterministic' AND raw_value->>'binding_constraint' IS NOT NULL "
              "GROUP BY 1, 2 ORDER BY 1, 3 DESC",
              (0, 10, 12, 8),
              desc="Across all frameworks. Under PRODIST (1.05 pu) overvoltage tends to bind; under EN 50160 "
                   "(1.10 pu) the transformer does."),
        panel(3, "Effect of unbalance (three-phase feeders)", "table",
              SNAPSHOT_LATEST + " SELECT network, framework, round((raw_value->>'source_vm_pu')::numeric, 3) AS source_vm_pu, "
              "round(max((raw_value->>'lambda_max')::numeric) FILTER (WHERE phase_model = 'unbalanced'), 2) "
              "AS unbalanced_kwp, round(max((raw_value->>'lambda_max')::numeric) FILTER (WHERE phase_model = "
              "'balanced_equivalent'), 2) AS balanced_equivalent_kwp, "
              "round(100 * (max((raw_value->>'lambda_max')::numeric) FILTER (WHERE phase_model = "
              "'balanced_equivalent') / nullif(max((raw_value->>'lambda_max')::numeric) FILTER (WHERE "
              "phase_model = 'unbalanced'), 0) - 1), 1) AS balanced_overestimate_pct, "
              "round(max((raw_value->>'max_vuf_percent')::numeric) FILTER (WHERE phase_model = 'unbalanced'), 2) "
              "AS max_vuf_pct FROM d WHERE method = 'deterministic' AND raw_value ? 'phase_model' "
              "GROUP BY 1, 2, 3 ORDER BY 1, 2, 3",
              (12, 10, 12, 8),
              desc="Same three-phase solver; balanced_equivalent spreads every load and PV evenly over the "
                   "phases. A positive overestimate means the balanced model reports more room than exists."),
        panel(4, "Hosting capacity by network (MW)", "bargauge",
              SNAPSHOT_LATEST + " SELECT network || CASE WHEN phase_model <> 'balanced' THEN ' / ' || "
              "phase_model ELSE '' END AS label, (raw_value->>'total_pv_mw')::float AS mw FROM d "
              "WHERE method = 'deterministic' AND framework = '$framework' AND raw_value->>'total_pv_mw' IS NOT NULL "
              "ORDER BY 2 DESC",
              (0, 18, 24, 9), unit="MW", decimals=3,
              options={"orientation": "horizontal", "displayMode": "gradient",
                       "reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": True}}),
    ]
    fw = (f"SELECT DISTINCT raw_value->'conditions'->>'framework' FROM {b} "
          "WHERE method = 'deterministic' AND raw_value->>'status' = 'ok' ORDER BY 1")
    return dashboard("gridsense-05-network-panel", "GridSense 5 - Network panel and unbalance", p,
                     [var("framework", fw)], time_from="now-90d", tags=("hosting-capacity", "networks"))


BUILDERS = (telemetry, voltage, hc_methods, hc_duration, network_panel)


def build() -> dict[str, dict]:
    return {d["uid"]: d for d in (f() for f in BUILDERS)}


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--out", default=str(OUT))
    args = p.parse_args(argv)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    for uid, d in build().items():
        (out / f"{uid}.json").write_text(json.dumps(d, indent=2) + "\n", encoding="utf-8")
        print(f"wrote {out / (uid + '.json')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
