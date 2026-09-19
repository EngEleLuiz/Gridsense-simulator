import psycopg2
conn = psycopg2.connect("postgresql://postgres:postgres@localhost:5432/gridsense")
cur = conn.cursor()
cur.execute("SELECT schemaname, tablename FROM pg_tables")
for row in cur.fetchall():
    if "gold" in row[0]:
        print(row)
