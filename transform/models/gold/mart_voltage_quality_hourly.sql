{{ config(materialized='table') }}

-- Hourly voltage-quality KPIs per bus. This is what a Grafana panel
-- or a weekly quality report would query directly: no jsonb, no
-- unnesting, just pre-aggregated numbers.

select
    network,
    bus_id,
    date_trunc('hour', event_timestamp) as hour_bucket,

    avg(voltage_pu)                     as avg_voltage_pu,
    min(voltage_pu)                     as min_voltage_pu,
    max(voltage_pu)                     as max_voltage_pu,
    stddev_samp(voltage_pu)             as stddev_voltage_pu,

    count(*)                                                as n_readings,
    sum(case when is_voltage_violation then 1 else 0 end)   as n_violations,
    round(
        100.0 * sum(case when is_voltage_violation then 1 else 0 end)
            / nullif(count(*), 0),
        2
    )                                                        as violation_rate_pct

from {{ ref('fct_bus_voltage') }}
group by 1, 2, 3
