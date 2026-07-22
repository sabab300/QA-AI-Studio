"""
QA AI Studio
Search Engine
Version: 2.0
"""

from Core.embedding_engine import EmbeddingEngine
from Core.vector_store import VectorStore
from Core.logger import Logger


class SearchEngine:

    def __init__(self):

        self.embedding_engine = EmbeddingEngine()
        self.vector_store = VectorStore()
        self.logger = Logger.get_logger()

    # --------------------------------------------------
    # Search Knowledge Base
    # --------------------------------------------------

    def search(
        self,
        query,
        top_k=5
    ):

        try:

            self.logger.info(
                f"Searching knowledge base: {query}"
            )

            query_embedding = self.embedding_engine.generate_embedding(
                query
            )

            search_result = self.vector_store.search(
                embedding=query_embedding,
                limit=top_k
            )

            if not search_result:

                return {

                    "success": False,

                    "error": "No result found."

                }

            formatted_results = []

            ids = search_result.get("ids", [[]])[0]
            documents = search_result.get("documents", [[]])[0]
            metadatas = search_result.get("metadatas", [[]])[0]
            distances = search_result.get("distances", [[]])[0]

            for index in range(len(ids)):

                formatted_results.append({

                    "id": ids[index],

                    "document": documents[index],

                    "metadata": metadatas[index],

                    "distance": distances[index]

                })

            return {

                "success": True,

                "query": query,

                "total_results": len(formatted_results),

                "results": formatted_results

            }

        except Exception as error:

            self.logger.error(
                f"Search failed: {error}"
            )

            return {

                "success": False,

                "error": str(error)

            }