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
  
- **✓ Phase 2 — Streaming & Bronze ingestion** (ready to run): a local
  Kafka broker receives telemetry from the simulator, and a Python
  consumer lands it as a partitioned Parquet "Bronze" layer on disk.
  See [`PHASE2_OVERVIEW.md`](PHASE2_OVERVIEW.md) for complete guide or
  [`PHASE2_QUICKSTART.md`](PHASE2_QUICKSTART.md) for TL;DR.
  
- **Phase 3 (planned)** — dbt models (Silver/Gold) + TimescaleDB.
- **Phase 4 (planned)** — Grafana dashboards + FastAPI read API.
- **Phase 5 (planned)** — Real datasets (ONS, NREL NSRDB/WIND Toolkit)
  replacing synthetic profiles; ML models (forecasting, anomaly
  detection).

## Phase 2 — Architecture

```
 simulator (Python)                bronze_consumer (Python)
 ┌───────────────────┐   Kafka     ┌──────────────────────┐
 │ pandapower engine  │  topics    │  reads topics         │
 │ KafkaPublisher     ├───────────▶│  writes local Parquet │
 └───────────────────┘             └──────────┬───────────┘
                                               │
                                               ▼
                                    data/bronze/topic=.../date=.../*.parquet
                                    (queryable directly with DuckDB —
                                     no database server needed)
```

Kafka topics used:
- `grid.telemetry.raw` — one record per simulated power-flow step.
- `grid.events.alerts` — contingencies and non-convergence events.

The Bronze layer follows a **schema-on-read** pattern: each Parquet
row stores the raw JSON payload untouched, plus ingestion metadata
(topic, Kafka partition/offset, ingestion timestamp). Typed parsing
and data-quality checks are deferred to the Silver layer (dbt),
which is a later phase — this keeps the ingestion path simple and
lossless.

## Requirements

- Docker + Docker Compose (for Kafka — free, runs locally)
- Python 3.10+

## Quick Start

### Phase 2 — Automated Setup (Windows PowerShell)

```powershell
# One-line setup (installs deps + starts Kafka)
powershell -ExecutionPolicy Bypass -File scripts/setup_phase2.ps1
```

### Or Step-by-Step

```bash
# 1. Create Python virtual environment
python -m venv .venv
source .venv/bin/activate   # on Windows: .venv\Scripts\Activate.ps1

# 2. Install dependencies
cd simulator && pip install -e ".[dev,kafka]" && cd ..
pip install -r ingestion/requirements.txt

# 3. Create data directories
mkdir -p data/bronze data/raw

# 4. Start local Kafka (KRaft mode, single broker) + Kafka UI
docker compose up -d
# Kafka:    localhost:9092
# Kafka UI: http://localhost:8080  (browse topics/messages visually)
```

## Run Phase 2 End-to-End

### Using Make (Linux/Mac/Git Bash)

```bash
make up                    # Start Kafka
make produce               # Terminal 1: Simulator → Kafka
make consume-bronze        # Terminal 2: Kafka → Parquet
make query-bronze          # Terminal 3: Verify with DuckDB
make down                  # Stop everything
```

### Manual (Windows PowerShell / Any OS)

**Terminal 1 — Produce telemetry into Kafka:**

```powershell
python -m gridsense_sim.cli \
  --network case14 \
  --steps 200 \
  --kafka \
  --kafka-bootstrap-servers localhost:9092 \
  -v
```

**Terminal 2 — Consume from Kafka and land it as Parquet:**

```powershell
python ingestion/bronze_consumer.py \
  --bootstrap-servers localhost:9092 \
  --output-dir data/bronze \
  -v
```

Stop the consumer with Ctrl+C once the producer finishes — it flushes
any buffered records before exiting, so nothing is lost.

**Terminal 3 — View Kafka UI (optional):**

Open browser to http://localhost:8080

## Verify the Bronze layer

No database server needed — query the Parquet files directly with
[DuckDB](https://duckdb.org/) (free, embedded, zero setup):

```bash
pip install duckdb
make query-bronze
```

Or interactively:

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

You can also open http://localhost:8080 (Kafka UI) while the producer
is running to watch messages flow through the topics in real time.

## Tests

Both the simulator and the ingestion consumer are unit tested with
fake Kafka producer/consumer doubles, so the test suite runs without
Docker or a real broker:

```bash
make test
# or individually:
make test-simulator
make test-ingestion
```

## Shutting down

```bash
make down          # stop containers, keep the Kafka data volume
docker compose down -v   # also wipe local Kafka data
```

## Repository layout

```
gridsense-simulator/
├── docker-compose.yml     # Local Kafka + Kafka UI (free, self-hosted)
├── Makefile                # Shortcuts for the commands above
├── simulator/               # Phase 1: simulation engine (+ Kafka publisher)
│   ├── gridsense_sim/
│   └── tests/
├── ingestion/               # Phase 2: Kafka -> Parquet bronze consumer
│   ├── bronze_consumer.py
│   └── tests/
└── data/
    └── bronze/              # Generated Parquet lake (gitignored)
```
