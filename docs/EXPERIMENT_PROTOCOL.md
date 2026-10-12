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
- Tap control (C8): delimitation; MV/LV distribution transformers usually have off-load taps.
- Inverter power factor other than unity (sensitivity planned).
- Independent power-flow validation (OpenDSS): required before publication, not before running.
