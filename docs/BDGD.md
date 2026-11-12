# Brazilian feeders from ANEEL's BDGD (Phase 8)

BDGD (*Base de Dados Geográfica da Distribuidora*, PRODIST Module 10) is the
geographic database each Brazilian distributor submits to ANEEL; ANEEL publishes it
as open data. `gridsense-bdgd` converts the low-voltage network behind one
distribution transformer into a pandapower network for the hosting-capacity studies.

**No BDGD data is in this repository, and none should be committed.** Raw tables and
converted networks live under `data/` (git-ignored). Check the licence and the
personal-data rules that apply to your release before publishing anything derived
from it; the converter drops identifiers and never reads coordinates, but that is
not a legal assessment.

## Workflow

```
# 0. export the layers to CSV or Parquet (QGIS or ogr2ogr), one file per layer,
#    into data/raw/bdgd/  -- or point the tool at the .gdb if pyogrio is installed

gridsense-bdgd inspect data/raw/bdgd                 # are the layers and columns there?
gridsense-bdgd list    data/raw/bdgd --min-consumers 30 --top 20

gridsense-bdgd build data/raw/bdgd --transformer <COD_ID> \
    --mv-kv 13.8 --lv-kv 0.38 --load-factor 0.45 --vk-percent 4.5 \
    --source-label "BDGD <year>, <distributor>" \
    --out data/networks/br_feeder_a.json

python scripts/run_hosting_capacity_study.py --network br_feeder_a \
    --network-file data/networks/br_feeder_a.json \
    --methods deterministic stochastic qsts --qsts-series data/series/<series>.parquet \
    --qsts-criterion prodist_m8_drp_drc --critical-point-from-series --qsts-backend torch
```

`build` writes the network and a sidecar `br_feeder_a.json.meta.json` with the default
voltage framework (`prodist_m8_bt`), the customer voltage level, the citation and the
full build report. The runner reads the sidecar and stores the file's SHA-256 with
every result (`params.network_source`).

## Layers and columns

| layer | used for | required |
|---|---|---|
| `SSDBT` | LV segments (`PAC_1`, `PAC_2`, `TIP_CND`, `COMP`, `UNI_TR_MT`) | yes |
| `UNTRMT` | distribution transformers (`COD_ID`, `PAC_1`, `PAC_2`, `POT_NOM`) | yes |
| `SEGCON` | conductor catalogue (`COD_ID`, `R1`, `X1`, `CNOM`) | yes |
| `UCBT_tab` | LV consumers (`PAC`, `UNI_TR_MT`, `ENE_01`..`ENE_12`, `FAS_CON`, `TIP_CC`) | yes |
| `RAMLIG` | service drops | no |
| `EQTRMT` | transformer impedance (`R`, `XHL`) | no |
| `UGBT_tab` | existing LV generation (`PAC`, `POT_INST`) | no |
| `CRVCRG` | typical load curves (`COD_ID`, `TIP_DIA`, `POT_01`..`POT_96`) | no |

**Status of these names.** `COD_ID`, `PAC_1`, `PAC_2`, `PAC`, `CTMT`, `POT_NOM`,
`PER_FER`, `PER_TOT` and the layers `SSDBT`, `UNTRMT`, `SEGCON`, `CRVCRG` were checked
against the open-source BDGD-Tools converter. The other defaults were written from
the author's knowledge of the BDGD manual and **must be checked against the data
dictionary of your release** — `inspect` does that mechanically. Releases differ
(older ones use `UNTRD` / `UNI_TR_D`); override any name with a JSON file:

```
gridsense-bdgd inspect data/raw/bdgd --schema my_schema.json --columns
```

where `my_schema.json` holds only the keys of `bdgd/schema.py:BdgdSchema` that differ,
e.g. `{"transformers": "UNTRD", "transformer_link": "UNI_TR_D", "equipment_link": "UNI_TR_D"}`.
Units assumed: `COMP` in metres, `R1`/`X1` in ohm/km, `CNOM` in amperes, `POT_NOM` in
kVA, `PER_FER`/`PER_TOT` in watts, `R`/`XHL` in percent, `ENE_xx` in kWh, `POT_INST`
in kW. Voltage codes are not decoded: the primary and secondary voltages are given on
the command line.

## Modelling choices (all written to the build report)

| choice | why | how to change |
|---|---|---|
| Nominal load = peak demand = annual energy / 8760 h / load factor | BDGD has energy, not demand; the study convention is "nominal load = peak" | the load factor comes from the consumer's typical curve (`CRVCRG`) when present, otherwise from `--load-factor`, which has **no default**: it scales every capacity in MW |
| Power factor 0.92 for every load | not in BDGD; 0.92 is the PRODIST reference power factor | `--power-factor` |
| Transformer impedance from `EQTRMT`; otherwise resistance from copper losses and `--vk-percent` | the short-circuit voltage is not always in the data | `--vk-percent`, `--vkr-percent` |
| Balanced (positive-sequence) network | the batched solver and the balanced methods need it | see below |
| Line capacitance neglected | LV overhead lines | — |
| Nodes not connected to the transformer are left out; consumers on them are dropped | incomplete topology is common | the build fails when more than `--max-dropped-load-share` (5 %) of the load would be lost |
| Existing generators become static generators | `--existing-pv remove` (default) studies the empty feeder, `keep` the additional capacity | runner option |

### The balanced-model limitation

Brazilian LV feeders serve many single-phase and two-phase consumers. A balanced
model spreads each of them over three phases, which underestimates voltage rise on
the loaded phase and therefore **overestimates** hosting capacity. Phase 7.4 measured
this on the IEEE European LV feeder (unbalanced capacity about 20 % below the
balanced equivalent). The build report gives the share of single- and two-phase
loads and repeats the warning; `net.load.phases` keeps the phase count of each load
so a three-phase model can be derived later. Results on BDGD feeders from this phase
should be presented as balanced-equivalent capacities.

### Voltage base

Feeders at 380/220 V use the built-in PRODIST criterion (220 V table). For 220/127 V
systems build with `--lv-kv 0.22` and select the 127 V criterion from
`protocol/criteria/prodist_m8_127v.json` (band edges still marked `to_verify`).

## Time series

BDGD has no measured load series. For QSTS use a series built with
`gridsense-data build-series` (Phase 7): INMET irradiance for the site, and a load
dataset whose transfer to the feeder is an explicit, documented assumption. Turning
the typical curves of `CRVCRG` into a per-consumer series is not implemented.

## Results in the warehouse

Results on a file network carry the name given with `--network`. The dbt seeds
`network_voltage_limits.csv` and `network_voltage_scope.csv` only know the built-in
panel, so loading such results into TimescaleDB needs those two seeds extended for
the new name first; until then analyse them from the Parquet output.

## Keeping the dissertation and the paper apart

The study code never names a Brazilian network: a local feeder is just a registered
key. The dissertation runs local feeders with `--network-file`; the paper runs the
built-in international panel through `protocol-v1` / `protocol-v2`, and
`tests/test_protocol_v2.py` fails if a frozen protocol references a network file or a
network outside the built-in panel.
