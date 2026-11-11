# Batched power flow (Phase 8)

The duration-criterion QSTS (`--qsts-criterion prodist_m8_drp_drc`, `en50160_weekly`,
`prodist_m8_zero_tolerance`) can run on a batched solver written in PyTorch:

```
python scripts/run_hosting_capacity_study.py --network simbench_lv_rural1 --methods qsts \
    --qsts-network-profiles --qsts-start 2016-01-04 --qsts-days 357 \
    --qsts-criterion prodist_m8_drp_drc --qsts-backend torch --device auto
```

`--device auto` uses a GPU when PyTorch sees one and the CPU otherwise. pandapower
remains the default backend and the reference.

## What changes and what does not

| | pandapower backend | torch backend |
|---|---|---|
| Criterion, PV attribution, weekly sweep (`duration.py`) | same code | same code |
| Result type and fields | `QstsDurationResult` | the same, plus `backend`, `backend_info` |
| Power flow | sparse Newton-Raphson, one operating point per call | fixed point on the bus impedance matrix, a whole batch per call |
| Search per window | bracket, shrink, bisect a few steps (built to *avoid* power flows) | every daylight step evaluated at each trial; every event threshold bisected in parallel |
| Networks | everything pandapower supports | balanced; lines, two-winding transformers, shunts, switches, constant-power loads and static generators, several external grids |

The model is not written from scratch: it is read from the admittance data pandapower
builds for one reference power flow (`accel/model.py`), and it must reproduce that
power flow before it is used (`check_tolerance`, default 1e-6 pu) — otherwise the run
stops with `UnsupportedNetworkError` and the pandapower backend has to be used.
Refused explicitly: voltage-controlled generators, voltage-dependent (ZIP) loads,
three-winding transformers, tap-dependent impedance, controllers, storage/ward
elements and three-phase unbalanced networks.

## Agreement with pandapower

* **Per power flow:** voltages agree to about 1e-8 pu and loadings to about 1e-4 % on
  the network panel (pandapower's own convergence tolerance is the limit).
  `tests/test_accel_solver.py` enforces 1e-7 pu.
* **Per study:** both backends report the lower end of a bracket of width
  `<= --tolerance` around the same threshold, reached by different bisection
  sequences. Capacities therefore agree **within the tolerance**, not bit for bit.
  `tests/test_qsts_batched.py` enforces it on three network/criterion pairs.
* **Censored windows** (no exceedance found): the pandapower backend reports the cap
  it would have tried next, the torch backend the largest penetration it actually
  evaluated. Both are lower bounds and both are flagged `bounded = false`.
* **Non-convergence** is treated as a violation by both. The penetration at which a
  solver stops converging is not the same for the two iterations; it lies far above
  the voltage limits on the panel, so it does not decide any capacity there. A result
  whose binding step did not converge should be read with that in mind.

Because power flows are cheap on this backend, a tighter search costs little:
`--tolerance 0.001` is practical here and removes most of the bracket width.

## Precision

`--torch-dtype float64` (default) is the only setting for reported results.
`float32` is faster on consumer GPUs but accurate to about 1e-5 pu, which is larger
than the event margin of the studies (1e-6 pu): an event can flip. Use it to explore.

## Installing PyTorch

CPU only (works everywhere, already a large speed-up):

```
pip install -e "simulator[gpu]"
```

GPU (NVIDIA): install the CUDA build **before** the line above, with the command the
selector at <https://pytorch.org/get-started/locally/> gives for your system
(Windows / Pip / your CUDA version). Then check:

```
python -c "import torch; print(torch.__version__, torch.cuda.is_available(), torch.cuda.get_device_name(0))"
```

Use a Python version for which PyTorch publishes CUDA wheels for your platform
(3.12 at the time of writing). `--device cuda` fails with a clear message when
PyTorch cannot see a GPU; `--device auto` silently falls back to the CPU and records
the device that actually ran in `backend_info`.

## Benchmark

```
python scripts/benchmark_power_flow.py                       # per power flow, panel
python scripts/benchmark_power_flow.py --end-to-end cigre_lv # one PRODIST week, both backends
```

The script reports pandapower's time per power flow (and whether numba is installed),
the batched solver's time per operating point for each batch size, device and
precision, and the largest voltage difference to pandapower. Two effects are kept
apart on purpose:

* **algorithm** — batched matrix products instead of one sparse solve per point; this
  is available on a CPU;
* **hardware** — the same batched algorithm on a GPU.

On small feeders (tens of buses) the CPU is expected to be as fast as a GPU or faster,
because a batch is a few megabytes and the transfer dominates. The GPU pays off with
large networks (thousands of buses, where the dense products are the cost) and large
batches. Report measured numbers from your machine, with the baseline stated
(pandapower with numba), not the numbers of this page.

Measured while developing this phase with `scripts/benchmark_power_flow.py` (2 CPU
threads, no GPU, pandapower 3.5.6 with numba, PyTorch 2.15). The pandapower column is
what the pandapower backend pays per operating point (setting the injections plus
`runpp`):

| network | buses | pandapower | torch, CPU, batch 1008 | max dV (pu) |
|---|---:|---:|---:|---:|
| `cigre_lv` | 44 | 27 ms / power flow | 5.8 µs / power flow | 2.6e-09 |
| `kerber_landnetz` | 15 | 25 ms | 4.3 µs | 1.8e-08 |
| `kerber_vorstadt` | 294 | 29 ms | 70 µs | 9.6e-09 |
| `simbench_lv_rural1` | 15 | 29 ms | 5.6 µs | 3.1e-09 |
| `simbench_lv_semiurb4` | 44 | 32 ms | 11 µs | 2.6e-09 |
| `simbench_lv_urban6` | 59 | 30 ms | 10 µs | 3.9e-10 |
| `lv_schutterwald` | 2940 | 77 ms | 2.6 ms | 3.9e-11 |

The ratio falls from thousands on the small feeders to about 30 on the 2940-bus
network: there the dense products are the cost, which is the case a GPU is for.

| study | pandapower backend | torch backend (CPU) | capacities |
|---|---:|---:|---|
| `cigre_lv`, one synthetic PRODIST week (1008 readings), DRP/DRC | 74 s, 2845 power flows | 0.5 s, 10761 power flows | 2.0168 vs 2.0156 |
| `simbench_lv_semiurb4`, own profiles, 14 days, DRP/DRC (whole command) | 87 s | 13 s | 3.0691 vs 3.0656 |
| `simbench_lv_rural1`, own profiles, 357 days, tau = 0 (whole command) | not re-run here (protocol-v1 run D) | 14 s | compare with your run D |

The torch backend runs *more* power flows (it bisects every event instead of a few
steps) and is still faster. The last two rows include start-up (imports, reading the
SimBench profiles), which is most of the torch time. The annual pandapower reference
was not re-run during development, so that row has no measured agreement yet.

## Acceptance: protocol v2

`protocol/protocol-v2.json` is `protocol-v1` with `--qsts-backend torch` added to the
19 runs that use a duration criterion (`scripts/derive_protocol.py`; the v1 file is
not touched). Same networks, same data, same criteria:

```
python scripts/run_protocol.py --protocol protocol/protocol-v2.json --list
python scripts/run_protocol.py --protocol protocol/protocol-v2.json
python scripts/compare_protocols.py data/protocol-v1 data/protocol-v2 --only-backend-changes
```

Results go to `data/protocol-v2/`. The acceptance criterion for the solver is that
every duration-QSTS capacity of v2 agrees with the corresponding v1 capacity within
the protocol tolerance (0.01). Runs with a three-phase network (group B) and the
deterministic and stochastic methods still use pandapower.

## Limits of this phase

* Balanced networks only. The three-phase path (`runpp_3ph`) is not batched; Brazilian
  LV feeders with many single-phase consumers are therefore modelled as balanced
  (see `docs/BDGD.md`).
* Only the duration-criterion QSTS is accelerated. The deterministic and stochastic
  methods and the classic `zero_tolerance` QSTS use pandapower.
* Dense matrices: memory grows with (injection buses) x (buses + branches). Fine for
  LV networks up to a few thousand buses; a medium-voltage feeder with tens of
  thousands of buses needs a sparse formulation.
