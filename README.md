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
  by 23 automated data-quality tests. See
  [`transform/README.md`](transform/README.md).

- **✓ Phase 4 — API & Dashboards** (`api/`, `grafana/`): a FastAPI
  read layer serves the Gold marts over HTTP (typed, tested,
  auto-documented), and a pre-provisioned Grafana dashboard
  visualizes them. See [`api/README.md`](api/README.md) and
  [`grafana/README.md`](grafana/README.md).

- **Phase 5 (planned)** — Real datasets (ONS, NREL NSRDB/WIND Toolkit)
  replacing synthetic profiles; ML models (forecasting, anomaly
  detection).

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
                              typed, tested REST                      auto-provisioned
                              endpoints over Gold                     dashboard over Gold
                              http://localhost:8000/docs               http://localhost:3000
```

Kafka topics used:
- `grid.telemetry.raw` — one record per simulated power-flow step.
- `grid.events.alerts` — contingencies and non-convergence events.

The Bronze layer follows a **schema-on-read** pattern: each record
stores the raw JSON payload untouched, plus ingestion metadata (topic,
Kafka partition/offset, ingestion timestamp). Typed parsing,
unnesting, and data-quality checks all happen downstream in the dbt
transform layer — this keeps ingestion simple and lossless, and means
a schema change in the simulator only ever touches one staging model.
The API and Grafana dashboard both read exclusively from the Gold
layer, never from Bronze/Silver directly — one boundary, one place to
change if the warehouse schema evolves.

## Requirements

- Docker + Docker Compose (Kafka, TimescaleDB, Grafana — all free, run locally)
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

# 3. Start local Kafka + TimescaleDB + Grafana
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
make dbt-test                        # Run 23 data-quality tests
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
dbt run
dbt test
```

**5. Serve the Gold layer over HTTP:**

```bash
cd api
uvicorn app.main:app --reload --port 8000
```

Open http://localhost:8000/docs for interactive API docs.

**6. View the dashboard:**

Open http://localhost:3000 — the **GridSense Overview** dashboard is
already there, pre-loaded, pointed at the same Gold tables.

## Verify

**Bronze (Parquet, via DuckDB — no server needed):**

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
```

**API (curl or browser):**

```bash
curl http://localhost:8000/api/v1/kpis/daily
```

## Tests

```bash
make test                  # simulator + ingestion + API unit tests (mocked, no Docker needed)
make dbt-test                # 23 dbt data-quality tests (needs TimescaleDB running)
```

The simulator, ingestion consumer, and API are all unit tested with
fake dependency doubles (Kafka producer/consumer, DB session), so
those suites run without Docker. The dbt test suite needs a live
database since it validates real data.

## Shutting down

```bash
make down                  # stop containers, keep data volumes
docker compose down -v     # also wipe Kafka, TimescaleDB, and Grafana data
```

## Repository layout

```
gridsense-simulator/
├── docker-compose.yml        # Kafka + TimescaleDB + Grafana (+ optional API)
├── Makefile                    # Shortcuts for every command above
├── simulator/                    # Phase 1: simulation engine (+ Kafka publisher)
│   ├── gridsense_sim/
│   └── tests/
├── ingestion/                      # Phase 2 & 3: Kafka -> Parquet -> TimescaleDB
│   ├── bronze_consumer.py
│   ├── load_bronze_to_timescale.py
│   └── tests/
├── transform/                        # Phase 3: dbt project (Silver/Gold)
│   ├── models/{staging,silver,gold}/
│   ├── tests/
│   └── README.md
├── api/                                 # Phase 4: FastAPI read layer
│   ├── app/
│   ├── tests/
│   └── README.md
├── grafana/                                # Phase 4: dashboards
│   ├── provisioning/
│   ├── dashboards/
│   └── README.md
└── data/
    └── bronze/                               # Generated Parquet lake (gitignored)
```
