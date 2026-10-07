# Code Review — Phase 6 after the bug fixes (`main` @ `0063514`)

> **Date:** 2026-10-06 · **Scope:** the whole diff `49fb13b..0063514` (42 files) plus the files it touches indirectly (loaders, Silver, Grafana, compose).
> **Method:** line-by-line reading **and** execution. Every finding below has a command or a measured number that reproduces it.
> **Verification environment:** Python 3.12 and 3.10, pandapower 3.5.5, numpy 2.4, dbt-core 1.12.5, real PostgreSQL 16.
>
> **Resolution status (updated):** R01–R03 fixed in `22b70cf`; R04–R22 fixed in the `phase-6.1-review-fixes` series (see §6 and [`CHANGES_phase6.1-review-fixes.md`](CHANGES_phase6.1-review-fixes.md)). Every fix is locked by a regression test that fails on the old code.

---

## 0. Executive summary

The scientific core of the bug fix was **correct and well built**. The 12 C/N fixes of the changelog were in the code, the golden numbers reproduced, the monotonicity assumption behind bisection was verified empirically, and simulator → Bronze → TimescaleDB → dbt → API worked end to end.

What was broken was **the delivery**: the merge was half done (three blockers), and the review found four high-severity issues that could produce a wrong scientific number without warning.

| Severity | Count | Summary |
|---|---|---|
| 🔴 Blocker | 3 | README committed with merge-conflict markers; Makefile deleted; legacy test turning CI red |
| 🟠 High | 4 | Unenergized bus (NaN) judged "no violation"; stochastic row without a CI flagged comparable; dashboard mixing operating points; Silver and Python judging different buses |
| 🟡 Medium | 7 | Runner losing results; late argument validation; `TimeSeries` side effects; QSTS cost; divergent search controls; untested loader; Grafana port |
| 🔵 Low | 6 | Wrong schema in README; dbt deprecation; provenance; seed without `unique`; stray scripts; git history |
| ➕ Found while fixing | 2 | R21 CI cancels runs on `main`; R22 long QSTS baseline-infeasible with the synthetic profile |

---

## 1. Verified correct (no action needed)

| Changelog item | Verification | Status |
|---|---|---|
| C1 critical point (`load_scale`) | λ 2.289 (×1.0) → 1.586 (×0.25), EN 50160 | ✅ |
| C2 named frameworks | `FRAMEWORKS` + `CriterionKind`; `cigre_lv` default preserved | ✅ |
| C3 baseline gate | PRODIST ×1.0 → `BaselineInfeasibleError`, bus 35 = 0.912 pu | ✅ |
| C4 new stochastic estimator | PRODIST p10 = **0.4375** (n = 40), 95 % CI [0.281; 0.531] | ✅ |
| C5 night window | `NoDaylightError` on [0, 24) at 288 steps/day | ✅ |
| C6 series sampled once | identical runs | ✅ |
| C10 relative-exceedance ranking | 1.12 pu outranks a trafo at 100.5 % | ✅ |
| C11 voltage scope | slack, generator, MV excluded (in **Python**; see R07) | ✅ |
| C12, N1, N2, N4 | Q scaled with P; unbounded flagged; no mutation; provenance | ✅ |
| Quantile CI maths | both binomial inequalities and the exact coverage checked against the derivation | ✅ |
| **Monotonicity assumption** | λ ∈ [0; 3], step 0.02, PRODIST ×0.25, deterministic + 6 stochastic scenarios: **exactly one** feasible→infeasible transition each | ✅ |
| JSON safety | `allow_nan=False` + `_finite()` keep `Infinity` out of jsonb | ✅ |
| Python 3.10 | suite behaves as on 3.12 | ✅ |

---

## 2. 🔴 Blockers

| # | Finding | Evidence | Resolution |
|---|---|---|---|
| R01 | `README.md` committed with 5 conflict blocks (a stopped `git merge` followed by `git add .`) | `git grep` finds the markers at lines 224–327 | ✅ `22b70cf` — README rewritten; CI `hygiene` job blocks markers |
| R02 | `Makefile` deleted in `faa0a3b`, while every README and the changelog call `make ...` (`test-hc`, `hc-study` never existed) | `git show --stat faa0a3b` | ✅ `22b70cf` — restored, plus `test-hc`, `hc-study`, `hc-qsts`, `hc-load`, `dbt-seed`, `dbt-build`, `lint`, `help`; `dbt-run` now seeds first |
| R03 | Legacy `test_hosting_capacity.py` kept: its QSTS test relied on the C5 bug and fails now → CI red | `1 failed, 64 passed` | ✅ `22b70cf` — removed (57 tests, all green) |

---

## 3. 🟠 High severity (a wrong number without warning)

### R04 — An unenergized bus (voltage `NaN`) passed as "no violation"
`assess_violations()` compared `nan < v_min` and `nan > v_max`, both false. Opening the last `cigre_lv` line disconnects bus 43 (Load C20, 7.2 kW): `has_violation = False`, load silently unserved. Latent for the estimators, but any HC study under N-1 contingencies would have approved it.
**Resolution:** ✅ `c3ac274` — NaN voltage in scope → `ViolationReport.isolated_buses`, counts as a violation, ranked first (`isolated@bus_43`); NaN thermal results explicitly ignored. Tests: `test_isolated_bus_is_a_violation_not_a_pass`, `test_isolated_bus_outranks_any_finite_exceedance`.

### R05 — Stochastic row without a confidence interval flagged comparable
With n = 8 the p10 is the smallest of 8 samples and the lower CI bound does not exist, yet `is_comparable = true` and the number reached API and dashboard.
**Resolution:** ✅ `f1a2a3d` — `is_comparable` requires a finite two-sided CI for stochastic rows (n ≥ 36 for p10 at 95 %). dbt test `assert_stochastic_comparable_rows_have_ci` (fails on the old staging); the CI e2e job runs an under-powered study and asserts it is gated out.

### R06 — Dashboard put different operating points side by side
`DISTINCT ON (method, criterion_framework)` ignored `load_scale` and `hc_alpha`: a deterministic run at ×1.0 could sit next to a stochastic run at ×0.25 as "comparable".
**Resolution:** ✅ `f1a2a3d` — panels keyed by `(method, criterion_framework, load_scale, hc_alpha)`; labels show the operating point.

### R07 — Silver and Python judged different buses
`fct_bus_voltage` applied the band to every bus; the HC code excludes slack, generator and MV buses (C11). Measured on case14: buses 0 (slack, 1.06 pu), 5 and 7 (generators) flagged in Silver. The cross-layer test compared only `v_min/v_max`.
**Resolution:** ✅ `7d08010` — `scripts/generate_voltage_scope_seed.py` writes `network_voltage_scope.csv` from `voltage_scope_buses()`; the cross-layer test regenerates it in memory and fails on drift. `fct_bus_voltage.is_in_scope`; violation rates over in-scope readings only (NULL for excluded buses); API returns `is_in_scope`; dashboards no longer hardcode case14 bus numbers. dbt tests: scope present for every network, seed keys unique, out-of-scope buses never violate.

---

## 4. 🟡 Medium severity

| # | Finding | Resolution |
|---|---|---|
| R08 | Runner caught only `HostingCapacityError` and wrote Parquet at the end: any other exception (e.g. QSTS after an hour) discarded results already computed | ✅ `56fb489` — recorded as `status="error"` with type and message; rows written; exit code 1 |
| R09 | Invalid arguments (`--mc-alpha 1.5`) failed only after the Monte Carlo | ✅ `56fb489` — argparse validation before any power flow; warning below `min_samples_for_ci` |
| R10 | `TimeSeries` made the caller's float64 arrays read-only (`np.asarray` aliases) and crashed on `==`/`hash` | ✅ `c3ac274` — inputs copied; `eq=False` |
| R11 | QSTS 60-day default ≈ 139 k power flows ≈ 87 min without numba | ✅ `e1a8c57` — exact `min_over_steps` strategy (see note below) |
| R12 | `max_expansions`/`max_bisections` differed (20/12/20, 40/60/20); `resolved` not exposed | ✅ `e1a8c57` — single `SEARCH_DEFAULTS`; shared `bisect_within`; `resolved` in all results, payload, mart; required by `is_comparable` |
| R13 | HC loader untested, DB URL literal | ✅ `af38d09` — `$GRIDSENSE_DB_URL` default in both loaders; 4 unit tests with a fake psycopg2 |
| R14 | Grafana on 3001 in compose, docs said 3000 | ✅ `af38d09` — `${GRAFANA_PORT:-3001}`; docs aligned |

**Note on R11.** The review proposed visiting steps in risk order inside the global bisection. Measured: only **1.1×**, because every *feasible* candidate still scans the whole series. The implemented fix uses the structure of the zero-tolerance criterion instead: HC = minₜ λ\*ₜ and each step is monotone on its own, so steps are visited in risk order keeping a bracket [L, U] — a step feasible at L costs one power flow; a violating step is bisected alone inside [0, L]. Same contract as the global bisection (kept as `strategy="global_bisection"`); measured **4–5.6× fewer search power flows**, 2.4–4.6× faster end to end, overlapping brackets and the same first violating step.

---

## 5. 🔵 Low severity

| # | Finding | Resolution |
|---|---|---|
| R15 | README used `gold.`; the schema is `public_gold.` | ✅ `22b70cf` |
| R16 | dbt 1.10+ deprecation: `accepted_values` arguments must sit under `arguments:` | ✅ `af38d09` |
| R17 | `git status --porcelain` counted untracked files as dirty | ✅ `af38d09` — dirty = tracked changes; `git_untracked_files` reported separately; 4 tests |
| R18 | Seed without `unique` on `network` | ✅ `7d08010` — `transform/seeds/_seeds.yml` |
| R19 | Unused `import numpy` in `qsts.py`; six ad-hoc scripts at the repo root (three duplicates) | ✅ numpy now used (`e1a8c57`); scripts → `scripts/debug/` (`af38d09`) |
| R20 | Monolithic bug-fix commit; merge commit with a feature message | ⚠️ not rewritten (shared history); prevented going forward by CI + PRs |

---

## 6. Found while fixing

| # | Finding | Resolution |
|---|---|---|
| R21 | `cancel-in-progress: true` also cancelled runs on `main`: merging 13 Dependabot PRs in a row left no completed run and a red badge without any failure | ✅ `af38d09` — never cancel on `main` |
| R22 | The synthetic load profile reaches ~1.19× nominal; above ~1.126× bus 35 drops below 0.90 pu with **no PV**, so a 60-day QSTS at 5-min resolution is baseline-infeasible under every framework (170 steps, seed 42). The 60-day study (doc-05 issue #10) could never have run as configured | ✅ `e1a8c57` — explicit, recorded `peak_load_mult` / `--qsts-peak-load` (default unchanged); `make hc-qsts` uses 1.0. **Modelling choice to confirm with the advisor.** |

---

## 7. Scientific limitations still open (not bugs)

- **C7** no QSTS duration criterion τ̄ (Eq. 3.20–3.21): EN 50160 applied instantaneously remains an acknowledged category error.
- **C8** no tap control. **C13** VVSI/OSI/RPFI not implemented.
- **C9** synthetic load peaks at midday: **no PRODIST QSTS number exists yet**.
- `DEFAULT_CRITICAL_LOAD_SCALE = 0.25` and `AdoptionModel` (30–100 % adoption, dispersion 0.5) are assumptions; the stochastic p10 is sensitive to them.
- `is_comparable` does not look at `criterion_kind`: a statistical band applied instantaneously still passes (deliberate, `cigre_lv` default preserved).

---

## 8. How each finding was reproduced

| Finding | Command / evidence |
|---|---|
| R01 | `git grep -nE '^(<<<<<<<\|>>>>>>>)( \|$)'` |
| R03 | `python -m pytest simulator/tests -q` → 1 failed, 64 passed |
| R04 | open `net.line.index[-1]` on `cigre_lv` → bus 43 NaN, `has_violation=False` |
| R05 | runner with `--mc-n-scenarios 8` → loader → `dbt build` → query the mart |
| R07 | `case14` telemetry → Bronze → dbt → `public_silver.fct_bus_voltage` buses 0/5/7 |
| R08–R09 | `build_records(...)` with `total_steps=0`; `stochastic_alpha=1.5` |
| R10 | `TimeSeries(a, b, 4); a.flags.writeable` |
| R11 | mean of 30 `pp.runpp`; step counts of `build_synthetic_series(17280, 288)`; strategy comparison on 1-, 3- and 2-day series |
| R22 | bisection on the load multiplier at PV = 0 → threshold ×1.126; count of steps above it |
| Monotonicity | λ sweep, Δλ = 0.02, 7 allocations, count transitions |
