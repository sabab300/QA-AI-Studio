"""Anthropic Messages API adapter (https://docs.anthropic.com)."""

import time

from Core.ai_platform.providers.base import (
    GenerationResult, ProviderAdapter, ProviderError, http_error,
    MAX_RESPONSE_CHARS, estimate_tokens,
)

API_VERSION = "2023-06-01"


class AnthropicAdapter(ProviderAdapter):

    adapter_id = "anthropic"

    def _headers(self):
        return {
            "x-api-key": self.api_key,
            "anthropic-version": API_VERSION,
            "content-type": "application/json",
        }

    def list_models(self):

        r = self._get(f"{self.base_url}/v1/models?limit=100", self._headers())
        if r.status_code != 200:
            raise http_error(self.name, r, self.secrets)

        return [
            {"model_id": m["id"], "display_name": m.get("display_name") or m["id"],
             "context_limit": 0}
            for m in (r.json().get("data") or []) if m.get("id")
        ]

    def generate(self, model, prompt, system="", params=None, cancel=None):

        params = params or {}
        payload = {
            "model": model,
            # max_tokens is required by the Messages API
            "max_tokens": int(params.get("max_tokens") or 1024),
            "messages": [{"role": "user", "content": prompt}],
        }
        if system:
            payload["system"] = system
        if params.get("temperature") is not None:
            payload["temperature"] = float(params["temperature"])
        if params.get("top_p") is not None and params.get("temperature") is None:
            payload["top_p"] = float(params["top_p"])

        started = time.time()
        r = self._post_cancellable(f"{self.base_url}/v1/messages",
                                   self._headers(), payload, cancel)

        if r.status_code != 200:
            raise http_error(self.name, r, self.secrets)

        try:
            body = r.json()
            text = "".join(b.get("text", "") for b in (body.get("content") or [])
                           if b.get("type") == "text")
        except Exception:
            raise ProviderError(f"{self.name}: unreadable response from provider.")

        text = text[:MAX_RESPONSE_CHARS]
        usage = body.get("usage") or {}
        ptok = int(usage.get("input_tokens") or 0)
        ctok = int(usage.get("output_tokens") or 0)
        estimated = not (ptok or ctok)
        if estimated:
            ptok = estimate_tokens(prompt) + estimate_tokens(system)
            ctok = estimate_tokens(text)

        return GenerationResult(
            text=text, provider=self.name, model=model,
            prompt_tokens=ptok, completion_tokens=ctok,
            latency_ms=int((time.time() - started) * 1000),
            finish_reason=body.get("stop_reason") or "",
            cloud=self.is_cloud, usage_estimated=estimated,
        )
