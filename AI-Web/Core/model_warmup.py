"""
QA AI Studio
Model Warmup
"""

from Core.logger import Logger
from Core.ollama_provider import OllamaProvider


class ModelWarmup:

    _done = False

    @classmethod
    def warmup(cls):

        if cls._done:
            return

        logger = Logger.get_logger()

        logger.info("Warming up Ollama model...")

        provider = OllamaProvider()

        provider.generate(
            prompt="Hello",
            max_tokens=5
        )

        cls._done = True

        logger.info("Model warmup completed.")