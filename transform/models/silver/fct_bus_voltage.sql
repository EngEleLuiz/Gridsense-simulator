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
-- is_voltage_violation flags readings outside the network's own
-- voltage tolerance, looked up from the network_voltage_limits seed
-- (transform/seeds/network_voltage_limits.csv) instead of a single
-- hardcoded 0.95-1.05 (ANSI C84.1 Range A) for every network.
--
-- Why this changed: cigre_lv is a real low-voltage distribution
-- feeder. At nominal load, with zero PV, several of its buses
-- already sit at 0.93-0.94 pu -- physically normal for a radial LV
-- feeder, but outside ANSI Range A. Flagging that as a "violation"
-- here would disagree with simulator/gridsense_sim/hosting_capacity/
-- limits.py, which correctly uses +-10% (0.90-1.10) for cigre_lv, and
-- would show an inflated, misleading violation rate on the
-- "GridSense Overview" dashboard and mart_voltage_quality_hourly as
-- soon as cigre_lv telemetry flows through this model. The seed is
-- the single source of truth both this model and the Python hosting-
-- capacity code should stay aligned with; keep them in sync by hand
-- if the physical limits ever change.
--
-- Voltage scope (review finding R07): only customer service points are
-- judged. Slack, voltage-controlled generator and (in cigre_lv) MV buses
-- are excluded, exactly as hosting_capacity/scope.py does. The list of
-- in-scope buses is the network_voltage_scope seed, generated from the
-- Python rule by scripts/generate_voltage_scope_seed.py and checked by
-- simulator/tests/test_hc_cross_layer.py. Out-of-scope readings are
-- kept (is_in_scope = false) but are never a violation.
--
-- If a network appears in stg_grid_telemetry with no matching row in
-- the seed, network_v_min_pu/network_v_max_pu are NULL for those
-- rows and is_voltage_violation is NULL rather than silently falling
-- back to ANSI Range A -- see
-- transform/tests/assert_all_networks_have_voltage_limits.sql, which
-- fails the test suite loudly in that case instead of letting a new,
-- unregistered network get judged by the wrong tolerance by accident.

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

),

with_limits as (

    select
        unnested.*,
        limits.v_min_pu as network_v_min_pu,
        limits.v_max_pu as network_v_max_pu
    from unnested
    left join {{ ref('network_voltage_limits') }} as limits
        on unnested.network = limits.network

),

with_scope as (

    select
        with_limits.*,
        scope.bus_id is not null as is_in_scope
    from with_limits
    left join {{ ref('network_voltage_scope') }} as scope
        on with_limits.network = scope.network
       and with_limits.bus_id = scope.bus_id

)

select
    event_timestamp,
    step,
    network,
    bus_id,
    voltage_pu,
    network_v_min_pu,
    network_v_max_pu,
    is_in_scope,
    case
        when not is_in_scope then false
        else (voltage_pu < network_v_min_pu or voltage_pu > network_v_max_pu)
    end as is_voltage_violation
from with_scope
