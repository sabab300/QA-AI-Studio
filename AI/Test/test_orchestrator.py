from Core.ai_orchestrator import AIOrchestrator

brain = AIOrchestrator()

result = brain.process(
    task="Analyze Uploaded Document"
)

print(result)