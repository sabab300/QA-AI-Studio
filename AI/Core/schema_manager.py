"""
QA AI Studio
Schema Manager

Version: 2.0

Database initialization wrapper.
"""

from Database.db_manager import DatabaseManager
from Core.logger import Logger


class SchemaManager:


    SCHEMA_VERSION = 2


    def __init__(self):

        self.db = DatabaseManager()

        self.logger = Logger.get_logger()



    # --------------------------------------------------
    # Initialize Database
    # --------------------------------------------------

    def initialize_database(self):

        self.logger.info(
            "Initializing database schema..."
        )


        self.db.initialize_database()


        self.logger.info(
            "Database schema initialized successfully."
        )



    # --------------------------------------------------
    # Schema Version
    # --------------------------------------------------

    def get_schema_version(self):

        conn = self.db.get_connection()

        cursor = conn.cursor()


        cursor.execute(
            "PRAGMA user_version"
        )


        result = cursor.fetchone()


        conn.close()


        return result[0] if result else 0