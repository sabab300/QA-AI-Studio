from Core.vector_store import VectorStore


store = VectorStore()

result = store.collection.get(
    limit=10
)

print("====================")
print("Documents:")
print(result["documents"])

print("====================")
print("Metadata:")
print(result["metadatas"])