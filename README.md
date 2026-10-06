# GridSense Simulator

[![CI](https://github.com/EngEleLuiz/Gridsense-simulator/actions/workflows/tests.yml/badge.svg?branch=main)](https://github.com/EngEleLuiz/Gridsense-simulator/actions/workflows/tests.yml)

Electrical Grid Digital Twin & Data Platform, and the experimental
infrastructure of a master's dissertation on **PV hosting capacity**
(UFSC). This monorepo is built incrementally, phase by phase, always
runnable locally before any component is deployed anywhere else.

Every tool used here is free and open-source and runs entirely on your
own machine (Docker containers + local Python processes). No cloud
account, managed service, or paid license is required at any phase.

## Phases

| Phase | Status | What it delivers |
|---|---|---|
| 1 — Simulation engine | ✅ | `simulator/`: IEEE test networks, synthetic load/renewable profiles, N-1/N-2 contingencies, power flow, telemetry. See [`simulator/README.md`](simulator/README.md). |
| 2 — Streaming & Bronze | ✅ | `ingestion/`: local Kafka; a consumer lands telemetry as partitioned Parquet. |
| 3 — Transform | ✅ | `transform/`: Bronze → TimescaleDB → dbt Silver facts and Gold marts, with data-quality tests. See [`transform/README.md`](transform/README.md). |
| 4 — API & dashboards | ✅ | `api/` (FastAPI) and `grafana/` (3 provisioned dashboards). See [`api/README.md`](api/README.md), [`grafana/README.md`](grafana/README.md). |
| 5 — Network migration | ✅ | `cigre_lv`, a 44-bus radial LV distribution feeder (CIGRE TF C6.04.02), alongside `case14/39/57/118`. |
| 6 — Hosting-capacity methods | ✅ | Deterministic, stochastic and QSTS hosting capacity, compared in a dbt mart, served by the API and a dashboard. |
| **6.1 — Methodology review & bugfixes** | ✅ **current** | Line-by-line review against the literature: 12 fixes that change published numbers, then a post-merge code review (R01–R22). See [below](#phase-61--methodology-review-and-bugfixes) and [`docs/CHANGES_phase6-bugfix.md`](docs/CHANGES_phase6-bugfix.md). |
| 7 — Certified surrogate + open dataset | 🔄 in progress, not merged | Physics prior (LinDistFlow) + learned residual with conformal screening, as an accelerator inside the Phase 6 estimators. |
| 8 — Real data | 🔲 planned | Real irradiance (SONDA/INMET) and residential load replacing the synthetic profiles. |
| 9 — Streaming HC / operating envelope | 🔲 planned | Continuous envelope recomputation over the existing Kafka pipeline. |

## Phase 6.1 — methodology review and bugfixes

The Phase 6 code ran, but a review against Bollen & Rönnberg (2017),
Torquato et al. (2018) and Jain et al. (2019/2020) found that several
reported numbers measured the code, not the network. Each fix is locked
by a regression test.

| ID | Problem | Fix |
|---|---|---|
| C1 | Deterministic search ran at nominal load, not at the critical point (max PV, min load) | `load_scale` (default 0.25), recorded with every result |
| C2 | A ±10 % band applied without naming it; EN 50160's ±10 % is statistical, not instantaneous | Named frameworks: `ansi_c84_range_a`, `prodist_m8_bt`, `en50160_envelope`, each tagged instantaneous/statistical |
| C3 | No PV = 0 check; a load-driven violation made bisection return a meaningless number | Baseline gate → `BaselineInfeasibleError`, recorded as a status, never as a capacity |
| C4 | Monte Carlo measured its own sampling ceiling (p50 scaled linearly with it) | New estimator: critical penetration λ\* per random adoption scenario; HC = F⁻¹(α) with a distribution-free CI |
| C5 | QSTS test window was night-only → λ ≈ 10⁶ | `NoDaylightError` |
| C6 | QSTS redrew profile noise for every bisection candidate | One immutable `TimeSeries` per study |
| C10 | Binding-constraint ranking mixed units | Relative exceedance beyond each limit |
| C11 | Slack, generator and MV buses were voltage-checked | `voltage_scope_buses()` |
| C12 | QSTS scaled load P but not Q | P and Q scaled together |
| N1–N4 | Unbounded search reported as capacity; caller's network mutated; invalid numbers reaching API/dashboard; no provenance | `bounded` flag; deep copies; payload v2 with `is_comparable`; git commit + library versions on every row |

**Current numbers** (`cigre_lv`, synthetic profiles — preliminary, not dissertation results):

| Method | Criterion | Load | λ (PV / nominal load) | Total PV | Binding |
|---|---|---|---|---|---|
| Deterministic | EN 50160 envelope | ×1.0 | 2.289 | 1.572 MW | trafo 0 (old published number, reproduced) |
| Deterministic | EN 50160 envelope | ×0.25 | 1.586 | 1.089 MW | trafo 0 |
| Deterministic | PRODIST M8 BT | ×0.25 | 1.164 | 0.799 MW | overvoltage, bus 16 |
| Deterministic | PRODIST M8 BT | ×1.0 | — | — | `baseline_infeasible` (bus 35 at 0.912 pu with no PV) |
| Stochastic p10 | PRODIST M8 BT | ×0.25 | ≈ 0.44 (95 % CI ≈ 0.28–0.53, n = 40) | ≈ 0.30 MW | depends on the adoption model |
| QSTS, 60 days at 5 min | EN 50160 envelope | profile peak = 1.0 | 2.108 | 1.448 MW | trafo 0 (day 47, 11:40); zero-tolerance criterion |

Two findings are already methodological results: the operating point
and the regulatory criterion dominate the answer (≈ 2× between the old
and corrected deterministic numbers), and uncoordinated adoption hosts
far less than load-proportional allocation.

**Post-merge code review** ([`docs/CODE_REVIEW_phase6-bugfix.md`](docs/CODE_REVIEW_phase6-bugfix.md)),
all fixed with regression tests: unenergized buses are reported as
violations (`isolated@bus_N`); a stochastic result is comparable only
with a finite confidence interval; the Silver layer judges exactly the
buses the hosting-capacity code judges (generated
`network_voltage_scope` seed); the runner records unexpected errors and
validates arguments up front; one set of search defaults for the three
methods; and an exact `min_over_steps` QSTS strategy (4–5.6× fewer
search power flows than the global bisection, same bracket).

**Still open** (scientific work, not bugs): QSTS has no duration
criterion τ̄ (C7) or tap control (C8); the synthetic load peaks at
midday together with solar (C9), so **no PRODIST QSTS number exists
yet**; severity metrics VVSI/OSI/RPFI are not implemented (C13). The
raw synthetic profile reaches ~1.19× nominal load, which makes long QSTS
runs baseline-infeasible on `cigre_lv` (R22): pass
`--qsts-peak-load 1.0` (nominal load = peak demand) — an explicit,
recorded modelling choice.

## Architecture

```
simulator            bronze_consumer          load_bronze_to_timescale.py
┌────────────┐ Kafka ┌─────────────────┐ COPY ┌────────────────────┐
│ pandapower │ topics│ Kafka -> Parquet│ ───▶ │ bronze.raw_events  │
│ publisher  ├──────▶│ (Bronze)        │      │ (hypertable)       │
└────────────┘       └────────┬────────┘      └─────────┬──────────┘
                              ▼                         │ dbt
                   data/bronze/*.parquet                ▼
                   (DuckDB, no server)     staging → silver → gold
                                                        │
                                  ┌─────────────────────┴─────────────┐
                                  ▼                                   ▼
                       FastAPI  (api/)                     Grafana (grafana/)
                       http://localhost:8000/docs          http://localhost:3001

hosting_capacity/ ── run_hosting_capacity_study.py ──▶ data/hosting_capacity/*.parquet
(deterministic | stochastic | qsts,                         │ load_hosting_capacity_to_timescale.py
 shared limits/scope/violations/                            ▼
 baseline/search)                          bronze.hosting_capacity_results (plain table)
                                                            │ dbt
                                                            ▼
                               stg_hosting_capacity_results → mart_hosting_capacity
                               (same API + Grafana as above)
```

Kafka topics: `grid.telemetry.raw` (one record per power-flow step) and
`grid.events.alerts` (contingencies, non-convergence).

Bronze is **schema-on-read**: each record keeps the raw JSON payload
plus ingestion metadata; typing, unnesting and quality checks happen in
dbt. The API and Grafana read only the Gold layer. Hosting-capacity
results follow the same pattern but are one-off studies, so they land in
a plain table instead of a hypertable.

## Requirements

- Docker + Docker Compose
- Python 3.10+ (CI tests 3.10 and 3.12, on Linux and Windows)
- `make` (Linux/macOS, or Git Bash on Windows) — optional; every target has a manual equivalent below

## Setup

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate

pip install -e "simulator[dev,fast,kafka]"   # 'fast' = numba, ~10x faster power flow
pip install -r ingestion/requirements.txt -r api/requirements.txt

# dbt pins a deepdiff version incompatible with pandapower: use its own venv
python -m venv .venv-dbt
.venv-dbt/bin/pip install -r transform/requirements.txt   # Windows: .venv-dbt\Scripts\pip

cp .env.example .env    # optional: only to override the dev credentials
docker compose up -d
# Kafka:       localhost:9092
# Kafka UI:    http://localhost:8080
# TimescaleDB: localhost:5432 (db=gridsense, user=postgres, password=postgres)
# Grafana:     http://localhost:3001 (admin/admin)
```

## Run the telemetry pipeline

```bash
make up              # Kafka + TimescaleDB + Grafana
make produce         # simulator -> Kafka
make consume-bronze  # Kafka -> Bronze Parquet
make load-timescale  # Bronze -> TimescaleDB (idempotent)
make dbt-build       # seeds + Silver/Gold + all dbt tests
make seed-scope      # regenerate the voltage-scope seed after changing scope.py or adding a network
make api-run         # http://localhost:8000/docs
make help            # every target
```

Manual equivalents (any OS):

```bash
python -m gridsense_sim.cli --network case14 --steps 200 --kafka --kafka-bootstrap-servers localhost:9092 -v
python ingestion/bronze_consumer.py --bootstrap-servers localhost:9092 --output-dir data/bronze -v
python ingestion/load_bronze_to_timescale.py --bronze-dir data/bronze \
  --db-url postgresql://postgres:postgres@localhost:5432/gridsense -v
cd transform && dbt build --profiles-dir . && cd ..      # 'build' = seed + run + test
cd api && uvicorn app.main:app --reload --port 8000
```

`--network` also accepts `cigre_lv`. Without Kafka, `--output telemetry.jsonl`
writes JSON Lines, and `scripts/ci/jsonl_to_bronze.py` turns them into
Bronze Parquet (this is how CI tests the pipeline).

## Run a hosting-capacity study

```bash
make hc-study    # deterministic + stochastic, cigre_lv, PRODIST, critical point (minutes)
make hc-qsts     # QSTS, 7 days at 5 min, profile normalized to peak 1.0
make hc-load     # Parquet -> bronze.hosting_capacity_results
make dbt-build
```

or directly:

```bash
python scripts/run_hosting_capacity_study.py --network cigre_lv \
  --framework prodist_m8_bt --load-scale 0.25 \
  --methods deterministic stochastic qsts --mc-n-scenarios 60 -v
```

| Option | Meaning |
|---|---|
| `--framework` | `ansi_c84_range_a` (0.95–1.05) · `prodist_m8_bt` (0.92–1.05, verify the revision in force) · `en50160_envelope` (0.90–1.10, statistical). Default: per network (`cigre_lv` → EN 50160 envelope). |
| `--load-scale` | Load multiplier at the critical operating point for deterministic/stochastic (default 0.25, an assumption until Phase 8). `1.0` reproduces the old nominal-load numbers. |
| `--mc-n-scenarios`, `--mc-alpha` | Stochastic HC = F⁻¹(α) of λ\*. A finite 95 % CI for p10 needs **n ≥ 36**; use ≥ 60. |
| `--mc-adoption-min/max`, `--mc-size-dispersion` | Adoption model (assumptions — run a sensitivity analysis before citing). |
| `--qsts-total-steps`, `--qsts-steps-per-day` | QSTS horizon (default 60 days at 5 min). Cost ≈ one power flow per step for the baseline gate plus about one per daylight step for the search. |
| `--qsts-peak-load` | Rescale the synthetic load so its peak equals this multiplier (e.g. `1.0`). Required for long `cigre_lv` runs (R22); recorded in the payload. |

**Reading the results.** Compare only rows with `is_comparable = true`,
the same `criterion_framework`, and (for snapshot methods) the same
`load_scale`. `is_comparable` requires payload v2, `status = ok`, a
bounded and resolved search, and — for stochastic rows — a finite
two-sided confidence interval (`hc_lambda_ci_low/high`). A failed study
is kept as a row with `status = baseline_infeasible | no_daylight | error`
and a NULL capacity; pre-review rows are `legacy_invalid`. QSTS is
zero-tolerance (`qsts_criterion = zero_tolerance`) until τ̄ lands.

## Verify

```sql
-- dbt schema names = profile schema 'public' + model schema
SELECT * FROM public_gold.mart_grid_kpis_daily;
SELECT method, criterion_framework, load_scale, total_pv_mw_comparable, binding_constraint
FROM public_gold.mart_hosting_capacity
WHERE is_comparable ORDER BY run_timestamp DESC;
```

```bash
curl http://localhost:8000/api/v1/kpis/daily
curl "http://localhost:8000/api/v1/hosting-capacity/compare?network=cigre_lv&framework=prodist_m8_bt"
curl "http://localhost:8000/api/v1/hosting-capacity/compare?comparable_only=false"   # audit failed/legacy rows
```

## Tests and CI

```bash
make test        # simulator + ingestion + API (no Docker needed)
make test-hc     # hosting-capacity tests only (~40 s)
make lint        # same ruff checks as CI
make dbt-build   # dbt tests (needs TimescaleDB running)
```

| Suite | Where | What it covers |
|---|---|---|
| Simulator | `simulator/tests/test_hc_*.py`, `test_study_runner.py`, `test_provenance.py`, `test_engine.py` | Every C/N/R fix, golden numbers, payload v2, cross-layer limits **and voltage scope** (Python ↔ dbt seeds), QSTS strategy equivalence |
| Ingestion | `ingestion/tests/` | Bronze consumer (Kafka mocked), HC loader (psycopg2 mocked) |
| API | `api/tests/` | All endpoints (DB session mocked), `comparable_only` default |
| dbt | `transform/tests/`, schema YAML | Voltage limits and scope per network, out-of-scope buses never violate, seed keys unique, HC bracket ordering, comparable rows complete (stochastic needs a CI), non-negative capacity |

**Every commit on every branch** (and every PR) runs
[`.github/workflows/tests.yml`](.github/workflows/tests.yml):

| Job | Checks |
|---|---|
| `hygiene` | No merge-conflict markers, no CRLF, required files present, all JSON/YAML parse, `docker compose config` valid |
| `lint` | ruff: syntax errors and undefined names block; the rest is advisory |
| `simulator` | pytest + coverage on Ubuntu (3.10, 3.12) and Windows (3.12) |
| `ingestion`, `api` | Unit tests |
| `e2e` | Real TimescaleDB: simulated telemetry → Bronze → loader → HC studies (incl. a deliberate `baseline_infeasible`) → `dbt build` → data-contract assertions (`scripts/ci/assert_e2e.py`) → the real API queried over HTTP |
| `docker` | API image builds, runs as non-root, imports the app |
| `ci-ok` | Single gate: green only if all of the above are green |

Recommended: protect `main` (Settings → Branches) with "require a pull
request" and "require status check `ci-ok`". Dependabot opens weekly
update PRs for Actions and pip, each validated by the same workflow.

## Shutting down

```bash
make down                 # stop containers, keep volumes
docker compose down -v    # also wipe Kafka, TimescaleDB and Grafana data
```

## Repository layout

```
gridsense-simulator/
├── .github/
│   ├── workflows/tests.yml          # CI: hygiene, lint, tests, e2e, docker, ci-ok
│   └── dependabot.yml
├── docker-compose.yml               # Kafka + TimescaleDB + Grafana (+ optional API)
├── .env.example
├── Makefile                         # `make help` lists every target
├── docs/
│   ├── CHANGES_phase6-bugfix.md     # Phase 6.1 changelog (C/N fixes)
│   ├── CHANGES_phase6.1-review-fixes.md  # post-merge review fixes (R01–R22)
│   └── CODE_REVIEW_phase6-bugfix.md # post-merge review, open issues
├── simulator/
│   ├── gridsense_sim/
│   │   ├── hosting_capacity/        # limits, scope, violations, baseline, search,
│   │   │                            # conditions, allocation, quantiles, timeseries,
│   │   │                            # deterministic, stochastic, qsts, errors
│   │   ├── provenance.py            # git commit + library versions per result
│   │   ├── engine.py, cli.py, profiles.py, scenarios.py, ...
│   └── tests/                       # test_hc_*.py: one file per module
├── scripts/
│   ├── generate_voltage_scope_seed.py  # Python scope rule -> dbt seed
│   ├── debug/                       # check_db.py, query_bronze.py (ad-hoc checks)
│   ├── run_hosting_capacity_study.py
│   └── ci/                          # jsonl_to_bronze.py, assert_e2e.py
├── ingestion/                       # Kafka -> Parquet -> TimescaleDB (+ HC loader)
├── transform/                       # dbt: seeds, staging/silver/gold, tests
├── api/                             # FastAPI read layer (+ Dockerfile)
├── grafana/                         # provisioning + 3 dashboards
└── data/                            # generated, gitignored
```

## License

MIT — see [`LICENSE`](LICENSE).
