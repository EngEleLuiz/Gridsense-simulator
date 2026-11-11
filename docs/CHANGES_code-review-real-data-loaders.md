# Code review fixes — real-data loaders (`gridsense_sim.datasets`)

Branch `fix/real-data-loaders`, based on `main` @ `f888f77`.
Every fix ships with an offline regression test (fixtures from
`simulator/tests/_dataset_fixtures.py`, fake HTTP responses served via
`file://` URLs). Each test fails on the old code and passes on the new one.

## Finding → fix → test

| ID | File | Change | Test |
|---|---|---|---|
| CR-03 | `datasets/ausgrid.py` (`_wide`) | `pivot_table(aggfunc="sum")` turned an all-NaN slot into 0 kW and a slot with one blank channel (GC blank, CL present) into a partial sum, so missing data passed the 95 % completeness check and biased load/PV low. Channels are now summed with `groupby`; a slot is NaN unless every channel row present for it has a value. Column/index order unchanged. | `test_ausgrid_blank_day_stays_missing_and_fails_completeness` |
| CR-12 | `datasets/ausgrid.py` (`load`) | `YEAR_URLS[year][0]` raised `IndexError` (all URL lists are empty), so `gridsense-data qc ausgrid` without `--source-file` or a cached file printed a traceback. `load()` now calls `cache.fetch()` with the canonical `YEAR_FILES` name and the (empty) URL list, which raises `DataNotAvailableError` with the manual-download path. The CLI exits with code 2, as for every `DatasetError`. | `test_ausgrid_without_file_or_url_raises_manual_download_hint` |
| CR-13 | `datasets/cache.py`, `base.py`, `inmet.py`, `lcl.py`, `ausgrid.py`, `pvgis.py`, `nasa_power.py` | Any HTTP 200 body was cached permanently, so an HTML error page saved as `2023.zip` broke every later run with `BadZipFile` (traceback). `DataCache.fetch(..., validate=...)` now checks the finished `.part` file before it enters the cache; a rejected body is deleted and the next mirror is tried, then `DataNotAvailableError` is raised. Validators: `validate_zip` (INMET, LCL) and `json_validator(...)` with the keys each parser reads (PVGIS, NASA POWER). Reading a bad ZIP/JSON already on disk raises the new `InvalidContentError` (a `DatasetError`) telling the user to delete the file and its `.source.json`. | `test_cache_rejects_invalid_content_before_caching`, `test_json_validator_checks_parse_and_nested_keys`, `test_inmet_html_error_page_is_not_cached_and_bad_cache_is_explained`, `test_html_saved_as_zip_is_a_dataset_error[ausgrid,lcl]`, `test_json_apis_reject_error_bodies_and_explain_bad_cache[pvgis,nasa_power]` |
| S-06 | `datasets/lcl.py` (`_read_filtered`) | The first `max_households` IDs were chosen in file order before the `[start, end)` filter, so households without data in the period used up slots and were then dropped: far fewer homes than requested, or none. Households are now chosen only among rows that pass the period and tariff filters. Reading stays chunked; once the quota is full, other households are dropped before their stamps are parsed. | `test_lcl_max_households_counts_only_households_with_data_in_the_period` |

## Interface changes

- `DataCache.fetch()` gained an optional `validate: Callable[[Path], None]`. Existing calls are unchanged.
- New `InvalidContentError(DatasetError)` (exported from `gridsense_sim.datasets`), and the helpers `cache.validate_zip`, `cache.json_validator`, `base.invalid_file_error`, `base.head_bytes`.
- Fixture writers: `write_ausgrid(..., blank=...)`, `write_lcl(..., first_id=, tou=, header=)`.

## Results impact

- **Ausgrid-based series must be rebuilt.** Gaps now stay NaN instead of
  becoming 0 kW (or a GC-less partial sum), so customers with missing days
  can fall below the 95 % completeness threshold and be dropped, and more
  days may be rejected by the completeness checks downstream. Load and PV
  levels of the remaining customers are no longer biased low by zero-filled
  gaps. Series manifests and every QSTS result produced from them change.
- **LCL selection may change which households are picked** when
  `max_households` is used with a period: homes without data in the period
  no longer take slots, so the set (and number) of homes can differ from
  earlier runs. Without a period, selection is unchanged (file order).
- **Existing cached downloads stay valid.** Validation only runs on new
  downloads; cached files and their SHA-256 sidecars are reused as before.
  A cached file that is not a valid ZIP/JSON now fails with a clear
  `InvalidContentError` instead of a traceback.
