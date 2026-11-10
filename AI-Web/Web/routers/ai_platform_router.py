# Create: AI-Web/Web/routers/ai_platform_router.py

"""
QA AI Studio — Web
Settings -> LLM API (Task 4)

Admin API for LLM providers, models, routing, RAG, token optimisation,
security and Plugins / MCP integrations.

Access control reuses the existing RBAC resource "settings":
    view    - read configuration (secrets are never returned, only has_secret)
    create  - add providers / integrations
    edit    - change providers, models, settings, integrations
    delete  - delete providers / integrations
    execute - test connections, discover / test / compare models, run an MCP
              tool manually, run retrieval diagnostics

Roles without any "settings" permission (QA Engineer, Viewer by default) get
403 on every endpoint here. Secrets are encrypted at rest and never included
in any response or log line.
"""

from typing import Any, Dict, Optional

from fastapi import APIRouter, Body, Depends, HTTPException

from Core.ai_platform import constants as C
from Core.ai_platform import runtime
from Core.ai_platform.cache import ANSWER_CACHE, SEMANTIC_CACHE
from Core.ai_platform.gateway import GatewayError
from Core.ai_platform.service import ValidationError
from Core.ai_platform.tools import ToolError
from Web.deps import require_permission

router = APIRouter(prefix="/api/settings/llm", tags=["settings-llm"])


def _svc():
    return runtime.get_service()


def _fail(error, status=400):
    raise HTTPException(status_code=status, detail=str(error))


def _guard(fn):
    try:
        return fn()
    except ValidationError as error:
        _fail(error)
    except GatewayError as error:
        _fail(error, 502 if error.code == "provider_failed" else 400)
    except ToolError as error:
        _fail(error)


def _name(user):
    return user.get("username") or ""


# ---------------------------------------------------------------- overview

@router.get("/overview")
def overview(user=Depends(require_permission("settings", "view"))):

    svc = _svc()

    return {
        "providers": svc.list_providers(),
        "catalog": svc.catalog(),
        "settings": svc.get_settings(),
        "tasks": list(C.TASKS),
        "modes": list(C.MODES),
        "mcp": runtime.get_mcp().list(),
        "usage": svc.store.usage_summary(7),
        "cache": ANSWER_CACHE.stats(),
        "embedding": _embedding_info(),
    }


def _embedding_info():
    """Read-only facts about the knowledge store (never modifies it)."""

    info = {"model": None, "dimension": None, "vectors": None,
            "distance_space": "l2", "note": (
                "The embedding model is independent of the answer LLM. Changing the "
                "answer LLM never re-indexes. Changing the embedding model needs a "
                "separate, backed-up migration that is not part of this screen.")}
    try:
        from Config import settings as cfg
        info["model"] = getattr(cfg, "EMBEDDING_MODEL", None)
        info["reranker"] = getattr(cfg, "RERANK_MODEL", None)
    except Exception:
        pass
    # Read-only: the SQLite catalogue of the vector store is opened with
    # mode=ro. The vector store itself is never opened here, so this screen
    # cannot create, migrate or modify anything in the knowledge base.
    try:
        import json
        import sqlite3
        from pathlib import Path
        from Core.config_manager import ConfigManager

        db_path = Path(str(ConfigManager().chroma_db)) / "chroma.sqlite3"
        if db_path.exists():
            conn = sqlite3.connect(db_path.resolve().as_uri() + "?mode=ro", uri=True, timeout=2)
            try:
                row = conn.execute(
                    "SELECT id, dimension, config_json_str FROM collections WHERE name=?",
                    ("psw_knowledge",)).fetchone()
                if row:
                    info["dimension"] = row[1]
                    info["vectors"] = conn.execute(
                        "SELECT COUNT(*) FROM embeddings WHERE segment_id="
                        "(SELECT id FROM segments WHERE collection=? AND scope='METADATA')",
                        (row[0],)).fetchone()[0]
                    try:
                        cfg = json.loads(row[2] or "{}")
                        space = (cfg["keys"]["#embedding"]["float_list"]["vector_index"]
                                 ["config"]["space"])
                        info["distance_space"] = str(space)
                    except Exception:
                        pass
            finally:
                conn.close()
    except Exception:
        pass
    return info


# --------------------------------------------------------------- providers

@router.get("/providers")
def list_providers(user=Depends(require_permission("settings", "view"))):
    return {"providers": _svc().list_providers()}


@router.post("/providers")
def create_provider(payload: Dict[str, Any] = Body(...),
                    user=Depends(require_permission("settings", "create"))):
    return _guard(lambda: _svc().create_provider(payload, _name(user)))


@router.put("/providers/{provider_id}")
def update_provider(provider_id: int, payload: Dict[str, Any] = Body(...),
                    user=Depends(require_permission("settings", "edit"))):
    return _guard(lambda: _svc().update_provider(provider_id, payload, _name(user)))


@router.delete("/providers/{provider_id}")
def delete_provider(provider_id: int,
                    user=Depends(require_permission("settings", "delete"))):
    _guard(lambda: _svc().delete_provider(provider_id, _name(user)))
    return {"status": "deleted"}


@router.post("/providers/{provider_id}/test")
def test_provider(provider_id: int,
                  user=Depends(require_permission("settings", "execute"))):
    return _guard(lambda: _svc().test_connection(provider_id, _name(user)))


@router.post("/providers/{provider_id}/discover")
def discover_models(provider_id: int,
                    user=Depends(require_permission("settings", "execute"))):
    return {"models": _guard(lambda: _svc().discover_models(provider_id, _name(user)))}


# ------------------------------------------------------------------ models

@router.post("/providers/{provider_id}/models")
def add_model(provider_id: int, payload: Dict[str, Any] = Body(...),
              user=Depends(require_permission("settings", "create"))):
    return _guard(lambda: _svc().add_model(provider_id, payload.get("model_id"), _name(user)))


@router.put("/providers/{provider_id}/models/{model_id:path}")
def update_model(provider_id: int, model_id: str, payload: Dict[str, Any] = Body(...),
                 user=Depends(require_permission("settings", "edit"))):
    return _guard(lambda: _svc().update_model(provider_id, model_id, payload, _name(user)))


@router.post("/models/test")
def test_model(payload: Dict[str, Any] = Body(...),
               user=Depends(require_permission("settings", "execute"))):
    return _guard(lambda: _svc().test_model(int(payload.get("provider_id") or 0),
                                            payload.get("model") or "", _name(user)))


@router.post("/models/compare")
def compare_models(payload: Dict[str, Any] = Body(...),
                   user=Depends(require_permission("settings", "execute"))):
    return {"results": _guard(lambda: _svc().compare_models(
        payload.get("models") or [], payload.get("prompt") or "", _name(user)))}


# ---------------------------------------------------------------- settings

@router.get("/settings")
def get_settings(user=Depends(require_permission("settings", "view"))):
    return _svc().get_settings()


@router.put("/settings/{section}")
def save_settings(section: str, payload: Dict[str, Any] = Body(...),
                  user=Depends(require_permission("settings", "edit"))):
    saved = _guard(lambda: _svc().save_settings(section, payload, _name(user)))
    # settings change answer behaviour; never serve answers from the old config
    ANSWER_CACHE.clear()
    SEMANTIC_CACHE.clear()
    return {"section": section, "settings": saved}


@router.post("/cache/clear")
def clear_cache(user=Depends(require_permission("settings", "edit"))):
    ANSWER_CACHE.clear()
    SEMANTIC_CACHE.clear()
    _svc()._audit(_name(user), "llm_cache_clear", "answer_cache")
    return {"status": "cleared"}


@router.get("/usage")
def usage(days: int = 7, user=Depends(require_permission("settings", "view"))):
    return _svc().store.usage_summary(max(1, min(int(days), 90)))


@router.get("/audit")
def audit(limit: int = 100, user=Depends(require_permission("settings", "view"))):
    return {"audit": _svc().store.list_audit(max(1, min(int(limit), 500)))}


# --------------------------------------------------------------------- RAG

def _validate_scope(scope):
    """Domain / Module / Knowledge names must exist in the canonical hierarchy.

    Free text was accepted before, so a typo silently produced an empty result
    that looked like "no evidence". Unknown names are rejected with the valid
    choices; omitted fields mean "all"."""

    if not scope:
        return
    try:
        from Core.knowledge_web_repository import KnowledgeRepository
        repo = KnowledgeRepository()
        domains = [d.get("name") if isinstance(d, dict) else d for d in (repo.list_domains() or [])]
    except Exception:
        return   # hierarchy unavailable: do not block diagnostics
    domain, module = scope.get("domain"), scope.get("module")
    if module and not domain:
        _fail("Choose a Domain before choosing a Module.")
    if domain and domain not in domains:
        _fail("Unknown Domain '%s'. Choose one of: %s." % (domain, ", ".join(sorted(map(str, domains))[:25]) or "(none)"))
    if module:
        modules = [m.get("name") if isinstance(m, dict) else m for m in (repo.list_modules(domain) or [])]
        if module not in modules:
            _fail("Unknown Module '%s' in Domain '%s'. Choose one of: %s." % (
                module, domain, ", ".join(sorted(map(str, modules))[:25]) or "(none)"))
    if scope.get("knowledge_name") and domain and module:
        try:
            names = [n.get("name") if isinstance(n, dict) else n
                     for n in (repo.list_knowledge_names(domain, module) or [])]
            if names and scope["knowledge_name"] not in names:
                _fail("Unknown Knowledge '%s' in %s / %s." % (scope["knowledge_name"], domain, module))
        except HTTPException:
            raise
        except Exception:
            pass


@router.post("/rag/diagnostics")
def rag_diagnostics(payload: Dict[str, Any] = Body(...),
                    user=Depends(require_permission("settings", "execute"))):
    """Run retrieval ONLY (no LLM call, no write) and show per-candidate scores.

    Returns file names, ranks and scores - never chunk text - so it can be used
    to calibrate the similarity thresholds on the live collection. Also explains
    every threshold decision and what AUTO routing would do with the question.
    """

    question = (payload.get("question") or "").strip()
    if not question:
        _fail("Enter a question.")
    if len(question) > 2000:
        _fail("The question is too long (max 2000 characters).")

    from Core.ai_platform.assistant import AssistantService
    from Core.ai_platform import evidence as ev

    svc = _svc()
    rag = svc.store.get_section("rag")
    routing = svc.store.get_section("routing")
    scope = {k: payload.get(k) for k in ("domain", "module", "knowledge_name",
                                         "version", "document_type")
             if payload.get(k)}
    if payload.get("document_version_id") not in (None, ""):
        try:
            scope["document_version_id"] = int(payload["document_version_id"])
        except (TypeError, ValueError):
            _fail("document_version_id must be a number.")
    _validate_scope(scope)

    asst = AssistantService(platform=svc)
    result = asst.retriever.retrieve(question, rag, scope)
    chunks = result.get("chunks") or []
    diag_in = result.get("diagnostics") or {}
    assessment = ev.assess_local(chunks, rag, diag_in)
    probe = ev.assess_probe(chunks, rag)
    route = asst.route_prompt("auto", scope, question, routing)
    diag = result.get("diagnostics") or {}

    min_best = float(rag.get("min_best_similarity", 0.38))
    floor = float(rag.get("similarity_threshold", 0.30))
    steps = [
        "The vector store returned %s candidate(s) (pool setting %s)." % (diag.get("pool"), rag.get("candidate_pool")),
        "%s candidate(s) scored at or above the candidate floor %.2f (similarity = cosine of question and passage)." % (
            diag.get("after_threshold"), floor),
        ("Cross-encoder reranking was applied (relevance is a separate 0-1 score)." if diag.get("rerank_used")
         else "Reranking was not applied (%s)." % ("disabled in settings" if not rag.get("rerank_enabled", True)
                                                  else "reranker unavailable")),
        "%s passage(s) from %s document(s) were selected for the model (top-k %s, max %s per document)." % (
            diag.get("returned"), diag.get("documents_covered"), rag.get("top_k"), rag.get("max_chunks_per_document")),
    ]
    if assessment["sufficient"]:
        steps.append("Decision: ENOUGH evidence - best similarity %s >= minimum %.2f%s." % (
            assessment["best_similarity"], min_best,
            " (or a strong reranker / keyword / reference signal)" if assessment["best_similarity"] < min_best else ""))
    else:
        steps.append("Decision: NOT ENOUGH evidence - best similarity %s < minimum %.2f and no strong "
                     "reranker, keyword or reference signal. No answer would be generated." % (
                         assessment["best_similarity"], min_best))

    return {"assessment": assessment, "diagnostics": diag,
            "candidates": result.get("candidates"),
            "metrics": {"best_similarity": assessment.get("best_similarity"),
                        "best_relevance": assessment.get("best_relevance"),
                        "best_keyword_coverage": probe.get("best_keyword"),
                        "similarity_meaning": "cosine similarity between question and passage; a distance, not a probability",
                        "relevance_meaning": "cross-encoder score 0-1; independent of the similarity"},
            "decision_steps": steps,
            "auto_route": {"mode": route["mode"], "probe": route["probe"],
                           "reasons": route["reasons"], "signals": route["signals"],
                           "probe_result": probe},
            "scope_used": scope,
            "settings_used": {
                k: rag.get(k) for k in ("top_k", "candidate_pool", "similarity_threshold",
                                        "min_best_similarity", "rerank_enabled",
                                        "max_chunks_per_document", "probe_min_relevance",
                                        "probe_min_keyword_coverage")}}


# ------------------------------------------------------------- plugins/MCP

@router.get("/mcp")
def list_mcp(user=Depends(require_permission("settings", "view"))):
    return {"integrations": runtime.get_mcp().list()}


@router.post("/mcp")
def create_mcp(payload: Dict[str, Any] = Body(...),
               user=Depends(require_permission("settings", "create"))):
    return _guard(lambda: runtime.get_mcp().create(payload, _name(user)))


@router.put("/mcp/{integration_id}")
def update_mcp(integration_id: int, payload: Dict[str, Any] = Body(...),
               user=Depends(require_permission("settings", "edit"))):
    return _guard(lambda: runtime.get_mcp().update(integration_id, payload, _name(user)))


@router.delete("/mcp/{integration_id}")
def delete_mcp(integration_id: int,
               user=Depends(require_permission("settings", "delete"))):
    runtime.get_mcp().delete(integration_id, _name(user))
    return {"status": "deleted"}


@router.post("/mcp/{integration_id}/test")
def test_mcp(integration_id: int,
             user=Depends(require_permission("settings", "execute"))):
    return _guard(lambda: runtime.get_mcp().test(integration_id, _name(user)))


@router.post("/mcp/{integration_id}/call")
def call_mcp(integration_id: int, payload: Dict[str, Any] = Body(...),
             user=Depends(require_permission("settings", "execute"))):
    """Manual tool run from Settings. Write tools need allow_write + confirm."""
    return _guard(lambda: runtime.get_mcp().call(
        integration_id, payload.get("tool") or "", payload.get("arguments") or {},
        _name(user), "user", confirm=bool(payload.get("confirm"))))


@router.get("/mcp/calls/recent")
def recent_calls(limit: int = 100,
                 user=Depends(require_permission("settings", "view"))):
    return {"calls": _svc().store.list_tool_calls(max(1, min(int(limit), 500)))}
