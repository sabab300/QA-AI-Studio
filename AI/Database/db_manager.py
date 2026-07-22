import sqlite3
from pathlib import Path

from Database.schema import (
    KNOWLEDGE_ITEMS_TABLE,
    KNOWLEDGE_TAGS_TABLE,
    EMBEDDING_QUEUE_TABLE,
    KNOWLEDGE_VERSIONS_TABLE,
    KNOWLEDGE_ITEMS_MIGRATION
)


class DatabaseManager:

    def __init__(self):

        db_folder = Path("Database")
        db_folder.mkdir(exist_ok=True)
        
        self.db_path = db_folder / "metadata.db"

        
    def get_connection(self):

        return sqlite3.connect(self.db_path)

    def initialize_database(self):

        conn = self.get_connection()

        cursor = conn.cursor()

        cursor.execute(KNOWLEDGE_ITEMS_TABLE)

        for migration in KNOWLEDGE_ITEMS_MIGRATION:

            try:
                cursor.execute(migration)

            except sqlite3.OperationalError:

                pass

        cursor.execute(KNOWLEDGE_VERSIONS_TABLE)

        cursor.execute(KNOWLEDGE_TAGS_TABLE)

        cursor.execute(EMBEDDING_QUEUE_TABLE)

        conn.commit()

        conn.close()

        print("Metadata database initialized successfully.")

        