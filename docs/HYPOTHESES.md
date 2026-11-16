# Hypotheses and decision rules

**Status: draft, not frozen.** Fill in "Frozen on" and commit this file *before*
running protocol v2 and v3 on a clean tree. After that date, a change to a rule
is a new, dated entry at the end of the file, never an edit.

| | |
|---|---|
| Frozen on | *(not yet)* |
| Frozen at commit | *(not yet)* |
| Agreed with the advisor | *(not yet)* |

The rules below are proposals. They are written so that
`scripts/analyze_protocol.py` can check them without a judgement call; the
"Hypothesis checks" table of its report is the evidence for each one.

## What had already been seen when this was written

A rule written after looking at the answer is not a test. For the record:

* Protocol v1, group A (deterministic panel) and run `D-simbench_lv_rural1-tau0`
  had been run on the author's machine.
* While the analysis script was developed, a reduced battery was run to produce
  real payloads: three networks of group A, one stochastic run and ten weeks
  (2016-05-02 + 70 days) of the SimBench rural and semi-urban grids under the
  three duration criteria. Its numbers were visible to whoever wrote this file.

Hypotheses H1, H3 and H4 are therefore *not* blind for those two SimBench
grids over those ten weeks. They are blind for the annual window, for the
urban grid, and for all of protocol v3.

## Measurement resolution

Every capacity is the lower end of a bisection bracket of width
`tolerance = 0.01` (in λ). Two capacities whose difference is at or below
0.01 are a tie. No rule below counts a tie as support.

## Hypotheses

Capacities are λ = installed PV / nominal load. "Main case" means the three
SimBench grids with their own profiles over the annual window (group D).

### H1 — the duration allowance matters

*Claim.* Under PRODIST, allowing DRP ≤ 3 % and DRC ≤ 0.5 % raises the QSTS
capacity above the zero-tolerance capacity.

*Response.* g = (λ[DRP/DRC] − λ[τ = 0]) / λ[τ = 0], per network (runs
`D-<net>-drp-drc` and `D-<net>-tau0`, method `qsts`). g ≥ 0 holds by
construction and is tested in the code; the hypothesis is about its size.

*Rule.* Supported if the difference exceeds the resolution in all three main-case
networks. Reported with it: the median and the range of g. If g exceeds the
resolution in one or two networks only, the result is "depends on the network"
and the networks are named.

### H2 — the operating point of a snapshot study matters

*Claim.* The deterministic capacity at the literature point (load × 0.25, full PV
output) differs from the deterministic capacity at the point taken from the
series (maximum PV with minimum load).

*Response.* Relative difference between `A-<net>-prodist_m8_bt` and the
deterministic row of `D-<net>-tau0`, per network.

*Rule.* Supported if the difference exceeds the resolution in all three networks.
The sign is reported, not hypothesised: the two capacities do not measure the
same quantity (the series point scales PV by its output at that step, so its λ
is installed capacity), and the text must say so next to the number.

### H3 — load-proportional allocation is not conservative

*Claim.* At the same operating point, the stochastic capacity at α = 0.10
(uncoordinated adoption) is lower than the deterministic capacity
(load-proportional allocation).

*Response.* (λ[stochastic p10] − λ[deterministic]) / λ[deterministic], both rows
of `D-<net>-tau0`.

*Rule.* Supported if the stochastic capacity is lower by more than the resolution
**and** the upper end of its 95 % confidence interval is below the deterministic
capacity, in all three networks. The second condition is not in the report's
count; read it from the "Methods on the same data" table. This claim depends on
the adoption model: it is only made together with group J of protocol v3, and it
is withdrawn if the high-adoption range (0.7 to 1.0) reverses the sign.

### H4 — coarse data overstate the capacity

*Claim.* Under a duration criterion, hourly block averages give a higher capacity
than the native series.

*Response.* (λ[60-min blocks] − λ[native]) / λ[native]: `E-<net>-drp-drc-hourly`
against `D-<net>-drp-drc`; and, in protocol v3, the London curve b(w) for
w = 15, 30, 60 min against the 10-min reference.

*Rule.* Supported if the difference is positive and above the resolution in at
least two of the three SimBench grids **and** b(60) > 0 above the resolution on
the London series. A negative difference above the resolution in any network is
reported as a counter-example, with the binding constraint of both runs (a
thermally bound week can move either way under averaging).

*Use of b(15).* The main case applies a 10-minute criterion to 15-minute data.
b(15) measured on the London series is reported as the order of magnitude of
that error. It is not applied as a correction: it comes from another network
and another climate.

### H5 — the regulation changes what limits the network

*Claim.* The binding constraint changes between the PRODIST adequate band and
the EN 50160 envelope.

*Response.* Kind of binding constraint (voltage or thermal) of
`A-<net>-prodist_m8_bt` and `A-<net>-en50160_envelope`, per network, for the
networks where both runs give a capacity.

*Rule.* Reported as a count over the panel ("k of n networks"). Supported as a
general statement if k ≥ n − 1; otherwise the networks are named.

## Sensitivities (protocol v3): what would change a conclusion

These are not hypotheses. Each states, before the runs, what result would force
a conclusion of the main case to be qualified.

| Group | Factor | The conclusion is qualified if |
|---|---|---|
| G | PV power factor 0.95 / 0.92 | the *ranking* of the networks by capacity changes, or the binding constraint changes kind in the main case. A change of level alone is reported as the cost of the unity-power-factor assumption. With reactive absorption, `verification.lower_side_compliant` must be true at the capacity; if it is false the capacity is not reported for that power factor |
| H | Source voltage 0.98 to 1.04 pu | the capacity changes by more than 20 % per 0.02 pu in a main-case network: then every capacity is stated together with the source voltage it assumes, in the abstract as well |
| I | Temporal resolution | see H4 |
| J | Adoption range; 200 scenarios | H3 changes sign under an adoption range; or the 200-scenario estimate falls outside the 95 % interval of the 60-scenario estimate (then 60 scenarios were not enough and group C is re-run with 200) |

## What is not claimed

* Nothing about other weather years: the SimBench profiles cover one year, and
  the within-year interval of a weekly quantile says nothing about the next year.
* Nothing about unbalanced operation in the main case: the networks are balanced.
  Group B measures the effect on one three-phase feeder, as an order of magnitude.
* No significance language for comparisons across three or seven networks. The
  report prints the smallest p-value the sample size can attain next to every
  Wilcoxon p-value for that reason.
