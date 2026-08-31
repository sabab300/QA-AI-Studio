"""
QA AI Studio
Answer Generator
Version: 3.0
"""

from Core.logger import Logger
from Core.qa_assistant import QAAssistant


class AnswerGenerator:

    def __init__(self):

        self.logger = Logger.get_logger()

        self.qa = QAAssistant()

    # --------------------------------------------------

    def generate(

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
                f"Generating answer: {question}"
            )

            result = self.qa.ask(

                question=question,

                top_k=top_k,

                domain=domain,

                module=module,

                knowledge_name=knowledge_name,

                version=version

            )

            if not isinstance(result, dict):

                return {

                    "success": False,

                    "error": "Invalid QA response."

                }

            if not result.get("success"):

                return result

            return {

                "success": True,

                "question": question,

                "answer": result.get(
                    "answer",
                    ""
                ),

                "context": result.get(
                    "context",
                    ""
                ),

                "references": result.get(
                    "references",
                    []
                ),

                "citations": result.get(
                    "citations",
                    []
                ),

                "confidence": result.get(
                    "confidence",
                    0
                ),

                "provider": result.get(
                    "provider"
                ),

                "model": result.get(
                    "model"
                ),

                "execution_time": result.get(
                    "execution_time"
                )

            }

        except Exception:

            self.logger.exception(
                "Answer Generator failed."
            )

            return {

                "success": False,

                "error": "Answer generation failed."

            }