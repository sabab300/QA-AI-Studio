from Core.llm_engine import LLMEngine

ai = LLMEngine()

while True:

    question = input("\nYou: ")

    if question.lower() == "exit":
        break

    result = ai.generate(question)

    print("\nAI:")
    print(result["response"])