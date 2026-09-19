{{
    config(
        materialized='table',
        post_hook="{{ make_hypertable('event_timestamp') }}"
    )
}}

-- Unpivots the per-line loading map into a tidy long-format fact
-- table. is_overloaded flags loading_percent > 100, i.e. the line is
-- carrying more current than its thermal rating allows.

with staged as (

    select * from {{ ref('stg_grid_telemetry') }}

),

unnested as (

    select
        staged.event_timestamp,
        staged.step,
        staged.network,
        (line.key)::integer            as line_id,
        (line.value)::double precision as loading_percent
    from staged,
    lateral jsonb_each_text(staged.line_loading_percent) as line (key, value)

)

select
    event_timestamp,
    step,
    network,
    line_id,
    loading_percent,
    (loading_percent > 100) as is_overloaded
from unnested
