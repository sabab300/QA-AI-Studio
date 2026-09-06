# Replace: AI/Database/db_manager.py

"""
QA AI Studio
Database Manager

Version: 3.0  (Unified — Phase 1 Consistency)

This is now the ONLY DatabaseManager. db_manager_v3.py is deleted —
having two DatabaseManager classes pointed at the same physical
file, each creating a different subset of tables, was the root of
several bugs already fixed this project (wrong-column data, Smart
Upload domain/module never registering). Everything now imports
from here.
"""

import sqlite3
from pathlib import Path

from Database.schema import (
    ALL_TABLES,
    KNOWLEDGE_ITEMS_MIGRATION,
    DISCOVERY_VARIANTS_MIGRATION,
)


class DatabaseManager:

    def __init__(self):

        # Anchored to this file's own folder (AI/Database), not the
        # process's current working directory — a CWD-relative path
        # here meant the app could silently create/read a different,
        # empty metadata.db depending on how it was launched (double
        # -click vs. shortcut vs. terminal), with uploaded knowledge
        # appearing to have vanished. See App/main.py's APP_DIR/AI_DIR
        # for the same fix already applied to import resolution.
        # Desktop and Web are presentation layers over one canonical store.
        # Keep the existing Web database as the central location so browser,
        # Desktop, and QA Automation always address the same records.
        project_root = Path(__file__).resolve().parents[2]
        db_folder = project_root / "AI-Web" / "Database"
        db_folder.mkdir(exist_ok=True, parents=True)

        self.db_path = db_folder / "metadata.db"
        
    def get_connection(self):

        conn = sqlite3.connect(self.db_path)

        conn.execute("PRAGMA foreign_keys = ON")

        return conn

    def initialize_database(self):
        """
        Creates every table the app needs, if it doesn't already
        exist, and applies any column migrations. Safe to call on
        every single app startup — this IS meant to run every time,
        not once.
        """

        conn = self.get_connection()

        cursor = conn.cursor()

        try:

            for statement in ALL_TABLES:

                cursor.execute(statement)

            for migration in KNOWLEDGE_ITEMS_MIGRATION + DISCOVERY_VARIANTS_MIGRATION:

                try:

                    cursor.execute(migration)

                except sqlite3.OperationalError:

                    # Column already exists — expected on every
                    # run after the first.
                    pass

            conn.commit()

        except Exception:

            conn.rollback()

            raise

        finally:

            conn.close()

        print("Metadata database initialized successfully.")
