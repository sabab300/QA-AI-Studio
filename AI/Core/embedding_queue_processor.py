"""
QA AI Studio
Embedding Queue Processor

Version: 1.0
"""

from Core.embedding_manager import EmbeddingManager
from Core.metadata_manager import MetadataManager


class EmbeddingQueueProcessor:

    def __init__(self):

        self.embedding = EmbeddingManager()

        self.metadata = MetadataManager()

    # --------------------------------------------------

    def process(self):

        pending = self.embedding.pending()

        processed = 0

        for item in pending:

            knowledge_id = item[0]

            # Future:
            # Extract Text
            # Chunk
            # Create Embedding
            # Store in ChromaDB

            self.metadata.update_embedding_status(

                knowledge_id,

                "COMPLETED"

            )

            processed += 1

        return processed