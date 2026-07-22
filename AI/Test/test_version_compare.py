from Core.knowledge_manager import KnowledgeManager
from Core.version_compare import VersionCompare

manager = KnowledgeManager()

old_item = manager.get(3)
new_item = manager.get(4)

compare = VersionCompare()

print("=" * 80)
print("VERSION COMPARISON")
print("=" * 80)

result = compare.compare(
    old_item,
    new_item
)

print(result)