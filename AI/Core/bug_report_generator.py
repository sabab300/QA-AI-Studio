"""
QA AI Studio
Production Bug Report Generator

Version: 5.0
"""

from Core.logger import Logger
from Core.rag_engine import RAGEngine
from Core.llm_engine import LLMEngine
from Core.prompt_builder import PromptBuilder


class BugReportGenerator:


    def __init__(self):

        self.logger = Logger.get_logger()

        self.rag = RAGEngine()

        self.llm = LLMEngine()

        self.prompts = PromptBuilder()


    # --------------------------------------------------
    # Generate Bug Report
    # --------------------------------------------------

    def generate(
        self,
        issue,
        top_k=5,
        domain=None,
        module=None,
        knowledge_name=None,
        version=None
    ):

        try:

            self.logger.info(
                "Generating bug report..."
            )


            rag = self.rag.get_context(

                question=issue,

                top_k=top_k,

                domain=domain,

                module=module,

                knowledge_name=knowledge_name,

                version=version

            )


            if not isinstance(rag, dict):

                return {

                    "success": False,

                    "error": "Invalid RAG response."

                }


            if not rag.get("success"):

                return rag



            context = rag.get(
                "context",
                ""
            )


            prompt = self.prompts.build_bug_prompt(

                issue=issue,

                context=context

            )


            result = self.llm.generate(

                prompt=prompt,

                system_prompt=self.prompts.system_prompt(),

                temperature=0.1,

                max_tokens=2000

            )


            if not result.get("success"):

                return result



            bug_report = result.get(

                "response",

                ""

            ).strip()



            if not bug_report:

                return {

                    "success": False,

                    "error": "Empty bug report generated."

                }



            return {

                "success": True,

                "issue": issue,

                "bug_report": bug_report,

                "context": context,

                "references": rag.get(

                    "references",

                    []

                ),

                "provider": result.get(

                    "provider"

                ),

                "model": result.get(

                    "model"

                ),

                "warnings": result.get(

                    "warnings",

                    []

                )

            }



        except Exception as error:


            self.logger.exception(

                "Bug Report Generator failed."

            )


            return {

                "success": False,

                "error": str(error)

            }