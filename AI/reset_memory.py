from Core.sqlite_manager import SQLiteManager

db = SQLiteManager()

db.execute_non_query(

    "DROP TABLE IF EXISTS ai_memory"

)

print("ai_memory table deleted.")