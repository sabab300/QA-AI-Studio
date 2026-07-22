from Core.ai_service import AIService

ai = AIService()

answer = ai.generate(
    "Say Hello in one sentence."
)

print(answer)