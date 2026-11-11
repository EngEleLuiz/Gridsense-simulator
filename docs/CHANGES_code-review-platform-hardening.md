# Code review — platform hardening (CR-14, S-01, S-02, S-03, S-10, S-11)

> Base: `main` @ `f888f77`. Branch: `fix/platform-hardening`.
> Scope: API, local Docker stack, CI and the experiment runner script.
> **No simulation code or result changes**: nothing under `simulator/gridsense_sim/`
> or `scripts/run_hosting_capacity_study.py` was modified.

## Changes

| ID | File(s) | Change | How verified |
|---|---|---|---|
| CR-14 | `api/app/db.py`, `api/tests/test_db.py` | The DB URL is built with `sqlalchemy.URL.create(...)` instead of an f-string, so passwords/user names containing `@`, `/` or `:` no longer corrupt the URL. `TIMESCALE_PASSWORD` no longer has a hard-coded default (compose, Makefile and READMEs already set it); without it the driver fails with "no password supplied". Module-level `engine` unchanged. | New tests: no default password; a per-run generated password with reserved characters and user `grid:user` round-trip through `render_as_string` → `make_url`; non-numeric port raises. `cd api && python -m pytest -q` (26 passed). |
| S-01 | `docker-compose.yml` | Every published port binds to `127.0.0.1` (Kafka 9092, kafka-ui 8080, TimescaleDB 5432, Grafana `${GRAFANA_PORT:-3001}`, API 8000). Header comment explains how to expose a service deliberately (and to change credentials / Kafka's advertised listener first). | `docker compose -f docker-compose.yml --profile api config` shows `host_ip: 127.0.0.1` on all five ports. Kafka's `PLAINTEXT_HOST` advertised listener is already `localhost:9092`, so host clients (`make produce`, `make consume-bronze`) are unaffected. |
| S-02 | `docker-compose.yml`, `.github/workflows/tests.yml` | `provectuslabs/kafka-ui:v0.7.2` pinned (last release; comment points to the `ghcr.io/kafbat/kafka-ui` fork). CI e2e service pinned to `timescale/timescaledb:2.17.2-pg16` (always an empty database). In compose, TimescaleDB and Grafana use `${TIMESCALEDB_TAG:-latest-pg16}` / `${GRAFANA_TAG:-latest}` instead of a hard pin, because an existing volume cannot be opened by an older version; the comments show how to read the running version and pin it in `.env`. | `docker compose config --quiet` passes; workflow parses and passes `actionlint` 1.7.12. **Tags were not pulled** (no registry access) — see "Human verification". |
| S-03 | `api/requirements*.txt`, `ingestion/requirements*.txt`, `.github/workflows/tests.yml`, `README.md`, `api/README.md`, `Makefile` | `requirements.txt` = runtime only (what the API image installs); `requirements-dev.txt` = `-r requirements.txt` + `pytest` (+ `httpx` for the API). CI `api`/`ingestion` jobs install the dev files (and key the pip cache on both). The e2e venvs keep the runtime files on purpose: they run the real loaders and API server, not unit tests (pytest there comes from `simulator[dev]`). | `pip install -r api/requirements-dev.txt -r ingestion/requirements-dev.txt`, then API 26 passed, ingestion 11 passed. The Docker image build was **not** run locally (no Docker daemon); CI's `docker` job covers it. |
| S-10 | `scripts/run_experiments.py`, `simulator/tests/test_run_experiments.py` | (a) `started_utc` is recorded before the study subprocess starts (it was the end time). (b) A cell is skipped only if its manifest has `returncode == 0` **and** the stored `cell` equals the current spec (`is_done()`); legacy/corrupt manifests mean "not done". (c) `checks()` pairs results on `block_average_min` and `existing_pv` (deterministic side limited to PRODIST frameworks), checks every pair instead of `det[0]`/`zero[0]`/`duration[0]`, and prints `[SKIP]` for a result with no counterpart. | 9 new tests with fake subprocess/manifests/Parquet (no studies); 8 of them fail on the old script. Under the current `experiments/experiment_spec.json` the E3 hourly PRODIST cell now shows up as `[SKIP]` (no hourly τ = 0 cell exists) instead of vanishing; it is never compared against the 10-min τ = 0 result. |
| S-11 | `.github/workflows/tests.yml` | Simulator job gains `--cov-fail-under=80`. Measured coverage: **86%** (Linux, Python 3.12, `simulator[dev,fast]`, 181 passed / 10 skipped). Full pyflakes set stays advisory: `ruff check --select F .` is not clean (1 finding, unused `import pandapower.networks as pn` in `scripts/run_hosting_capacity_study.py`). | Coverage measured locally with `pytest-cov`; workflow parses and passes `actionlint` 1.7.12 (shellcheck not installed, so embedded scripts were not shell-linted). |

## Human verification required

- **Image tags pull**: `provectuslabs/kafka-ui:v0.7.2` (compose) and
  `timescale/timescaledb:2.17.2-pg16` (CI e2e service only). They were chosen as releases
  known to exist, but could not be checked against the registries; CI will confirm the
  TimescaleDB tag.
- **Pin your local TimescaleDB and Grafana tags.** Compose keeps `latest-pg16` / `latest`
  as defaults so existing volumes keep starting. To make the local stack reproducible, set
  `TIMESCALEDB_TAG` and `GRAFANA_TAG` in `.env` to the versions your volumes already use
  (`SELECT extversion FROM pg_extension WHERE extname = 'timescaledb';` and
  `grafana server -v`). Never set an older version than the one that created the volume.
- Anyone who reached the stack from another machine (VM, LAN) must now opt in as the
  `docker-compose.yml` header explains.

## Follow-ups (out of scope here)

- Pin GitHub Actions to commit SHAs (could not be verified offline).
- Add lock files (`pip-compile`/`uv lock`) for the runtime requirements.
- Switch kafka-ui to the maintained `ghcr.io/kafbat/kafka-ui` with a verified tag.
- Remove the unused import in `scripts/run_hosting_capacity_study.py`, then make
  `ruff check --select F .` blocking in CI.
- Add an hourly (`block_average_min: 60`) τ = 0 cell in a new spec version if the E3
  duration result should be invariant-checked (the spec is pre-registered, so this is a
  protocol decision, not a code fix).
