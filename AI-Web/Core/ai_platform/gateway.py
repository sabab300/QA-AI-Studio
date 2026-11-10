"""
LLMGateway - one controlled path from QA AI Studio to any answer LLM.

Guarantees
----------
* The knowledge repository, embeddings and vector store are never touched
  here. Switching provider / model only changes which LLM writes the answer.
* No cloud transmission unless (a) the provider is cloud, (b) the master
  switch ``security.cloud_data_sharing_enabled`` is on, and (c) the provider
  itself is permitted for the kind of data in the prompt.
* No silent provider switch: a fallback only happens when fallback is enabled,
  the fallback provider is permitted, and every attempt is reported back in
  ``GenerationResult.attempts`` / ``fallback_used``.
* Secrets are decrypted only inside the adapter call and scrubbed from errors.
"""

import ipaddress
import threading
import time
from urllib.parse import urlparse

from Core.ai_platform.providers import ProviderError, build_adapter
from Core.ai_platform.providers.base import GenerationResult
from Core.ai_platform.sanitize import redact_for_cloud, scrub


class GatewayError(Exception):
    def __init__(self, message, attempts=None, code="provider_failed"):
        super().__init__(message)
        self.attempts = attempts or []
        self.code = code


class CancelRegistry:
    """request_id -> cancel event + progress stage (for Stop / progress UI)."""

    def __init__(self):
        self._lock = threading.Lock()
        self._items = {}

    def register(self, request_id):
        return self.ensure(request_id)[0]

    def ensure(self, request_id, owner=None):
        """Return (event, created). Idempotent so the assistant service and the
        gateway can share one request_id / cancel event."""
        with self._lock:
            self._prune()
            item = self._items.get(request_id)
            if item:
                return item["event"], False
            item = {"event": threading.Event(), "stage": "queued",
                    "started": time.time(), "detail": "", "owner": owner}
            self._items[request_id] = item
            return item["event"], True

    def stage(self, request_id, stage, detail=""):
        with self._lock:
            item = self._items.get(request_id)
            if item:
                item["stage"] = stage
                item["detail"] = detail

    def cancel(self, request_id, owner=None):
        with self._lock:
            item = self._items.get(request_id)
            if not item:
                return False
            if item.get("owner") is not None and owner != item["owner"]:
                return False
            item["event"].set()
            item["stage"] = "cancelling"
            return True

    def progress(self, request_id, owner=None):
        with self._lock:
            item = self._items.get(request_id)
            if not item:
                return None
            if item.get("owner") is not None and owner != item["owner"]:
                return None
            return {"stage": item["stage"], "detail": item["detail"],
                    "elapsed_ms": int((time.time() - item["started"]) * 1000),
                    "cancelled": item["event"].is_set()}

    def finish(self, request_id):
        with self._lock:
            self._items.pop(request_id, None)

    def _prune(self):
        cutoff = time.time() - 3600
        for key in [k for k, v in self._items.items() if v["started"] < cutoff]:
            self._items.pop(key, None)


CANCELS = CancelRegistry()


def is_local_host(base_url):
    """True when the endpoint is on this machine or a private network."""

    host = (urlparse(base_url or "").hostname or "").lower()

    if not host:
        return False
    if host in ("localhost", "host.docker.internal") or host.endswith(
            (".local", ".lan", ".internal", ".localhost")):
        return True
    try:
        ip = ipaddress.ip_address(host)
        return ip.is_loopback or ip.is_private or ip.is_link_local
    except ValueError:
        return "." not in host  # single-label intranet host name


def effective_kind(provider):
    """Treat a 'local' provider pointing at a public host as cloud."""

    if provider.get("kind") == "cloud":
        return "cloud"
    return "local" if is_local_host(provider.get("base_url")) else "cloud"


def check_permission(provider, data_class, security):
    """Return (allowed, reason). Local-network providers are always allowed."""

    if effective_kind(provider) == "local":
        return True, ""

    if data_class == "none":
        return True, ""   # fixed diagnostic prompt, no user/knowledge data

    if not security.get("cloud_data_sharing_enabled"):
        return False, ("Cloud data sharing is switched off "
                       "(Settings → LLM → Security).")

    perms = provider.get("permissions") or {}
    key = "allow_private_data" if data_class == "private" else "allow_public_data"

    if not perms.get(key):
        what = "private knowledge" if data_class == "private" else "public queries"
        return False, (f"'{provider['name']}' is not permitted to receive {what} "
                       "(Settings → LLM → Providers).")

    return True, ""


class LLMGateway:

    def __init__(self, store):
        self.store = store

    # ---------------------------------------------------------- resolution

    def _provider_with_key(self, provider_id):
        return self.store.get_provider(provider_id, include_secret=True)

    def resolve_primary(self, task, selection=None):
        """Return (provider_id, model, source) for a task."""

        routing = self.store.get_section("routing")

        if selection and selection.get("provider_id") and selection.get("model"):
            if routing.get("allow_manual_model_selection", True):
                return int(selection["provider_id"]), selection["model"], "manual"

        route = (routing.get("task_routes") or {}).get(task) or {}
        if route.get("provider_id") and route.get("model"):
            return int(route["provider_id"]), route["model"], "task_route"

        pid = routing.get("default_provider_id")
        model = routing.get("default_model") or ""
        if pid:
            if not model:
                p = self.store.get_provider(pid)
                model = (p or {}).get("default_model", "")
            return int(pid), model, "default"

        providers = [p for p in self.store.list_providers() if p["enabled"]]
        if providers:
            return providers[0]["id"], providers[0]["default_model"], "first_enabled"

        raise GatewayError("No LLM provider is configured. Add one in Settings → LLM.",
                           code="not_configured")

    def candidates(self, task, data_class, selection=None):
        """Ordered attempts: primary, then permitted fallbacks (if enabled).

        Returns (candidates, notes). A candidate is (provider, model, role).
        The primary is returned even if not permitted so the caller can
        raise an accurate permission error rather than silently using another.
        """

        routing = self.store.get_section("routing")
        pid, model, source = self.resolve_primary(task, selection)

        primary = self._provider_with_key(pid)
        if not primary:
            raise GatewayError(
                "The selected provider no longer exists. Choose another in Settings → LLM.",
                code="not_configured")

        out = [(primary, model or primary["default_model"], "primary:" + source)]
        notes = []

        fb = routing.get("fallback") or {}
        if fb.get("enabled"):
            for entry in fb.get("chain") or []:
                try:
                    fid = int(entry.get("provider_id"))
                except (TypeError, ValueError):
                    continue
                fprov = self._provider_with_key(fid)
                if not fprov or fid == pid and (entry.get("model") or "") in ("", model):
                    continue
                if effective_kind(fprov) == "cloud" and not fb.get("allow_cloud_fallback"):
                    notes.append(f"Fallback '{fprov['name']}' skipped: cloud fallback is not allowed.")
                    continue
                out.append((fprov, entry.get("model") or fprov["default_model"], "fallback"))

        return out, notes

    # ------------------------------------------------------------ generate

    def _params(self, provider, model_row, task, override):

        params = {"temperature": 0.2, "max_tokens": None}
        params.update(provider.get("params") or {})
        if model_row:
            params.update(model_row.get("params") or {})
        params.update({k: v for k, v in (override or {}).items() if v is not None})

        cap = (model_row or {}).get("max_output") or 0
        if cap and params.get("max_tokens") and params["max_tokens"] > cap:
            params["max_tokens"] = cap

        tokens = self.store.get_section("tokens")
        if not params.get("max_tokens"):
            params["max_tokens"] = int(tokens.get("output_budget_tokens", 1024))

        if provider["adapter"] == "ollama" and not params.get("num_ctx"):
            # Stable per settings (changing num_ctx between calls makes Ollama
            # reload the model): input budget + output budget + headroom.
            need = int(tokens.get("input_budget_tokens", 3500)) \
                + int(tokens.get("output_budget_tokens", 1024)) + 512
            params["num_ctx"] = max(4096, -(-need // 1024) * 1024)
            limit = (model_row or {}).get("context_limit") or 0
            if limit:
                params["num_ctx"] = min(params["num_ctx"], int(limit))

        return params

    def generate(self, task, prompt, system="", data_class="private",
                 selection=None, params=None, username="", mode="",
                 request_id=None):
        """Generate with permission checks, bounded retry and explicit fallback."""

        settings = self.store.get_settings()
        security, tokens = settings["security"], settings["tokens"]
        cancel, created = (CANCELS.ensure(request_id) if request_id
                           else (None, False))
        attempts = []

        try:
            cands, notes = self.candidates(task, data_class, selection)

            for note in notes:
                attempts.append({"skipped": True, "reason": note})

            last_error = None

            for idx, (provider, model, role) in enumerate(cands):

                label = {"provider": provider["name"], "model": model, "role": role}

                if not provider["enabled"]:
                    attempts.append({**label, "skipped": True,
                                     "reason": "Provider is disabled."})
                    if idx == 0:
                        last_error = GatewayError(
                            f"Provider '{provider['name']}' is disabled. Enable it or "
                            "choose another provider in Settings → LLM.",
                            attempts, "provider_disabled")
                    continue

                if not model:
                    attempts.append({**label, "skipped": True,
                                     "reason": "No model selected."})
                    if idx == 0:
                        last_error = GatewayError(
                            f"No model selected for '{provider['name']}'. "
                            "Pick a default model in Settings → LLM → Models.",
                            attempts, "no_model")
                    continue

                model_row = self.store.get_model(provider["id"], model)
                if model_row and not model_row["enabled"]:
                    attempts.append({**label, "skipped": True,
                                     "reason": "Model is disabled."})
                    if idx == 0:
                        last_error = GatewayError(
                            f"Model '{model}' is disabled in Settings → LLM → Models.",
                            attempts, "model_disabled")
                    continue

                ok, reason = check_permission(provider, data_class, security)
                if not ok:
                    attempts.append({**label, "skipped": True, "reason": reason,
                                     "blocked": True})
                    if idx == 0:
                        # never silently use another provider for the primary
                        # unless fallback is enabled AND permitted for it
                        last_error = GatewayError(
                            reason + " The request was not sent.", attempts,
                            "not_permitted")
                    continue

                cloud = effective_kind(provider) == "cloud"
                send_prompt, send_system = prompt, system
                if cloud and security.get("redact_cloud_prompts", True) \
                        and data_class != "none":
                    send_prompt = redact_for_cloud(prompt)
                    send_system = redact_for_cloud(system)

                adapter = build_adapter(provider)
                call_params = self._params(provider, model_row, task, params)

                tries = 1 + max(0, int(tokens.get("retries", 1)))
                for attempt_no in range(tries):

                    if cancel is not None and cancel.is_set():
                        raise GatewayError("Cancelled by user.", attempts, "cancelled")

                    if request_id:
                        CANCELS.stage(request_id, "generating",
                                      f"{provider['name']} / {model}")

                    try:
                        result = adapter.generate(model, send_prompt, send_system,
                                                  call_params, cancel)
                    except ProviderError as exc:
                        if exc.cancelled:
                            raise GatewayError("Cancelled by user.", attempts,
                                               "cancelled")
                        msg = scrub(str(exc), adapter.secrets)
                        attempts.append({**label, "ok": False, "error": msg,
                                         "attempt": attempt_no + 1})
                        self._usage(username, task, mode, provider, model, None,
                                    False, msg, cloud, idx > 0)
                        last_error = GatewayError(msg, attempts)
                        if exc.transient and attempt_no + 1 < tries:
                            time.sleep(0.6)
                            continue
                        break

                    result.cloud = cloud
                    result.fallback_used = idx > 0
                    attempts.append({**label, "ok": True, "attempt": attempt_no + 1})
                    result.attempts = attempts
                    self._usage(username, task, mode, provider, model, result,
                                True, "", cloud, idx > 0)

                    if cloud and security.get("audit_enabled", True):
                        self.store.write_audit(
                            username, "cloud_inference", provider["name"],
                            f"task={task} model={model} data_class={data_class} "
                            f"redacted={security.get('redact_cloud_prompts', True)}")
                    return result

                # this candidate failed; loop continues to a permitted fallback

            if last_error is None:
                last_error = GatewayError(
                    "No permitted provider could handle this request.",
                    attempts, "not_permitted")
            last_error.attempts = attempts
            raise last_error

        finally:
            if request_id and created:
                CANCELS.finish(request_id)

    def _usage(self, username, task, mode, provider, model, result, success,
               error, cloud, fallback):

        if not self.store.get_section("tokens").get("usage_tracking", True):
            return

        self.store.record_usage(
            username=username, task=task, mode=mode,
            provider=provider["name"], model=model,
            prompt_tokens=result.prompt_tokens if result else 0,
            completion_tokens=result.completion_tokens if result else 0,
            latency_ms=result.latency_ms if result else 0,
            success=success, error=error, cloud=cloud, fallback=fallback)

    # ------------------------------------------------------ admin helpers

    def discover_models(self, provider_id):
        """Query the provider for models and sync llm_models.

        Existing manual settings (enabled, params, limits) are preserved; models
        no longer reported are kept (so saved choices don't silently vanish)
        but flagged as not discovered.
        """

        provider = self._provider_with_key(provider_id)
        if not provider:
            raise GatewayError("Provider not found.", code="missing")

        try:
            found = build_adapter(provider).list_models()
        except ProviderError as exc:
            msg = scrub(str(exc), (provider.get("_api_key"),))
            self.store.set_provider_status(provider_id, "error", msg)
            raise GatewayError(msg)

        seen = set()
        for m in found:
            seen.add(m["model_id"])
            existing = self.store.get_model(provider_id, m["model_id"])
            self.store.upsert_model(
                provider_id, m["model_id"],
                display_name=(existing or {}).get("display_name") or m.get("display_name"),
                context_limit=m.get("context_limit") or (existing or {}).get("context_limit") or 0,
                max_output=m.get("max_output") or (existing or {}).get("max_output") or 0,
                discovered=True)

        for row in self.store.list_models(provider_id):
            if row["model_id"] not in seen and row["discovered"]:
                self.store.upsert_model(provider_id, row["model_id"], discovered=False)

        self.store.set_provider_status(provider_id, "ok")
        return self.store.list_models(provider_id)

    def test_connection(self, provider_id):
        """Connectivity + auth check (lists models). Sends no user data."""

        provider = self._provider_with_key(provider_id)
        if not provider:
            raise GatewayError("Provider not found.", code="missing")

        started = time.time()
        try:
            models = build_adapter(provider).list_models()
        except ProviderError as exc:
            msg = scrub(str(exc), (provider.get("_api_key"),))
            self.store.set_provider_status(provider_id, "error", msg)
            return {"ok": False, "error": msg,
                    "latency_ms": int((time.time() - started) * 1000)}

        self.store.set_provider_status(provider_id, "ok")
        return {"ok": True, "models_found": len(models),
                "latency_ms": int((time.time() - started) * 1000)}

    def test_model(self, provider_id, model, username=""):
        """Send a fixed diagnostic prompt (data_class none) to one model."""

        provider = self._provider_with_key(provider_id)
        if not provider:
            raise GatewayError("Provider not found.", code="missing")

        try:
            result = build_adapter(provider).generate(
                model, "Reply with the single word: OK", "",
                {"temperature": 0, "max_tokens": 16})
        except ProviderError as exc:
            return {"ok": False,
                    "error": scrub(str(exc), (provider.get("_api_key"),))}

        return {"ok": True, "reply": (result.text or "").strip()[:60],
                "latency_ms": result.latency_ms,
                "prompt_tokens": result.prompt_tokens,
                "completion_tokens": result.completion_tokens}
