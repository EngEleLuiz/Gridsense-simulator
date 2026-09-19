-- power_balance_mw is a derived column (generation - load); this
-- test re-derives it independently and fails on any drift, catching
-- accidental logic changes in fct_grid_summary.sql.

select *
from {{ ref('fct_grid_summary') }}
where abs(power_balance_mw - (total_generation_mw - total_load_mw)) > 0.001
