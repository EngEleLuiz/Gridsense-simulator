{{ config(materialized='view') }}

-- Thin typing layer over bronze.hosting_capacity_results. Each row
-- is one hosting-capacity study run for one method; because the
-- three methodologies produce different result shapes (see
-- simulator/gridsense_sim/hosting_capacity/{deterministic,
-- stochastic,qsts}.py), only fields that make sense for a given
-- method are populated below -- everything else is NULL, not
-- defaulted to 0 or "N/A", so downstream aggregation never silently
-- treats "not applicable to this method" as "measured zero".
--
-- total_pv_mw_comparable is the one field every method fills, so the
-- gold mart can put all three side by side without needing to know
-- method-specific field names: deterministic/qsts report it
-- directly (total_pv_mw, the PV at their binding constraint);
-- stochastic reports its p50 in that slot instead. Treat that
-- stochastic value with care -- see hosting_capacity/stochastic.py's
-- module docstring: at the default Monte Carlo sampling range, it's
-- a lower bound under an arbitrary PV-size budget, not yet a ceiling
-- comparable to the other two methods. Recalibration is deferred to
-- Phase 8 (real PV-sizing data), not fixed here.

with source as (

    select * from {{ source('bronze', 'hosting_capacity_results') }}

)

select
    network,
    method,
    run_id,
    run_timestamp,

    case
        when method in ('deterministic', 'qsts')
            then (raw_value ->> 'total_pv_mw')::double precision
        when method = 'stochastic'
            then (raw_value ->> 'hosting_capacity_mw_p50')::double precision
    end as total_pv_mw_comparable,

    case
        when method = 'stochastic'
            then (raw_value ->> 'hosting_capacity_mw_p95')::double precision
    end as total_pv_mw_p95,

    case
        when method in ('deterministic', 'qsts')
            then (raw_value ->> 'lambda_max')::double precision
    end as lambda_max,

    case
        when method in ('deterministic', 'qsts')
            then raw_value ->> 'binding_constraint'
    end as binding_constraint,

    case
        when method = 'stochastic'
            then (raw_value ->> 'violation_rate')::double precision
    end as violation_rate,

    case
        when method = 'stochastic'
            then (raw_value ->> 'n_trials')::integer
    end as n_trials,

    case
        when method = 'qsts'
            then (raw_value ->> 'total_steps')::integer
    end as qsts_total_steps,

    case
        when method = 'qsts'
            then (raw_value ->> 'steps_per_day')::integer
    end as qsts_steps_per_day

from source
