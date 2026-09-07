# Convenience shortcuts for the local, zero-cost Phase 2 workflow.
# All commands run entirely on your machine (Docker containers + local
# Python processes) — no cloud account or paid service involved.

.PHONY: up down logs status produce consume-bronze query-bronze test-simulator test-ingestion test

up:
	docker compose up -d
	@echo "Kafka:    localhost:9092"
	@echo "Kafka UI: http://localhost:8080"

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

test-simulator:
	cd simulator && python -m pytest -v

test-ingestion:
	cd ingestion && python -m pytest tests/ -v

test: test-simulator test-ingestion
