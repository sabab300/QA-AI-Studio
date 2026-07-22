"""
QA AI Studio
Search Engine V2
Version: 1.0
"""

from Core.embedding_engine import EmbeddingEngine
from Core.vector_store import VectorStore
from Core.search_result_formatter import SearchResultFormatter


class SearchEngineV2:

    def __init__(self):

        self.embedding = EmbeddingEngine()
        self.vector_store = VectorStore()
        self.formatter = SearchResultFormatter()

    # --------------------------------------------------
    # Search
    # --------------------------------------------------

    def search(
        self,
        query,
        limit=5
    ):

        embedding = self.embedding.generate_embedding(query)

        if embedding is None:

            return []

        results = self.vector_store.search(

            embedding=embedding,

            limit=limit

        )

        if not results:

            return []

        return self.formatter.format(results)

    # --------------------------------------------------
    # Search by Domain
    # --------------------------------------------------

    def search_domain(
        self,
        query,
        domain,
        limit=5
    ):

        results = self.search(query, limit)

        return [

            item

            for item in results

            if item["domain"] == domain

        ]

    # --------------------------------------------------
    # Search by Module
    # --------------------------------------------------

    def search_module(
        self,
        query,
        module,
        limit=5
    ):

        results = self.search(query, limit)

        return [

            item

            for item in results

            if item["module"] == module

        ]

    # --------------------------------------------------
    # Search by Knowledge Name
    # --------------------------------------------------

    def search_knowledge(
        self,
        query,
        knowledge_name,
        limit=5
    ):

        results = self.search(query, limit)

        return [

            item

            for item in results

            if item["knowledge_name"] == knowledge_name

        ]

    # --------------------------------------------------
    # Search by Version
    # --------------------------------------------------

    def search_version(
        self,
        query,
        version,
        limit=5
    ):

        results = self.search(query, limit)

        return [

            item

            for item in results

            if item["version"] == version

        ]