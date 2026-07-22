from Core.vector_store import VectorStore

store = VectorStore()

store.collection.delete()

print("Collection cleared.")

print("Count:", store.collection.count())