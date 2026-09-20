# GridSense Simulator

Electrical Grid Digital Twin & Data Platform. This monorepo is built
incrementally, phase by phase, always runnable locally before any
component is deployed anywhere else.

Every tool used in this repository is free and open-source and runs
entirely on your own machine (Docker containers + local Python
processes). No cloud account, managed service, or paid license is
required at any phase.

## Phases

- **✓ Phase 1 — Simulation engine** (`simulator/`): loads an IEEE test
  power network, applies synthetic load/renewable profiles and
  contingencies, runs power flow, and emits telemetry. See
  [`simulator/README.md`](simulator/README.md).

- **✓ Phase 2 — Streaming & Bronze ingestion** (`ingestion/`): a local
  Kafka broker receives telemetry from the simulator, and a Python
  consumer lands it as a partitioned Parquet "Bronze" layer on disk.

- **✓ Phase 3 — Transform layer** (`transform/`): Bronze Parquet is
  bulk-loaded into TimescaleDB, then a dbt project builds typed,
  unnested Silver fact tables and pre-aggregated Gold marts, backed
  by automated data-quality tests. See
  [`transform/README.md`](transform/README.md).

- **✓ Phase 4 — API & Dashboards** (`api/`, `grafana/`): a FastAPI
  read layer serves the Gold marts over HTTP (typed, tested,
  auto-documented), and pre-provisioned Grafana dashboards
  visualize them. See [`api/README.md`](api/README.md) and
  [`grafana/README.md`](grafana/README.md).

- **✓ Phase 5 — Network migration** (`simulator/`): the simulator now
  also supports `cigre_lv`, a real 44-bus low-voltage distribution
  feeder (CIGRE Task Force C6.04.02), alongside the original IEEE
  transmission test cases (`case14`/`39`/`57`/`118`). This is the
  network the hosting-capacity work in Phase 6 runs against a
  distribution feeder, not a transmission network.

- **✓ Phase 6 — Hosting capacity methodologies**
  (`simulator/gridsense_sim/hosting_capacity/`): the three classic
  hosting-capacity methods deterministic (bisection), stochastic
  (Monte Carlo), and QSTS (quasi-static time series) implemented,
  compared side by side in a dbt mart, served over the API, and
  visualized in a third Grafana dashboard.

- **Phase 7+ (planned)** — real datasets (ONS, NREL NSRDB/WIND
  Toolkit, SONDA, BDGD) replacing synthetic profiles; a streaming
  Dynamic Operating Envelope prototype on top of the existing Kafka
  pipeline.

## Architecture

```
simulator            bronze_consumer          load_bronze_to_timescale.py
┌───────────┐  Kafka  ┌────────────────┐ bulk  ┌───────────────────┐
│pandapower  │ topics │ Kafka -> Parquet│ COPY  │ idempotent upsert  │
│KafkaPublish├───────▶│ (Bronze)        ├──────▶│ bronze.raw_events   │
└───────────┘         └───────┬────────┘        │ (TimescaleDB       │
                               │                 │  hypertable)       │
                               ▼                 └─────────┬─────────┘
                    data/bronze/*.parquet                  │  dbt
                    (queryable with DuckDB,                ▼
                     no server needed)          staging → silver → gold
                                                 (typed, unnested, tested)
                                                            │
                                        ┌───────────────────┼───────────────────┐
                                        ▼                                       ▼
                              FastAPI (api/)                          Grafana (grafana/)
                              typed, tested REST                      3 auto-provisioned
                              endpoints over Gold                     dashboards over Gold
                              http://localhost:8000/docs               http://localhost:3000

hosting_capacity/ (Phase 6) ─── run_hosting_capacity_study.py ──▶ data/hosting_capacity/*.parquet
(deterministic/stochastic/qsts,                                            │
 run against cigre_lv or any                                               ▼ load_hosting_capacity_to_timescale.py
 other SUPPORTED_NETWORKS entry)                          bronze.hosting_capacity_results (plain table,
                                                            not a hypertable one-off study results,
                                                            not streamed telemetry)
                                                                            │  dbt
                                                                            ▼
                                                   stg_hosting_capacity_results → mart_hosting_capacity
                                                            (same staging → gold path as telemetry,
                                                             feeding the same FastAPI + Grafana above)
```

Kafka topics used:
- `grid.telemetry.raw` — one record per simulated power-flow step.
- `grid.events.alerts` — contingencies and non-convergence events.

The Bronze layer follows a **schema-on-read** pattern: each record
stores the raw JSON payload untouched, plus ingestion metadata (topic,
Kafka partition/offset, ingestion timestamp). Typed parsing,
unnesting, and data-quality checks all happen downstream in the dbt
transform layer this keeps ingestion simple and lossless, and means
a schema change in the simulator only ever touches one staging model.
The API and Grafana dashboards both read exclusively from the Gold
layer, never from Bronze/Silver directly one boundary, one place to
change if the warehouse schema evolves.

Hosting-capacity study results follow the same Bronze-pattern
philosophy (raw JSON payload + run metadata, typed downstream in dbt)
even though they don't arrive via Kafka they're one-off study runs,
not streamed telemetry, so they get a plain table
(`bronze.hosting_capacity_results`) instead of a hypertable, but the
same staging → gold path and the same API/Grafana consumers apply
once they land.

## Requirements

- Docker + Docker Compose (Kafka, TimescaleDB, Grafana all free, run locally)
- Python 3.10+

## Setup

```bash
# 1. Create a Python virtual environment
python -m venv .venv
source .venv/bin/activate   # on Windows: .venv\Scripts\Activate.ps1

# 2. Install dependencies
cd simulator && pip install -e ".[dev,kafka]" && cd ..
pip install -r ingestion/requirements.txt
pip install -r transform/requirements.txt
pip install -r api/requirements.txt

# 3. (Optional) override default credentials
# Every credential below has a working default baked into
# docker-compose.yml (${VAR:-default} interpolation) you only need
# this step if you want something different from postgres/postgres
# and admin/admin. Copy .env.example to .env (gitignored) and edit it;
# .env is picked up automatically by `docker compose`.
cp .env.example .env

# 4. Start local Kafka + TimescaleDB + Grafana
docker compose up -d
# Kafka:       localhost:9092
# Kafka UI:    http://localhost:8080  (browse topics/messages visually)
# TimescaleDB: localhost:5432 (db=gridsense, user=postgres, password=postgres)
# Grafana:     http://localhost:3000  (user=admin, password=admin)
```

## Run the full pipeline

### Using Make (Linux/Mac/Git Bash)

```bash
make up                 # Start Kafka + TimescaleDB + Grafana
make produce              # Simulator -> Kafka
make consume-bronze         # Kafka -> Parquet (Bronze)
make load-timescale            # Bronze Parquet -> TimescaleDB
make dbt-run                      # Build Silver + Gold dbt models
make dbt-test                        # Run the dbt data-quality tests
make api-run                            # Serve the Gold layer over HTTP
make down                                  # Stop everything
```

### Manual, step by step (any OS)

**1. Produce telemetry into Kafka:**

```bash
python -m gridsense_sim.cli \
  --network case14 --steps 200 --kafka \
  --kafka-bootstrap-servers localhost:9092 -v
```

`--network` also accepts `cigre_lv` (Phase 5's 44-bus LV distribution
feeder) alongside the transmission test cases.

**2. Consume from Kafka, land it as Parquet (Bronze):**

```bash
python ingestion/bronze_consumer.py \
  --bootstrap-servers localhost:9092 \
  --output-dir data/bronze -v
```

**3. Load Bronze into TimescaleDB:**

```bash
python ingestion/load_bronze_to_timescale.py \
  --bronze-dir data/bronze \
  --db-url postgresql://postgres:postgres@localhost:5432/gridsense -v
```

Idempotent: safe to re-run after a partial consumer run.

**4. Build and test the dbt models:**

```bash
cd transform
export DBT_PROFILES_DIR=$(pwd)
dbt seed   # loads transform/seeds/network_voltage_limits.csv
dbt run
dbt test
```

**5. Serve the Gold layer over HTTP:**

```bash
cd api
uvicorn app.main:app --reload --port 8000
```

Open http://localhost:8000/docs for interactive API docs.

**6. View the dashboards:**

Open http://localhost:3000 three dashboards are already there,
pre-loaded and pointed at the same Gold tables:
**GridSense Overview**, **GridSense Presentation**, and
**GridSense Hosting Capacity**.

### Running a hosting-capacity study (Phase 6)

Independent of the telemetry pipeline above this runs the three
methodologies against a network and pushes the comparison into the
same Gold layer the API and dashboard #3 read from:

```bash
# Run the three methods against cigre_lv (writes Parquet)
python scripts/run_hosting_capacity_study.py \
  --network cigre_lv --methods deterministic stochastic qsts -v

# QSTS is by far the most expensive: it defaults to a 60-day horizon
# (up to ~350k power flows for one answer see hosting_capacity/
# qsts.py's module docstring). Pass --qsts-total-steps to control it,
# e.g. --qsts-total-steps 2016 for a 7-day horizon during iteration.

# Load the results into TimescaleDB (idempotent)
python ingestion/load_hosting_capacity_to_timescale.py \
  --results-dir data/hosting_capacity -v

# Propagate through dbt and refresh the dashboard
cd transform && dbt run && dbt test && cd ..
docker compose restart grafana
```

Read the `stochastic` method's numbers with care: at the default
Monte Carlo sampling range, they're a lower bound under an arbitrary
PV-size budget, not yet a ceiling comparable to the
deterministic/QSTS results see
`simulator/gridsense_sim/hosting_capacity/stochastic.py`'s module
docstring.

## Verify

**Bronze (Parquet, via DuckDB so no server needed):**

```python
import duckdb
con = duckdb.connect()
con.sql("""
    SELECT raw_value::JSON->>'step' AS step,
           (raw_value::JSON->>'total_load_mw')::DOUBLE AS total_load_mw
    FROM read_parquet('data/bronze/**/*.parquet')
    ORDER BY step::INT
    LIMIT 10
""").show()
```

**Gold (TimescaleDB, via psql or any SQL client):**

```sql
SELECT * FROM gold.mart_grid_kpis_daily;
SELECT * FROM gold.mart_hosting_capacity;
```

**API (curl or browser):**

```bash
curl http://localhost:8000/api/v1/kpis/daily
curl "http://localhost:8000/api/v1/hosting-capacity/compare?network=cigre_lv"
```

## Tests

```bash
make test                  # simulator + ingestion + API unit tests (mocked, no Docker needed)
make dbt-test                # dbt data-quality tests (needs TimescaleDB running)
```

The simulator, ingestion consumer, and API are all unit tested with
fake dependency doubles (Kafka producer/consumer, DB session), so
those suites run without Docker including the hosting-capacity
methods (`simulator/tests/test_hosting_capacity.py`) and the
`/api/v1/hosting-capacity/compare` endpoint
(`api/tests/test_hosting_capacity.py`). The dbt test suite needs a
live database since it validates real data, and includes network-
aware voltage-limit checks and a hosting-capacity sanity test
(hosting capacity can't be physically negative) alongside the
original Phase 3 tests.

## Shutting down

```bash
make down                  # stop containers, keep data volumes
docker compose down -v     # also wipe Kafka, TimescaleDB, and Grafana data
```

## Repository layout

```
gridsense-simulator/
├── docker-compose.yml               # Kafka + TimescaleDB + Grafana (+ optional API)
├── .env.example                     # Overridable credentials, copy to .env to use
├── Makefile                         # Shortcuts for every command above
├── simulator/                       # Phase 1 & 5: simulation engine (+ Kafka publisher)
│   ├── gridsense_sim/
│   │   ├── hosting_capacity/        # Phase 6: deterministic/stochastic/qsts + shared
│   │   │                            # limits.py, violations.py, allocation.py
│   │   ├── engine.py                # SUPPORTED_NETWORKS incl. cigre_lv (Phase 5)
│   │   └── ...
│   └── tests/
├── scripts/
│   └── run_hosting_capacity_study.py  # Phase 6: runs the 3 methods, writes Parquet
├── ingestion/                        # Phase 2 & 3: Kafka -> Parquet -> TimescaleDB
│   ├── bronze_consumer.py
│   ├── load_bronze_to_timescale.py
│   ├── load_hosting_capacity_to_timescale.py  # Phase 6
│   └── tests/
├── transform/                        # Phase 3 & 6: dbt project (Silver/Gold)
│   ├── seeds/
│   │   └── network_voltage_limits.csv  # Phase 6: per-network voltage tolerance
│   ├── models/{staging,silver,gold}/   # incl. stg/mart_hosting_capacity (Phase 6)
│   ├── tests/
│   └── README.md
├── api/                                 # Phase 4 & 6: FastAPI read layer
│   ├── app/
│   │   └── routers/hosting_capacity.py    # Phase 6
│   ├── tests/
│   └── README.md
├── grafana/                                # Phase 4 & 6: dashboards
│   ├── provisioning/
│   ├── dashboards/                            # 3 dashboards, incl.
│   │                                          # gridsense-hosting-capacity.json
│   └── README.md
└── data/
    ├── bronze/                                # Generated Parquet lake (gitignored)
    └── hosting_capacity/                      # Phase 6 study results (gitignored)
```
