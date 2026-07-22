from Core.retrieval_manager import RetrievalManager

manager = RetrievalManager()

print("=" * 50)
print(manager.retrieve("What is PSW?"))

print("=" * 50)
print(manager.retrieve("Generate test cases for SD Warehousing"))