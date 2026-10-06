-- For bisection methods the search bracket must be ordered:
-- lambda_max (verified feasible) < lambda_fail (verified infeasible).

select *
from {{ ref('mart_hosting_capacity') }}
where method in ('deterministic', 'qsts')
  and lambda_fail is not null
  and lambda_max >= lambda_fail
