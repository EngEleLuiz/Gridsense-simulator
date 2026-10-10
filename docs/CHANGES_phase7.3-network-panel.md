# Phase 7.3 — Network panel: German LV grids

Branch `phase-7.3-network-panel`. External validity: the methods now run on a panel of
distribution grids instead of a single benchmark, including a **real** German LV grid
and a case where network and profiles come from the same source (no data transfer).

## What was added

| Area | Change |
|---|---|
| `gridsense_sim/networks.py` | Single network registry. Each entry records factory, country, kind (benchmark / reference / real), description and citation. Before this, network factories were duplicated in the runner and in the dbt seed generator. |
| Panel | `cigre_lv`, `kerber_landnetz`, `kerber_vorstadt`, `simbench_lv_rural1`, `simbench_lv_semiurb4`, `simbench_lv_urban6`, and `lv_schutterwald` (real grid: 14 MV/LV substations, 1,506 loads). The IEEE transmission cases stay for the simulator but are excluded from the HC panel. |
| Existing PV | `make_network(key, existing_pv="remove" or "keep")`; runner option `--existing-pv`. SimBench grids ship with PV. `remove` (default) gives capacity on an empty grid, comparable across networks. `keep` gives additional capacity on top of the installed PV. Every result records the policy, network kind and country in `params`. |
| SimBench own profiles | `datasets.simbench_profiles.network_series(net, start, days)`: one relative profile per load as a per-load matrix, plus the capacity-weighted PV profile of the grid's own PV units (15 min, 2016). Runner option `--qsts-network-profiles`, which needs a duration criterion (`prodist_m8_zero_tolerance` for τ = 0). `--qsts-block-average` also averages the per-load matrix. |
| `limits.py` | The panel is registered with the EN 50160 envelope default and 0.4 kV voltage scope. |
| dbt seeds | `network_voltage_limits.csv`: 6 new rows. `network_voltage_scope.csv`: regenerated from the registry (3,540 buses, of which 2,926 are in Schutterwald). |
| Seed test | `test_hc_cross_layer` compares the seed for every network whose optional dependencies are installed. CI has no `simbench`, so it checks all networks except the three SimBench grids. |

## A flaw found and fixed while running the panel

The ordering QSTS(τ = 0) ≤ deterministic(series point) was stated as a guarantee. On `simbench_lv_semiurb4` it **failed**: the deterministic gave 2.523 against a QSTS of 2.664. With one profile per load, the QSTS sees each load's own value at the critical step, but the deterministic applied one aggregate multiplier to every load. They did not read the same operating point.

Fix: `load_multipliers` in the deterministic and stochastic estimators (`conditions.apply_load_multipliers`). The runner passes the per-load state of the critical step. A regression test reproduces the case.

## First results with no data transfer (preliminary)

Setup: SimBench grids with their own profiles, 1 week from 2016-06-01 at 15 min, PRODIST adequate band. λ is installed PV per unit of nominal load; existing PV removed.

| Grid | Deterministic (series point) | QSTS τ = 0 | QSTS PRODIST DRP/DRC | Duration effect |
|---|---|---|---|---|
| `simbench_lv_rural1` | 4.992 | 4.666 | 4.814 (PV-attributable overload) | +3 % |
| `simbench_lv_semiurb4` | 2.664 | 2.664 | 3.473 (upper DRP) | +30 % |
| `simbench_lv_urban6` | 3.250 | 3.180 | 3.511 (upper DRP) | +10 % |

Each QSTS run took 1,400–2,000 power flows (40–85 s). Lower-side baseline compliance held in all three grids, and the worst-bus DRC was 0 at the capacity.

Deterministic panel at the literature point (load ×0.25), PRODIST vs EN 50160:

| Network | PRODIST λ (binding) | EN 50160 λ (binding) |
|---|---|---|
| `cigre_lv` | 1.164 (overvoltage) | 1.586 (transformer) |
| `kerber_landnetz` | 1.297 (overvoltage) | 1.867 (transformer) |
| `kerber_vorstadt` | 2.453 (transformer) | 2.453 (transformer) |
| `simbench_lv_rural1` | 2.266 (overvoltage) | 2.328 (transformer) |
| `simbench_lv_semiurb4` | 1.422 (overvoltage) | 1.969 (transformer) |
| `simbench_lv_urban6` | 1.570 (overvoltage) | 1.727 (transformer) |
| `lv_schutterwald` (real) | 1.289 (transformer 3) | 1.289 (transformer 3) |

Under the Brazilian 1.05 pu limit, overvoltage binds in five of the seven grids; under EN 50160, the transformer binds in all of them. The regulatory criterion moves the bottleneck. One week and one season only: these numbers are not dissertation results yet.

## Tests

`test_network_panel.py` covers:
- the registry and provenance;
- deterministic HC on every non-SimBench panel network;
- the existing-PV policy (keeping installed PV never increases the capacity);
- the SimBench own-profile series and a duration QSTS on it;
- the runner on a German grid, including option validation.

Suite: **163 passed, 5 skipped** (with the `data` extra; without `simbench` the SimBench tests skip).

## Open

- **IEEE European LV test feeder (UK).** Its loads are single-phase (`asymmetric_load`), so it needs a three-phase power-flow path (`runpp_3ph`). Next phase.
- **Schutterwald provenance.** The pandapower docs give the source; confirm it before citing.
- **Schutterwald substations.** HC uses one global λ across its 14 substations, so the weakest substation binds. A per-substation analysis is a natural extension.
