{{ config(materialized='table') }}

-- Hourly loading KPIs per transmission line, including overload
-- event counts -- the metric a grid operator cares about most:
-- how often, and how badly, a line exceeded its thermal rating.

select
    network,
    line_id,
    date_trunc('hour', event_timestamp) as hour_bucket,

    avg(loading_percent)                as avg_loading_percent,
    max(loading_percent)                as max_loading_percent,

    count(*)                                            as n_readings,
    sum(case when is_overloaded then 1 else 0 end)       as n_overload_events,
    round(
        100.0 * sum(case when is_overloaded then 1 else 0 end)
            / nullif(count(*), 0),
        2
    )                                                     as overload_rate_pct

from {{ ref('fct_line_loading') }}
group by 1, 2, 3
