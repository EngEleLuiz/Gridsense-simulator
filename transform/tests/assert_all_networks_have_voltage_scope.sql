-- Fails if a network present in the telemetry has no row in the
-- network_voltage_scope seed: every one of its readings would be
-- treated as out of scope and no violation could ever be reported.
-- Fix: re-run scripts/generate_voltage_scope_seed.py.

select distinct staged.network
from {{ ref('stg_grid_telemetry') }} as staged
left join {{ ref('network_voltage_scope') }} as scope
    on staged.network = scope.network
where scope.network is null
