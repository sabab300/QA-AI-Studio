"""
QA AI Studio
Automation Generator
Version: 3.1
"""

from Core.rag_engine import RAGEngine
from Core.prompt_builder import PromptBuilder
from Core.llm_engine import LLMEngine
from Core.logger import Logger


class AutomationGenerator:


    def __init__(self):

        self.logger = Logger.get_logger()

        self.rag = RAGEngine()

        self.prompt = PromptBuilder()

        self.llm = LLMEngine()



    # --------------------------------------------------

    def generate(
        self,
        requirement,
        domain=None,
        module=None,
        knowledge_name=None,
        version=None
    ):

        try:

            self.logger.info(
                f"Generating automation for: {requirement}"
            )

            rag = self.rag.get_context(
                question=requirement,
                top_k=5,
                domain=domain,
                module=module,
                knowledge_name=knowledge_name,
                version=version
            )

            if not rag.get("success"):

                return rag


            prompt = self.prompt.build_automation_prompt(

                request=requirement,

                context=rag.get(
                    "context",
                    ""
                )

            )


            result = self.llm.generate(

                prompt=prompt,

                system_prompt=self.prompt.system_prompt()

            )


            if not result.get("success"):

                return result


            return {

                "success": True,

                "requirement": requirement,

                "automation_code": result.get(
                    "response",
                    ""
                ),

                "references": rag.get(
                    "references",
                    []
                ),

                "provider": result.get(
                    "provider"
                ),

                "model": result.get(
                    "model"
                )

            }


        except Exception as error:

            self.logger.exception(
                "Automation Generator failed."
            )

            return {

                "success": False,

                "error": str(error)

            }