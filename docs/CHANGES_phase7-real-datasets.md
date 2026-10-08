# Phase 7 — Real datasets

Branch `phase-7-real-datasets`. Replaces the synthetic QSTS profiles with
measured, open data and closes review finding **C9** (synthetic load
peaking at midday together with solar) and **issue #3** (no real
profiles). Roadmap renumbering: real data is now Phase 7. The certified
surrogate moves to Phase 8 (its large speed-up needs the τ̄ criterion and
benefits from training on real profiles). Streaming HC / operating
envelope stays Phase 9.

## What was added

| Area | Change |
|---|---|
| `simulator/gridsense_sim/datasets/` | New package. Loaders: `inmet`, `nasa_power`, `pvgis`, `ausgrid`, `lcl`, `simbench_profiles`. Infrastructure: `base` (types), `cache` (download/SHA-256/offline), `quality` (QC report), `solar` (geometry, BSRN limits, PV model), `series` (build/save/load QSTS series), `registry`, `cli` (`gridsense-data`). |
| `hosting_capacity/timeseries.py` | `TimeSeries` gains `source` (`"synthetic"` or `"real:<load>+<pv>"`) and `manifest_sha256`. Defaults keep every existing call unchanged. |
| `hosting_capacity/qsts.py` | `QstsHCResult` reports `series_source` and `series_manifest_sha256`. |
| `scripts/run_hosting_capacity_study.py` | `--qsts-series PATH`. The file is integrity-checked on load. Its path, manifest SHA-256, source, resolution, start and site are recorded in `params`, and `series_source` / `series_manifest_sha256` in the result. Synthetic-only knobs are not recorded with a real series. Combining it with `--qsts-peak-load` is rejected up front. |
| `simulator/pyproject.toml` | Extra `data = [pyarrow, simbench]`. Console script `gridsense-data`. `pandas>=2.2`. |
| `Makefile` | `data-list`, `data-qc`, `series-flo`, `hc-qsts-real`, `test-datasets`, `test-real-data`. |
| `docs/DATASETS.md` | Sources, licences, citations, the quirks found in the real files, the QC and series pipeline, limitations. |
| `.gitignore` | `data/series/`. |

No change to the deterministic or stochastic estimators, the dbt models,
the API or the dashboards. QSTS rows produced from a real series flow
through the existing pipeline (the new payload fields are schema-on-read
extras).

## Verification on genuine raw files

Every loader was run on a real file from its source, in addition to the
format fixtures. The opt-in suite `tests/test_datasets_real_files.py`
(`GRIDSENSE_REAL_DATA_DIR`) passed 5/5 on:

| Source | Real file used |
|---|---|
| INMET | A806 Florianópolis 2024-01..07 (re-exported layout) and A304 Natal 2025-01..05 (header block lost) |
| Ausgrid | `2012-2013 Solar home electricity data v2.csv`, 300 homes |
| LCL | One household, 2013 |
| NASA POWER | API v2.10 hourly response |
| PVGIS | `seriescalc` JSON response |
| SimBench | `simbench` 1.6.3 package data |

The timing conventions were **measured** against solar noon. INMET labels
the hour end; Ausgrid uses the Sydney wall clock with DST; LCL uses UTC;
SimBench uses CET/CEST. See `docs/DATASETS.md` for the numbers.

## Bugs found by the tests while building this phase (fixed before commit)

1. The alignment check used partial days: a 10-hour morning-only file read as −135 min. Now only days with ≥ 90 % of their steps count.
2. Night spikes were filed as "above the physical limit" (the BSRN limit is 100 W/m² at night). The night check now runs first.
3. The local-clock conversion meant to fill the DST spring-forward slot also nibbled the first step of genuine data gaps (pandas `interpolate(limit=)`). Now only slots created by the clock change are filled.
4. `urlopen` on `file://` has no HTTP status. The redundant status check was removed (`urlopen` raises `HTTPError` itself).

## First hosting-capacity results on real data (preliminary)

`cigre_lv`, Florianópolis. The load is the aggregate of 299 Ausgrid homes;
irradiance is INMET A806 2024 with the horizontal PV model. Each window is
28 days at 15 min. The annual peak of the aggregate maps to load ×1.0
(`source_peak`). Search: `min_over_steps`, zero tolerance, tolerance 0.01.

| Window | Criterion | λ | Total PV | Binding | First violation |
|---|---|---|---|---|---|
| Jan 8 – Feb 4 (summer) | EN 50160 envelope | 1.961 | 1.347 MW | trafo 0 (100.0 %) | 1 Feb 12:30 |
| Jan 8 – Feb 4 (summer) | PRODIST M8 BT | — | — | `baseline_infeasible`: bus 35 at 0.918 pu at 17:30 on day 1, no PV | — |
| Jun 1 – Jun 28 (winter) | EN 50160 envelope | 3.158 | 2.168 MW | trafo 0 (100.2 %) | 27 Jun 12:00 |
| Jun 1 – Jun 28 (winter) | PRODIST M8 BT | **2.302** | **1.581 MW** | overvoltage, bus 16 (1.050 pu) | 5 Jun 12:00 |

Series diagnostics:

| Window | Load mean | Load max | PV max | PV capacity factor | Load at the PV-peak hour / load peak |
|---|---|---|---|---|---|
| January | 0.29 | 1.00 (Sydney heatwave in the source window) | 0.80 | 0.181 | 0.73 |
| June | 0.36 | 0.72 | 0.51 | 0.092 | 0.50 |

Sensitivity (not a result): the same January window under PRODIST with
the annual peak mapped to ×0.9 (`--peak-load 0.9`, below the ×0.919
baseline threshold) gives **λ = 1.383 (0.949 MW), overvoltage at bus 16**.
That is ~40 % below June. The normalisation peak is a modelling choice to
settle with the advisor.

Readings:

- **First PRODIST QSTS number.** This is the first PRODIST QSTS number the project has produced (impossible with the synthetic profile, C9). It is binding on overvoltage at the same bus as the deterministic PRODIST result, which is consistent.
- **The PRODIST baseline gate fails in summer.** On `cigre_lv` it holds only for load ≤ ×0.919 (measured by bisection with PV = 0). Any window containing the evening peak of a hot week therefore makes zero-tolerance QSTS undefined. This is the scientific case for the PV-attributable / duration criterion (open item C7/S1), not a bug.
- **Summer binds.** It has the strongest sun and, in this source, a lower midday load relative to the annual peak. The winter window's higher λ partly reflects the horizontal PV model (no tilt gain in winter). Confirm with PVGIS at a realistic tilt before citing.
- **Comparison with the synthetic QSTS.** The synthetic 60-day EN 50160 run gave λ = 2.108. The real January window gives 1.961 (−7 %).

These are **preliminary**. They use one benchmark network, an Australian
household proxy for Brazilian load, the horizontal PV model and the
zero-tolerance criterion. They are not Chapter 5 results.

## Tests

- **New:** 47 tests in `test_datasets_core.py`, `test_datasets_loaders.py` and `test_datasets_series.py`, all offline with format-faithful fixtures generated in `tests/_dataset_fixtures.py` (no third-party data committed).
- **Opt-in:** 5 tests in `test_datasets_real_files.py`, skipped unless `GRIDSENSE_REAL_DATA_DIR` is set.
- **Existing suites:** unchanged and still passing (see the PR description for counts).

## Open items

- Confirm the licence terms of LCL and SimBench before quoting them (**[VERIFY]**).
- Run the first real downloads (INMET, NASA POWER, PVGIS, LCL) on a normal network. The development sandbox blocks those hosts, so their *download paths* are tested with `file://` mirrors only.
- Methodology decisions for the advisor:
  - PRODIST with real evening peaks (gate per step vs PV-attributable criterion).
  - The normalisation peak.
  - Ausgrid as the Brazilian load proxy.
- Per-bus assignment of individual homes (the per-home columns are kept for it).
- PVGIS tilted-plane series for the seasonal comparison.
