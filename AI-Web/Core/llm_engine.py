"""
QA AI Studio
Production LLM Engine

Version: 5.0
"""

import time
import hashlib

from Config import settings

from Core.logger import Logger
from Core.memory_manager import MemoryManager
from Core.ollama_provider import OllamaProvider
from Core.cloud_provider import CloudProvider
from Core.prompt_optimizer import PromptOptimizer
from Core.response_validator import ResponseValidator
from Core.ai_guard import AIGuard
from Core.output_formatter import OutputFormatter
from Core.llm_cache import LLMCache
from Core.conversation_manager import ConversationManager
from Core.tool_registry import ToolRegistry
from Core.Tools.time_tool import TimeTool


class LLMEngine:


    _memory = None
    _local_provider = None
    _cloud_provider = None
    _prompt_optimizer = None
    _response_validator = None
    _cache = None


    def __init__(self):

        self.logger = Logger.get_logger()

        if LLMEngine._memory is None:
            LLMEngine._memory = MemoryManager()

        if LLMEngine._local_provider is None:
            LLMEngine._local_provider = OllamaProvider()

        if LLMEngine._cloud_provider is None:
            LLMEngine._cloud_provider = CloudProvider()

        if LLMEngine._prompt_optimizer is None:
            LLMEngine._prompt_optimizer = PromptOptimizer()

        if LLMEngine._response_validator is None:
            LLMEngine._response_validator = ResponseValidator()

        if LLMEngine._cache is None:
            LLMEngine._cache = LLMCache()


        self.memory = LLMEngine._memory

        self.local_provider = LLMEngine._local_provider

        self.cloud_provider = LLMEngine._cloud_provider

        self.prompt_optimizer = LLMEngine._prompt_optimizer

        self.response_validator = LLMEngine._response_validator

        self.cache = LLMEngine._cache


        self.guard = AIGuard()

        self.formatter = OutputFormatter()

        self.conversation = ConversationManager()


        self.tools = ToolRegistry()

        self.tools.register(
            TimeTool()
        )



    # --------------------------------------------------
    # Provider Selection
    # --------------------------------------------------

    def _provider(self):

        # QA-AI-STUDIO-TASK4: every answer-LLM call goes through the AI
        # platform gateway (Settings -> LLM). Provider/model switching and
        # the cloud-permission / fallback policy live there. The legacy
        # Ollama provider is kept only as a safety net if the platform
        # package cannot be imported.
        try:

            from Core.ai_platform.compat import GatewayLLMProvider

            return GatewayLLMProvider()

        except Exception:

            self.logger.exception(
                "AI platform gateway unavailable; using legacy Ollama provider."
            )

            return self.local_provider



    def _cache_enabled(self):

        try:

            from Core.ai_platform import runtime

            tokens = runtime.get_store().get_section("tokens")

            return bool((tokens.get("exact_cache") or {}).get("enabled", True))

        except Exception:

            return True



    # --------------------------------------------------
    # Prompt Type Detection
    # --------------------------------------------------

    def _detect_prompt_type(self, prompt):

        prompt = prompt.lower()


        if "test case" in prompt or "test id:" in prompt:

            return "test_case"


        if "steps to reproduce" in prompt:

            return "bug"


        if "generate sql" in prompt:

            return "sql"


        if "endpoint:" in prompt:

            return "api"


        if "selenium" in prompt:

            return "automation"


        if (
            "what time" in prompt
            or "current time" in prompt
            or "today date" in prompt
            or "today's date" in prompt
        ):

            return "utility"


        return "answer"



    # --------------------------------------------------
    # Cache Key
    # --------------------------------------------------

    def _cache_key(self, prompt):

        # QA-AI-STUDIO-TASK4: the key used to be the prompt alone, so after
        # switching provider/model (or user) a stale answer from the previous
        # model could be returned. It now also covers the resolved
        # provider/model, the requesting user and the knowledge token.
        scope = ""

        try:

            from Core.ai_platform import runtime

            ctx = runtime.get_context()
            gw = runtime.get_gateway()
            pid, model, source = gw.resolve_primary(
                ctx.get("task") or "answer", ctx.get("selection")
            )
            scope = "|".join([
                str(pid), str(model), str(ctx.get("username", "")),
                str(ctx.get("data_class", "")),
            ])

        except Exception:

            scope = "legacy"

        return hashlib.md5(
            (scope + "\x00" + prompt).encode("utf-8")
        ).hexdigest()



    # --------------------------------------------------
    # Generate
    # --------------------------------------------------

    def generate(
        self,
        prompt,
        system_prompt=None,
        temperature=None,
        max_tokens=None
    ):

        try:

            profile = getattr(
                settings,
                "LLM_PROFILE",
                "balanced"
            )


            config = settings.LLM_PROFILES.get(
                profile,
                settings.LLM_PROFILES["balanced"]
            )


            temperature = (
                temperature
                if temperature is not None
                else config["temperature"]
            )


            max_tokens = (
                max_tokens
                if max_tokens is not None
                else config["max_tokens"]
            )


            prompt_type = self._detect_prompt_type(
                prompt
            )


            provider = self._provider()


            if provider is None:

                return {

                    "success": False,

                    "error": "AI provider unavailable."

                }



            if prompt_type == "utility":

                tool = self.tools.get_tool(prompt)

                if tool:

                    return tool.execute(prompt)



            history = self.conversation.get_history()


            full_prompt = ""

            if history:

                full_prompt += history + "\n\n"


            full_prompt += prompt


            self.conversation.add_user_message(
                prompt
            )


            optimized_prompt = self.prompt_optimizer.optimize(
                full_prompt,
                prompt_type
            )


            cache_key = self._cache_key(
                optimized_prompt
            )


            cached = self.cache.get(cache_key) if self._cache_enabled() else None


            if cached:

                self.logger.info(
                    "LLM cache hit."
                )

                return cached



            start = time.perf_counter()

            # [AI-DIAG] temporary diagnostic — confirms which provider/mode
            # and configured model is actually about to be invoked for
            # this request, per the requested Phase 2/3 boundary logging.
            self.logger.info(
                "[AI-DIAG] LLM provider selected: %s (AI_MODE=%s) model=%s"
                % (
                    type(provider).__name__,
                    settings.AI_MODE,
                    getattr(provider, "model", None),
                )
            )


            result = provider.generate(

                prompt=optimized_prompt,

                system_prompt=system_prompt,

                temperature=temperature,

                max_tokens=max_tokens

            )


            elapsed = time.perf_counter() - start


            self.logger.info(
                f"LLM completed in {elapsed:.2f} seconds."
            )

            # [AI-DIAG] temporary diagnostic — raw provider response shape,
            # before any validator/guard stage has touched it.
            self.logger.info(
                "[AI-DIAG] Provider raw result: type=%s success=%s "
                "response_chars=%s error=%s" % (
                    type(result).__name__,
                    result.get("success") if isinstance(result, dict) else None,
                    len(str(result.get("response") or "")) if isinstance(result, dict) else 0,
                    result.get("error") if isinstance(result, dict) else "non-dict result",
                )
            )


            if not result.get("success"):

                return result



            response = result.get(
                "response",
                ""
            )


            validation = self.response_validator.validate(

                response=response,

                intent=prompt_type,

                context=optimized_prompt

            )


            if not validation.get("success"):

                return validation



            response = validation["response"]



            guard = self.guard.validate(

                response=response,

                context=optimized_prompt,

                intent=prompt_type

            )


            if not guard.get("success"):

                return guard



            response = self.formatter.format(

                response,

                prompt_type

            )


            self.conversation.add_ai_message(
                response
            )


            warnings = (
                validation.get("warnings", [])
                +
                guard.get("warnings", [])
            )


            # QA-AI-STUDIO-TASK4: this used to write the full optimized
            # prompt (including retrieved knowledge text) into ai_memory
            # under the shared session "default". The orchestrator already
            # stores the user's question and the answer under the user's own
            # session, so the duplicate (and the knowledge leak into a shared
            # table) is removed.

            # [AI-DIAG] temporary diagnostic — final generated answer
            # length after validator + guard + formatter, i.e. exactly
            # what LLMEngine.generate() is about to hand back as a
            # success.
            self.logger.info(
                "[AI-DIAG] LLM generate() success: final answer_chars=%s"
                % len(response or "")
            )


            final = {

                "success": True,

                "provider": result.get(
                    "provider"
                ),

                "model": result.get(
                    "model"
                ),

                "response": response,

                "warnings": warnings

            }


            if self._cache_enabled():

                self.cache.save(

                    cache_key,

                    final

                )


            return final



        except Exception as error:


            self.logger.exception(
                "LLM generation failed."
            )


            return {

                "success": False,

                "error": str(error)

            }



    # --------------------------------------------------
    # Stream Generate
    # --------------------------------------------------

    def stream_generate(
        self,
        prompt,
        system_prompt=None,
        temperature=None,
        max_tokens=None
    ):


        provider = self._provider()


        if provider is None:

            yield {

                "success": False,

                "error": "AI provider unavailable."

            }

            return



        prompt_type = self._detect_prompt_type(
            prompt
        )


        optimized_prompt = self.prompt_optimizer.optimize(

            prompt,

            prompt_type

        )


        collected = ""


        for chunk in provider.stream_generate(

            prompt=optimized_prompt,

            system_prompt=system_prompt,

            temperature=temperature,

            max_tokens=max_tokens

        ):

            collected += chunk

            yield chunk



        response = self.formatter.format(

            collected,

            prompt_type

        )


        self.conversation.add_ai_message(
            response
        )


        self.memory.save(

            optimized_prompt,

            response

        )