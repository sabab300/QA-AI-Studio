from Core.llm_engine import LLMEngine

ai = LLMEngine()

print(
    ai.generate(
        "What is the current time?"
    )["response"]
)