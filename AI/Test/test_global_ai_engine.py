"""
QA AI Studio
Test Global AI Engine
"""

from Core.global_ai_engine import GlobalAIEngine


engine = GlobalAIEngine()

questions = [

    "What is Pakistan Single Window?",

    "What is the history of WeBOC?",

    "Explain Selenium.",

    "What is ISO 25010?"

]

for question in questions:

    print("=" * 80)

    print(question)

    print("=" * 80)

    result = engine.answer(question)

    print(result)

    print()