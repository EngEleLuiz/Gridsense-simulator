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
| `prodist_m8_127v.json` | PRODIST Module 8 for 220/127 V systems (127 V base) | band edges `to_verify` |

The built-in criteria (`prodist_m8_drp_drc` on the 220 V base, `en50160_weekly`,
`prodist_m8_zero_tolerance`) live in `duration.py`.
