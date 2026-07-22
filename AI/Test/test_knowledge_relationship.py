from Core.knowledge_relationship import KnowledgeRelationship

manager = KnowledgeRelationship()

print("=" * 80)
print("RELATED KNOWLEDGE")
print("=" * 80)

for item in manager.related(4):

    print(item)