"""
PlatformService - validated administration of providers, models and settings.

Every mutating call validates input, never returns secrets, and writes an
audit record. Reads return sanitised dictionaries only.
"""

import ipaddress
import re
from urllib.parse import urlparse

from Core.ai_platform import constants as C
from Core.ai_platform.gateway import (
    LLMGateway, GatewayError, effective_kind, is_local_host,
)
from Core.ai_platform.store import PlatformStore

_NAME = re.compile(r"^[\w .:+/()-]{1,60}$")


class ValidationError(Exception):
    def __init__(self, errors):
        if isinstance(errors, str):
            errors = [errors]
        super().__init__("; ".join(errors))
        self.errors = errors


def _num(value, lo, hi, label, integer=False):
    try:
        v = int(value) if integer else float(value)
    except (TypeError, ValueError):
        raise ValidationError(f"{label} must be a number.")
    if v < lo or v > hi:
        raise ValidationError(f"{label} must be between {lo} and {hi}.")
    return v


def validate_base_url(url, kind):

    url = (url or "").strip().rstrip("/")
    parsed = urlparse(url)

    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise ValidationError("Base URL must start with http:// or https://")

    if parsed.username or parsed.password:
        raise ValidationError("Do not put credentials in the base URL; use the API key field.")

    try:
        ip = ipaddress.ip_address(parsed.hostname)
        if ip.is_link_local or ip.is_multicast or ip.is_unspecified:
            raise ValidationError("This address range is not allowed for a provider.")
    except ValueError:
        pass

    if kind == "cloud" and parsed.scheme != "https" and not is_local_host(url):
        raise ValidationError("Cloud providers must use https://")

    return url


def validate_params(params):

    out = {}
    params = params or {}

    if "temperature" in params and params["temperature"] not in (None, ""):
        out["temperature"] = _num(params["temperature"], 0, 2, "Temperature")
    if "top_p" in params and params["top_p"] not in (None, ""):
        out["top_p"] = _num(params["top_p"], 0, 1, "Top-p")
    if "max_tokens" in params and params["max_tokens"] not in (None, ""):
        out["max_tokens"] = _num(params["max_tokens"], 16, 128000, "Max output tokens", True)
    if "num_ctx" in params and params["num_ctx"] not in (None, ""):
        out["num_ctx"] = _num(params["num_ctx"], 512, 262144, "Context window", True)

    return out


class PlatformService:

    def __init__(self, store=None, gateway=None, audit_hook=None):
        self.store = store or PlatformStore()
        self.gateway = gateway or LLMGateway(self.store)
        self.audit_hook = audit_hook   # fn(username, action, detail)
        self.store.ensure_default_provider()

    # -------------------------------------------------------------- audit

    def _audit(self, username, action, target="", detail=""):

        if not self.store.get_section("security").get("audit_enabled", True):
            return

        self.store.write_audit(username, action, target, detail)

        if self.audit_hook:
            try:
                self.audit_hook(username, action, f"{target} {detail}".strip())
            except Exception:
                pass

    # ----------------------------------------------------------- providers

    @staticmethod
    def public_provider(p):
        """Provider dict safe for the frontend (never any key material)."""

        out = {k: v for k, v in p.items() if not k.startswith("_")}
        out["effective_kind"] = effective_kind(p)
        out["adapter_label"] = C.ADAPTER_LABELS.get(p["adapter"], p["adapter"])
        return out

    def list_providers(self):
        providers = []
        for p in self.store.list_providers():
            item = self.public_provider(p)
            item["models"] = self.store.list_models(p["id"])
            providers.append(item)
        return providers

    def catalog(self):
        return [
            {"adapter": a, "label": C.ADAPTER_LABELS[a], "kind": k,
             "default_base_url": url, "needs_api_key": needs}
            for a, (k, url, needs) in C.ADAPTERS.items()
        ]

    def create_provider(self, data, username):

        adapter = (data.get("adapter") or "").strip()
        if adapter not in C.ADAPTERS:
            raise ValidationError("Unknown provider type.")

        kind_default, url_default, needs_key = C.ADAPTERS[adapter]
        name = (data.get("name") or "").strip()
        if not _NAME.match(name):
            raise ValidationError("Name must be 1-60 characters (letters, digits, spaces, . - _ : + / ( )).")
        if self.store.get_provider_by_name(name):
            raise ValidationError(f"A provider named '{name}' already exists.")

        kind = kind_default if kind_default == "cloud" else (data.get("kind") or "local")
        if kind not in C.PROVIDER_KINDS:
            raise ValidationError("Kind must be local or cloud.")

        api_key = (data.get("api_key") or "").strip()
        if needs_key and not api_key:
            raise ValidationError("An API key is required for this provider.")

        payload = {
            "name": name, "adapter": adapter, "kind": kind,
            "base_url": validate_base_url(data.get("base_url") or url_default, kind),
            "api_key": api_key,
            "enabled": bool(data.get("enabled", True)),
            "timeout_s": _num(data.get("timeout_s") or 300, 5, 1800, "Timeout", True),
            "params": validate_params(data.get("params")),
            "limits": self._limits(data.get("limits")),
            "permissions": self._permissions(data.get("permissions"), kind),
            "default_model": (data.get("default_model") or "").strip()[:120],
        }

        pid = self.store.create_provider(payload)
        if payload["default_model"]:
            self.store.upsert_model(pid, payload["default_model"], enabled=True)

        self._audit(username, "llm_provider_create", name,
                    f"adapter={adapter} kind={kind} key_set={bool(api_key)}")
        return self.public_provider(self.store.get_provider(pid))

    def update_provider(self, provider_id, data, username):

        current = self.store.get_provider(provider_id)
        if not current:
            raise ValidationError("Provider not found.")

        upd = {}
        kind = current["kind"]

        if "name" in data:
            name = (data["name"] or "").strip()
            if not _NAME.match(name):
                raise ValidationError("Invalid provider name.")
            other = self.store.get_provider_by_name(name)
            if other and other["id"] != provider_id:
                raise ValidationError(f"A provider named '{name}' already exists.")
            upd["name"] = name

        if "kind" in data and current["adapter"] not in ("openai", "anthropic",
                                                         "gemini", "openrouter"):
            if data["kind"] not in C.PROVIDER_KINDS:
                raise ValidationError("Kind must be local or cloud.")
            upd["kind"] = kind = data["kind"]

        if "base_url" in data:
            upd["base_url"] = validate_base_url(data["base_url"], kind)
        if "enabled" in data:
            upd["enabled"] = bool(data["enabled"])
        if "timeout_s" in data:
            upd["timeout_s"] = _num(data["timeout_s"], 5, 1800, "Timeout", True)
        if "params" in data:
            upd["params"] = validate_params(data["params"])
        if "limits" in data:
            upd["limits"] = self._limits(data["limits"])
        if "permissions" in data:
            upd["permissions"] = self._permissions(data["permissions"], kind)
        if "default_model" in data:
            upd["default_model"] = (data["default_model"] or "").strip()[:120]
            if upd["default_model"]:
                self.store.upsert_model(provider_id, upd["default_model"], enabled=True)

        key_changed = False
        if data.get("clear_key"):
            upd["clear_key"] = True
            key_changed = True
        elif (data.get("api_key") or "").strip():
            upd["api_key"] = data["api_key"].strip()
            key_changed = True

        self.store.update_provider(provider_id, upd)

        perm_note = ""
        if "permissions" in upd:
            perm_note = f" permissions={upd['permissions']}"
        self._audit(username, "llm_provider_update", current["name"],
                    f"fields={sorted(k for k in upd if k not in ('api_key',))} "
                    f"key_changed={key_changed}{perm_note}")

        return self.public_provider(self.store.get_provider(provider_id))

    def delete_provider(self, provider_id, username):

        provider = self.store.get_provider(provider_id)
        if not provider:
            raise ValidationError("Provider not found.")

        routing = self.store.get_section("routing")
        in_use = (routing.get("default_provider_id") == provider_id or
                  any(int(r.get("provider_id") or 0) == provider_id
                      for r in (routing.get("task_routes") or {}).values()) or
                  any(int(e.get("provider_id") or 0) == provider_id
                      for e in (routing.get("fallback") or {}).get("chain", [])))
        if in_use:
            raise ValidationError(
                "This provider is used as the default, a task route or a fallback. "
                "Change AI Routing first, then delete it.")

        outcome = self.store.delete_provider(provider_id)
        if outcome == "builtin":
            raise ValidationError(
                "The built-in Local Ollama provider cannot be deleted; disable it instead.")

        self._audit(username, "llm_provider_delete", provider["name"])

    @staticmethod
    def _limits(limits):
        limits = limits or {}
        out = {}
        for key, label in (("context_limit", "Context limit"),
                           ("max_output", "Max output")):
            if limits.get(key) not in (None, "", 0):
                out[key] = _num(limits[key], 256, 2000000, label, True)
        return out

    @staticmethod
    def _permissions(perms, kind):
        perms = perms or {}
        return {
            "allow_private_data": bool(perms.get("allow_private_data")),
            "allow_public_data": bool(perms.get("allow_public_data")),
        }

    # -------------------------------------------------------------- models

    def update_model(self, provider_id, model_id, data, username):

        provider = self.store.get_provider(provider_id)
        if not provider:
            raise ValidationError("Provider not found.")

        fields = {}
        if "enabled" in data:
            fields["enabled"] = bool(data["enabled"])
        if "display_name" in data:
            fields["display_name"] = (data["display_name"] or "")[:120]
        if "context_limit" in data:
            fields["context_limit"] = _num(data["context_limit"] or 0, 0, 2000000,
                                           "Context limit", True)
        if "max_output" in data:
            fields["max_output"] = _num(data["max_output"] or 0, 0, 2000000,
                                        "Max output", True)
        if "params" in data:
            fields["params"] = validate_params(data["params"])
        if "capabilities" in data and isinstance(data["capabilities"], dict):
            fields["capabilities"] = {
                k: bool(v) for k, v in data["capabilities"].items()
                if k in ("chat", "tools", "vision", "json", "fast", "reasoning")}

        self.store.upsert_model(provider_id, model_id, **fields)
        self._audit(username, "llm_model_update",
                    f"{provider['name']}/{model_id}", f"fields={sorted(fields)}")
        return self.store.get_model(provider_id, model_id)

    def add_model(self, provider_id, model_id, username):
        model_id = (model_id or "").strip()
        if not model_id or len(model_id) > 120:
            raise ValidationError("Model id is required (max 120 characters).")
        provider = self.store.get_provider(provider_id)
        if not provider:
            raise ValidationError("Provider not found.")
        self.store.upsert_model(provider_id, model_id, enabled=True)
        self._audit(username, "llm_model_add", f"{provider['name']}/{model_id}")
        return self.store.get_model(provider_id, model_id)

    # ------------------------------------------------------------ settings

    def get_settings(self):
        s = self.store.get_settings()
        s["_meta"] = self.store.settings_meta()
        return s

    def save_settings(self, section, patch, username):

        if section not in C.SECTIONS:
            raise ValidationError("Unknown settings section.")
        if not isinstance(patch, dict):
            raise ValidationError("Settings must be an object.")

        current = self.store.get_section(section)
        merged = self._merge_validate(section, current, patch)

        before_cloud = self.store.get_section("security").get("cloud_data_sharing_enabled")
        self.store.save_section(section, merged, username)

        detail = f"keys={sorted(patch)}"
        if section == "security" and "cloud_data_sharing_enabled" in patch:
            detail += (f" cloud_data_sharing {before_cloud}->"
                       f"{merged.get('cloud_data_sharing_enabled')}")
        self._audit(username, "llm_settings_update", section, detail)

        return merged

    def _merge_validate(self, section, current, patch):

        defaults = C.DEFAULT_SETTINGS[section]
        merged = dict(current)

        unknown = [k for k in patch if k not in defaults]
        if unknown:
            raise ValidationError(f"Unknown setting(s): {', '.join(sorted(unknown))}")

        for key, value in patch.items():

            default = defaults[key]

            if isinstance(default, bool):
                if not isinstance(value, bool):
                    raise ValidationError(f"{key} must be true or false.")
                merged[key] = value

            elif isinstance(default, (int, float)) and not isinstance(default, bool):
                lo, hi = C.SETTING_BOUNDS.get((section, key), (0, 10**9))
                merged[key] = _num(value, lo, hi, key, isinstance(default, int))

            elif isinstance(default, str):
                merged[key] = str(value or "")[:200]

            elif key == "default_provider_id":
                merged[key] = value

            elif isinstance(default, dict):
                if not isinstance(value, dict):
                    raise ValidationError(f"{key} must be an object.")
                sub = dict(current.get(key) or {})
                sub.update(value)
                merged[key] = sub

            else:
                merged[key] = value

        self._validate_special(section, merged)
        return merged

    def _validate_special(self, section, s):

        if section == "routing":

            if s["default_mode"] not in C.MODES:
                raise ValidationError("default_mode must be auto, local, global or hybrid.")
            if s["model_mode"] not in ("auto", "manual"):
                raise ValidationError("model_mode must be auto or manual.")
            if s.get("global_search_policy", "when_needed") not in ("when_needed", "always", "never"):
                raise ValidationError("global_search_policy must be when_needed, always or never.")

            pid = s.get("default_provider_id")
            if pid is not None:
                if not self.store.get_provider(int(pid)):
                    raise ValidationError("Default provider does not exist.")
                s["default_provider_id"] = int(pid)

            routes = {}
            for task, route in (s.get("task_routes") or {}).items():
                if task not in C.TASKS:
                    raise ValidationError(f"Unknown task '{task}'.")
                if not route:
                    continue
                rp = int(route.get("provider_id") or 0)
                if not self.store.get_provider(rp):
                    raise ValidationError(f"Task '{task}': provider does not exist.")
                if not (route.get("model") or "").strip():
                    raise ValidationError(f"Task '{task}': choose a model.")
                routes[task] = {"provider_id": rp, "model": route["model"].strip()}
            s["task_routes"] = routes

            fb = s.get("fallback") or {}
            chain = []
            for entry in fb.get("chain") or []:
                fp = int(entry.get("provider_id") or 0)
                if not self.store.get_provider(fp):
                    raise ValidationError("A fallback provider does not exist.")
                chain.append({"provider_id": fp,
                              "model": (entry.get("model") or "").strip()})
            if len(chain) > 5:
                raise ValidationError("At most 5 fallback providers.")
            s["fallback"] = {"enabled": bool(fb.get("enabled")), "chain": chain,
                             "allow_cloud_fallback": bool(fb.get("allow_cloud_fallback"))}

        if section == "tokens":
            ec = s.get("exact_cache") or {}
            s["exact_cache"] = {
                "enabled": bool(ec.get("enabled")),
                "ttl_s": _num(ec.get("ttl_s", 600), 10, 86400, "Cache TTL", True),
                "max_items": _num(ec.get("max_items", 200), 10, 5000, "Cache size", True)}
            sc = s.get("semantic_cache") or {}
            s["semantic_cache"] = {
                "enabled": bool(sc.get("enabled")),
                "threshold": _num(sc.get("threshold", 0.97), 0.85, 1.0, "Semantic threshold"),
                "ttl_s": _num(sc.get("ttl_s", 600), 10, 86400, "Semantic cache TTL", True)}

        if section == "security":
            cmds = s.get("mcp_allowed_commands")
            if not isinstance(cmds, list) or len(cmds) > 20 or any(
                    (not isinstance(c, str)) or not re.match(r"^[A-Za-z0-9._-]{1,30}$", c)
                    for c in cmds):
                raise ValidationError(
                    "Allowed MCP commands must be a list of up to 20 bare executable names.")
            s["mcp_allowed_commands"] = sorted({c.strip() for c in cmds})

        if section == "rag":
            if s["top_k"] > s["candidate_pool"]:
                raise ValidationError("Top-K cannot exceed the candidate pool.")

    # -------------------------------------------------------------- tests

    def test_connection(self, provider_id, username):
        res = self.gateway.test_connection(provider_id)
        self._audit(username, "llm_provider_test", str(provider_id),
                    f"ok={res['ok']}")
        return res

    def discover_models(self, provider_id, username):
        models = self.gateway.discover_models(provider_id)
        self._audit(username, "llm_models_discover", str(provider_id),
                    f"count={len(models)}")
        return models

    def test_model(self, provider_id, model, username):
        res = self.gateway.test_model(provider_id, model, username)
        self._audit(username, "llm_model_test", f"{provider_id}/{model}",
                    f"ok={res['ok']}")
        return res

    def compare_models(self, entries, prompt, username):
        """Run the same small prompt on up to 3 provider/model pairs.

        Uses data class 'public' - the prompt is typed by the administrator, no
        knowledge-base content is included - and the normal permission gate,
        so a cloud provider needs the same explicit permission.
        """

        from Core.ai_platform.gateway import check_permission

        if not (prompt or "").strip():
            raise ValidationError("Enter a comparison prompt.")
        if len(prompt) > 1500:
            raise ValidationError("Comparison prompt is limited to 1500 characters.")
        if not entries or len(entries) > 3:
            raise ValidationError("Choose 1 to 3 models to compare.")

        security = self.store.get_section("security")
        out = []

        for e in entries:
            provider = self.store.get_provider(int(e.get("provider_id") or 0),
                                               include_secret=True)
            model = (e.get("model") or "").strip()
            if not provider or not model:
                out.append({"ok": False, "error": "Provider or model missing."})
                continue

            ok, reason = check_permission(provider, "public", security)
            label = {"provider": provider["name"], "model": model}
            if not ok:
                out.append({**label, "ok": False, "error": reason})
                continue

            from Core.ai_platform.providers import build_adapter, ProviderError
            from Core.ai_platform.sanitize import scrub
            try:
                r = build_adapter(provider).generate(
                    model, prompt, "", {"temperature": 0.2, "max_tokens": 300})
                out.append({**label, "ok": True, "text": r.text[:1500],
                            "latency_ms": r.latency_ms,
                            "prompt_tokens": r.prompt_tokens,
                            "completion_tokens": r.completion_tokens,
                            "cloud": effective_kind(provider) == "cloud"})
            except ProviderError as exc:
                out.append({**label, "ok": False,
                            "error": scrub(str(exc), (provider.get("_api_key"),))})

        self._audit(username, "llm_models_compare", "",
                    f"models={len(entries)} prompt_chars={len(prompt)}")
        return out


GatewayError = GatewayError  # re-export for routers
