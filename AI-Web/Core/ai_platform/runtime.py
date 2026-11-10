"""Process-wide access to the AI platform + per-request context."""

import contextvars
import os
import threading

_lock = threading.Lock()
_service = None
_ctx = contextvars.ContextVar("ai_platform_ctx", default=None)


_mcp = None
_broker = None
_retriever = None


def _user_audit(username, action, detail):
    """Mirror platform audit events into the application audit log."""
    from Core.user_repository import UserRepository
    UserRepository().write_audit_log(
        None, username or "", action, "ai_platform", (detail or "")[:500], None)


def get_service():
    """Lazy singleton PlatformService (creates Database/ai_platform.db)."""

    global _service
    with _lock:
        if _service is None:
            from Core.ai_platform.service import PlatformService
            _service = PlatformService(audit_hook=_user_audit)
        return _service


def get_retriever():
    """Process-wide Retriever (embedding model + reranker are loaded once)."""

    global _retriever
    with _lock:
        if _retriever is None:
            from Core.ai_platform.retrieval import Retriever
            _retriever = Retriever.default()
        return _retriever


_warm = {"started": False, "result": None}


def warm_up():
    """Load the slow parts once, in the background, when the AI Assistant is opened.

    The first question used to pay for loading the embedding model, the reranker and
    the Ollama model (tens of seconds on CPU) on top of answering. This starts that
    work earlier. It changes no setting and no data, sends no user text anywhere, and
    only contacts a provider that is local (the Ollama preload is an empty prompt to a
    local-network provider; a cloud provider is never contacted here). Disable with
    QA_AI_WARMUP=off. Returns True when a warm-up was started by this call.
    """

    if os.environ.get("QA_AI_WARMUP", "on").strip().lower() in ("off", "0", "false", "no"):
        return False
    with _lock:
        if _warm["started"]:
            return False
        _warm["started"] = True

    def work():
        result = {"retriever": False, "model": None}
        try:
            r = get_retriever()
            r._get_searcher()
            r._get_reranker()
            result["retriever"] = True
        except Exception:  # noqa: BLE001 - warm-up must never break anything
            pass
        try:
            from Core.ai_platform.gateway import effective_kind
            svc = get_service()
            pid, model, _ = svc.gateway.resolve_primary("answer")
            prov = svc.store.get_provider(pid)
            if prov and model and prov.get("adapter") == "ollama" and effective_kind(prov) == "local":
                import requests
                requests.post(prov["base_url"].rstrip("/") + "/api/generate",
                              json={"model": model, "prompt": "", "keep_alive": "30m", "stream": False},
                              timeout=300)
                result["model"] = model
        except Exception:  # noqa: BLE001
            pass
        _warm["result"] = result

    threading.Thread(target=work, name="ai-warmup", daemon=True).start()
    return True


def get_mcp():
    global _mcp
    svc = get_service()
    with _lock:
        if _mcp is None:
            from Core.ai_platform.tools import McpService
            _mcp = McpService(svc.store, audit=svc._audit)
        return _mcp


def get_broker():
    global _broker
    svc, mcp = get_service(), get_mcp()
    with _lock:
        if _broker is None:
            from Core.ai_platform.tools import ToolBroker
            _broker = ToolBroker(svc, mcp)
        return _broker


def reset_service_for_tests(service=None):
    global _service, _mcp, _broker, _retriever
    with _lock:
        _warm["started"] = False
        _warm["result"] = None
        _service = service
        _mcp = None
        _broker = None
        _retriever = None


def get_store():
    return get_service().store


def get_gateway():
    return get_service().gateway


def set_context(**values):
    """Attach request-scoped values (username, mode, selection, request_id,
    task, data_class). Returns a token for ``reset_context``."""
    merged = dict(_ctx.get() or {})
    merged.update(values)
    return _ctx.set(merged)


def reset_context(token):
    _ctx.reset(token)


def get_context():
    return dict(_ctx.get() or {})
