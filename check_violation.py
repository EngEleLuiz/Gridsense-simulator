import psycopg2
conn = psycopg2.connect("postgresql://postgres:postgres@localhost:5432/gridsense")
cur = conn.cursor()
cur.execute("""
    SELECT 'Bus ' || bus_id::text AS metric, avg(violation_rate_pct) AS value
    FROM public_gold.mart_voltage_quality_hourly
    WHERE network = 'case14'
    GROUP BY bus_id
    ORDER BY value DESC
""")
for row in cur.fetchall():
    print(row)