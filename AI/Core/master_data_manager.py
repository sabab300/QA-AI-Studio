"""
QA AI Studio
Master Data Manager
"""

from Core.config_manager import ConfigManager
from Core.sqlite_manager import SQLiteManager
from Core.logger import Logger


class MasterDataManager:

    def __init__(self):

        self.config = ConfigManager()

        self.db = SQLiteManager()

        self.logger = Logger.get_logger()

        self.master_data = self.config.master_data

    # --------------------------------------------------

    def sync_all(self):

        self.sync_table(
            "business_domains",
            self.master_data.get("business_domains", [])
        )

        self.sync_table(
            "platforms",
            self.master_data.get("platforms", [])
        )

        self.sync_table(
            "categories",
            self.master_data.get("categories", [])
        )

        self.sync_table(
            "business_processes",
            self.master_data.get("business_processes", [])
        )

        self.sync_table(
            "document_types",
            self.master_data.get("document_types", [])
        )

        self.logger.info("Master data synchronized successfully.")

    # --------------------------------------------------

    def sync_table(self, table_name, values):

        for value in values:

            self.db.execute_non_query(

                f"""
                INSERT OR IGNORE
                INTO {table_name}(name)
                VALUES(?)
                """,

                (value,)

            )

    # --------------------------------------------------

    def get_id(self, table_name, name):

        if not name:

            return None

        row = self.db.fetch_one(

            f"""
            SELECT id
            FROM {table_name}
            WHERE name=?
            """,

            (name,)

        )

        if row:

            return row[0]

        return None

    # --------------------------------------------------

    def get_business_domain_id(self, name):

        return self.get_id(
            "business_domains",
            name
        )

    # --------------------------------------------------

    def get_platform_id(self, name):

        return self.get_id(
            "platforms",
            name
        )

    # --------------------------------------------------

    def get_category_id(self, name):

        return self.get_id(
            "categories",
            name
        )

    # --------------------------------------------------

    def get_business_process_id(self, name):

        return self.get_id(
            "business_processes",
            name
        )

    # --------------------------------------------------

    def get_document_type_id(self, name):

        return self.get_id(
            "document_types",
            name
        )