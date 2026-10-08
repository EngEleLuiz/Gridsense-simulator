# Convenience shortcuts for the local, zero-cost dev workflow.
# All commands run entirely on your machine (Docker containers + local
# Python processes) -- no cloud account or paid service involved.
# On Windows, run these from Git Bash (or use the manual commands in README.md).

PYTHON ?= python
DB_URL ?= postgresql://postgres:postgres@localhost:5432/gridsense
HC_DIR ?= data/hosting_capacity

DBT_ENV := DBT_PROFILES_DIR=transform \
	TIMESCALE_HOST=localhost TIMESCALE_PORT=5432 \
	TIMESCALE_USER=postgres TIMESCALE_PASSWORD=postgres TIMESCALE_DB=gridsense
DBT := $(DBT_ENV) dbt --no-use-colors
DBT_ARGS := --project-dir transform --profiles-dir transform

.PHONY: help up down logs status produce consume-bronze query-bronze \
        load-timescale seed-scope dbt-seed dbt-run dbt-test dbt-build dbt-docs \
        hc-study hc-qsts hc-load api-run \
        data-list data-qc series-flo hc-qsts-real test-datasets test-real-data \
        test test-simulator test-hc test-ingestion test-api lint

help:
	@grep -E '^[a-z-]+:.*?## ' $(MAKEFILE_LIST) | awk -F':.*?## ' '{printf "  %-16s %s\n", $$1, $$2}'

# ---------------------------------------------------------------- stack
up: ## Start Kafka + TimescaleDB + Grafana
	docker compose up -d
	@echo "Kafka:        localhost:9092"
	@echo "Kafka UI:     http://localhost:8080"
	@echo "TimescaleDB:  localhost:5432 (db=gridsense, user=postgres, password=postgres)"
	@echo "Grafana:      http://localhost:$${GRAFANA_PORT:-3001} (user=admin, password=admin)"

down: ## Stop containers (keeps volumes)
	docker compose down

logs: ## Follow container logs
	docker compose logs -f

status: ## Show container status
	docker compose ps

# ------------------------------------------------------------ telemetry
produce: ## Simulator -> Kafka
	cd simulator && $(PYTHON) -m gridsense_sim.cli \
		--network case14 --steps 200 --kafka \
		--kafka-bootstrap-servers localhost:9092 -v

consume-bronze: ## Kafka -> Bronze Parquet
	$(PYTHON) ingestion/bronze_consumer.py \
		--bootstrap-servers localhost:9092 \
		--output-dir data/bronze -v

query-bronze: ## Count Bronze rows per topic (DuckDB, no server)
	$(PYTHON) -c "import duckdb; \
		print(duckdb.sql(\"SELECT topic, count(*) AS n FROM read_parquet('data/bronze/**/*.parquet') GROUP BY topic\"))"

load-timescale: ## Bronze Parquet -> bronze.raw_events (idempotent)
	$(PYTHON) ingestion/load_bronze_to_timescale.py \
		--bronze-dir data/bronze --db-url $(DB_URL) -v

# ------------------------------------------------------------------ dbt
seed-scope: ## Regenerate transform/seeds/network_voltage_scope.csv from hosting_capacity/scope.py
	$(PYTHON) scripts/generate_voltage_scope_seed.py

dbt-seed: ## Load seeds (network_voltage_limits.csv)
	$(DBT) seed $(DBT_ARGS)

dbt-run: dbt-seed ## Seed + build Silver and Gold models
	$(DBT) run $(DBT_ARGS)

dbt-test: ## dbt schema + business-rule tests
	$(DBT) test $(DBT_ARGS)

dbt-build: ## Seed + run + test in dependency order (what CI runs)
	$(DBT) build $(DBT_ARGS)

dbt-docs: ## Generate and serve dbt docs on :8081
	$(DBT) docs generate $(DBT_ARGS)
	$(DBT) docs serve $(DBT_ARGS) --port 8081

# ----------------------------------------------------- hosting capacity
hc-study: ## Deterministic + stochastic HC on cigre_lv under PRODIST (minutes)
	$(PYTHON) scripts/run_hosting_capacity_study.py --network cigre_lv \
		--framework prodist_m8_bt --methods deterministic stochastic \
		--output-dir $(HC_DIR) -v

hc-qsts: ## QSTS, 7 days at 5 min, load profile normalized to peak 1.0 (see R22)
	$(PYTHON) scripts/run_hosting_capacity_study.py --network cigre_lv \
		--methods qsts --qsts-total-steps 2016 --qsts-peak-load 1.0 \
		--output-dir $(HC_DIR) -v

hc-load: ## HC Parquet -> bronze.hosting_capacity_results (idempotent)
	$(PYTHON) ingestion/load_hosting_capacity_to_timescale.py \
		--results-dir $(HC_DIR) --db-url $(DB_URL) -v

# ------------------------------------------------------- real data (Phase 7)
SERIES_DIR ?= data/series
FLO_SERIES ?= $(SERIES_DIR)/flo_ausgrid_inmet_2023-01.parquet

data-list: ## List the six registered real datasets
	gridsense-data list

data-qc: ## Quality report for INMET A806 (Florianopolis) 2023 (downloads ~100 MB once)
	gridsense-data qc inmet --opt station=A806 --opt year=2023

series-flo: ## Real QSTS series: Ausgrid homes + INMET A806, 28 days of Jan 2023 at 15 min
	gridsense-data build-series --load ausgrid --pv inmet \
		--pv-opt station=A806 --pv-opt year=2023 --site florianopolis \
		--start 2023-01-09 --days 28 --steps-per-day 96 --out $(FLO_SERIES)

hc-qsts-real: ## QSTS on the real Florianopolis series (run series-flo first)
	$(PYTHON) scripts/run_hosting_capacity_study.py --network cigre_lv \
		--methods qsts --qsts-series $(FLO_SERIES) --output-dir $(HC_DIR) -v

# ------------------------------------------------------------------ api
api-run: ## Serve the Gold layer on :8000 (docs at /docs)
	cd api && \
	TIMESCALE_HOST=localhost TIMESCALE_PORT=5432 \
	TIMESCALE_USER=postgres TIMESCALE_PASSWORD=postgres TIMESCALE_DB=gridsense \
	uvicorn app.main:app --reload --port 8000

# ---------------------------------------------------------------- tests
test-simulator: ## All simulator tests
	$(PYTHON) -m pytest simulator/tests -q -ra

test-hc: ## Hosting-capacity tests only (~40-80 s)
	$(PYTHON) -m pytest simulator/tests/test_hc_*.py simulator/tests/test_study_runner.py -q -ra

test-datasets: ## Phase 7 dataset tests (fixtures; offline)
	$(PYTHON) -m pytest simulator/tests/test_datasets_*.py -q -ra

test-real-data: ## Same checks on genuine raw files in $$GRIDSENSE_REAL_DATA_DIR
	GRIDSENSE_REAL_DATA_DIR=$${GRIDSENSE_REAL_DATA_DIR:-data/raw/real-samples} \
	$(PYTHON) -m pytest simulator/tests/test_datasets_real_files.py -v -ra

test-ingestion: ## Ingestion tests (Kafka mocked)
	cd ingestion && $(PYTHON) -m pytest tests -q -ra

test-api: ## API tests (DB mocked)
	cd api && $(PYTHON) -m pytest -q -ra

test: test-simulator test-ingestion test-api ## Every unit-test suite (no Docker needed)

lint: ## Same ruff checks as CI
	ruff check --select E9,F63,F7,F82 .
	-ruff check --select F .
