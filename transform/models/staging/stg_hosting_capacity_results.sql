{{ config(materialized='view') }}

-- Typed view over bronze.hosting_capacity_results.
--
-- Payload versions
--   v1 (pre-review, no 'schema_version' key): deterministic/QSTS at
--      nominal load with no baseline check, stochastic = budget-bound
--      Monte Carlo (review findings C1, C3, C4). Kept for history, but
--      NEVER comparable.
--   v2 (this review): shared envelope with status, conditions
--      (framework, load_scale), params and provenance; stochastic =
--      F^-1(alpha) of the critical penetration.
--
-- is_comparable is the single gate every consumer must use:
--   schema_version >= 2 AND status = 'ok' AND bounded
--   AND the bisection bracket closed (resolved; absent in early v2 rows)
--   AND, for stochastic rows, a finite two-sided confidence interval.
-- The CI condition (review finding R05) exists because F^-1(alpha) from
-- too few scenarios is just the smallest sample: with alpha = 0.10 the
-- 95% interval has no lower bound until n >= 36. Such a row is kept for
-- audit but never presented as a capacity.
-- total_pv_mw_comparable is NULL whenever is_comparable is false, so
-- no dashboard can plot an invalid number by accident. The raw value
-- is still available as total_pv_mw_reported.
--
-- Comparability across rows additionally requires equal
-- criterion_framework (and, for snapshot methods, equal load_scale);
-- the mart exposes both so consumers can filter on them.

with source as (

    select
        *,
        coalesce((raw_value ->> 'schema_version')::integer, 1) as schema_version
    from {{ source('bronze', 'hosting_capacity_results') }}

),

typed as (

    select
        network,
        method,
        run_id,
        run_timestamp,
        schema_version,

        case when schema_version >= 2 then raw_value ->> 'status' else 'legacy_invalid' end
            as status,
        raw_value ->> 'error' as error,

        coalesce((raw_value ->> 'bounded')::boolean, false) as is_bounded,

        case
            when schema_version >= 2 then (raw_value ->> 'total_pv_mw')::double precision
            when method in ('deterministic', 'qsts') then (raw_value ->> 'total_pv_mw')::double precision
            when method = 'stochastic' then (raw_value ->> 'hosting_capacity_mw_p50')::double precision
        end as total_pv_mw_reported,

        (raw_value ->> 'lambda_max')::double precision as lambda_max,
        (raw_value ->> 'lambda_fail')::double precision as lambda_fail,
        raw_value ->> 'binding_constraint' as binding_constraint,

        raw_value -> 'conditions' ->> 'framework' as criterion_framework,
        raw_value -> 'conditions' ->> 'criterion_kind' as criterion_kind,
        (raw_value -> 'conditions' ->> 'v_min_pu')::double precision as v_min_pu,
        (raw_value -> 'conditions' ->> 'v_max_pu')::double precision as v_max_pu,
        (raw_value -> 'conditions' ->> 'load_scale')::double precision as load_scale,

        -- stochastic (v2)
        (raw_value ->> 'alpha')::double precision as hc_alpha,
        (raw_value ->> 'hc_lambda_ci_low')::double precision as hc_lambda_ci_low,
        (raw_value ->> 'hc_lambda_ci_high')::double precision as hc_lambda_ci_high,
        (raw_value ->> 'hc_mw_p50')::double precision as total_pv_mw_p50,
        (raw_value ->> 'n_scenarios')::integer as n_scenarios,
        (raw_value ->> 'n_censored')::integer as n_censored,
        (raw_value ->> 'resolved')::boolean as is_resolved,

        -- qsts
        raw_value ->> 'criterion' as qsts_criterion,
        (raw_value ->> 'total_steps')::integer as qsts_total_steps,
        (raw_value ->> 'steps_per_day')::integer as qsts_steps_per_day,
        (raw_value ->> 'first_violating_step')::integer as qsts_first_violating_step,

        raw_value -> 'provenance' ->> 'git_commit' as git_commit,
        (raw_value -> 'provenance' ->> 'git_dirty')::boolean as git_dirty

    from source

),

gated as (

    select
        *,
        (
            schema_version >= 2
            and status = 'ok'
            and is_bounded
            and coalesce(is_resolved, true)
            and (
                method <> 'stochastic'
                or (hc_lambda_ci_low is not null and hc_lambda_ci_high is not null)
            )
        ) as is_comparable
    from typed

)

select
    *,
    case when is_comparable then total_pv_mw_reported end as total_pv_mw_comparable
from gated
