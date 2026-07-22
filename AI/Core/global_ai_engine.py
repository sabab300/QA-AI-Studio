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

            if not search_result.get(

                "success",

                False

            ):

                return search_result


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


            answer = self.llm.generate(

                prompt=prompt,

                system_prompt=self.global_system_prompt()

            )


            if isinstance(

                answer,

                dict

            ):

                answer_text = (

                    answer.get("response")

                    or answer.get("answer")

                    or ""

                )

            else:

                answer_text = str(answer)

                answer = {}


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