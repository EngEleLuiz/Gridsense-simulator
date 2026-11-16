# Experiment protocol v1 (frozen before the long runs)

This file is the human-readable side of `protocol/protocol-v1.json`. Every number in the dissertation's results chapter must come from a run of this protocol, tagged `protocol-v1`, on a clean git tree.

```bash
python scripts/run_protocol.py --list      # runs, estimates, what is done
python scripts/run_protocol.py --dry-run   # exact commands
python scripts/run_protocol.py             # run (resumable; skips finished runs)
```

## Decisions (advisor-facing; changing any of them means a new protocol version)

| # | Decision | Choice |
|---|---|---|
| 1 | Primary voltage criterion | PRODIST Module 8; EN 50160 as the European contrast |
| 2 | Main data case | SimBench grids with **their own per-load profiles** (network and data from one source, no transfer) |
| 3 | Load model | Per-load profiles on SimBench (data as published); **aggregated load is the sensitivity** (group E). London: aggregated households |
| 4 | Existing PV | Removed (capacity on an empty grid, comparable across networks) |
| 5 | Snapshot operating point | Literature point (load ×0.25) for the panel (groups A–C); **series-derived point** (max PV, min load; per-load state) for the same-data comparison (group D) |
| 6 | Stochastic | α = 0.10, 60 scenarios (≥ 36 needed for the distribution-free CI of p10), adoption 30–100 % of load buses |
| 7 | Compliance window | Every 168-h window must comply (eventual measurement). PRODIST permanent measurement uses the monthly mean of the windows (item 26.6), which is less strict; the per-window capacities are stored so the monthly-mean variant can be computed afterwards |

## PRODIST verification status (rev. 13, REN 956/2021)

| Element | Status |
|---|---|
| 1008 readings of 10 min (item 26.2) | Verified |
| DRP ≤ 3 %, DRC ≤ 0.5 % (item 28) | Verified |
| nlp, nlc = worst phase (item 26.5) | Verified (balanced model: phases equal) |
| Adequate band 202–231 V for 220/127 V (Table 4) | Verified |
| Precarious/critical edges 191 V and 233 V | Secondary sources only; check Annex 8.A, Table 4, before citing |
| Unbalance: FD95 % ≤ 3 % below 2.3 kV (Table 3) | Verified |

## Groups

| Group | Runs | Content | Estimate |
|---|---|---|---|
| A | 14 | Deterministic panel, 7 networks × {PRODIST, EN 50160} | ~15 min |
| B | 8 | IEEE European LV (UK), unbalanced vs balanced-equivalent, source 1.05 / 1.00 pu | ~10 min |
| C | 8 | Stochastic (PRODIST) on 6 networks + UK feeder in both phase models | ~30 min |
| D | 9 | **Main case**: 3 SimBench grids × annual window (2016-01-04 + 357 d) × {τ = 0 with deterministic and stochastic at the series point, PRODIST DRP/DRC, EN 50160 weekly} | ~6 h |
| E | 6 | Sensitivities on SimBench, annual DRP/DRC: hourly blocks (E3) and aggregated load | ~4 h |
| F | 4 | London secondary case (LCL 2013 + `uk_pv` 2018, 28 days): E1, τ = 0, hourly, EN 50160 weekly | ~30 min |

Total about 11 h. Estimates come from measured runs (one SimBench week ≈ 40 s); the first annual run should confirm them.

**SimBench resolution.** SimBench profiles are 15-min: the PRODIST fractions (3 %, 0.5 %) are applied to 15-min readings (672 per week, allowances 20 and 3). This is recorded as `resolution_matches_criterion = false` in every such result.

## What each result records

`params.run_label = protocol-v1/<run>`, plus the following fields:

- the git commit and dirty flag;
- the series source and manifest hash;
- the block-average resolution;
- the phase model;
- the existing-PV policy.

The dbt mart exposes all of them (`series_source`, `series_block_average_min`, `phase_model`, `existing_pv`, `run_label`), so different variants never overwrite each other in the dashboards.

## Out of scope for v1 (declared)

- QSTS on three-phase networks (the UK feeder ships snapshots only).
- Weather-year replicates for London (E2): `uk_pv` 5-min data start in 2018 and LCL ends in Feb 2014; it needs a full-year download per PV year.
- Tap control (C8): delimitation; MV/LV distribution transformers usually have off-load taps. Protocol v3, group H, measures how much a capacity depends on the fixed source voltage.
- Inverter power factor other than unity: protocol v3, group G.
- Independent power-flow validation (OpenDSS): done for the six single-source panel networks, see [`VALIDATION.md`](VALIDATION.md).

## Protocol v3: sensitivity addendum (Phase 8.1)

`protocol/protocol-v3.json` is written by `scripts/build_sensitivity_protocol.py` from protocol v2. It does not repeat any base run: each of its 70 runs changes **one** assumption of a v2 run and names that run (`baseline`) and the factor (`factor`). Results go to `data/protocol-v3`.

| Group | Runs | Factor | Levels | Base runs |
|---|---|---|---|---|
| G | 17 | PV power factor (inductive, inverter rated S = P / pf) | 0.95, 0.92 on the deterministic panel; 0.92 on the annual DRP/DRC of the three SimBench grids | A (PRODIST), D (DRP/DRC) |
| H | 34 | Source voltage (fixed tap) | 0.98, 1.00, 1.02, 1.04 pu on the deterministic panel; 0.98 and 1.02 on the annual DRP/DRC | A (PRODIST), D (DRP/DRC) |
| I | 7 | Temporal resolution | 30-min blocks on SimBench (hourly is group E); London from **5-minute** data: 10 min (reference), 15, 30, 60 | D (DRP/DRC); the London 10-min run |
| J | 12 | Adoption model | adoption 0.1–0.4 and 0.7–1.0; 200 scenarios at the base range | C |

About 2.3 h. The London runs need `data/series/london_2018_06_5min.parquet` (`make series-london-5min`); without it they are skipped and reported as missing.

**Why 5-minute data for the resolution curve.** A 10-minute criterion cannot be evaluated exactly on 15-minute data, which is what the main case does (recorded as `resolution_matches_criterion = false`). The London PV is measured every 5 minutes, so the same series can be averaged over 10, 15, 30 and 60 minutes and the bias b(w) measured against the criterion's own reading length. b(15) is the order of magnitude of the error of the main case; it is not applied as a correction (other network, other climate).

**Reactive power and the lower side.** With `--pv-power-factor < 1` the PV absorbs reactive power and can lower voltages behind the transformer. The duration search follows the upper side only; `verification.worst_bus_lower_drp` / `_drc` and `lower_side_compliant` report the lower side at the capacity, next to the no-PV `baseline_lower` of the same window.

```bash
make protocol-v3          # python scripts/run_protocol.py --protocol protocol/protocol-v3.json
make report               # tables and figures from data/protocol-v2 (+ v3 when present)
```

## Before the long runs

1. Freeze `docs/HYPOTHESES.md` (date, commit, advisor).
2. `python scripts/run_protocol.py --protocol protocol/protocol-v2.json --list` names the criteria still marked `to_verify`. Clear them against Annex VIII of REN 956/2021, or run knowingly: every run marker and every result records the flag, and the report lists the affected rows.
3. `make validate-opendss` on the machine that will produce the results.
4. `make env-lock`, commit, tag; run on the clean tree.
