{{
    config(
        materialized='table',
        post_hook="{{ make_hypertable('event_timestamp') }}"
    )
}}

-- Unpivots the per-generator dispatch map into a tidy long-format
-- fact table (one row per generator per step).

with staged as (

    select * from {{ ref('stg_grid_telemetry') }}

),

unnested as (

    select
        staged.event_timestamp,
        staged.step,
        staged.network,
        (gen.key)::integer            as gen_id,
        (gen.value)::double precision as power_mw
    from staged,
    lateral jsonb_each_text(staged.gen_power_mw) as gen (key, value)

)

select
    event_timestamp,
    step,
    network,
    gen_id,
    power_mw
from unnested
