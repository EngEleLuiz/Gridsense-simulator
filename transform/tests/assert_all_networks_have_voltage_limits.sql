-- Fails if any network present in the telemetry has no matching row
-- in the network_voltage_limits seed. Without this test, a new
-- network added to SUPPORTED_NETWORKS (simulator/gridsense_sim/
-- engine.py) but forgotten in transform/seeds/network_voltage_limits.csv
-- would silently get NULL limits in fct_bus_voltage, and
-- is_voltage_violation would be NULL for every one of its rows
-- instead of a loud failure -- the same silent-wrong-default bug
-- this seed was introduced to fix in the first place, just moved to
-- a different layer.

select distinct staged.network
from {{ ref('stg_grid_telemetry') }} as staged
left join {{ ref('network_voltage_limits') }} as limits
    on staged.network = limits.network
where limits.network is null
