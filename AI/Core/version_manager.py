"""
QA AI Studio
Version Manager

Version: 1.0
"""

from Core.metadata_manager import MetadataManager


class VersionManager:

    def __init__(self):

        self.metadata = MetadataManager()

    # --------------------------------------------------

    def versions(
        self,
        knowledge_id
    ):

        return self.metadata.get_versions(
            knowledge_id
        )

    # --------------------------------------------------

    def latest(
        self,
        knowledge_id
    ):

        versions = self.versions(
            knowledge_id
        )

        if versions:
            return versions[0]

        return None
    
    # --------------------------------------------------
    # Add New Version
    # --------------------------------------------------

    def add_version(
        self,
        knowledge_id,
        version,
        file_info
    ):

        conn = self.metadata.db.get_connection()

        cursor = conn.cursor()

        from datetime import datetime

        now = datetime.now().isoformat()

        cursor.execute(
            """
            INSERT INTO knowledge_versions
            (
                knowledge_item_id,
                version,
                sha256,
                repository_path,
                created_date
            )
            VALUES
            (?, ?, ?, ?, ?)
            """,
            (
                knowledge_id,
                version,
                file_info["sha256"],
                file_info["repository_path"],
                now
            )
        )

        conn.commit()

        conn.close()

        return True
    
    # --------------------------------------------------
    # Duplicate File Check
    # --------------------------------------------------

    def exists(
        self,
        sha256
    ):

        conn = self.metadata.db.get_connection()

        cursor = conn.cursor()

        cursor.execute(
            """
            SELECT id,
                knowledge_name,
                version
            FROM knowledge_items
            WHERE sha256=?
            """,
            (
                sha256,
            )
        )

        row = cursor.fetchone()

        conn.close()

        return row