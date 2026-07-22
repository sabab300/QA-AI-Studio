from Core.knowledge_manager import KnowledgeManager

manager = KnowledgeManager()

print("=" * 80)
print("VERSIONS")
print("=" * 80)

for version in manager.versions(4):
    print(version)