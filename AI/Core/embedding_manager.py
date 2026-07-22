"""
QA AI Studio
Embedding Manager

Version: 1.0
"""

from Core.metadata_manager import MetadataManager


class EmbeddingManager:

    def __init__(self):

        self.metadata = MetadataManager()

    # --------------------------------------------------

    def pending(self):

        conn = self.metadata.db.get_connection()

        cursor = conn.cursor()

        cursor.execute(
            """
            SELECT
                knowledge_id,
                status,
                priority,
                created_date
            FROM embedding_queue
            WHERE status='PENDING'
            ORDER BY priority DESC,
                     created_date ASC
            """
        )

        rows = cursor.fetchall()

        conn.close()

        return rows

    # --------------------------------------------------

    def rebuild(

        self,

        knowledge_id

    ):

        self.metadata.update_embedding_status(

            knowledge_id,

            "PENDING"

        )

        return True