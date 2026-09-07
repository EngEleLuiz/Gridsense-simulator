"""Command-line entry point to run the simulator locally.

Example:
    python -m gridsense_sim.cli --network case14 --steps 200 \\
        --output telemetry.jsonl --console
"""

from __future__ import annotations

import argparse
import logging

from .engine import GridSimulationEngine, SimulationConfig
from .publisher import ConsolePublisher, JSONLFilePublisher, MultiPublisher, TelemetryPublisher


def build_publisher(
    output: str | None,
    console: bool,
    kafka: bool,
    kafka_bootstrap_servers: str,
) -> TelemetryPublisher:
    publishers: list[TelemetryPublisher] = []
    if console or (output is None and not kafka):
        publishers.append(ConsolePublisher())
    if output:
        publishers.append(JSONLFilePublisher(output))
    if kafka:
        # Imported lazily so the base CLI works without confluent-kafka
        # installed when --kafka is not requested.
        from .kafka_publisher import KafkaPublisher

        publishers.append(KafkaPublisher(bootstrap_servers=kafka_bootstrap_servers))
    return publishers[0] if len(publishers) == 1 else MultiPublisher(publishers)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the GridSense grid simulator locally.")
    parser.add_argument(
        "--network",
        default="case14",
        choices=["case14", "case39", "case57", "case118"],
        help="IEEE test network to simulate (default: case14).",
    )
    parser.add_argument("--steps", type=int, default=100, help="Number of simulation steps to run.")
    parser.add_argument(
        "--step-seconds", type=int, default=5, help="Simulated seconds represented by each step."
    )
    parser.add_argument("--output", default=None, help="Path to a JSON Lines file for telemetry output.")
    parser.add_argument(
        "--console",
        action="store_true",
        help="Also print telemetry to stdout (default on if neither --output nor --kafka is set).",
    )
    parser.add_argument(
        "--kafka",
        action="store_true",
        help="Publish telemetry to Kafka (requires a broker; see docker-compose.yml).",
    )
    parser.add_argument(
        "--kafka-bootstrap-servers",
        default="localhost:9092",
        help="Kafka bootstrap servers (default: localhost:9092).",
    )
    parser.add_argument("--seed", type=int, default=42, help="Random seed for reproducible profiles.")
    parser.add_argument("-v", "--verbose", action="store_true", help="Enable INFO-level logging.")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    config = SimulationConfig(
        network_name=args.network,
        step_seconds=args.step_seconds,
        total_steps=args.steps,
        seed=args.seed,
    )
    publisher = build_publisher(args.output, args.console, args.kafka, args.kafka_bootstrap_servers)
    engine = GridSimulationEngine(config, publisher)
    engine.run()


if __name__ == "__main__":
    main()
