"""
QA AI Studio
Knowledge Search

Version: 5.0
"""

from Core.vector_store import VectorStore
from Core.embedding_engine import EmbeddingEngine


class KnowledgeSearch:

    def __init__(self):

        self.store = VectorStore()
        self.embedding = EmbeddingEngine()

    # --------------------------------------------------
    # Search Knowledge
    # --------------------------------------------------

    def search_knowledge(
        self,
        query,
        top_k=5,
        domain=None,
        module=None,
        knowledge_name=None,
        version=None,
        source_file_names=None,
    ):

        vector = self.embedding.generate_embedding(query)

        if vector is None:
            return None

        filters = []

        if domain:
            filters.append({"domain": domain})

        if module:
            filters.append({"module": module})

        if knowledge_name:
            filters.append({"knowledge_name": knowledge_name})

        if version:
            filters.append({"version": version})

        if source_file_names:
            filters.append({"file_name": {"$in": list(source_file_names)}})

        where = None

        if len(filters) == 1:
            where = filters[0]

        elif len(filters) > 1:
            where = {
                "$and": filters
            }

        return self.store.search(
            embedding=vector,
            limit=top_k,
            where=where
        )
