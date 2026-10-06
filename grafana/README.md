# GridSense Grafana Dashboards (Phase 4)

Pre-provisioned Grafana setup: the TimescaleDB datasource and the
**GridSense Overview** dashboard are loaded automatically on startup —
no manual click-through configuration needed.

## Run

```bash
docker compose up -d grafana
```

(or just `docker compose up -d`, which starts everything). Then open
**http://localhost:3000** — user `admin`, password `admin` (change it
if you expose this beyond localhost).

## GridSense Overview dashboard

Four panels, all filterable by the `$network` template variable
(auto-populated from `public_gold.mart_grid_kpis_daily`):

1. **Daily Load & Generation** — average/peak load and average
   generation over time.
2. **Load Factor (latest day)** — a single-number health indicator
   (average load ÷ peak load); green above 60%.
3. **Voltage Violation Rate by Bus** — percentage of hourly in-scope
   readings outside the network's own voltage band
   (`transform/seeds/network_voltage_limits.csv`), per bus. Slack,
   generator and MV buses are outside the voltage scope
   (`transform/seeds/network_voltage_scope.csv`) and are not shown;
   load buses without local voltage support can still show high rates.
4. **Line Overload Events Over Time** — count of `loading_percent >
   100` readings per hour, summed across all lines.

## Requirements

The dashboard queries `public_gold.mart_grid_kpis_daily`,
`public_gold.mart_voltage_quality_hourly`, and `public_gold.mart_line_loading_hourly`
directly, so it only shows data after you've run the full pipeline
through `dbt run` (see the root README).

## Project layout

```
grafana/
├── provisioning/
│   ├── datasources/
│   │   └── timescaledb.yml   # Auto-connects to the timescaledb service
│   └── dashboards/
│       └── dashboards.yml    # Points Grafana at dashboards/ below
└── dashboards/
    └── gridsense-overview.json
```

## Adding your own panels

Edit `dashboards/gridsense-overview.json` directly, or build a panel
in the Grafana UI and use "Export > Save JSON to file" to add a new
dashboard file here — anything dropped into `grafana/dashboards/` is
picked up automatically (polled every 30s per `dashboards.yml`).
