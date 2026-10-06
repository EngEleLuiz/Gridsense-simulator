# Phase 6.1 — post-merge review fixes (R04–R22)

> Base: `main` @ `bc6b39c`. Findings: [`CODE_REVIEW_phase6-bugfix.md`](CODE_REVIEW_phase6-bugfix.md).
> Every fix ships with a regression test that **fails on the old code** and passes on the new one.

## Commits

| Commit | Findings | Summary |
|---|---|---|
| `fix(hc): report unenergized buses as violations; copy TimeSeries inputs` | R04, R10 | NaN voltage in scope → `isolated@bus_N` violation; `TimeSeries` copies its inputs, identity equality |
| `fix(pipeline): stochastic HC comparable only with a finite CI; dashboard keyed by operating point` | R05, R06 | `is_comparable` requires a CI for stochastic rows; HC dashboard keyed by `(method, framework, load_scale, alpha)` |
| `fix(pipeline): judge only in-scope buses in the Silver voltage facts` | R07, R18 | Generated `network_voltage_scope` seed; `is_in_scope` through Silver, Gold, API and dashboards; seed tests |
| `fix(runner): record unexpected method errors; validate arguments up front` | R08, R09 | `status="error"` rows, exit code 1; argparse validation; `min_samples_for_ci` |
| `perf(hc): shared search defaults, exact min-over-steps QSTS, explicit peak normalization` | R11, R12, R22 | `SEARCH_DEFAULTS`, `bisect_within`, `resolved`; QSTS `strategy="min_over_steps"`; `--qsts-peak-load` |
| `chore: CI never cancels main, debug scripts consolidated, env-var DB URLs, dbt syntax` | R13, R14, R16, R17, R19, R21 | see the review |
| `docs: English review/changelogs, README for Phase 6.1` | — | this file, translated review and Phase 6 changelog, README |

## Interface changes

- `ViolationReport` gained `isolated_buses`; a NaN voltage in scope is now a violation.
- Every HC result gained `resolved`; payload v2 carries `resolved`; the mart exposes `is_resolved`, and `is_comparable` requires it (absent in older v2 rows → treated as resolved).
- Stochastic rows without a finite two-sided CI are **no longer comparable** (`total_pv_mw_comparable = NULL`).
- `estimate_hosting_capacity_stochastic` and the QSTS use the shared defaults: `max_expansions` 12 → 20 (stochastic), `max_bisections` 40/20 → 60. No measured number changes (tolerance 0.01 closes long before the cap).
- `find_hosting_capacity_qsts(strategy="min_over_steps" | "global_bisection", peak_load_mult=None)`; result gained `strategy` and `peak_load_mult`; payload gained `search_strategy` and `peak_load_mult`.
- `build_synthetic_series(..., peak_load_mult=None)`; runner `--qsts-peak-load`.
- `fct_bus_voltage` gained `is_in_scope`; out-of-scope readings are never a violation. `mart_voltage_quality_hourly` gained `is_in_scope`, and `violation_rate_pct` is over in-scope readings (NULL for excluded buses). **Dashboard violation rates for case14 slack/generator buses disappear by design.**
- API `/api/v1/voltage/*` returns `is_in_scope`; `/hosting-capacity/compare` returns `is_resolved`.
- Runner exits with code 1 when a method records `status="error"`; invalid arguments exit with code 2 before any power flow.
- Loaders default `--db-url` to `$GRIDSENSE_DB_URL`.
- `Provenance` gained `git_untracked_files`; `git_dirty` now means tracked changes only.
- Root scripts `check.py`, `check_data.py`, `check_gold.py`, `check_violation.py`, `query_bronze.py`, `ingestion_query.py` → `scripts/debug/check_db.py` and `scripts/debug/query_bronze.py`.
- New: `scripts/generate_voltage_scope_seed.py` (`make seed-scope`) — rerun it after changing `scope.py` or adding a network; the cross-layer test fails otherwise.

## Measured

| What | Result |
|---|---|
| QSTS search power flows, `min_over_steps` vs global bisection | 114 → 30 (1 day hourly), 309 → 77 (3 days hourly), 2068 → 371 (2 days at 5 min) |
| QSTS wall time, same cases | 4.6×, 2.4×, 2.9× faster (the mandatory baseline gate now dominates) |
| Equivalence | brackets overlap, same first violating step and binding element in every case |
| **60-day QSTS** (`cigre_lv`, EN 50160, 5 min, `--qsts-peak-load 1.0`, numba) | λ = 2.108 (1.448 MW), binding trafo 0 at 100.2 %, first violation day 47 11:40 (step 13388); 32 735 power flows (17 280 baseline gate + 15 455 search) in ~15 min — the global bisection needed an estimated ~139 k. Closes doc-05 issue #10. |
| R22 threshold | `cigre_lv` at PV = 0 violates EN 50160 above load ×1.126; raw synthetic profile reaches ×1.194 (170 steps in 60 days, seed 42) |

## Validation

| Suite | Before | After |
|---|---|---|
| Simulator (pytest) | 57 | **84** |
| API (pytest) | 22 | **23** |
| Ingestion (pytest) | 7 | **11** |
| dbt `build` (seeds + models + tests) | 53 | **65** |
| E2E contract assertions (`scripts/ci/assert_e2e.py`) | 7 | **9** |
| Grafana queries executed against the database | — | 13 / 13 |

## Still open

C7 (QSTS duration criterion τ̄), C8 (tap control), C9 (real profiles), C13 (VVSI/OSI/RPFI); the peak normalization of R22 and the stochastic adoption model are modelling choices to confirm with the advisor.
