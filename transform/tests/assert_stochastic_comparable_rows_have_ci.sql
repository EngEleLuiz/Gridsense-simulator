-- Review finding R05: a stochastic hosting capacity is F^-1(alpha) of the
-- critical penetration. With too few scenarios the distribution-free
-- confidence interval does not exist (e.g. alpha = 0.10 needs n >= 36
-- for a 95% lower bound), and the "quantile" is just the smallest
-- sample. Such a row must never be flagged comparable.

select *
from {{ ref('mart_hosting_capacity') }}
where method = 'stochastic'
  and is_comparable
  and (hc_lambda_ci_low is null or hc_lambda_ci_high is null)
