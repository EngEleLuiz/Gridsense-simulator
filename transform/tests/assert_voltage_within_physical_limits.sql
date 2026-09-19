-- Fails if any bus voltage falls outside physically plausible bounds
-- (0.8-1.2 pu). This is a broader "sanity" check than the ANSI
-- operational-tolerance flag in fct_bus_voltage: a reading outside
-- this wider range points to a simulation, ingestion, or unit-parsing
-- bug rather than a normal (if undesirable) operational excursion.

select *
from {{ ref('fct_bus_voltage') }}
where voltage_pu < 0.8 or voltage_pu > 1.2
