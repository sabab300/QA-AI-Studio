"""
Integration Test
LLM Engine
"""

from Config import settings

from Core.ai_orchestrator import AIOrchestrator


def main():

    print("=" * 60)

    print("QA AI Studio")

    print("=" * 60)

    print()

    print("AI Mode :", settings.AI_MODE)

    print()

    orchestrator = AIOrchestrator()

    result = orchestrator.prompt(

        "Say Hello from QA AI Studio."

    )

    print()

    print(result)

    print()

    print("=" * 60)


if __name__ == "__main__":

    main()