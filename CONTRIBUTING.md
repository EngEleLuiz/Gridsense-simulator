# Contributing to GridSense Simulator

This is a learning/portfolio project, but we follow professional
development practices. Here's how to work on it.

## Development Setup

```bash
# Clone the repo
git clone https://github.com/YOUR_USERNAME/gridsense-simulator.git
cd gridsense-simulator

# Create a feature branch
git checkout -b feature/your-feature-name

# Create a Python virtual environment
python -m venv .venv
source .venv/bin/activate   # on Windows: .venv\Scripts\activate

# Install dependencies for both simulator and ingestion
cd simulator && pip install -e ".[dev,kafka]" && cd ..
pip install -r ingestion/requirements.txt
```

## Coding Standards

- **Python version:** 3.10+
- **Code style:** PEP 8 (use tools like `black`, `flake8` if you like)
- **Type hints:** Use `from __future__ import annotations` and type all
  function signatures (demonstrates senior-level practices)
- **Docstrings:** Module-level docstrings on all modules; function
  docstrings on public APIs
- **Tests:** Write unit tests for new features (use `pytest`)

## Making Changes

### 1. Work on your feature branch

```bash
git checkout -b feature/add-solar-curtailment-logic
# ... make changes ...
git add .
git commit -m "Add solar curtailment detection in profiles

- Detect when solar output exceeds grid capacity
- Add curtailment_percent field to telemetry records
- Tests: test_solar_curtailment_at_capacity"
```

Good commit messages follow this pattern:
```
Short summary (50 chars max)

Longer explanation of what changed and why. Wrap at 72 chars.
- Bullet points are fine
- Mention any related issues or PRs
```

### 2. Run tests before pushing

```bash
make test           # run all tests
make test-simulator # only simulator tests
make test-ingestion # only ingestion tests
```

All tests must pass. If you add a feature, add a test too.

### 3. Push and open a Pull Request

```bash
git push -u origin feature/add-solar-curtailment-logic
```

Then on GitHub:
1. Go to your fork
2. Click "Compare & pull request"
3. Write a clear description of what changed and why
4. Review the changes yourself (look for typos, logic errors, etc.)
5. Merge once you're happy

## Project Structure

```
simulator/                 # Phase 1: Simulation engine
  gridsense_sim/
    engine.py             # Main simulation loop
    profiles.py           # Load/solar/wind curve generators
    scenarios.py          # Contingency definitions
    publisher.py          # Base publisher interface
    kafka_publisher.py    # Kafka implementation
    cli.py                # Command-line entry point
  tests/
    test_engine.py        # Unit tests for engine
    test_kafka_publisher.py

ingestion/                 # Phase 2: Kafka -> Parquet consumer
  bronze_consumer.py       # Main consumer logic
  requirements.txt
  tests/
    test_bronze_consumer.py

data/
  bronze/                  # Generated Parquet files (gitignored)

scripts/
  init_github.sh           # GitHub repo setup
```

## Roadmap (Phases)

- **Phase 1 ✓** Simulation engine (pandapower, profiles, scenarios)
- **Phase 2 ✓** Kafka streaming + Bronze landing zone
- **Phase 3** dbt models (Silver/Gold) + TimescaleDB
- **Phase 4** Grafana dashboards + FastAPI API
- **Phase 5** Real datasets (ONS, NREL NSRDB/WIND) + ML models

Feel free to pick a phase and start coding. If you're working on
something new, open an Issue first to discuss the design.

## Testing

We maintain high test coverage. Before pushing:

```bash
pytest -v           # run all tests
pytest --cov        # run with coverage report (if pytest-cov installed)
```

Tests use fake/mock objects (e.g., FakeProducer, FakeConsumer) so they
run **without** Docker or a real Kafka broker. This makes CI/CD fast.

## Documentation

- **READMEs:** Keep up-to-date when you change architecture
- **Code comments:** Only on complex logic; good code is self-documenting
- **Docstrings:** On every module and public function
- **Commit messages:** Clear, descriptive, mention the "why"

## Common Tasks

### Add a new simulation profile (e.g., hydroelectric)

1. Add a class to `simulator/gridsense_sim/profiles.py`
2. Implement `value_at(step, steps_per_day)` method
3. Add unit test to `simulator/tests/test_engine.py`
4. Update the engine to use it if needed
5. Test: `make test-simulator`

### Add a new Kafka topic

1. Update `ingestion/bronze_consumer.py`: add topic to `DEFAULT_TOPICS`
2. Update `simulator/gridsense_sim/cli.py`: add `--topics` option
3. Add corresponding tests
4. Test: `make test`

### Deploy to the cloud (future)

When you're ready (not now):
1. Add Terraform configs in `terraform/`
2. Document in `DEPLOYMENT.md`
3. Ensure CI/CD passes before deploy

## Questions?

Check:
- [`README.md`](README.md) — overview and quick start
- [`simulator/README.md`](simulator/README.md) — Phase 1 details
- [`GITHUB_SETUP.md`](GITHUB_SETUP.md) — GitHub setup
- Docstrings in the code
- Commit history for examples

---

Thanks for contributing! Every line of code, test, and documentation
makes this a better portfolio project. 🚀
