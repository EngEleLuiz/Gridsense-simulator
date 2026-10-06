-- A row flagged comparable must carry everything needed to interpret
-- it: a criterion, a capacity, and (for v2) the code version that
-- produced it. Fails loudly instead of letting a half-specified number
-- reach the dashboard.

select *
from {{ ref('mart_hosting_capacity') }}
where is_comparable
  and (
        criterion_framework is null
     or total_pv_mw_comparable is null
     or lambda_max is null
     or (method = 'stochastic' and hc_alpha is null)
     or (method in ('deterministic', 'stochastic') and load_scale is null)
  )
