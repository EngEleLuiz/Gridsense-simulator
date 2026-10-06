# Phase 6 bug fixes (P0) — changelog

> Base: `main` @ `49fb13b`. Merged in `faa0a3b` / `0063514`.
> Validation at the time: 57 simulator tests, 22 API, 7 ingestion, and 15
> dbt tests run against a real PostgreSQL 16, fed by the real runner and loader.
> The follow-up post-merge fixes are in
> [`CHANGES_phase6.1-review-fixes.md`](CHANGES_phase6.1-review-fixes.md).

## 1. Finding → fix → test that locks the regression

| ID | Problem | Fix | Test |
|---|---|---|---|
| C1 | Deterministic search at nominal load | `load_scale` (default 0.25 = critical point), recorded in `StudyConditions` | `test_golden_numbers_match_doc06`, `test_critical_point_lowers_hosting_capacity` |
| C2 | EN 50160 ±10 % used without a name or its nature | Named `FRAMEWORKS` (`ansi_c84_range_a`, `prodist_m8_bt`, `en50160_envelope`) + `CriterionKind`. **`cigre_lv` default kept** (advisor's decision) | `test_hc_limits_scope.py` |
| C3 | No baseline check → invalid bisection | `require_feasible_baseline` in every method → `BaselineInfeasibleError` | `test_prodist_at_nominal_load_raises_baseline_infeasible`, `test_prodist_baseline_violation_is_detected` |
| C4 | Monte Carlo measured its sampling budget | New `estimate_hosting_capacity_stochastic`: λ\* per adoption scenario, HC = F⁻¹(α), CI from binomial order statistics. Old `run_monte_carlo` emits `DeprecationWarning` | `test_hc_stochastic.py` |
| C5 | False-positive QSTS test (night window, λ ≈ 10⁶) | `NoDaylightError` | `test_night_only_window_raises_no_daylight` |
| C6 | QSTS redrew profile noise for every λ candidate | Immutable `TimeSeries`, sampled once | `test_series_is_sampled_once_so_runs_are_identical` |
| C10 | Ranking mixed units | Relative exceedance beyond each element's own limit | `test_ranking_uses_relative_exceedance_not_mixed_units` |
| C11 | Slack, generator and MV buses voltage-checked | `voltage_scope_buses` (+ `voltage_levels_kv=(0.4,)` for `cigre_lv`) | `test_cigre_scope_is_lv_only`, `test_case14_slack_is_never_reported_as_a_violation` |
| C12 | QSTS scaled P but not Q | P and Q scaled together | (covered by the QSTS tests) |
| N1 | Expansion cap returned as HC | `bounded=False`, `lambda_fail=None` | `test_unbounded_search_is_flagged*` |
| N2 | Functions mutated the caller's `net` | Internal `deepcopy` in every method | `test_caller_network_is_not_mutated` (deterministic and QSTS) |
| N3 | Mart, API and Grafana showed the invalid estimator | Payload v2, `is_comparable`, `total_pv_mw_comparable` NULL for invalid/legacy rows, API `comparable_only=True` by default | `api/tests/test_hosting_capacity.py`, 3 new dbt tests |
| N4 | Results without provenance | `provenance.py`: commit, dirty flag, library versions on every row | `test_payload_v2_envelope_and_provenance` |
| N5 | No CI | `.github/workflows/tests.yml` | — |
| — | Python limits and dbt seed could drift apart | Cross-layer consistency test | `test_dbt_seed_matches_python_default_limits` |

Cost improvement without changing the answer: QSTS skips night steps
during the search. Justification (module docstring): with a clean
baseline and PV = 0, the night state equals the baseline state.

## 2. Reproduced numbers (`cigre_lv`, deterministic)

| Criterion | Load | λ | MW | Binding |
|---|---|---|---|---|
| EN 50160 envelope | ×1.0 | 2.289 | 1.572 | trafo_0 (old number, reproduced) |
| EN 50160 envelope | ×0.25 | 1.586 | 1.089 | trafo_0 |
| PRODIST M8 BT | ×0.25 | 1.164 | 0.799 | overvoltage, bus 16 |
| PRODIST M8 BT | ×1.0 | — | — | `baseline_infeasible` (bus 35 = 0.912 pu) |

## 3. New findings during implementation

1. **case14 is baseline-infeasible under ANSI Range A**, even with the slack excluded: load buses 6, 8, 9, 11 and 12 sit at about 1.055–1.062 pu because of the case's generator setpoints. Any HC study on case14 now fails explicitly instead of returning a meaningless number.
2. **The stochastic number differs from the review document.** PRODIST p10 gave λ ≈ 0.45–0.46 versus 0.562 there; the difference comes from the adoption model (size dispersion 0.5 is an assumption). The direction holds (stochastic below deterministic), but **the number depends on `AdoptionModel` and needs a sensitivity analysis before being cited**.
3. **The quantile CI needs enough samples.** With n = 20 and α = 0.10 there is no lower bound (0.9²⁰ > 0.025); the code returns `None` rather than inventing one. For p10 at 95 % use n ≥ 36; n ≥ 60 recommended.
4. **QSTS under PRODIST is baseline-infeasible** at the synthetic profile's midday load peak — a direct consequence of C9, solved with real data.
5. **dbt-postgres and pandapower require incompatible `deepdiff` versions**, so they need separate environments; CI does this.

## 4. Breaking interface changes

- **Keyword-only arguments.** `find_hosting_capacity_*` take `framework=` and `load_scale=` by name only. Results gained `lambda_fail`, `bounded` and `conditions`.
- **Deterministic load default** is now `load_scale=0.25`; pass `load_scale=1.0` for the old number.
- **Stochastic estimator.** The runner no longer writes the legacy estimator; `--mc-max-pv-mw-per-bus`, `--mc-n-trials` and `--mc-include-trials` were removed.
- **Payload v2.** Staging reads v1 and v2; v1 rows become `legacy_invalid`.
- **Parquet names** gained a random suffix so two writes in the same millisecond cannot overwrite each other.
- **API.** `/compare` returns only comparable rows by default; use `comparable_only=false` to audit.
- **Tests.** `simulator/tests/test_hosting_capacity.py` was replaced by `test_hc_*.py`.

## 5. Still open (outside P0, by decision)

| Item | Where it will be solved |
|---|---|
| C7: QSTS duration criterion τ̄ | S1 |
| C8: tap control | S1 |
| C9: load profile peaking at midday | Real data (Phase 8) |
| C13: VVSI/OSI/RPFI metrics | S1 (reverse power flow is already reported) |
| `DEFAULT_CRITICAL_LOAD_SCALE = 0.25` | Assumption until Phase 8 |
| PRODIST band 0.92–1.05 | Verify against the Module 8 revision in force |
