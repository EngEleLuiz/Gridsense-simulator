{{ config(materialized='table') }}

-- Side-by-side comparison of the three hosting-capacity methods.
-- One row per (network, method, run_id).
--
-- Consumers MUST filter on is_comparable and compare only rows with
-- the same criterion_framework (and the same load_scale for snapshot
-- methods). Legacy (v1) rows are kept with is_comparable = false and
-- total_pv_mw_comparable = NULL -- see stg_hosting_capacity_results.sql.
--
-- Stochastic rows are comparable only with a finite two-sided CI (R05).
-- Stochastic rows: total_pv_mw_comparable = F^-1(hc_alpha) of the
-- critical penetration (conservative), total_pv_mw_p50 = median,
-- [hc_lambda_ci_low, hc_lambda_ci_high] = distribution-free CI.

select
    network,
    method,
    run_id,
    run_timestamp,
    schema_version,
    status,
    error,
    is_comparable,
    is_bounded,
    criterion_framework,
    criterion_kind,
    v_min_pu,
    v_max_pu,
    load_scale,
    total_pv_mw_comparable,
    total_pv_mw_reported,
    total_pv_mw_p50,
    lambda_max,
    lambda_fail,
    binding_constraint,
    hc_alpha,
    hc_lambda_ci_low,
    hc_lambda_ci_high,
    n_scenarios,
    n_censored,
    is_resolved,
    qsts_criterion,
    qsts_total_steps,
    qsts_steps_per_day,
    qsts_first_violating_step,
    git_commit,
    git_dirty
from {{ ref('stg_hosting_capacity_results') }}
order by network, run_timestamp desc, method
