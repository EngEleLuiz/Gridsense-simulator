# Phase 8.1 — Rigor: validation, sensitivities, analysis

Branch `phase-8.1-rigor-gaps`, on top of `main` after Phase 8 (PR #45). No
existing result changes: every new option defaults to the previous behaviour,
unity-power-factor runs keep their parameter set, and protocols v1 and v2 are
untouched.

| Area | Change |
|---|---|
| `hosting_capacity/allocation.py` | `reactive_per_active(pf)`; `apply_allocation(..., q_per_p)` sets P and Q of the study PV. |
| Deterministic, stochastic, QSTS (both criteria families, both backends) | `pv_power_factor` argument (0.8 to 1, inductive). λ keeps measuring active power; the inverter is assumed rated S = P / pf. Recorded in `conditions.pv_power_factor`. Three-phase networks refuse it. |
| `accel/model.py` | `build_model(..., pv_q_per_p)`: the study PV injection is complex. |
| `qsts_duration.py`, `qsts_batched.py` | Verification of the critical window also reports the **lower** side at the capacity (`worst_bus_lower_drp`, `worst_bus_lower_drc`, `lower_side_compliant`), for every run. |
| Runner | `--pv-power-factor`; `--source-vm-pu` now also applies to balanced networks (every external grid; the network's own default is recorded); `--require-verified-criterion`. |
| `hosting_capacity/duration.py` | `DurationCriterion.verification` (what was checked, against what, when); `unverified()`. |
| `scripts/run_protocol.py` | `--list` names the criteria still `to_verify`; run markers record `criterion_to_verify`; `--require-verified-criteria`. |
| `gridsense_sim/analysis/` | `load_results` (flat records, latest row per run), `relative_effect` (ties at the search resolution), `weekly_summary`, `block_bootstrap_quantile`, `paired_summary`, `resolution_bias`. |
| `scripts/analyze_protocol.py` | `results.csv`, `tables.md`, `analysis.json` (with the SHA-256 of every result file) and, with `--figures`, four figures stamped with protocol id and commits. |
| `scripts/build_sensitivity_protocol.py`, `protocol/protocol-v3.json` | Sensitivity addendum: 70 runs, one factor each, each naming its base run. |
| `gridsense_sim/validation/opendss.py`, `scripts/validate_against_opendss.py` | pandapower against OpenDSS: voltages, loadings and the capacity. |
| `provenance.py` | Also records the versions of `torch`, `simbench` and `pyarrow`. |
| `accel/solver.py` | `gpu_backend()` tells CUDA (NVIDIA) and ROCm (AMD) builds apart and `backend_info` records it with the HIP version; `GRIDSENSE_DEVICE` replaces `--device auto`, so a frozen protocol can be sent to the CPU or a chosen GPU without editing it. |
| `scripts/check_environment.py` | Packages, PyTorch build, GPU visible, GPU equal to CPU in float64 (1e-10 pu), CPU/GPU timing and a device recommendation, protocol input files. |
| `docs/SETUP.md` | A new machine from scratch, including AMD (ROCm on Windows 11) and NVIDIA GPUs. |
| Repository | `CITATION.cff`; extras `plots` and `validation`; `make protocol-v3`, `series-london-5min`, `report`, `validate-opendss`, `env-lock`; CI installs both extras. |
| Docs | `HYPOTHESES.md`, `VALIDATION.md`; protocol v3 in `EXPERIMENT_PROTOCOL.md`; threats table in `STUDY_DESIGN.md`. |

## Why each piece

**Power factor.** Unity power factor is the worst case for overvoltage, and the
connection rules allow or require reactive support. Without the option, a
reader cannot tell how much of a voltage-bound capacity is the assumption.

**Source voltage.** Tap control stays out of scope. What a fixed tap leaves open
is the voltage the feeder is operated at, and a capacity bound by overvoltage
moves almost linearly with it.

**Lower side at the capacity.** The duration search follows the constraints
that PV at unity power factor can make worse. With reactive absorption that is
no longer the whole picture, so the lower side is measured where it matters.

**Analysis layer.** A number copied by hand from a Parquet file has no
uncertainty and no commit. The report gives both, and says which rows must not
be cited and why.

**Protocol v3 as an addendum.** Repeating the base runs under a new id would
produce two sets of "the same" numbers. v3 only adds runs and points at v2.

**OpenDSS.** The batched solver was validated against pandapower, and
pandapower against nothing.

## Measured while developing

Development environment: Linux, Python 3.13, pandapower 3.5.6, PyTorch 2.15
(CPU), opendssdirect.py 0.9.4. These are checks of the code, not results.

| Check | Result |
|---|---|
| pandapower vs OpenDSS, six single-source panel networks, two criteria, three states | capacities identical; voltages within 4e-5 pu (2e-8 pu on `cigre_lv`); loadings within 0.004 percentage point. `lv_schutterwald` not covered. Table in `VALIDATION.md` |
| Batched model with reactive PV vs pandapower, `cigre_lv`, pf 0.9 | voltages within 1e-7 pu, transformer loading within 1e-4 % |
| Duration QSTS at pf 0.92, torch vs pandapower backend, `cigre_lv`, one week | capacities within the tolerance, same binding constraint, same lower-side report |
| `cigre_lv`, deterministic, PRODIST, pf 1 → 0.95 → 0.92 | λ 1.164 → 1.438 → 1.375; the binding constraint moves from overvoltage (bus 16) to the transformer |
| `cigre_lv`, deterministic, EN 50160 envelope, pf 0.92 | lower than at unity (transformer-bound: the reactive current costs capacity) |
| `cigre_lv`, deterministic, PRODIST, source 0.98 / 1.00 / 1.02 / 1.04 pu | λ 1.508 / 1.164 / 0.820 / 0.477 |
| Simulator suite | 304 passed, 6 skipped (5 need real data files, 1 needs a GPU); coverage 91 % |

## Not done

* **The PRODIST band edges are still `to_verify`.** They agree with the ARSESP
  guide on voltage conformity, but Annex VIII of REN 956/2021 could not be
  fetched while this was written. Read the table and clear the flag in
  `duration.py` and `protocol/criteria/prodist_m8_127v.json`.
* **`prodist_m8_bt` uses 0.92 pu as the lower edge of the adequate band; the
  duration criterion uses 202/220 = 0.918.** The difference is on the lower
  side, which does not decide a PV capacity, but it can decide whether a
  baseline is feasible. Left as it is: changing it changes published numbers
  and is a decision, not a fix.
* **No Volt-VAr or Volt-Watt.** A voltage-dependent control breaks the
  proportionality between λ and the injections that the monotone search relies
  on, and the batched fixed point would have to iterate on it.
* **No tap controller**, no VVSI / OSI / RPFI metrics (C8, C13): unchanged.
* **OpenDSS cross-check of `lv_schutterwald` and of the three-phase feeder.**
* **BDGD time series.** The converter already takes the load factor from the
  typical curves (`CRVCRG`) when the layer is present. Running a QSTS on a
  converted feeder still needs a per-load series built from those curves; that
  needs a real BDGD export to confirm the column names first.
* **Weather-year replicates** (E2) and the re-run of the stochastic
  `kerber_vorstadt` case with the fixed seed: these are runs, not code.
* **`HYPOTHESES.md` is a draft.** The thresholds are proposals until dated and
  agreed.
