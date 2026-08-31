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
        version=None
    ):

        start_time = time.perf_counter()


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
                version=version
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


        return self.output.format_response(result)

    # --------------------------------------------------

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

