# GridSense Simulator — Simulation Engine (Phase 1)

> This is the simulator sub-project. For the streaming/ingestion layer
> (Phase 2), see the [root README](../README.md).

This is the first runnable piece of the GridSense Simulator project: a
Python engine that loads an IEEE test power network, applies synthetic
load/renewable profiles and contingency events, runs an AC power flow
at every time step, and emits telemetry records — locally, with no
external infrastructure (no Kafka, no database, no cloud) required.

Everything here is meant to be run and inspected on your own machine
before we plug in streaming, storage, dashboards, and ML on top of it.

## What it does

- Loads an IEEE test network (14/39/57/118-bus) via `pandapower`.
- Applies a synthetic daily load curve with noise.
- Applies synthetic solar/wind output curves (used once renewable
  static generators are added to the network — safe no-op otherwise).
- Runs power flow (`pandapower.runpp`) at each simulated step.
- Optionally triggers scheduled contingencies (e.g. a line outage).
- Publishes each step's telemetry (bus voltages, line loading,
  generation, alerts) to the console and/or a local JSON Lines file.

## Requirements

- Python 3.10+
- pip

## Setup

```bash
cd simulator
python -m venv .venv
source .venv/bin/activate   # on Windows: .venv\Scripts\activate
pip install -e ".[dev]"
```

## Run the tests

```bash
pytest -v
```

You should see 6 passing tests covering the load/solar profiles, the
main simulation loop, contingency handling, and the file publisher.

## Run the simulator locally

Print telemetry to your terminal:

```bash
python -m gridsense_sim.cli --network case14 --steps 50 --console -v
```

Write telemetry to a local file instead (or in addition):

```bash
python -m gridsense_sim.cli --network case14 --steps 200 --output telemetry.jsonl
```

Inspect the output afterwards, e.g. with pandas:

```python
import pandas as pd
df = pd.read_json("telemetry.jsonl", lines=True)
df[["step", "total_load_mw", "total_generation_mw"]].head()
```

Or with `jq`:

```bash
jq '.total_load_mw' telemetry.jsonl
```

### CLI options

| Flag | Description | Default |
|------|-------------|---------|
| `--network` | IEEE test case: `case14`, `case39`, `case57`, `case118` | `case14` |
| `--steps` | Number of simulation steps to run | `100` |
| `--step-seconds` | Simulated seconds per step | `5` |
| `--output` | Path to a JSON Lines output file | none |
| `--console` | Also print to stdout | on if `--output` is not set |
| `--seed` | Random seed for reproducible profiles | `42` |
| `-v` | Verbose (INFO-level) logging | off |

## Project layout

```
simulator/
├── gridsense_sim/
│   ├── engine.py       # Main simulation loop (power flow + telemetry)
│   ├── profiles.py     # Synthetic load / solar / wind curve generators
│   ├── scenarios.py    # Contingency (N-1/N-2) event definitions
│   ├── publisher.py    # Console + JSONL publishers (Kafka-ready interface)
│   └── cli.py          # Command-line entry point
├── tests/
│   └── test_engine.py
└── pyproject.toml
```

## Streaming to Kafka (Phase 2)

The engine can now also publish to Kafka via `KafkaPublisher`
(`gridsense_sim/kafka_publisher.py`), which implements the exact same
`TelemetryPublisher` interface as the console/file publishers — the
simulation engine code did not change at all.

```bash
pip install -e ".[kafka]"
python -m gridsense_sim.cli --network case14 --steps 200 --kafka -v
```

This requires a Kafka broker reachable at `localhost:9092`. See the
[root README](../README.md) for the free, local Docker Compose setup
and the rest of the Phase 2 ingestion pipeline.

## What's next

3. **dbt models** (bronze/silver/gold) and **TimescaleDB** for storage.
4. **Grafana** dashboards and the **FastAPI** read API.
5. Real datasets (ONS load curves, NREL NSRDB solar, WIND Toolkit)
   replacing the synthetic profiles.
