# GridSense Transform — dbt Project (Phase 3)

Bronze → Silver → Gold transformation layer, built with
[dbt](https://www.getdbt.com/) against TimescaleDB. This is the
warehouse-side counterpart to the streaming pipeline in `../ingestion/`:
Kafka messages land as Parquet (Bronze, Phase 2), get bulk-loaded into
a TimescaleDB hypertable, and this project turns that raw JSON into
typed, tested, query-ready tables.

## Layers

```
bronze.raw_events (TimescaleDB hypertable, loaded by ../ingestion/load_bronze_to_timescale.py)
    │  raw_value: jsonb (untouched Kafka payload)
    ▼
staging.stg_grid_telemetry (view)
    │  scalar fields typed; per-bus/line/gen maps still jsonb
    ▼
silver.fct_bus_voltage      — one row per bus per step   (hypertable)
silver.fct_line_loading     — one row per line per step  (hypertable)
silver.fct_gen_power        — one row per generator/step (hypertable)
silver.fct_grid_summary     — one row per step, system totals (hypertable)
    ▼
gold.mart_voltage_quality_hourly  — hourly voltage KPIs per bus
gold.mart_line_loading_hourly     — hourly loading KPIs per line
gold.mart_grid_kpis_daily         — daily system-level KPIs
```

Design notes:

- **Schema-on-read at Bronze, schema-on-write everywhere else.** The
  raw JSON is never modified upstream of `stg_grid_telemetry` — if the
  simulator's payload shape changes, only the staging model needs to
  change.
- **Silver is intentionally "tidy" (long format).** Per-bus and
  per-line readings arrive as JSON maps (`{"0": 1.06, "1": 1.045,
  ...}`); the Silver models unpivot them into one row per
  bus/line/generator per step via `jsonb_each_text` + `LATERAL`. This
  is the natural shape for time-series queries and for the
  aggregations Gold builds on top.
- **Silver tables are TimescaleDB hypertables** (via the
  `make_hypertable` macro), partitioned on `event_timestamp`, for
  efficient time-range queries. The macro no-ops safely against plain
  PostgreSQL (checked in CI without the extension installed).
- **Business-rule tests, not just schema tests.** Beyond `not_null`
  checks, `tests/` has singular tests asserting physically meaningful
  invariants: voltage within plausible bounds, loading never negative,
  power balance reconciles with load/generation.

## Requirements

- Python 3.10+
- A running TimescaleDB (or plain PostgreSQL) instance — see the root
  `docker-compose.yml`
- Bronze data already loaded via
  `../ingestion/load_bronze_to_timescale.py` (see the root README)

## Setup

```bash
pip install -r requirements.txt
```

Set connection details via environment variables (defaults match the
root `docker-compose.yml`):

```bash
export TIMESCALE_HOST=localhost
export TIMESCALE_PORT=5432
export TIMESCALE_USER=postgres
export TIMESCALE_PASSWORD=postgres
export TIMESCALE_DB=gridsense
export DBT_PROFILES_DIR=$(pwd)   # points dbt at profiles.yml in this directory
```

## Run

```bash
dbt debug   # verify the connection
dbt run     # build staging (views) -> silver (hypertables) -> gold (marts)
dbt test    # 23 tests: schema tests + business-rule assertions
dbt docs generate && dbt docs serve   # interactive lineage graph, http://localhost:8080
```

Or from the project root, use the Makefile shortcuts:

```bash
make load-timescale   # Bronze Parquet -> bronze.raw_events
make dbt-run
make dbt-test
make dbt-docs
```

## Known, expected test observation

`mart_voltage_quality_hourly` will show bus 0 at a 100% "violation"
rate against the ANSI C84.1 operational tolerance (0.95-1.05 pu). This
is correct, not a bug: bus 0 is the case14 network's slack bus, fixed
at its 1.06 pu setpoint by definition. It's a good sanity check that
the pipeline is calibrated correctly — a slack bus reading exactly its
setpoint, every step, is exactly what should happen.

## Project layout

```
transform/
├── dbt_project.yml
├── profiles.yml           # env-var driven, safe to commit (no secrets)
├── requirements.txt
├── macros/
│   └── make_hypertable.sql
├── models/
│   ├── staging/
│   │   ├── _sources.yml
│   │   ├── _staging.yml
│   │   └── stg_grid_telemetry.sql
│   ├── silver/
│   │   ├── _silver.yml
│   │   ├── fct_bus_voltage.sql
│   │   ├── fct_line_loading.sql
│   │   ├── fct_gen_power.sql
│   │   └── fct_grid_summary.sql
│   └── gold/
│       ├── _gold.yml
│       ├── mart_voltage_quality_hourly.sql
│       ├── mart_line_loading_hourly.sql
│       └── mart_grid_kpis_daily.sql
├── tests/
│   ├── assert_voltage_within_physical_limits.sql
│   ├── assert_no_negative_loading.sql
│   └── assert_power_balance_reconciles.sql
└── seeds/
```

## What's next (Phase 4)

Grafana dashboards reading directly from the Gold marts, plus a
FastAPI read-only API for programmatic access to the same tables.
