"""Drop-in replacement for OllamaProvider that routes through the gateway.

Existing callers (LLMEngine, Impact Analysis, SQL generator, ...) keep calling
``generate(prompt, system_prompt, temperature, max_tokens)`` and receive the
same dict contract, so switching provider/model needs no call-site rewrites.
"""

import time

from Core.ai_platform import runtime
from Core.ai_platform.gateway import GatewayError
from Core.ai_provider import AIProvider


class GatewayLLMProvider(AIProvider):

    def __init__(self, task="answer", data_class="private"):
        self.task = task
        self.data_class = data_class

    @property
    def provider_name(self):
        return "gateway"

    @property
    def model(self):
        try:
            return runtime.get_store().get_section("routing").get("default_model", "")
        except Exception:
            return ""

    def is_available(self):
        try:
            return any(p["enabled"] for p in runtime.get_store().list_providers())
        except Exception:
            return False

    def generate(self, prompt, system_prompt=None, temperature=None,
                 max_tokens=None, stream=False):

        ctx = runtime.get_context()
        started = time.perf_counter()

        params = {}
        if temperature is not None:
            params["temperature"] = temperature
        if max_tokens:
            params["max_tokens"] = max_tokens

        try:
            r = runtime.get_gateway().generate(
                task=ctx.get("task") or self.task,
                prompt=prompt, system=system_prompt or "",
                data_class=ctx.get("data_class") or self.data_class,
                selection=ctx.get("selection"), params=params,
                username=ctx.get("username", ""), mode=ctx.get("mode", ""),
                request_id=ctx.get("request_id"))
        except GatewayError as exc:
            return {"success": False, "error": str(exc), "code": exc.code,
                    "attempts": exc.attempts, "provider": "gateway",
                    "model": self.model}

        text = (r.text or "").strip()
        if not text:
            return {"success": False, "provider": r.provider, "model": r.model,
                    "error": "Model returned empty response.",
                    "attempts": r.attempts}

        return {
            "success": True, "provider": r.provider, "model": r.model,
            "response": text,
            "prompt_eval_count": r.prompt_tokens, "eval_count": r.completion_tokens,
            "prompt_tokens": r.prompt_tokens, "completion_tokens": r.completion_tokens,
            "latency_ms": r.latency_ms, "usage_estimated": r.usage_estimated,
            "cloud": r.cloud, "fallback_used": r.fallback_used,
            "attempts": r.attempts, "finish_reason": r.finish_reason,
            "execution_time": round(time.perf_counter() - started, 2),
        }

    def stream_generate(self, prompt, **kwargs):
        # Streaming to the UI is not used by the web app; return one chunk.
        r = self.generate(prompt, kwargs.get("system_prompt"),
                          kwargs.get("temperature"), kwargs.get("max_tokens"))
        if r.get("success"):
            yield r["response"]
