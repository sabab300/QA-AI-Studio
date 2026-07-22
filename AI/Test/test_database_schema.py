from Core.sqlite_manager import SQLiteManager

db = SQLiteManager()

tables = db.fetch_all("""
SELECT name
FROM sqlite_master
WHERE type='table'
ORDER BY name;
""")

print("TABLES")
print("=" * 50)

for table in tables:
    print(f"\n{table[0]}")
    print("-" * 50)

    rows = db.fetch_all(f"PRAGMA table_info({table[0]});")

    for row in rows:
        print(row)