-- (network, bus_id) must be unique in the scope seed: a duplicate would
-- silently duplicate every matching row of fct_bus_voltage.

select network, bus_id, count(*) as n
from {{ ref('network_voltage_scope') }}
group by network, bus_id
having count(*) > 1
