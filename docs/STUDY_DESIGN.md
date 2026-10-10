# Study design (Phase 7.2)

The goal is a controlled comparison of hosting-capacity methods. Rigor
comes from three rules: every method reads **identical inputs**; every
simplification of the data is **declared and measured**; variation in
the data is an **experimental factor**, never a confound.

## Site and data

| Item | Choice | Why |
|---|---|---|
| Site | London | Measured load and measured PV in the same city, clock and hemisphere |
| Load | Low Carbon London, 2013 (265 homes, 30 min) | Largest open measured residential panel |
| PV | `uk_pv` 5-minute product, 2018–2024 (systems within 10 km) | Measured output at the resolution a 10-min criterion needs |
| Network | CIGRE LV benchmark (`cigre_lv`) | Public, reproducible; declared limitation |
| Criterion (primary) | PRODIST Module 8 DRP/DRC, weekly 10-min readings | Brazilian regulatory criterion |

**The one transfer: year.** The two products do not overlap in time:
- LCL covers Nov 2011 – Feb 2014;
- `uk_pv` 5-min data start in 2018.

The load year is held fixed (2013) and projected onto each PV year:
- by month and day;
- moved by at most 3 days so **weekdays match** (`align_weekday`, default);
- with the season unchanged and the clock physical (same place).

Bank holidays are not aligned; that is a declared residual.

## Experiments

| ID | Question | Factor varied | Held fixed |
|---|---|---|---|
| E1 | How much do method and criterion change the capacity? | Method × criterion (see below) | One series: 2013 load + one PV year |
| E2 | Is E1 robust to the weather year? | PV year 2018–2024 (7 replicates) | 2013 load, network, allocation |
| E3 | How much does hourly data misstate a 10-min criterion? | Series as measured vs `block_average(…, 60)` | Everything else |
| E4 | How much do the declared transfers matter? | Weekday alignment on/off; normalisation peak (×1.0 vs ×0.9) | Everything else |

E1 levels, all taking their operating point from the same series (`--critical-point-from-series`):

| Method | Criterion | Note |
|---|---|---|
| Deterministic | PRODIST adequate band | — |
| Stochastic | PRODIST adequate band | p10, n ≥ 60 |
| QSTS | `prodist_m8_zero_tolerance` | Equivalent to the legacy QSTS |
| QSTS | `prodist_m8_drp_drc` | Primary criterion |
| QSTS | `en50160_weekly` | Native criterion of the site |

**Structural results (hold by construction, tested):**
- QSTS(τ = 0) ≤ deterministic(series point);
- QSTS with a duration allowance ≥ QSTS(τ = 0).

The gaps therefore separate two effects: the cost of choosing the critical point by proxy, and the value of the duration allowance.

## Responses

- λ (installed capacity per unit of nominal load) and MW.
- Binding constraint.
- Per-week λ distribution (QSTS: min, p10, p50).
- Worst-bus DRP/DRC at the capacity.
- Baseline (no-PV) lower-side compliance.
- Power flows (cost).

## Threats to validity (declared)

| Threat | Mitigation in the design |
|---|---|
| Load and PV years differ | Load year fixed, weather year replicated (E2); weekdays aligned (E4 measures it) |
| London load, not Brazilian | Explicit delimitation; sensitivity with other load sources is possible (SimBench, Ausgrid) |
| Benchmark network, balanced | Delimitation; per-bus diversity planned |
| PV fleet of ~10 km, not one feeder | Fleet average is smoother than a single roof; noted |
| PRODIST band edges from secondary sources | Marked `to_verify`; confirm in the Module 8 revision in force |
| Tap fixed (C8) | MV/LV distribution transformers usually have off-load taps (to confirm) |

## Commands (one PV year)

```bash
# series: 2013 London load + June 2018 London PV, 10-min steps
python -m gridsense_sim.datasets build-series --load lcl --load-file <LCL csv> \
    --load-opt start=2013-01-01 --load-opt end=2014-01-01 \
    --pv uk_pv --pv-file data/raw/uk_pv --pv-opt start=2018-05-25 --pv-opt end=2018-07-10 \
    --site london --start 2018-06-01 --days 28 --steps-per-day 144 \
    --out data/series/london_2018_06.parquet

# E1: three methods, same data, primary criterion
python scripts/run_hosting_capacity_study.py --network cigre_lv \
    --methods deterministic stochastic qsts --framework prodist_m8_bt \
    --qsts-series data/series/london_2018_06.parquet --qsts-criterion prodist_m8_drp_drc \
    --critical-point-from-series

# E3: same series at hourly resolution
python scripts/run_hosting_capacity_study.py --network cigre_lv --methods qsts \
    --qsts-series data/series/london_2018_06.parquet --qsts-criterion prodist_m8_drp_drc \
    --qsts-block-average 60
```
