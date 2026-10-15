select *
from {{ ref('mart_hosting_capacity') }}
where method = 'stochastic'
  and is_comparable
  and (hc_lambda_ci_low is null or hc_lambda_ci_high is null)
