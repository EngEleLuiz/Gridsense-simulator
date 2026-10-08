# Real datasets (Phase 7)

Phase 7 replaces the synthetic load and solar profiles with **measured,
open, citable data**. Six sources are registered in
`simulator/gridsense_sim/datasets/`. Each has a loader that fetches the
raw file (or accepts a manual copy), checks its SHA-256, parses it,
runs quality control and normalises it into a `ProfileSet`. A `ProfileSet`
is UTC-indexed, labelled by interval start, and on a regular grid.

```bash
pip install -e "simulator[dev,fast,data]"     # 'data' = pyarrow + simbench (381 MB)
gridsense-data list
gridsense-data info inmet                     # citation, licence, notes
gridsense-data qc inmet --opt station=A806 --opt year=2023
gridsense-data build-series --load ausgrid --pv inmet \
    --pv-opt station=A806 --pv-opt year=2023 --site florianopolis \
    --start 2023-01-09 --days 28 --steps-per-day 96 \
    --out data/series/flo_ausgrid_inmet_2023-01.parquet
python scripts/run_hosting_capacity_study.py --network cigre_lv --methods qsts \
    --qsts-series data/series/flo_ausgrid_inmet_2023-01.parquet -v
```

Raw files are cached under `data/raw/<dataset>/` (or `$GRIDSENSE_DATA_DIR`).
Each cached file gets a `.source.json` sidecar recording its URL, SHA-256 and retrieval time.
`GRIDSENSE_OFFLINE=1` forbids downloads. When a source cannot be reached,
the error message gives the exact path for a manually downloaded copy.
Nothing under `data/` is committed.

## The six sources

| Key | Provides | Coverage | Step | Access | Licence |
|---|---|---|---|---|---|
| `inmet` | GHI + air temperature (ground station) | ~600 Brazilian automatic stations, 2000– | 1 h | Yearly ZIP, all stations | Brazilian public open data; cite INMET |
| `nasa_power` | GHI + T2M (CERES SYN1deg + MERRA-2) | Global, 2001– | 1 h | REST API, no key | NASA open data |
| `pvgis` | PV output per kWp, tilted plane | Global (SARAH-3 / ERA5) | 1 h | REST API, no key | Free reuse with attribution (EU 2011/833) |
| `ausgrid` | Load (GC + CL) and gross PV (GG) per home | 300 PV homes, Sydney, 2010-07 – 2013-06 | 30 min | Yearly CSV/ZIP; manual copy accepted | CC BY (Ausgrid) |
| `lcl` | Load per home | 5,567 London homes, 2011-11 – 2014-02 | 30 min | Large ZIP, read in chunks | London Datastore open data **[VERIFY]** |
| `simbench` | Benchmark load and PV profiles | Representative German profiles, 2016 | 15 min | Bundled in the `simbench` pip package | ODbL (data) **[VERIFY]** |

Citations (also in `gridsense-data info <key>`):

- **INMET.** Banco de Dados Meteorológicos – Dados Históricos, https://portal.inmet.gov.br/dadoshistoricos
- **NASA POWER.** NASA Langley Research Center POWER Project, https://power.larc.nasa.gov
- **PVGIS.** Huld, Müller & Gambardella (2012), *Solar Energy* 86, 1803–1815; JRC PVGIS.
- **Ausgrid.** Ratnam, Weller, Kellett & Murray (2017), *Int. J. Sustainable Energy* 36(8), 787–806.
- **Low Carbon London.** UK Power Networks (2015), SmartMeter Energy Consumption Data in London Households.
- **SimBench.** Meinecke et al. (2020), *Energies* 13(12), 3290, doi:10.3390/en13123290.
- **BSRN quality limits.** Long & Shi (2008), *The Open Atmospheric Science Journal* 2, 23–37.

Items marked **[VERIFY]** must be checked on the publisher's page before
they are quoted in the dissertation.

## Format quirks found in the real files

Every loader was run on a genuine file from its source, not only on
fixtures. The opt-in suite `tests/test_datasets_real_files.py` repeats
those checks wherever `GRIDSENSE_REAL_DATA_DIR` points. What the real
files showed, and what the loaders do about it:

| Source | Quirk observed | Handling |
|---|---|---|
| INMET | Radiation is kJ/m² accumulated over the hour **ending** at the UTC stamp. Read as interval start, the energy centroid sat 45–71 min after solar noon (A806, 2024). | Shift −1 h. Centroid then −4 min. The check runs on every load. |
| INMET | Official file is latin-1 with `;` separators, decimal comma and an 8-line header block. Copies in the wild use `,` with decimal point, lose the header, or carry U+FFFD replacement characters. | Separator sniffing; the header row is located by its `Data`/`Hora` names; coordinates come from `KNOWN_STATIONS` when the header block is lost. |
| INMET | Night radiation empty or `-9999`. A806 2024: 7 % of daytime GHI and 34 % of temperature missing. | Night set to 0 (known value). Gaps ≤ 2 h interpolated. Longer gaps filled from same-hour climatology (±7 days) in the series builder, and counted. |
| Ausgrid | Slot labels are interval **ends** (`0:00` closes the day). Dates are `d/mm/yyyy`. `Row Quality = NA` marks estimated rows. | Converted to interval starts. Estimated rows kept and counted. |
| Ausgrid | Stamps follow the **Sydney wall clock with DST**. Read as fixed AEST, the PV centroid was +55…+64 min in Oct–Mar and ~0 in Apr–Sep. | Localised to `Australia/Sydney` → −1.2 min with 9 min spread across months. Slots at DST transitions are dropped (nonexistent) or interpolated (ambiguous), and counted. |
| LCL | Stamps are **UTC**: 2013-03-31 contains 01:00 and 01:30, which do not exist in London time that day. Some records are repeated (12 extra `00:00` rows in 2013 for one home). `Null` reads. | Read as UTC. Exact duplicates dropped, conflicting ones averaged, all counted. ToU-tariff homes excluded by default. |
| NASA POWER | Hourly GHI unit is `Wh/m^2` (= mean W/m²). Fill value −999. | Unit checked. A response not in UTC is rejected. |
| PVGIS | SARAH stamps are `HH:10`. `H_sun` is the sun height at the stamp; our solar geometry reproduces it within 0.1°. | Records placed in the containing hour. Alignment tolerance 35 min; the measured offset is recorded. |
| SimBench | The time axis is **CET/CEST wall clock**: 02:00–02:45 is missing on 2016-03-27 and repeated on 2016-10-30. The PV profiles have different, undocumented orientations; their mean peaks ~55 min before solar noon at 10° E. | Localised to `Europe/Berlin`. PV timing is reported as a warning, not validated. Use SimBench for load shapes only. |

Two more observations matter for modelling:

- **SimBench household shape.** The SimBench household mix (H0-A/B/C) peaks around 19–21 h wall clock in January and around 23 h in July. **For Brazilian residential load, Ausgrid is the better proxy.** It is in the southern hemisphere (seasons align without shifting) and its aggregate peaks at 18 h.
- **Ausgrid sample bias.** Ausgrid removed homes with extreme consumption or generation from the half-hour set, so it is not a representative sample of all households.

## Quality control

Every `ProfileSet` carries a `QualityReport`. Its findings come in three severities:

- **info** marks deterministic transformations (unit conversion, clock conversion).
- **warning** marks data that were altered or look suspicious but remain usable: gaps interpolated, implausible values removed, flat lines.
- **error** marks a violated physical invariant. In practice this is solar timing that disagrees with the sun: the energy-weighted daily centroid lies more than `max(20 min, step/3)` from solar noon overall, or monthly centroids spread by more than 45 min (a DST problem).

`build_series` refuses a set with errors unless `allow_quality_errors=True`.

Timing is **measured, not assumed**. The check computes each complete
day's energy-weighted centroid of the interval midpoints relative to
solar noon, then takes the monthly median. It is valid for horizontal or
equator-facing PV and for fleets of mixed orientation. It cannot validate
a single east- or west-facing system, nor load-only data (LCL's
interval-start labelling is an explicit, recorded assumption).

## From data to a QSTS series

`build_series(load, pv, target=..., start=..., days=..., steps_per_day=...)`
returns a `TimeSeries` (the existing QSTS input) and a manifest. It works in seven steps:

1. **Load aggregate.** It is the sum of the selected homes. A random subset can be drawn with `n_load_series` and `seed`. Missing homes at a step are handled as mean × count.
2. **PV per unit.** Irradiance is converted with the documented horizontal-plane model. It uses NOCT cell temperature, γ = −0.37 %/°C, 14 % losses and clipping at 1.0; it has no transposition (use PVGIS for a tilted plane). PV fleets use the capacity-weighted mean.
3. **Resampling.** Downsampling takes the mean. Upsampling is `hold` (energy-exact, default) or `linear`.
4. **Clock.**
   - **Load** follows the **source's wall clock**, DST included: people follow the clock, so 19:00 in Sydney maps to 19:00 at the target.
   - **PV** follows the PV site's **standard time**: the sun ignores DST.
   - The study runs on the target's local standard time (Brazil: UTC−3, no DST).
5. **Window.**
   - The builder picks a contiguous, fully covered source window matching the study dates.
   - If the source is in the other hemisphere, it applies an automatic ~6-month season shift.
   - It refuses a window that crosses the end of the source (for example Ausgrid's 30 June) instead of splicing years.
6. **Gaps.** Remaining NaN are filled with the same-time-of-day median over ±7 days. They are counted, and the build fails if any remain.
7. **Normalisation.**
   - The default is `source_peak`: the whole source period's peak maps to `peak_load_mult` (1.0 means nominal load = annual peak demand, the convention of finding R22). A summer window therefore sits below 1.0.
   - `window_peak` forces the window's own maximum to 1.0 instead.

The manifest records the target site, the source windows, each source
file's SHA-256, the full quality reports, the PV model, the normalisation,
the season shift and diagnostics. The diagnostics are the load and PV peak
hours, the load at the PV peak relative to the load peak, the capacity factor
and the load–PV correlation. `save_series` writes Parquet with the
manifest embedded plus a `.manifest.json` sidecar. `load_series` re-checks
the arrays and the manifest hash. The hosting-capacity runner records the
manifest SHA-256 in every QSTS result row.

## Known limitations

- **One aggregate profile.** All buses get one aggregate load multiplier and one PV multiplier, which keeps the QSTS comparable with Phase 6. Per-bus assignment of individual homes is a later extension; the per-home columns are already kept.
- **No Brazilian residential load.** There is still no open *Brazilian* household-level load dataset in the registry. ANEEL/PRODIST measurement campaigns are not published as open household series. Ausgrid is the documented proxy.
- **Simplified PV model.** The horizontal PV model ignores tilt, soiling and spectral and angular losses.
- **Coarse NASA POWER grid.** NASA POWER cells (~1°) smooth cloud variability compared with a ground station. Use INMET where one exists and NASA POWER as a cross-check.
- **SimBench PV timing.** The SimBench PV timing could not be validated; see above.
