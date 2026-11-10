"""
QA AI Studio
AI Orchestrator

Version: 5.0
"""
import time

from Core.logger import Logger

from Core.intent_detector import IntentDetector
from Core.knowledge_router import KnowledgeRouter
from Core.global_ai_engine import GlobalAIEngine
from Core.memory_manager import MemoryManager

from Core.answer_generator import AnswerGenerator
from Core.sql_generator import SQLGenerator
from Core.api_test_generator import APITestGenerator
from Core.automation_generator import AutomationGenerator
from Core.bug_report_generator import BugReportGenerator
from Core.test_case_generator import TestCaseGenerator

from Core.output_formatter import OutputFormatter
from Core.model_warmup import ModelWarmup
from Core.generator_registry import GeneratorRegistry


class _Fallback:
    """Marker: the enhanced pipeline took a documented, recoverable fallback."""

    def __init__(self, notice):

        self.notice = notice


class AIOrchestrator:

    ROUTE_LOCAL = "local"
    ROUTE_GLOBAL = "global"
    DEFAULT_GENERATOR = "answer"

    def __init__(self):

        self.logger = Logger.get_logger()
        self.intent = IntentDetector()
        self.router = KnowledgeRouter()
        self.global_ai = GlobalAIEngine()
        self.memory = MemoryManager()
        self.output = OutputFormatter()

    # -----------------------------
    # Generator Registry
    # -----------------------------

        self.registry = GeneratorRegistry()

        self._register_generators()

        self.logger.info(
            f"{len(self.registry.all())} generators loaded."
        )

        ModelWarmup.warmup()

    # --------------------------------------------------

    def _register_generators(self):

        self.registry.register(
            "answer",
            AnswerGenerator()
        )

        self.registry.register(
            "sql",
            SQLGenerator()
        )

        self.registry.register(
            "api",
            APITestGenerator()
        )

        self.registry.register(
            "automation",
            AutomationGenerator()
        )

        self.registry.register(
            "bug",
            BugReportGenerator()
        )

        self.registry.register(
            "test_case",
            TestCaseGenerator()
        )

    # --------------------------------------------------

    def process(
        self,
        prompt,
        session_id="default",
        domain=None,
        module=None,
        knowledge_name=None,
        version=None,
        document_type=None,
        document_version_id=None,
        mode=None,
        model_mode=None,
        model_selection=None,
        request_id=None,
        user=None,
        use_tools=False,
    ):

        start_time = time.perf_counter()

        # [AI-DIAG] temporary diagnostic — confirms the request actually
        # reached the orchestrator with the payload the frontend sent,
        # per the requested Phase 2 "request received" boundary.
        self.logger.info(
            "[AI-DIAG] AI Assistant request received: prompt_chars=%s "
            "domain=%s module=%s knowledge_name=%s version=%s" % (
                len(prompt or ""),
                domain,
                module,
                knowledge_name,
                version,
            )
        )


        # QA-AI-STUDIO-TASK4: enhanced pipeline (Settings -> LLM). Handles
        # Ask Knowledge in LOCAL / GLOBAL / HYBRID with grounded citations.
        # Generator requests (test cases, SQL, API, bug, automation) still use
        # the existing generators, now routed through the LLM gateway.
        enhanced = self._try_enhanced(
            prompt, session_id, domain, module, knowledge_name, version,
            document_type, document_version_id, mode, model_mode,
            model_selection, request_id, user, use_tools, start_time,
        )

        fallback_note = None

        if isinstance(enhanced, _Fallback):

            fallback_note = enhanced.notice

            enhanced = None

        if enhanced is not None:

            return enhanced

        # QA-AI-STUDIO-AI-ASSISTANT-PHASE2-ROUTING-FIX: KnowledgeRouter.route()
        # decides global-vs-local purely from keywords in the question text
        # (e.g. "what is" -> global), with no awareness of the Domain/Module
        # scope the user explicitly picked in the UI. That meant a question
        # like "What is SD Warehousing?" asked with Domain="PSW Core"
        # selected would still be routed to the internet-search GLOBAL path
        # and silently ignore the user's explicit scope choice — an
        # intentional user instruction should always win over keyword
        # inference. Domain (with or without Module) now forces the LOCAL
        # RAG path, scoped to exactly what the user selected; the keyword
        # router only runs when no explicit scope was given, preserving the
        # existing "What is PSW" (no scope) -> GLOBAL behavior.
        if domain:

            route = self.ROUTE_LOCAL

            self.logger.info(

                "Knowledge Route: local (explicit Domain%s scope overrides "
                "keyword routing)" % (" + Module" if module else "")

            )

        else:

            route = self.router.route(prompt)

            self.logger.info(

                f"Knowledge Route: {route}"

            )


        # GLOBAL INTERNET AI

        if route == self.ROUTE_GLOBAL:

            result = self.global_ai.answer(

                prompt

            )

            intent = self.ROUTE_GLOBAL


        # LOCAL RAG + GENERATORS

        else:

            intent = self.intent.detect(prompt)

            self.logger.info(

                f"Detected Intent: {intent}"

            )

            generator = self.registry.get(intent)

            if generator is None:

                self.logger.warning(
                    f"No generator registered for '{intent}'. Using default generator."
                )

                generator = self.registry.get(self.DEFAULT_GENERATOR)

            if generator is None:

                return self.output.format_response({

                    "success": False,

                    "error": "Default generator is not registered.",

                    "intent": intent,

                    "route": route

                })

            result = generator.generate(
                prompt,
                domain=domain,
                module=module,
                knowledge_name=knowledge_name,
                version=version,
                document_type=document_type,
                document_version_id=document_version_id,
            )

        execution_time = round(

            time.perf_counter() - start_time,

            2

        )


        if not isinstance(result, dict):

            result = {

                "success": False,

                "error": "Invalid generator response."

            }


        result["intent"] = intent

        result["route"] = route

        result["execution_time"] = execution_time



        if result.get("success", False):

            response = self._get_response_text(result)


            self.memory.save(

                prompt=prompt,

                response=response,

                intent=intent,

                session_id=session_id

            )


        self.logger.info(

            f"{intent} completed in {execution_time:.2f} sec"

        )


        response = self.output.format_response(result)

        if fallback_note:

            # never silent: tell the user the legacy pipeline answered
            response["warnings"] = list(response.get("warnings") or []) + [
                fallback_note["message"]
            ]

            platform = dict(response.get("platform") or {})

            platform["pipeline_fallback"] = {
                "used": True, "blocked": False,
                "reason": fallback_note["reason"],
            }

            response["platform"] = platform

        return response

    # --------------------------------------------------


    # --------------------------------------------------
    # QA-AI-STUDIO-TASK4 -- enhanced pipeline
    # --------------------------------------------------

    _assistant = None

    def _try_enhanced(
        self, prompt, session_id, domain, module, knowledge_name, version,
        document_type, document_version_id, mode, model_mode,
        model_selection, request_id, user, use_tools, start_time,
    ):

        # QA-AI-STUDIO-TASK4 (Phase 5): fallback policy lives in
        # Core/ai_platform/fallback.py. Only documented, recoverable
        # conditions may fall back to the legacy pipeline, and never silently.
        try:

            from Core.ai_platform import fallback as fb
            from Core.ai_platform import runtime
            from Core.ai_platform.assistant import AssistantService

        except ImportError as exc:

            self.logger.warning(
                "Enhanced AI components unavailable (%s); using legacy pipeline.",
                type(exc).__name__,
            )

            return _Fallback({
                "used": True,
                "reason": "enhanced_component_unavailable",
                "message": "The enhanced AI pipeline is unavailable "
                           "(enhanced component unavailable); this answer was "
                           "produced by the legacy pipeline.",
            })

        try:

            if fb.env_disabled():

                return None

            settings_ = runtime.get_store().get_settings()

            if not settings_["rag"].get("enhanced_pipeline", True):

                return None

            if AIOrchestrator._assistant is None:

                AIOrchestrator._assistant = AssistantService()

            assistant = AIOrchestrator._assistant

            scope = {
                "domain": domain, "module": module,
                "knowledge_name": knowledge_name, "version": version,
                "document_type": document_type,
                "document_version_id": document_version_id,
            }

            requested = (mode or "auto").lower()

            _scope = {k: v for k, v in scope.items() if v}

            if hasattr(assistant, "route_prompt"):

                route = assistant.route_prompt(
                    requested, _scope, prompt, settings_["routing"],
                )

            else:  # assistant objects without the explainable router

                _mode, _why = assistant.resolve_mode(
                    requested, _scope, prompt, settings_["routing"],
                )

                route = {"mode": _mode, "auto": False, "reasons": [_why]}

            resolved = route["mode"]

            intent = "answer"

            # Generator intents ("write test cases ...") are detected for LOCAL
            # and also when AUTO routing chose GLOBAL/HYBRID, because a request
            # such as "write test cases for a login page" contains no document cue.
            if requested in ("auto", "local") and (
                resolved == "local" or route.get("auto")
            ):

                intent = self.intent.detect(prompt)

            if intent != "answer" and self.registry.get(intent) is not None:

                return self._legacy_generator(
                    prompt, session_id, scope, intent, resolved,
                    model_mode, model_selection, request_id, user,
                    start_time,
                )

            result = assistant.ask(
                prompt, user=user or {}, scope=scope, mode=requested,
                model_mode=model_mode, selection=model_selection,
                use_tools=use_tools, request_id=request_id,
                session_id=session_id,
            )

            if result.get("success"):

                response = self._get_response_text(result)

                self.memory.save(
                    prompt=prompt,
                    response=response,
                    intent="answer",
                    session_id=session_id,
                    source=result.get("route", "local"),
                    domain=domain,
                    module=module,
                    knowledge_name=knowledge_name,
                    confidence=result.get("confidence", 0),
                    execution_time=result.get("execution_time", 0),
                )

            return self.output.format_response(result)

        except Exception as exc:

            info = fb.classify(exc)

            if info["recoverable"]:

                self.logger.warning(
                    "Enhanced AI pipeline: recoverable failure (%s); "
                    "using legacy pipeline.", info["category"],
                )

                fb.audit((user or {}).get("username"), info)

                return _Fallback(fb.notice(info))

            # Authorization, privacy, provider-permission, configuration,
            # security-validation and any unexpected error: do NOT fall back.
            self.logger.exception(
                "Enhanced AI pipeline failed (%s); NOT falling back.",
                info["category"],
            )

            return self.output.format_response(fb.blocked_response(exc, info))

    def _legacy_generator(
        self, prompt, session_id, scope, intent, route, model_mode,
        model_selection, request_id, user, start_time,
    ):

        from Core.ai_platform import runtime

        generator = self.registry.get(intent)

        selection = model_selection if model_mode == "manual" else None

        token = runtime.set_context(
            username=(user or {}).get("username", ""), mode=route,
            selection=selection, request_id=request_id, task=intent,
            data_class="private",
        )

        try:

            result = generator.generate(
                prompt,
                domain=scope.get("domain"),
                module=scope.get("module"),
                knowledge_name=scope.get("knowledge_name"),
                version=scope.get("version"),
                document_type=scope.get("document_type"),
                document_version_id=scope.get("document_version_id"),
            )

        finally:

            runtime.reset_context(token)

        if not isinstance(result, dict):

            result = {"success": False, "error": "Invalid generator response."}

        result["intent"] = intent
        result["route"] = route
        result["execution_time"] = round(time.perf_counter() - start_time, 2)
        result["platform"] = {
            "legacy_generator": intent,
            "llm": {"provider": result.get("provider"), "model": result.get("model")},
            "route_decision": {"mode_used": route, "reasons": [
                f"Request recognised as a '{intent}' generation task."
            ]},
        }

        if result.get("success", False):

            self.memory.save(
                prompt=prompt,
                response=self._get_response_text(result),
                intent=intent,
                session_id=session_id,
            )

        return self.output.format_response(result)

    # --------------------------------------------------
    # Extract response text from generator result
    # --------------------------------------------------

    def _get_response_text(self, result):

        if not isinstance(result, dict):
            return ""

        fields = [

            "answer",
            "sql",
            "api_tests",
            "automation_code",
            "bug_report",
            "test_cases",
            "response"

        ]

        for field in fields:

            value = result.get(field)

            if value:

                return str(value)

        return ""
    
    # --------------------------------------------------
    # Backward Compatibility Wrapper
    # --------------------------------------------------

    def prompt(self, text, session_id="default"):
        """
        Compatibility method for older integrations/tests.
        Routes request through the production process pipeline.
        """

        return self.process(
            prompt=text,
            session_id=session_id
        )


    def history(

        self,

        session_id="default",

        limit=10

    ):

        return self.memory.get_recent(

            session_id=session_id,

            limit=limit

        )



    # --------------------------------------------------

    def clear_history(

        self,

        session_id="default"

    ):

        self.memory.clear(

            session_id=session_id

        )
