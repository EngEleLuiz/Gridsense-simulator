-- Review finding R07: slack, generator and MV buses are outside the
-- voltage scope and must never be counted as a violation.

select *
from {{ ref('fct_bus_voltage') }}
where not is_in_scope
  and is_voltage_violation
