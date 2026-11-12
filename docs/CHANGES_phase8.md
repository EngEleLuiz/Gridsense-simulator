# Phase 8 — Acceleration and Brazilian networks

Branch `phase-8-acceleration-brazil`, on top of the four code-review branches
(`fix/hc-correctness`, `fix/pipeline-integrity`, `fix/real-data-loaders`,
`fix/platform-hardening`).

| Area | Change |
|---|---|
| `gridsense_sim/accel/model.py` | Compact model of a balanced network, read from pandapower's own admittance data and self-checked against a pandapower power flow. Refuses what it does not model (`UnsupportedNetworkError`). |
| `gridsense_sim/accel/solver.py` | Batched fixed-point power flow in PyTorch, CPU or CUDA, `float64` (default) or `float32`; per-sample convergence flag; batches split by a memory budget. |
| `hosting_capacity/qsts_batched.py` | Duration-criterion QSTS on the batched solver: same criterion code, same result type, capacities within the search tolerance of the pandapower backend. |
| `hosting_capacity/qsts_duration.py` | Window planning and result assembly moved into helpers shared by both backends (no behaviour change); result gains `backend`, `backend_info`. |
| Runner | `--qsts-backend torch`, `--device`, `--torch-dtype`, `--torch-memory-mb`; `--network-file` (+ `--network-default-framework`, `--network-voltage-kv`); `--qsts-criterion-file`. `params.network_source` records the network file's SHA-256. |
| `networks.py` | `register_network`, `register_network_file`, `unregister_network`: networks outside the built-in panel are registered data with mandatory voltage limits. |
| `duration.py` | `criterion_from_dict`, `register_criterion`, `load_criterion_file`; `protocol/criteria/prodist_m8_127v.json` as the first criterion defined as data (band edges `to_verify`). |
| `gridsense_sim/bdgd/` | `gridsense-bdgd inspect / list / build`: LV network of one distribution transformer from a BDGD export, with a build report of every assumption. No data shipped. |
| `scripts/benchmark_power_flow.py` | pandapower vs batched solver per power flow, per batch size / device / precision, with accuracy; optional end-to-end week. |
| `scripts/compare_protocols.py` | Run-by-run comparison of two protocol result directories; exit code 1 outside the tolerance. |
| `scripts/derive_protocol.py`, `protocol/protocol-v2.json` | Protocol v1 with the 19 duration-QSTS runs on the batched solver; v1 is untouched and results go to `data/protocol-v2/`. |
| CI | The simulator job installs the CPU build of PyTorch so the new code is tested and counted in coverage. |
| Docs | `docs/ACCELERATION.md`, `docs/BDGD.md`, `protocol/criteria/README.md`. |

## Found while validating the solver

**`kerber_vorstadt` was not the same network on every build.** pandapower draws the
type of each service cable ("NAYY 4x50" or "NYY 4x35") with Python's global `random`
module, so two calls of the factory — and therefore two processes of a protocol run —
gave different impedances. The batched model, built from one instance, disagreed with
pandapower on another instance by up to 9e-5 pu, which is how it was noticed. The
factory now uses a fixed seed and restores the caller's random state
(`networks._kerber_vorstadt`, test in `test_plugins.py`).

Effect on earlier results: the deterministic runs on this network were bound by the
transformer (the same in every draw), so runs A are not expected to move; the
stochastic run C on this network was computed on a draw that can no longer be
reproduced and should be re-run. The other panel networks were checked and are
deterministic.

## Checks

| Check | Result |
|---|---|
| Model vs pandapower, reference state, 7 balanced panel networks | max 4e-9 pu, loadings 3e-6 % |
| Batched solver vs pandapower, random operating points | max ~3e-8 pu |
| Capacity, torch vs pandapower backend, `cigre_lv`, 2 weeks at 10 min, 3 criteria | all differences below the tolerance of 0.01 |
| Capacity, `simbench_lv_semiurb4`, own per-load profiles, 14 days, PRODIST DRP/DRC | 3.0691 (pandapower) vs 3.0656 (torch) |
| Annual run D (`simbench_lv_rural1`, 357 days) torch vs pandapower | **not compared** in development (pandapower reference takes over an hour); `scripts/compare_protocols.py` does it on real results |
| Full simulator suite | 261 passed, 6 skipped (5 need real data files, 1 needs a GPU); coverage 92 % |
| Every protocol-v2 run passes the runner's validation; v2 equals the derivation of v1 | tests |
| GPU path | **not run** in development (no GPU available); `test_cuda_matches_cpu` runs it where a GPU exists |

## Not done in this phase

* No vectorised NumPy engine (dropped from the plan): PyTorch on CPU plays that role.
* No batched three-phase solver: BDGD feeders are balanced equivalents.
* Deterministic, stochastic and classic zero-tolerance QSTS still use pandapower.
* dbt seeds are not extended automatically for file networks.
* BDGD column names beyond those listed as confirmed in `bdgd/schema.py` are unverified
  against a real release; `gridsense-bdgd inspect` reports mismatches.
