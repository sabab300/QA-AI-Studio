from Core.embedding_engine import EmbeddingEngine
from Core.vector_store import VectorStore
from Core.search_result_formatter import SearchResultFormatter

embedding = EmbeddingEngine()
vector_store = VectorStore()
formatter = SearchResultFormatter()

query = embedding.generate_embedding(
    "What is SD Warehousing?"
)

results = vector_store.search(
    query,
    limit=3
)

formatted = formatter.format(results)

print("=" * 80)
print("FORMATTED SEARCH RESULTS")
print("=" * 80)

for item in formatted:

    print(item)
    print("-" * 80)