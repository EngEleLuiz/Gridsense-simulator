-- Fails if any hosting-capacity result reports a negative comparable
-- PV capacity -- physically meaningless (there's no such thing as
-- negative hosting capacity) and would indicate a bug in the
-- bisection or Monte Carlo sampling logic, not a real network
-- finding.

select *
from {{ ref('mart_hosting_capacity') }}
where total_pv_mw_comparable < 0
   or (total_pv_mw_p95 is not null and total_pv_mw_p95 < 0)
