from Core.knowledge_similarity import KnowledgeSimilarity

engine = KnowledgeSimilarity()

print("=" * 80)
print("KNOWLEDGE SIMILARITY")
print("=" * 80)

results = engine.search(

    "SD Warehousing"

)

for item in results:

    print(item)