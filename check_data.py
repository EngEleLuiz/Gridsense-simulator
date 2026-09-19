import psycopg2
conn = psycopg2.connect("postgresql://postgres:postgres@localhost:5432/gridsense")
cur = conn.cursor()
cur.execute("SELECT count(*) FROM public_gold.mart_grid_kpis_daily")
print("daily:", cur.fetchone())
cur.execute("SELECT DISTINCT network FROM public_gold.mart_grid_kpis_daily")
print("networks:", cur.fetchall())
