from Core.rag_engine import RAGEngine

rag = RAGEngine()

result = rag.search(

    "How is warehouse selected in SD?"

)

print()

print("=" * 60)

print(result["success"])

print()

print(result["context"][:2000])

print()

print("=" * 60)