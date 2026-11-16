# Independent validation of the power flow

Every capacity in this repository comes from pandapower. The batched solver of
Phase 8 is checked against pandapower (`docs/ACCELERATION.md`); this page is
about checking pandapower itself against a second, unrelated engine.

## Method

`gridsense_sim.validation.opendss` writes a balanced pandapower network as an
OpenDSS circuit and solves it with the EPRI OpenDSS engine through
`opendssdirect.py`. The two programs share no code and use different
algorithms (Newton-Raphson on the positive-sequence network; current injection
on the three-phase admittance matrix).

```
pip install -e "simulator[validation]"
make validate-opendss          # or: python scripts/validate_against_opendss.py
```

For each network and criterion the script

1. computes the deterministic hosting capacity twice, once with each engine as
   the only solver (same limits, voltage scope, allocation and bisection);
2. compares voltages and branch loadings at the critical operating point
   (load × 0.25) in three states: no PV, PV at the capacity, PV 20 % above it.

Acceptance: voltages within 1e-4 pu, loadings within 0.1 percentage point,
capacities within the search tolerance (0.01) with the same kind of binding
constraint. Exit code 1 otherwise. The report goes to
`reports/validation/opendss.{json,md}` with the commit and the engine version.

## What is translated

| pandapower | OpenDSS |
|---|---|
| one external grid | `Circuit`, stiff source at `vm_pu` |
| line | `Line`, r1 / x1 / c1 (zero sequence set equal to positive sequence) |
| two-winding transformer | `Transformer`, wye-wye: XHL, %loadloss, %noloadloss, %imag, ratio tap |
| load, static generator | constant-power `Load` (`model=1`), with the voltage window opened so OpenDSS never falls back to constant impedance |
| closed bus-bus switch | one node |

Voltage magnitudes of a balanced network do not depend on the vector group, so
transformers are written wye-wye and only magnitudes are compared. Everything
else is refused with `UnsupportedForOpenDss`: several external grids, open line
or transformer switches, shunts, generators, three-winding transformers, ZIP
loads, phase-shifting taps, unbalanced elements.

pandapower 3 applies a tap position only when `tap_changer_type` is set; older
versions apply it whenever it is given. The translation follows the installed
version's rule, and the test of an off-nominal tap sets the type explicitly.

## Result

Measured on 2026-11-15 in the development environment (Linux, Python 3.13,
pandapower 3.5.6, opendssdirect.py 0.9.4), at the commit that introduced the
check. Re-run the command above to reproduce it on your machine; the numbers to
cite are the ones of your own report.

| network | criterion | λ pandapower | λ OpenDSS | max abs dV (pu) | max abs d loading (pp) | binding |
|---|---|---|---|---|---|---|
| cigre_lv | prodist_m8_bt | 1.1641 | 1.1641 | 2.0e-08 | 5.3e-05 | overvoltage |
| cigre_lv | en50160_envelope | 1.5859 | 1.5859 | 2.1e-08 | 5.3e-05 | transformer loading |
| kerber_landnetz | prodist_m8_bt | 1.2969 | 1.2969 | 3.5e-05 | 2.5e-03 | overvoltage |
| kerber_landnetz | en50160_envelope | 1.8672 | 1.8672 | 3.5e-05 | 3.5e-03 | transformer loading |
| kerber_vorstadt | prodist_m8_bt | 2.4531 | 2.4531 | 1.0e-05 | 9.0e-04 | transformer loading |
| kerber_vorstadt | en50160_envelope | 2.4531 | 2.4531 | 1.0e-05 | 9.0e-04 | transformer loading |
| simbench_lv_rural1 | prodist_m8_bt | 2.2656 | 2.2656 | 2.2e-05 | 2.1e-03 | overvoltage |
| simbench_lv_rural1 | en50160_envelope | 2.3281 | 2.3281 | 2.2e-05 | 2.1e-03 | transformer loading |
| simbench_lv_semiurb4 | prodist_m8_bt | 1.4219 | 1.4219 | 2.0e-05 | 1.0e-03 | overvoltage |
| simbench_lv_semiurb4 | en50160_envelope | 1.9688 | 1.9688 | 2.0e-05 | 1.1e-03 | transformer loading |
| simbench_lv_urban6 | prodist_m8_bt | 1.5703 | 1.5703 | 1.7e-05 | 8.2e-04 | overvoltage |
| simbench_lv_urban6 | en50160_envelope | 1.7266 | 1.7266 | 1.7e-05 | 8.2e-04 | transformer loading |
| lv_schutterwald | both | – | – | – | – | not covered: 14 external grids |

The capacities coincide in all twelve cases.

**The residual has a known source.** `cigre_lv`, whose transformers carry no
no-load data, agrees to 2e-8 pu, which is numerical noise. The other networks
differ by 1e-5 to 4e-5 pu. Setting `pfe_kw` and `i0_percent` to zero removes the
difference (`test_without_a_magnetising_branch_the_engines_coincide`), and it
does not change with pandapower's `trafo_model`: the two programs represent the
no-load losses and the magnetising current of a transformer differently. The
effect is an order of magnitude below the voltage change that one search
tolerance of λ produces (about 6e-4 pu per 0.01 of λ on `cigre_lv`), which is
why the capacities coincide.

## Limits of this validation

* **lv_schutterwald is not covered.** It has 14 external grids and open line
  switches. Its results rest on pandapower alone.
* **Balanced networks only.** The three-phase path (`runpp_3ph`, the IEEE
  European LV feeder of group B) is not cross-checked. That feeder is published
  with an OpenDSS reference solution, which is the natural next check.
* **Snapshots only.** A QSTS is a sequence of the same power flows; the check
  covers the solver, not the search logic. The search logic is covered by the
  agreement of the two QSTS backends and by the structural tests
  (QSTS at τ = 0 never exceeds the snapshot at the critical step).
* **Same input data.** Both engines read the impedances pandapower holds. A
  wrong line type in the source data would pass this check.
