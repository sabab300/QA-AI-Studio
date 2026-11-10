"""Google Gemini API adapter (Generative Language API, generateContent)."""

import time

from Core.ai_platform.providers.base import (
    GenerationResult, ProviderAdapter, ProviderError, http_error,
    MAX_RESPONSE_CHARS, estimate_tokens,
)


class GeminiAdapter(ProviderAdapter):

    adapter_id = "gemini"

    def _headers(self):
        # Key in a header, never in the URL, so it cannot leak into logs.
        return {"x-goog-api-key": self.api_key, "Content-Type": "application/json"}

    def list_models(self):

        r = self._get(f"{self.base_url}/v1beta/models?pageSize=100", self._headers())
        if r.status_code != 200:
            raise http_error(self.name, r, self.secrets)

        out = []
        for m in (r.json().get("models") or []):
            if "generateContent" not in (m.get("supportedGenerationMethods") or []):
                continue
            mid = (m.get("name") or "").replace("models/", "", 1)
            if mid:
                out.append({
                    "model_id": mid,
                    "display_name": m.get("displayName") or mid,
                    "context_limit": int(m.get("inputTokenLimit") or 0),
                    "max_output": int(m.get("outputTokenLimit") or 0),
                })
        return out

    def generate(self, model, prompt, system="", params=None, cancel=None):

        params = params or {}
        gen = {}
        if params.get("temperature") is not None:
            gen["temperature"] = float(params["temperature"])
        if params.get("top_p") is not None:
            gen["topP"] = float(params["top_p"])
        if params.get("max_tokens"):
            gen["maxOutputTokens"] = int(params["max_tokens"])

        payload = {"contents": [{"role": "user", "parts": [{"text": prompt}]}]}
        if gen:
            payload["generationConfig"] = gen
        if system:
            payload["systemInstruction"] = {"parts": [{"text": system}]}

        started = time.time()
        r = self._post_cancellable(
            f"{self.base_url}/v1beta/models/{model}:generateContent",
            self._headers(), payload, cancel)

        if r.status_code != 200:
            raise http_error(self.name, r, self.secrets)

        try:
            body = r.json()
            cand = (body.get("candidates") or [{}])[0]
            parts = (cand.get("content") or {}).get("parts") or []
            text = "".join(p.get("text", "") for p in parts)
        except Exception:
            raise ProviderError(f"{self.name}: unreadable response from provider.")

        if not text and (body.get("promptFeedback") or {}).get("blockReason"):
            raise ProviderError(f"{self.name}: the prompt was blocked by the provider's safety filter.")

        text = text[:MAX_RESPONSE_CHARS]
        usage = body.get("usageMetadata") or {}
        ptok = int(usage.get("promptTokenCount") or 0)
        ctok = int(usage.get("candidatesTokenCount") or 0)
        estimated = not (ptok or ctok)
        if estimated:
            ptok = estimate_tokens(prompt) + estimate_tokens(system)
            ctok = estimate_tokens(text)

        return GenerationResult(
            text=text, provider=self.name, model=model,
            prompt_tokens=ptok, completion_tokens=ctok,
            latency_ms=int((time.time() - started) * 1000),
            finish_reason=cand.get("finishReason") or "",
            cloud=self.is_cloud, usage_estimated=estimated,
        )
