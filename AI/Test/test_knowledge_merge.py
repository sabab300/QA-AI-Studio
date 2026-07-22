from Core.knowledge_merge import KnowledgeMerge

merge = KnowledgeMerge()

result = merge.merge(

    [2, 3, 4]

)

print("=" * 80)
print("KNOWLEDGE MERGE")
print("=" * 80)

print("Documents :", result["count"])

print()

print("Tags")

print(result["tags"])

print()

print("Summary")

print(result["summary"][:1000])