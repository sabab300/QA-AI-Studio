"""Enhanced -> legacy pipeline fallback policy (Task 4, Phase 5).

The enhanced pipeline may hand a request to the legacy pipeline ONLY for the
documented, recoverable conditions below, and only visibly (warning in the
response, platform.pipeline_fallback, server log, audit row).

Recoverable (allow-list):
  * ``enhanced_component_unavailable`` - the enhanced modules cannot be
    imported (ImportError while loading them, before any request data is used).
  * ``vector_store_unavailable``       - the enhanced retriever could not reach
    the vector store / embedder. Raised explicitly as RecoverableEnhancedError
    at the single documented site (Retriever.retrieve); happens before any LLM
    or tool call, so nothing is sent twice.

Never falls back (always surfaced as an actionable error, nothing sent on):
  authorization failure, privacy-policy rejection, provider permission failure,
  invalid configuration, security validation failure, cancellation, provider
  failure after the gateway's own retry/fallback, and any unexpected exception.

Operator controls (explicit, not silent): Settings -> LLM -> RAG
``enhanced_pipeline`` and the environment variable QA_AI_ENHANCED_PIPELINE=off.
"""

import os

from .gateway import GatewayError

ENV_SWITCH = "QA_AI_ENHANCED_PIPELINE"

RECOVERABLE = ("enhanced_component_unavailable", "vector_store_unavailable")


class RecoverableEnhancedError(Exception):
    """A documented, recoverable enhanced-pipeline failure."""

    def __init__(self, message, reason="vector_store_unavailable"):
        super().__init__(message)
        self.reason = reason


class SecurityValidationError(Exception):
    """Input/output failed a security validation. Never falls back."""


class ConfigurationError(Exception):
    """AI platform configuration is invalid. Never falls back."""


class FallbackNotice(dict):
    """Returned by the orchestrator when a recoverable fallback was taken."""


_GATEWAY_CATEGORY = {
    "not_permitted": "permission_or_privacy_rejected",
    "not_configured": "invalid_configuration",
    "missing": "invalid_configuration",
    "model_disabled": "invalid_configuration",
    "cancelled": "cancelled",
}


def env_disabled():
    return os.environ.get(ENV_SWITCH, "").strip().lower() in ("off", "0", "false", "disabled")


def classify(exc):
    """-> {'recoverable': bool, 'category': str, 'message': str}"""
    if isinstance(exc, RecoverableEnhancedError):
        return {"recoverable": True, "category": exc.reason, "message": str(exc)}
    if isinstance(exc, GatewayError):
        cat = _GATEWAY_CATEGORY.get(getattr(exc, "code", ""), "provider_failure")
        return {"recoverable": False, "category": cat, "message": str(exc)}
    if isinstance(exc, PermissionError) or getattr(exc, "status_code", None) in (401, 403):
        return {"recoverable": False, "category": "authorization_failed",
                "message": "You are not authorised to perform this AI request."}
    if isinstance(exc, SecurityValidationError):
        return {"recoverable": False, "category": "security_validation_failed",
                "message": "The request was rejected by a security check."}
    if isinstance(exc, ConfigurationError):
        return {"recoverable": False, "category": "invalid_configuration",
                "message": "The AI platform configuration is invalid. "
                           "Review Settings → LLM."}
    return {"recoverable": False, "category": "unexpected_error",
            "message": "The AI assistant hit an unexpected error. "
                       "The request was not retried on another pipeline or provider."}


def blocked_response(exc, info=None):
    """Error response used instead of a silent fallback."""
    info = info or classify(exc)
    msg = info["message"]
    return {
        "success": False, "answer": "", "confidence": 0, "error": msg,
        "preformatted": True, "sources": [], "warnings": [],
        "provider": None, "model": None,
        "platform": {
            "error_code": info["category"],
            "attempts": list(getattr(exc, "attempts", None) or []),
            "llm": None, "tools": [], "insufficient_evidence": False,
            "pipeline_fallback": {"used": False, "blocked": True,
                                  "reason": info["category"]},
        },
    }


def notice(info):
    return FallbackNotice(
        used=True, reason=info["category"],
        message=("The enhanced AI pipeline was unavailable (%s); this answer was "
                 "produced by the legacy pipeline." % info["category"].replace("_", " ")))


def audit(username, info, store=None):
    """Best-effort audit row; never raises."""
    try:
        if store is None:
            from . import runtime
            store = runtime.get_store()
        store.write_audit(username or "", "pipeline_fallback",
                          info["category"], info.get("message", "")[:200])
    except Exception:
        pass
