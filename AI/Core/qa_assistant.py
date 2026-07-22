"""
QA AI Studio
QA Assistant
Version: 4.0
"""

from Core.logger import Logger
from Core.rag_engine import RAGEngine
from Core.llm_engine import LLMEngine
from Core.prompt_builder import PromptBuilder
from Core.citation_engine import CitationEngine
from Core.output_formatter import OutputFormatter


class QAAssistant:

    def __init__(self):

        self.logger = Logger.get_logger()
        self.rag = RAGEngine()
        self.llm = LLMEngine()
        self.prompts = PromptBuilder()
        self.citation = CitationEngine()
        self.formatter = OutputFormatter()

    # --------------------------------------------------

    def ask(
        self,
        question,
        top_k=5,
        domain=None,
        module=None,
        knowledge_name=None,
        version=None
    ):

        try:

            self.logger.info(
                f"QA Assistant: {question}"
            )

            rag = self.rag.get_context(
                question=question,
                top_k=top_k,
                domain=domain,
                module=module,
                knowledge_name=knowledge_name,
                version=version
            )

            if not rag.get("success", False):

                return {

                    "success": False,

                    "error": rag.get(
                        "error",
                        "RAG Engine failed."
                    )

                }

            context = str(rag.get("context", "") or "")

            if not context.strip():

                return {

                    "success": True,

                    "question": question,

                    "answer": "No relevant information found in the knowledge base.",

                    "context": "",

                    "references": [],

                    "provider": None,

                    "model": None,

                    "total_chunks": 0

                }

            prompt = self.prompts.build_answer_prompt(

                question,

                context

            )

            system_prompt = self.prompts.system_prompt()

            result = self.llm.generate(

                prompt=prompt,

                system_prompt=system_prompt

            )

            if not result.get("success", False):

                return result

            response = result.get("response", "")

            if isinstance(response, dict):
                response = response.get("response", "")

            answer = str(response).strip()

            answer = self.citation.append(
                answer,
                rag.get(
                    "references",
                    []
                )
            )

            answer = self.formatter.format(
                answer,
                "answer"
            )

            return {

                "success": True,

                "question": question,

                "answer": answer,

                "context": context,

                "references": rag.get(

                    "references",

                    []

                ),

                "total_chunks": rag.get("total_chunks", 0),
                "confidence": rag.get("confidence", 0),

                "provider": result.get(

                    "provider"

                ),

                "model": result.get(

                    "model"

                )

            }

        except Exception:

            self.logger.exception(

                "QA Assistant failed."

            )

            return {

                "success": False,

                "error": "QA Assistant failed."

            }