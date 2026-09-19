{{
    config(
        materialized='table',
        post_hook="{{ make_hypertable('event_timestamp') }}"
    )
}}

-- System-wide summary at the step grain: total load/generation, the
-- instantaneous power balance, and whether any contingency was active.
-- One row per step -- the natural grain for time-series charts of
-- overall grid state.

select
    event_timestamp,
    step,
    network,
    total_load_mw,
    total_generation_mw,
    (total_generation_mw - total_load_mw) as power_balance_mw,
    n_active_contingencies,
    (n_active_contingencies > 0)          as has_active_contingency
from {{ ref('stg_grid_telemetry') }}
