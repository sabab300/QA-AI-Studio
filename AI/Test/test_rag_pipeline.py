from Core.qa_assistant import QAAssistant

qa = QAAssistant()

result = qa.ask(
    question="What is SD Warehousing?",
    top_k=3
)

print("\n==============================")

print(result)

print("==============================")