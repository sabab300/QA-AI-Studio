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

        mode = settings.AI_MODE.lower()


        if mode == "local":

            return self.local_provider


        if mode == "cloud":

            return self.cloud_provider


        if mode == "hybrid":

            if self.local_provider.is_available():

                return self.local_provider

            return self.cloud_provider


        return None



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

        return hashlib.md5(
            prompt.encode("utf-8")
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


            cached = self.cache.get(cache_key)


            if cached:

                self.logger.info(
                    "LLM cache hit."
                )

                return cached



            start = time.perf_counter()


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


            self.memory.save(

                optimized_prompt,

                response

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