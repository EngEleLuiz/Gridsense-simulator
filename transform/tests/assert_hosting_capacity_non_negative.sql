-- Fails on any physically meaningless (negative) capacity or
-- penetration, which would indicate an estimator bug.

select *
from {{ ref('mart_hosting_capacity') }}
where total_pv_mw_reported < 0
   or total_pv_mw_p50 < 0
   or lambda_max < 0
