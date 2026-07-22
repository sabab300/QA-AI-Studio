from Core.ai_orchestrator import AIOrchestrator

ai = AIOrchestrator()

question = "What is World Customs Organization?"

print("=" * 80)
print(question)
print("=" * 80)

result = ai.process(question)

print(result)