"""
QA AI Studio
Database Manager

Version: 3.0
"""

import sqlite3
from pathlib import Path

from Database import schema


class DatabaseManager:

    def __init__(self):
        db_folder = Path("Database")
        db_folder.mkdir(exist_ok=True)
        self.db_path = db_folder / "metadata.db"

    def get_connection(self):
        conn = sqlite3.connect(self.db_path)
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def initialize_database(self):
        conn = self.get_connection()
        cur = conn.cursor()

        try:
            # Create core knowledge tables
            cur.execute(schema.KNOWLEDGE_ITEMS_TABLE)
            for migration in getattr(schema, "KNOWLEDGE_ITEMS_MIGRATION", []):
                try:
                    cur.execute(migration)
                except sqlite3.OperationalError:
                    pass

            cur.execute(schema.KNOWLEDGE_VERSIONS_TABLE)
            cur.execute(schema.KNOWLEDGE_TAGS_TABLE)
            cur.execute(schema.EMBEDDING_QUEUE_TABLE)

            # Create any additional tables declared in schema.ALL_TABLES
            for stmt in getattr(schema, "ALL_TABLES", []):
                cur.execute(stmt)

            # Create indexes
            for stmt in getattr(schema, "INDEXES", []):
                cur.execute(stmt)

            # Seed default data
            for stmt, params in getattr(schema, "SEED_DATA", []):
                cur.execute(stmt, params)

            # Schema version
            version = getattr(schema, "SCHEMA_VERSION", 3)
            cur.execute(f"PRAGMA user_version={version}")

            conn.commit()

        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
