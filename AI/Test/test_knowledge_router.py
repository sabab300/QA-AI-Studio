from Core.knowledge_router import KnowledgeRouter

router = KnowledgeRouter()

questions = [
    "Generate test cases for SD Warehousing",
    "What is PSW?",
    "What is the history of WeBOC?",
    "Generate SQL for Single Declaration",
    "Explain Selenium"
]

for q in questions:
    print(q)
    print(router.route(q))
    print("-" * 40)