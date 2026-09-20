{{ config(materialized='table') }}

-- Side-by-side comparison of the three hosting-capacity methodologies
-- (deterministic, stochastic, QSTS) for the same network -- the core
-- scientific comparison the dissertation is built around. One row
-- per (network, method, run_id); the API and Grafana's third
-- dashboard read this directly.
--
-- total_pv_mw_comparable is the only column meant for a naive
-- side-by-side number comparison across methods today. Read
-- stochastic's value there with care: at the default Monte Carlo
-- sampling range, it's a lower bound under an arbitrary PV-size
-- budget, not yet a ceiling comparable to the deterministic/QSTS
-- numbers -- see stg_hosting_capacity_results.sql and
-- hosting_capacity/stochastic.py's module docstring. Recalibration
-- is deferred to Phase 8 (real PV-sizing data).

select
    network,
    method,
    run_id,
    run_timestamp,
    total_pv_mw_comparable,
    total_pv_mw_p95,
    lambda_max,
    binding_constraint,
    violation_rate,
    n_trials,
    qsts_total_steps,
    qsts_steps_per_day
from {{ ref('stg_hosting_capacity_results') }}
order by network, run_timestamp desc, method
