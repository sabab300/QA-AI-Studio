import sqlite3
from contextlib import contextmanager

from Core.config_manager import ConfigManager


class SQLiteManager:
    """
    Enterprise SQLite Manager

    Responsibilities
    ----------------
    • Connection Management
    • Transactions
    • Foreign Keys
    • Generic CRUD Helpers
    • Future Schema Migrations
    """

    def __init__(self):

        self.config = ConfigManager()

        self.db_path = self.config.metadata_db

    # --------------------------------------------------

    @contextmanager
    def connection(self):

        conn = sqlite3.connect(self.db_path)

        conn.execute("PRAGMA foreign_keys = ON")

        try:

            yield conn

            conn.commit()

        except Exception:

            conn.rollback()

            raise

        finally:

            conn.close()

    # --------------------------------------------------

    def execute_non_query(
        self,
        query,
        parameters=()
    ):

        with self.connection() as conn:

            cursor = conn.cursor()

            cursor.execute(query, parameters)

    # --------------------------------------------------

    def execute_insert(
        self,
        query,
        parameters=()
    ):

        with self.connection() as conn:

            cursor = conn.cursor()

            cursor.execute(query, parameters)

            return cursor.lastrowid

    # --------------------------------------------------

    def execute_many(
        self,
        query,
        values
    ):

        with self.connection() as conn:

            cursor = conn.cursor()

            cursor.executemany(query, values)

    # --------------------------------------------------

    def fetch_one(
        self,
        query,
        parameters=()
    ):

        with self.connection() as conn:

            cursor = conn.cursor()

            cursor.execute(query, parameters)

            return cursor.fetchone()

    # --------------------------------------------------

    def fetch_all(
        self,
        query,
        parameters=()
    ):

        with self.connection() as conn:

            cursor = conn.cursor()

            cursor.execute(query, parameters)

            return cursor.fetchall()

    # --------------------------------------------------

    def table_exists(
        self,
        table_name
    ):

        row = self.fetch_one(

            """
            SELECT name

            FROM sqlite_master

            WHERE type='table'

            AND name=?

            """,

            (table_name,)

        )

        return row is not None

    # --------------------------------------------------

    def column_exists(
        self,
        table_name,
        column_name
    ):

        with self.connection() as conn:

            cursor = conn.cursor()

            cursor.execute(

                f"PRAGMA table_info({table_name})"

            )

            columns = cursor.fetchall()

        for column in columns:

            if column[1] == column_name:

                return True

        return False