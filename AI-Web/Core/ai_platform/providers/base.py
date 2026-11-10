"""Common adapter contract."""

import threading
import time
from dataclasses import dataclass, field

from Core.ai_platform.sanitize import scrub

MAX_RESPONSE_CHARS = 200_000


class ProviderError(Exception):
    """Sanitised provider failure.

    ``transient`` marks failures worth one retry / a permitted fallback
    (connection problems, timeouts, HTTP 429 / 5xx). ``cancelled`` marks a
    user cancellation (never retried, never falls back).
    """

    def __init__(self, message, transient=False, status=None, cancelled=False):
        super().__init__(message)
        self.transient = transient
        self.status = status
        self.cancelled = cancelled


@dataclass
class GenerationResult:
    text: str = ""
    provider: str = ""
    model: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    latency_ms: int = 0
    finish_reason: str = ""
    cloud: bool = False
    attempts: list = field(default_factory=list)
    fallback_used: bool = False
    cached: bool = False
    usage_estimated: bool = False
    # provider-reported stage timings in ms (Ollama: load / prompt_eval / generation / total)
    timings: dict = field(default_factory=dict)


def estimate_tokens(text):
    """Rough token estimate (documented as an estimate wherever shown)."""
    return max(1, int(len(text or "") / 3.4)) if text else 0


def http_error(provider_name, response, secrets=()):
    """Build a sanitised ProviderError from a non-2xx requests response."""

    status = response.status_code
    detail = ""
    try:
        body = response.json()
        err = body.get("error") if isinstance(body, dict) else None
        if isinstance(err, dict):
            detail = err.get("message") or ""
        elif isinstance(err, str):
            detail = err
        elif isinstance(body, dict):
            detail = body.get("message") or ""
    except Exception:
        detail = (response.text or "")[:200]

    hints = {
        400: "The provider rejected the request (check model name and parameters).",
        401: "Authentication failed. Check the API key.",
        403: "Access denied. The key may lack permission for this model.",
        404: "Endpoint or model not found. Check the base URL and model name.",
        408: "The provider timed out.",
        429: "Rate limit or quota reached.",
    }
    hint = hints.get(status) or (
        "The provider reported a server error." if status >= 500 else "Request failed.")

    message = scrub(f"{provider_name}: HTTP {status}. {hint} {detail}".strip(),
                    secrets, limit=320)

    return ProviderError(message, transient=(status == 429 or status >= 500),
                         status=status)


class ProviderAdapter:
    """Base class. Subclasses implement ``list_models`` and ``generate``."""

    adapter_id = ""

    def __init__(self, provider):
        self.provider = provider
        self.name = provider["name"]
        self.base_url = (provider["base_url"] or "").rstrip("/")
        self.api_key = provider.get("_api_key") or ""
        self.timeout = int(provider.get("timeout_s") or 300)
        self.is_cloud = provider.get("kind") == "cloud"

    # secrets to scrub from any message derived from this adapter
    @property
    def secrets(self):
        return (self.api_key,) if self.api_key else ()

    def list_models(self):  # -> [{"model_id", "display_name", "context_limit"?}]
        raise NotImplementedError

    def generate(self, model, prompt, system="", params=None, cancel=None):
        raise NotImplementedError

    # ---------------------------------------------------------------- utils

    def _connection_error(self, exc):
        import requests
        if isinstance(exc, requests.exceptions.Timeout):
            msg = f"{self.name}: request timed out after {self.timeout}s."
        elif isinstance(exc, requests.exceptions.ConnectionError):
            msg = (f"{self.name}: cannot connect to {self.base_url}. "
                   "Check that the server is running and the URL is correct.")
        else:
            msg = f"{self.name}: request failed ({type(exc).__name__})."
        return ProviderError(scrub(msg, self.secrets), transient=True)

    def _post_cancellable(self, url, headers, payload, cancel):
        """POST in a worker thread so a user cancellation returns promptly.

        Non-streaming HTTP cannot be aborted server-side; on cancel the call
        is abandoned (the provider may finish generating) and its result is
        discarded.
        """

        import requests

        box = {}

        def work():
            try:
                box["r"] = requests.post(url, headers=headers, json=payload,
                                         timeout=(10, self.timeout))
            except Exception as exc:  # noqa: BLE001
                box["e"] = exc

        t = threading.Thread(target=work, daemon=True)
        t.start()
        deadline = time.time() + self.timeout + 15

        while t.is_alive():
            if cancel is not None and cancel.is_set():
                raise ProviderError("Cancelled by user.", cancelled=True)
            if time.time() > deadline:
                raise ProviderError(f"{self.name}: request timed out.", transient=True)
            t.join(0.2)

        if "e" in box:
            raise self._connection_error(box["e"])

        return box["r"]

    def _get(self, url, headers=None):
        import requests
        try:
            return requests.get(url, headers=headers or {}, timeout=(5, min(self.timeout, 30)))
        except Exception as exc:  # noqa: BLE001
            raise self._connection_error(exc)
