"""OpenAI Chat Completions compatible adapter.

Used for OpenAI, OpenRouter, LM Studio (its OpenAI-compatible server) and any
other local server exposing ``/models`` and ``/chat/completions``.
"""

import time

from Core.ai_platform.providers.base import (
    GenerationResult, ProviderAdapter, ProviderError, http_error,
    MAX_RESPONSE_CHARS, estimate_tokens,
)


class OpenAICompatAdapter(ProviderAdapter):

    adapter_id = "openai_compatible"

    def _headers(self):
        h = {"Content-Type": "application/json"}
        if self.api_key:
            h["Authorization"] = f"Bearer {self.api_key}"
        if self.provider.get("adapter") == "openrouter":
            h["X-Title"] = "QA AI Studio"
        return h

    def list_models(self):

        r = self._get(f"{self.base_url}/models", self._headers())
        if r.status_code != 200:
            raise http_error(self.name, r, self.secrets)

        body = r.json()
        items = body.get("data") if isinstance(body, dict) else body
        out = []
        for m in items or []:
            mid = m.get("id")
            if not mid:
                continue
            ctx = m.get("context_length") or m.get("max_context_length") or 0
            out.append({"model_id": mid, "display_name": m.get("name") or mid,
                        "context_limit": int(ctx or 0)})
        return out

    def generate(self, model, prompt, system="", params=None, cancel=None):

        params = params or {}
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        payload = {"model": model, "messages": messages, "stream": False}
        if params.get("temperature") is not None:
            payload["temperature"] = float(params["temperature"])
        if params.get("top_p") is not None:
            payload["top_p"] = float(params["top_p"])
        if params.get("max_tokens"):
            # Newer OpenAI models reject max_tokens; local servers expect it.
            key = ("max_completion_tokens"
                   if self.provider.get("adapter") == "openai" else "max_tokens")
            payload[key] = int(params["max_tokens"])

        started = time.time()
        r = self._post_cancellable(f"{self.base_url}/chat/completions",
                                   self._headers(), payload, cancel)

        if r.status_code != 200:
            raise http_error(self.name, r, self.secrets)

        try:
            body = r.json()
            choice = (body.get("choices") or [{}])[0]
            text = ((choice.get("message") or {}).get("content") or "")
        except Exception:
            raise ProviderError(f"{self.name}: unreadable response from provider.")

        if isinstance(text, list):  # content parts
            text = "".join(p.get("text", "") for p in text if isinstance(p, dict))
        text = text[:MAX_RESPONSE_CHARS]

        usage = body.get("usage") or {}
        ptok = int(usage.get("prompt_tokens") or 0)
        ctok = int(usage.get("completion_tokens") or 0)
        estimated = not (ptok or ctok)
        if estimated:
            ptok = estimate_tokens(prompt) + estimate_tokens(system)
            ctok = estimate_tokens(text)

        return GenerationResult(
            text=text, provider=self.name, model=model,
            prompt_tokens=ptok, completion_tokens=ctok,
            latency_ms=int((time.time() - started) * 1000),
            finish_reason=choice.get("finish_reason") or "",
            cloud=self.is_cloud, usage_estimated=estimated,
        )
