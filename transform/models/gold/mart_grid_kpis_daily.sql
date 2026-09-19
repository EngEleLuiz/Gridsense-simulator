{{ config(materialized='table') }}

-- Daily system-level KPIs: peak/average load, a classic load factor
-- (average load over peak load -- how "flat" vs "peaky" demand was),
-- and how many simulation steps had an active contingency.

select
    network,
    date_trunc('day', event_timestamp) as day_bucket,

    avg(total_load_mw)                 as avg_load_mw,
    max(total_load_mw)                 as peak_load_mw,
    min(total_load_mw)                 as min_load_mw,
    avg(total_generation_mw)           as avg_generation_mw,

    round(
        (avg(total_load_mw) / nullif(max(total_load_mw), 0))::numeric,
        4
    )                                   as load_factor,

    sum(case when has_active_contingency then 1 else 0 end) as n_contingency_steps,
    count(*)                                                 as n_steps

from {{ ref('fct_grid_summary') }}
group by 1, 2
