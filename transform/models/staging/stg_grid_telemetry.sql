{{
    config(
        materialized='view'
    )
}}

-- Thin typing layer over Bronze. Scalar fields are cast to their
-- proper types; the per-bus/per-line/per-generator maps stay as jsonb
-- here and get unnested into tidy (long-format) fact tables in the
-- Silver layer -- unpivoting semi-structured telemetry like this
-- keeps each layer's job single-purpose and easy to test.

with source as (

    select *
    from {{ source('bronze', 'raw_events') }}
    where topic = 'grid.telemetry.raw'

)

select
    kafka_partition,
    kafka_offset,
    ingestion_timestamp,

    (raw_value ->> 'step')::integer                       as step,
    raw_value ->> 'network'                                as network,
    (raw_value ->> 'timestamp')::timestamptz               as event_timestamp,
    (raw_value ->> 'total_load_mw')::double precision       as total_load_mw,
    (raw_value ->> 'total_generation_mw')::double precision as total_generation_mw,

    jsonb_array_length(
        coalesce(raw_value -> 'active_contingencies', '[]'::jsonb)
    )                                                       as n_active_contingencies,

    raw_value -> 'bus_voltage_pu'                           as bus_voltage_pu,
    raw_value -> 'line_loading_percent'                     as line_loading_percent,
    raw_value -> 'gen_power_mw'                              as gen_power_mw

from source
