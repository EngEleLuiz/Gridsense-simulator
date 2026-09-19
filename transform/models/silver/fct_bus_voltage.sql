{{
    config(
        materialized='table',
        post_hook="{{ make_hypertable('event_timestamp') }}"
    )
}}

-- Unpivots the per-bus voltage map into a tidy long-format fact table
-- (one row per bus per step), which is the natural shape for
-- time-series queries, dashboards, and further aggregation.
--
-- is_voltage_violation flags readings outside ANSI C84.1 Range A
-- (0.95-1.05 pu), the standard normal-operation voltage tolerance
-- for power systems.

with staged as (

    select * from {{ ref('stg_grid_telemetry') }}

),

unnested as (

    select
        staged.event_timestamp,
        staged.step,
        staged.network,
        (bus.key)::integer         as bus_id,
        (bus.value)::double precision as voltage_pu
    from staged,
    lateral jsonb_each_text(staged.bus_voltage_pu) as bus (key, value)

)

select
    event_timestamp,
    step,
    network,
    bus_id,
    voltage_pu,
    (voltage_pu < 0.95 or voltage_pu > 1.05) as is_voltage_violation
from unnested
