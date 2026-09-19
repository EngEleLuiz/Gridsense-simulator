import duckdb
con = duckdb.connect()
result = con.execute('SELECT topic, count(*) as n FROM read_parquet("data/bronze/**/*.parquet") GROUP BY topic').fetchall()
print('\n✓ Records in Parquet:')
for topic, count in result:
    print(f'  {topic}: {count} records')
