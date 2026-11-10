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

    def ask(self, prompt, session_id="default", domain=None, module=None, knowledge_name=None, version=None, document_type=None, document_version_id=None, mode=None, model_mode=None, model_selection=None, request_id=None, user=None, use_tools=False):

        if not (prompt or "").strip():

            raise ValueError("A question is required.")

        return self.orchestrator.process(
            prompt=prompt, session_id=session_id, domain=domain,
            module=module, knowledge_name=knowledge_name, version=version,
            document_type=document_type,document_version_id=document_version_id,
            mode=mode, model_mode=model_mode, model_selection=model_selection,
            request_id=request_id, user=user, use_tools=use_tools,
        )

    def history(self, session_id="default", limit=10):

        return self.orchestrator.history(session_id=session_id, limit=limit)

    def clear_history(self, session_id="default"):

        self.orchestrator.clear_history(session_id=session_id)

        return {"status": "cleared", "session_id": session_id}

    # --------------------------------------------------
    # QA-AI-STUDIO-IMPACT-ANALYSIS-V1
    # --------------------------------------------------

    def impact_analysis(self, session_id, source, target, question=None, progress_callback=None, cancel_check=None):
        """Thin delegation to Core/impact_analysis_service.py's
        ImpactAnalysisService -- same pattern as ask() delegating to
        AIOrchestrator. Imported lazily so a role without Impact
        Analysis access never pays the extra import/construction cost
        of the retrieval/LLM/test-case stack this pulls in."""

        from Core.impact_analysis_service import ImpactAnalysisService

        if not isinstance(source, dict) or not isinstance(target, dict):

            raise ValueError("Source and target scope are required.")

        return ImpactAnalysisService().analyze(
            session_id=session_id, source=source, target=target, question=question,
            progress_callback=progress_callback, cancel_check=cancel_check,
        )

    def impact_analysis_history(self, session_id="default", limit=10):

        from Core.impact_analysis_repository import ImpactAnalysisRepository

        return ImpactAnalysisRepository().get_recent(session_id=session_id, limit=limit)
