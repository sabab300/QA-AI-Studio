from Core.knowledge_manager import KnowledgeManager

manager = KnowledgeManager()

print("=" * 80)
print("KNOWLEDGE ITEMS")
print("=" * 80)

for item in manager.list_all():
    print(item)

print("\n" + "=" * 80)
print("KNOWLEDGE TREE")
print("=" * 80)

tree = manager.tree()

for domain, modules in tree.items():

    print(domain)

    for module, knowledge in modules.items():

        print(f"  └── {module}")

        for name, versions in knowledge.items():

            print(f"      └── {name}")

            for version in versions:

                print(f"          └── {version}")

print("\n" + "=" * 80)
print("GET ID = 4")
print("=" * 80)

print(manager.get(4))