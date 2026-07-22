from Core.embedding_engine import EmbeddingEngine
from Core.vector_store import VectorStore


class KnowledgeSimilarity:

    def __init__(self):

        self.embedding = EmbeddingEngine()
        self.vector_store = VectorStore()

    def search(self, text, limit=5):

        embedding = self.embedding.generate_embedding(text)

        # Compatible with old/new VectorStore APIs
        try:

            results = self.vector_store.search(
                embedding,
                limit=limit
            )

        except TypeError:

            try:

                results = self.vector_store.search(
                    embedding,
                    top_k=limit
                )

            except TypeError:

                results = self.vector_store.search(
                    embedding
                )

        return results