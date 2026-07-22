import time

from Core.ai_orchestrator import AIOrchestrator

ai = AIOrchestrator()

questions = [
    "Explain SD Warehousing",
    "What is World Customs Organization?"
]

for q in questions:

    start = time.perf_counter()

    result = ai.process(q)

    elapsed = round(time.perf_counter() - start, 2)

    print("=" * 80)
    print(q)
    print(f"Time : {elapsed} sec")
    print(f"Success : {result.get('success')}")
    print(f"Intent : {result.get('intent')}")