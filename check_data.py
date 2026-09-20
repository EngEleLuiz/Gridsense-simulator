import os

import psycopg2

db_url = os.getenv(
    "GRIDSENSE_DB_URL", "postgresql://postgres:postgres@localhost:5432/gridsense"
)
conn = psycopg2.connect(db_url)
cur = conn.cursor()
cur.execute("SELECT count(*) FROM public_gold.mart_grid_kpis_daily")
print("daily:", cur.fetchone())
cur.execute("SELECT DISTINCT network FROM public_gold.mart_grid_kpis_daily")
print("networks:", cur.fetchall())
