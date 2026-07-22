from Core.ai_orchestrator import AIOrchestrator

ai = AIOrchestrator()

tests = [

    "Explain SD Warehousing",

    "Generate SQL to verify Single Declaration",

    "Generate API test cases for Single Declaration",

    "Create Selenium automation for Login page",

    "Write bug report for Login page validation",

    "Generate test cases for SD Warehousing",

    "What is World Customs Organization?"

]

for question in tests:

    print("\n" + "=" * 80)

    print("QUESTION:")

    print(question)

    print("-" * 80)

    result = ai.process(question)

    print(result)

print("\n" + "=" * 80)

print("MEMORY")

print("=" * 80)

history = ai.history()

for item in history:

    print(item)