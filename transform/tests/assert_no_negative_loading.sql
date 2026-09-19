-- A negative loading_percent is physically meaningless (current
-- magnitude can't be negative) and would indicate a bug upstream in
-- the simulator or the JSON unnesting logic.

select *
from {{ ref('fct_line_loading') }}
where loading_percent < 0
