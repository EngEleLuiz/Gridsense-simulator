# Convenience shortcuts for the local, zero-cost dev workflow.
# All commands run entirely on your machine (Docker containers + local
# Python processes) — no cloud account or paid service involved.

DBT_ENV := DBT_PROFILES_DIR=transform \
	TIMESCALE_HOST=localhost TIMESCALE_PORT=5432 \
	TIMESCALE_USER=postgres TIMESCALE_PASSWORD=postgres TIMESCALE_DB=gridsense

.PHONY: up down logs status produce consume-bronze query-bronze \
        load-timescale dbt-run dbt-test dbt-docs \
        api-run test-simulator test-ingestion test-api test

up:
	docker compose up -d
	@echo "Kafka:        localhost:9092"
	@echo "Kafka UI:     http://localhost:8080"
	@echo "TimescaleDB:  localhost:5432 (db=gridsense, user=postgres, password=postgres)"
	@echo "Grafana:      http://localhost:3000 (user=admin, password=admin)"

down:
	docker compose down

logs:
	docker compose logs -f

status:
	docker compose ps

# Runs the simulator and streams telemetry into Kafka.
produce:
	cd simulator && python -m gridsense_sim.cli \
		--network case14 --steps 200 --kafka \
		--kafka-bootstrap-servers localhost:9092 -v

# Consumes Kafka topics and lands them as local Parquet (Bronze layer).
consume-bronze:
	python ingestion/bronze_consumer.py \
		--bootstrap-servers localhost:9092 \
		--output-dir data/bronze -v

# Quick sanity check on the Bronze parquet lake using DuckDB (no server).
query-bronze:
	python -c "import duckdb; \
		print(duckdb.sql(\"SELECT topic, count(*) AS n FROM read_parquet('data/bronze/**/*.parquet') GROUP BY topic\"))"

# Loads Bronze Parquet files into the TimescaleDB bronze.raw_events
# hypertable (idempotent: safe to re-run).
load-timescale:
	python ingestion/load_bronze_to_timescale.py \
		--bronze-dir data/bronze \
		--db-url postgresql://postgres:postgres@localhost:5432/gridsense -v

# Builds the Silver and Gold dbt models on top of bronze.raw_events.
dbt-run:
	$(DBT_ENV) dbt run --project-dir transform --profiles-dir transform

# Runs dbt's schema tests (not_null, etc.) and singular business-rule tests.
dbt-test:
	$(DBT_ENV) dbt test --project-dir transform --profiles-dir transform

# Generates and serves dbt's interactive docs / lineage graph.
dbt-docs:
	$(DBT_ENV) dbt docs generate --project-dir transform --profiles-dir transform
	$(DBT_ENV) dbt docs serve --project-dir transform --profiles-dir transform --port 8081

test-simulator:
	cd simulator && python -m pytest -v

test-ingestion:
	cd ingestion && python -m pytest tests/ -v

test-api:
	cd api && python -m pytest -v

test: test-simulator test-ingestion test-api

# Runs the read API locally (not via Docker) for fast iteration.
# Interactive docs at http://localhost:8000/docs
api-run:
	cd api && \
	TIMESCALE_HOST=localhost TIMESCALE_PORT=5432 \
	TIMESCALE_USER=postgres TIMESCALE_PASSWORD=postgres TIMESCALE_DB=gridsense \
	uvicorn app.main:app --reload --port 8000
