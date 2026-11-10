"""Provider adapters (official documented APIs only)."""

from Core.ai_platform.providers.base import (  # noqa: F401
    GenerationResult, ProviderError, ProviderAdapter,
)


def build_adapter(provider):
    """Instantiate the adapter for a provider dict (with ``_api_key``)."""

    adapter = provider["adapter"]

    if adapter == "ollama":
        from Core.ai_platform.providers.ollama import OllamaAdapter
        return OllamaAdapter(provider)

    if adapter in ("openai", "lmstudio", "openrouter", "openai_compatible"):
        from Core.ai_platform.providers.openai_compat import OpenAICompatAdapter
        return OpenAICompatAdapter(provider)

    if adapter == "anthropic":
        from Core.ai_platform.providers.anthropic import AnthropicAdapter
        return AnthropicAdapter(provider)

    if adapter == "gemini":
        from Core.ai_platform.providers.gemini import GeminiAdapter
        return GeminiAdapter(provider)

    raise ProviderError(f"Unsupported provider adapter '{adapter}'.")
