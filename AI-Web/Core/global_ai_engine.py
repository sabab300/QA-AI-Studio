"""
QA AI Studio
Global AI Engine

Version: 1.1
"""

from Core.logger import Logger
from Core.internet_search import InternetSearch
from Core.global_context_builder import GlobalContextBuilder
from Core.prompt_builder import PromptBuilder
from Core.llm_engine import LLMEngine


class GlobalAIEngine:

    def __init__(self):

        self.logger = Logger.get_logger()

        self.internet = InternetSearch()

        self.builder = GlobalContextBuilder()

        self.prompt = PromptBuilder()

        self.llm = LLMEngine()

    # --------------------------------------------------
    # Global System Prompt
    # --------------------------------------------------

    def global_system_prompt(self):

        return """
You are a professional global knowledge assistant.

Rules:

1. Answer using the provided reference information.
2. Do not invent facts.
3. If information is unavailable, clearly mention it.
4. Keep answers accurate and concise.
5. Provide structured explanations.
"""

    # --------------------------------------------------
    # Answer Question
    # --------------------------------------------------

    def answer(

        self,

        question

    ):

        try:

            self.logger.info(

                f"Global AI Query: {question}"

            )

            search_result = self.internet.search(

                question

            )

            # [AI-DIAG] temporary diagnostic — internet search outcome and
            # whether it produced usable context, per the requested Phase 2
            # boundary logging. Safe to remove once the AI Assistant
            # pipeline has been confirmed stable in production.
            self.logger.info(
                "[AI-DIAG] Internet search result: success=%s provider=%s "
                "context_chars=%s references=%s error=%s" % (
                    search_result.get("success"),
                    search_result.get("provider"),
                    len(str(search_result.get("context") or "")),
                    len(search_result.get("references") or []),
                    search_result.get("error"),
                )
            )

            if not search_result.get(

                "success",

                False

            ):

                # QA-AI-STUDIO-AI-ASSISTANT-ROOT-FIX: this used to be
                # returned as-is. It already carries "success": False and
                # a real "error" (e.g. "No search results found." /
                # "Unable to extract web content." from
                # Core/duckduckgo_provider.py), but it has no "answer" and
                # no "confidence" key — which is exactly what let
                # OutputFormatter.format_response() silently default
                # confidence to 85 and answer to "". Filling those in here,
                # honestly, means a real internet-search failure now shows
                # up as a clearly-failed, zero-confidence response instead
                # of looking like a fake, high-confidence empty answer.
                return {

                    "success": False,
                    "answer": "",
                    "confidence": 0,
                    "context": "",
                    "references": search_result.get("references", []),
                    "provider": search_result.get("provider", "internet"),
                    "route": "global",
                    "error": search_result.get(
                        "error",
                        "Internet search failed."
                    ),

                }


            context = self.builder.build(

                search_result.get(

                    "context",

                    ""

                )

            )


            prompt = self.prompt.build_global_answer_prompt(

                question=question,

                context=context

            )

            # [AI-DIAG] temporary diagnostic — context length actually sent
            # to Ollama, and which model/mode will serve it.
            self.logger.info(
                "[AI-DIAG] Global context length sent to LLM: %s chars"
                % len(context or "")
            )


            answer = self.llm.generate(

                prompt=prompt,

                system_prompt=self.global_system_prompt()

            )

            if not isinstance(answer, dict):

                answer = {
                    "success": True,
                    "response": str(answer) if answer is not None else "",
                }

            # QA-AI-STUDIO-AI-ASSISTANT-ROOT-FIX: previously this method
            # never checked whether self.llm.generate() actually
            # succeeded — it unconditionally returned "success": True,
            # "confidence": 90 even when Ollama timed out, errored, or the
            # response_validator/ai_guard pipeline rejected the output
            # (all of which return {"success": False, ...} dicts with no
            # "response"/"answer" field). That is inconsistent with the
            # rest of the codebase's own convention (see
            # Core/answer_generator.py's "if not result.get('success'):
            # return result"), and it is exactly the kind of fake-success
            # response Phase 7/8 asked to eliminate. Checking success here
            # costs nothing for the (fixed) DuckDuckGo-failure symptom
            # above, but is required so an Ollama-side failure on a
            # request where internet search DID succeed is never
            # misreported as a confident, empty answer.
            if not answer.get("success", False):

                self.logger.warning(
                    "[AI-DIAG] Global AI: LLM generation failed after a "
                    "successful internet search — error=%s"
                    % answer.get("error")
                )

                return {

                    "success": False,
                    "answer": "",
                    "confidence": 0,
                    "context": context,
                    "references": search_result.get("references", []),
                    "provider": answer.get("provider")
                        or search_result.get("provider", "internet"),
                    "route": "global",
                    "model": answer.get("model"),
                    "error": answer.get(
                        "error",
                        "AI generation failed."
                    ),

                }

            answer_text = (

                answer.get("response")

                or answer.get("answer")

                or ""

            )

            # [AI-DIAG] temporary diagnostic — what actually came back from
            # the LLM stage before it is wrapped into the final response.
            self.logger.info(
                "[AI-DIAG] Global AI: LLM success=True answer_chars=%s "
                "model=%s provider=%s" % (
                    len(answer_text or ""),
                    answer.get("model"),
                    answer.get("provider"),
                )
            )


            return {

                "success": True,

                "answer": answer_text,

                "context": context,

                "references": search_result.get(

                    "references",

                    []

                ),

                "provider": search_result.get(

                    "provider",

                    "internet"

                ),

                "route": "global",

                "model": answer.get(

                    "model"

                ),

                "confidence": 90

            }


        except Exception as error:

            self.logger.exception(

                f"Global AI Engine failed: {error}"

            )

            return {

                "success": False,

                "error": str(error),

                "answer": "",

                "context": "",

                "references": [],

                "provider": "internet",

                "route": "global",

                "confidence": 0

            }