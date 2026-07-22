from Core.vector_store import VectorStore

store = VectorStore()

result = store.collection.get()

print("\n===== TOTAL DOCUMENTS =====")
print(len(result["documents"]))

print("\n===== FIRST 20 DOCUMENTS =====")

for i, doc in enumerate(result["documents"][:20]):
    print(f"\n--- DOCUMENT {i+1} ---")
    print(doc)

print("\n===== FIRST 20 METADATA =====")

for i, meta in enumerate(result["metadatas"][:20]):
    print(f"\n--- METADATA {i+1} ---")
    print(meta)