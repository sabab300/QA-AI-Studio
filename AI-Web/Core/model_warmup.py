"""
QA AI Studio
Model Warmup

QA-AI-STUDIO-TASK4: warm-up now follows the configured default provider.
It only warms a LOCAL Ollama model (a cloud call at start-up would be an
unrequested external request) and it never raises.
"""

from Core.logger import Logger


class ModelWarmup:

    _done = False

    @classmethod
    def warmup(cls):

        if cls._done:
            return

        logger = Logger.get_logger()

        try:

            from Core.ai_platform import runtime
            from Core.ai_platform.gateway import effective_kind
            from Core.ai_platform.providers import build_adapter

            store = runtime.get_store()
            pid = store.get_section("routing").get("default_provider_id")
            provider = store.get_provider(pid, include_secret=True) if pid else None

            if (
                provider
                and provider["enabled"]
                and provider["adapter"] == "ollama"
                and effective_kind(provider) == "local"
            ):

                model = (
                    store.get_section("routing").get("default_model")
                    or provider["default_model"]
                )

                logger.info("Warming up Ollama model %s...", model)

                build_adapter(provider).generate(
                    model, "Hello", "", {"max_tokens": 5, "temperature": 0}
                )

                logger.info("Model warmup completed.")

            else:

                logger.info("Model warmup skipped (default provider is not a local Ollama).")

        except Exception:

            logger.exception("Model warmup failed (continuing).")

        cls._done = True
