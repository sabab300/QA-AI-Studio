from Core.knowledge_manager import KnowledgeManager

manager = KnowledgeManager()

results = manager.search("Warehousing")

print("=" * 80)
print("SEARCH RESULTS")
print("=" * 80)

for row in results:
    print(row)