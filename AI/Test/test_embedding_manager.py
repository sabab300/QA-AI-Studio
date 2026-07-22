from Core.embedding_manager import EmbeddingManager

manager = EmbeddingManager()

print("=" * 80)
print("PENDING EMBEDDINGS")
print("=" * 80)

for item in manager.pending():

    print(item)

print()

print("=" * 80)
print("REBUILD")
print("=" * 80)

print(manager.rebuild(4))