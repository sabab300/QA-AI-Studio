"""
QA AI Studio
LLM Engine

Central AI execution layer.

Supports:
- Local AI (Ollama)
- Cloud AI (Future)
- Hybrid mode

Version: 2.0
"""

from Core.logger import Logger
from Core.llm import get_ai_provider


class LLMEngine:


    def __init__(self):

        self.logger = Logger.get_logger()

        self.provider = get_ai_provider()


        if self.provider:

            self.logger.info(
                "LLM Provider initialized"
            )

        else:

            self.logger.warning(
                "LLM disabled - no provider selected"
            )


    # ==================================================
    # Generate AI Response
    # ==================================================

    def generate(
        self,
        prompt: str
    ):

        try:

            self.logger.info(
                "LLM generation started"
            )


            if not self.provider:

                return {

                    "success": False,

                    "error": "AI provider is disabled"

                }


            response = self.provider.generate(
                prompt
            )


            return {

                "success": True,

                "response": response

            }


        except Exception as error:


            self.logger.error(
                f"LLM Engine failed: {error}"
            )


            return {

                "success": False,

                "error": str(error)

            }