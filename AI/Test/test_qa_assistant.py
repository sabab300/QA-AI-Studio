from Core.qa_assistant import QAAssistant

assistant = QAAssistant()

result = assistant.ask(
    "How to create Single Declaration?",
    domain="PSW Domain",
    module="SD Warehousing"
)

print(result)