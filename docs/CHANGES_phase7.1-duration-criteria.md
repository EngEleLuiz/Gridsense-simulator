# Phase 7.1: Duration criteria, one dataset for three methods, London data

Branch `phase-7.1-duration-criteria`. Closes review finding **C7** (QSTS without a duration criterion) and the design gap "the three methods do not read the same data".

## Why

A zero-tolerance QSTS only reports the worst instant of a series, which a snapshot study already gives. The criteria that define voltage quality are weekly statistics over 10-minute readings:

- **ANEEL PRODIST Module 8.** One measurement is 1008 valid 10-minute readings (one week). Readings are adequate, precarious or critical. The durations must stay within **DRP ≤ 3 %** and **DRC ≤ 0.5 %**.
- **EN 50160.** In each week, 95 % of the 10-minute means must lie within Un ±10 %, and all of them within Un +10 % / −15 %.

**Correction to earlier project docs.** Earlier docs called the EN 50160 ±10 % "statistical" on both sides. That is wrong for the PV side: no 10-minute mean may exceed +10 %. The 5 % allowance only relaxes the lower side. The `limits.py` docstring is corrected accordingly.

## What was added

| Module | Content |
|---|---|
| `hosting_capacity/duration.py` | `DurationCriterion` and three criteria:<br>• `prodist_m8_drp_drc` (primary)<br>• `en50160_weekly` (upper side: zero tolerance above +10 %)<br>• `prodist_m8_zero_tolerance` (τ̄ = 0 reference)<br>Also `weekly_capacity`, an exact first-exceedance sweep over per-step thresholds that also handles readings moving from precarious to critical. |
| `hosting_capacity/qsts_duration.py` | `find_hosting_capacity_duration`, a week-by-week search with PV attribution and a measured per-bus verification (see below). |
| `hosting_capacity/study_design.py` | `critical_point(series)`: the snapshot operating point taken from the series (max PV with min load, or max PV/load ratio). |
| `deterministic.py`, `stochastic.py` | New `pv_scale` argument. At a series-derived point, λ and the reported MW measure **installed** capacity, like QSTS. |
| `datasets/uk_pv.py` | Loader for Open Climate Fix `uk_pv` (Sheffield Solar metered PV, CC-BY-4.0). It selects systems within a radius, cleans as the dataset card recommends, and builds a capacity-weighted fleet profile. Timing is checked against the sun. |
| `datasets/series.py` | `load_clock="auto"`: physical time when the load and the target share a time zone (same place), wall clock otherwise. Without this, a London study would shift the load one hour against the sun during summer time. Adds a `london` preset. |
| Runner | New options:<br>• `--qsts-criterion` (default `zero_tolerance`: unchanged legacy behaviour)<br>• `--qsts-windows`<br>• `--qsts-skip-night-baseline`<br>• `--critical-point-from-series`, `--critical-point-rule`, `--critical-point-pv-fraction`<br>Duration rows stay `method = "qsts"`; `criterion` and `conditions.framework` carry the criterion name, so they never mix with zero-tolerance rows in the mart. |

### How the duration search works

| Rule | Detail |
|---|---|
| PV attribution | Upper voltage and PV-attributable overload (loading above both the rating and the no-PV loading) set the capacity. |
| Load-driven constraints | Lower voltage and forward overload are reported as baseline compliance and never block the search. The legacy `baseline_infeasible` failure on real evening peaks is gone. |
| Base-relative rule | Readings that already violate without PV cannot be made worse than `max(allowance, baseline count)` (Jain et al., NREL 2019). |
| Thresholds | Each step's thresholds are monotone in λ. The weekly capacity is exact given those thresholds. |
| Search cost | One full pass at the bracket, then a shrink over the steps that had an event (starting from the riskiest step's threshold), then bisection only for the remaining steps. |
| Verification | The critical week is re-solved at the capacity to report worst-bus and any-bus DRP/DRC. Any-bus counting is conservative; the gap is measured. |

## Measured on `cigre_lv` (one synthetic week at 10 min)

| Criterion | λ | Binding | Power flows |
|---|---|---|---|
| Legacy zero-tolerance QSTS (`prodist_m8_bt`) | 1.2969 | overvoltage, bus 16 | 1,846 |
| `prodist_m8_zero_tolerance` (new search) | 1.2971 | upper critical | 3,261 |
| `prodist_m8_drp_drc` | 1.3618 | upper DRP (31 > 30 readings) | 2,758 incl. verification |
| `en50160_weekly` | 1.6986 | PV-attributable transformer overload | 3,518 (before optimisation) |

| Search stage (PRODIST week) | Power flows |
|---|---|
| Baseline (1,008 steps) | 1,008 |
| Bracket | 511 |
| Shrink | 485 |
| Thresholds (32 steps) | 250 |

The new zero-tolerance criterion reproduces the legacy QSTS (Δλ = 0.0002, within tolerance). In this week the PRODIST duration allowance raises the capacity by 5 %. The verification found the worst bus at DRP 2.8 % and DRC 0 % at the capacity, so any-bus counting cost nothing here.

A full year (52 weeks, warm-started) is estimated at 50–60 minutes. `--qsts-skip-night-baseline` saves about 500 power flows per week but drops the lower-side report.

## Tests

- **`test_hc_duration.py` (17 tests).** Coverage:
  - criteria and allowances (30 / 5 readings out of 1008);
  - sweep edge cases: the sixth critical reading, the 31st precarious one, readings moving to critical, the base-relative allowance;
  - **agreement with exhaustive per-step thresholds**;
  - **equivalence with the legacy QSTS**;
  - the duration allowance never lowers the capacity;
  - load-driven undervoltage no longer blocks the search;
  - windows and per-load matrices;
  - the critical-point rules and the ordering QSTS(τ = 0) ≤ deterministic(series point);
  - the runner end to end.
- **`test_datasets_uk_pv.py` (3 tests).** Coverage:
  - selection, cleaning, `bad_data.csv`, night-faulty systems and period-ending stamps;
  - the 30-minute product and the error paths;
  - same-site physical clock vs wall clock.

## Open

- **Per-bus diversity.** The search accepts a per-load matrix, but building it from individual LCL homes is not wired yet.
- **PRODIST band edges.** They come from the 220 V table; verify against the revision in force (marked `to_verify`).
- **Thermal duration.** Instantaneous only for now. NREL's 2-hour moving average is a candidate sensitivity.
- **Tap control (C8).** Treated as a delimitation: MV/LV distribution transformers normally have off-load taps (to confirm in the applicable standard).
