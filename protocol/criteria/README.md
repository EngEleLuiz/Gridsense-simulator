# Duration criteria as data

The study runner accepts voltage-quality criteria defined in JSON, so a new
regulation does not need a code change:

```
python scripts/run_hosting_capacity_study.py ... \
    --qsts-criterion-file protocol/criteria/prodist_m8_127v.json \
    --qsts-criterion prodist_m8_drp_drc_127v
```

Fields are those of `gridsense_sim.hosting_capacity.duration.DurationCriterion`
(band edges in per unit of nominal voltage, allowed fractions of the window's
readings, reading length, window length). A file may hold one criterion or a
list. A name that is already registered with different numbers is rejected:
results cite a criterion by name, so a name must always mean the same numbers.

| file | criterion | status |
|---|---|---|
| `prodist_m8_127v.json` | PRODIST Module 8 for 220/127 V systems (127 V base) | `to_verify`: edges agree with a state regulator's guide; Annex VIII not read yet |

The built-in criteria (`prodist_m8_drp_drc` on the 220 V base, `en50160_weekly`,
`prodist_m8_zero_tolerance`) live in `duration.py`.

## Verification status

A criterion stays `to_verify: true` until every number has been read in the
primary source (the regulation, in the revision in force). `verification` says
what has been checked so far, against which document and when; both fields are
stored with every result (`criterion_spec`).

```
python scripts/run_protocol.py --list                          # names the criteria still to verify
python scripts/run_protocol.py --require-verified-criteria     # refuses to run while any is left
python scripts/run_hosting_capacity_study.py ... --require-verified-criterion
```

To clear the flag: read the table in the regulation, set `to_verify` to
`false` and write the document, table and date in `verification`. For a
built-in criterion that is an edit of `duration.py`, which changes the commit
recorded with every later result.
