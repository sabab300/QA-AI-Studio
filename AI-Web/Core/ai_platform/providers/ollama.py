"""Ollama adapter - /api/tags (discovery) and /api/chat (streamed)."""

import json
import time

import requests

from Core.ai_platform.providers.base import (
    GenerationResult, ProviderAdapter, ProviderError, http_error,
    MAX_RESPONSE_CHARS, estimate_tokens,
)


class OllamaAdapter(ProviderAdapter):

    adapter_id = "ollama"

    def list_models(self):

        r = self._get(f"{self.base_url}/api/tags")
        if r.status_code != 200:
            raise http_error(self.name, r, self.secrets)

        out = []
        for m in (r.json().get("models") or []):
            mid = m.get("name") or m.get("model")
            if mid:
                out.append({
                    "model_id": mid,
                    "display_name": mid,
                    "context_limit": 0,
                    "size_bytes": m.get("size"),
                })
        return out

    def generate(self, model, prompt, system="", params=None, cancel=None):

        params = params or {}
        options = {}
        if params.get("temperature") is not None:
            options["temperature"] = float(params["temperature"])
        if params.get("max_tokens"):
            options["num_predict"] = int(params["max_tokens"])
        if params.get("top_p") is not None:
            options["top_p"] = float(params["top_p"])
        options["num_ctx"] = int(params.get("num_ctx") or 4096)

        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        payload = {"model": model, "messages": messages, "stream": True,
                   "options": options,
                   # keep the model in memory between questions: a cold load of a
                   # 7B model costs tens of seconds on CPU (Ollama's default is 5 min)
                   "keep_alive": params.get("keep_alive") or "30m"}
        # Opt-in for structured callers; ordinary Assistant/replay prompts keep
        # their existing free-text contract.
        if params.get("response_format") == "json":
            payload["format"] = params.get("response_schema") or "json"

        started = time.time()
        text_parts, size = [], 0
        final = {}

        try:
            # No automatic retry: a re-sent generation can double the wait.
            with requests.post(f"{self.base_url}/api/chat", json=payload,
                               stream=True, timeout=(10, self.timeout)) as r:

                if r.status_code != 200:
                    raise http_error(self.name, r, self.secrets)

                for line in r.iter_lines():

                    if cancel is not None and cancel.is_set():
                        r.close()   # stops Ollama generating
                        raise ProviderError("Cancelled by user.", cancelled=True)

                    if time.time() - started > self.timeout:
                        # not transient: a retry would double an already long wait
                        raise ProviderError(
                            f"{self.name}: generation exceeded {self.timeout}s.",
                            transient=False)

                    if not line:
                        continue

                    try:
                        obj = json.loads(line)
                    except ValueError:
                        continue

                    if obj.get("error"):
                        raise ProviderError(
                            f"{self.name}: {str(obj['error'])[:200]}")

                    piece = (obj.get("message") or {}).get("content") or ""
                    if piece:
                        text_parts.append(piece)
                        size += len(piece)
                        if size > MAX_RESPONSE_CHARS:
                            break

                    if obj.get("done"):
                        final = obj
                        break

        except ProviderError:
            raise
        except requests.exceptions.RequestException as exc:
            raise self._connection_error(exc)

        text = "".join(text_parts)
        ptok = int(final.get("prompt_eval_count") or 0)
        ctok = int(final.get("eval_count") or 0)
        estimated = not (ptok or ctok)
        if estimated:
            ptok = estimate_tokens(prompt) + estimate_tokens(system)
            ctok = estimate_tokens(text)

        latency_ms = int((time.time() - started) * 1000)

        def ms(key):
            v = final.get(key)
            return int(v / 1e6) if isinstance(v, (int, float)) else None

        total_ms = ms("total_duration")
        timings = {"model_load": ms("load_duration"),
                   "prompt_eval": ms("prompt_eval_duration"),
                   "generation": ms("eval_duration"),
                   "provider_total": total_ms,
                   # time outside the model's own work: HTTP, scheduling, waiting for
                   # another request that held the model
                   "queue_and_overhead": (max(latency_ms - total_ms, 0)
                                          if total_ms is not None else None)}
        eval_s = (timings["generation"] or 0) / 1000.0
        if eval_s > 0 and ctok:
            timings["tokens_per_second"] = round(ctok / eval_s, 2)

        return GenerationResult(
            text=text, provider=self.name, model=model,
            prompt_tokens=ptok, completion_tokens=ctok,
            latency_ms=latency_ms,
            finish_reason=final.get("done_reason") or "",
            cloud=False, usage_estimated=estimated, timings=timings,
        )
