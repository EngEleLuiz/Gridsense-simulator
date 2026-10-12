# Phase 7.5 — Frozen experiment protocol

Branch `phase-7.5-protocol`. Everything needed so that the long simulations run **once**.

| Area | Change |
|---|---|
| `protocol/protocol-v1.json` | 49 runs in 6 groups (A–F), fixed parameters, time estimates. See `docs/EXPERIMENT_PROTOCOL.md`. |
| `scripts/run_protocol.py` | Resumable executor: refuses a dirty tree; labels every run; writes one marker per run (exit code, duration, commit, protocol SHA-256) and one log; skips finished runs; `--list`, `--dry-run`, `--only`. |
| dbt staging + `mart_hosting_capacity` | New columns `series_source`, `series_manifest_sha256`, `series_block_average_min`, `phase_model`, `existing_pv`, `run_label`. |
| Dashboards 3 and 4 | Keyed by series and conditions, so variants (10 min vs hourly, per-load vs aggregated, phase models) no longer overwrite each other. |
| Runner | `--run-label`; `--qsts-aggregate-load` (sensitivity); the aggregated variant is marked in `series_source`. |
| `duration.py` | PRODIST reference updated from REN 956/2021 (rev. 13). Verified: items 26.2, 26.5, 28 and the Table 4 adequate band. The 191/233 V edges remain secondary-source. |

## Checks done before freezing

| Check | Result |
|---|---|
| Warm start across weeks | Each window of a 4-window run equals the window run alone (new test) |
| Stochastic CI | p10 needs ≥ 36 scenarios; 60 are used. The real run is `is_comparable = true` |
| Real dbt build with the new columns, on real labelled payloads | 16/16 passed |
| Bug: aggregated and per-load variants shared `series_source` | Found in that validation and fixed, with a regression test |
| Bug: `--dry-run` created the output directories | Fixed |
| Every protocol run passes the runner's argument validation | Covered by a test |
