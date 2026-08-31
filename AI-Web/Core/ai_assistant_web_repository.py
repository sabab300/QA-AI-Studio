# Create: AI/Core/ai_assistant_web_repository.py

"""
QA AI Studio — Web
AI Assistant Web Repository (Milestone 4, backend)

Version: 1.0

Thin web-facing wrapper around Core/ai_orchestrator.py's
AIOrchestrator — the exact same routing/intent-detection/RAG/
generator/memory stack the desktop app's backend already has fully
wired (per the codebase analysis, this was backend-complete with
ZERO UI on the desktop app — "the nav item opens a blank
placeholder"). Nothing in Core/ changes to support this.

Requires a running Ollama server (Config/settings.py: OLLAMA_URL,
LLM_MODEL) to produce real answers — this sandbox has no route to a
local Ollama instance, so this class is delivered and structurally
verified here (construction, session persistence, history/clear),
but a live end-to-end "ask a real question, get a real AI answer"
round-trip needs to be verified on a machine that actually has
Ollama running, same requirement the desktop app already has.
"""

from Core.ai_orchestrator import AIOrchestrator


class AiAssistantRepository:

    def __init__(self):

        self.orchestrator = AIOrchestrator()

    def ask(self, prompt, session_id="default", domain=None, module=None, knowledge_name=None, version=None):

        if not (prompt or "").strip():

            raise ValueError("A question is required.")

        return self.orchestrator.process(
            prompt=prompt, session_id=session_id, domain=domain,
            module=module, knowledge_name=knowledge_name, version=version,
        )

    def history(self, session_id="default", limit=10):

        return self.orchestrator.history(session_id=session_id, limit=limit)

    def clear_history(self, session_id="default"):

        self.orchestrator.clear_history(session_id=session_id)

        return {"status": "cleared", "session_id": session_id}
