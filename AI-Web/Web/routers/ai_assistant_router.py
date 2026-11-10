# Create: AI/Web/routers/ai_assistant_router.py

"""
QA AI Studio — Web
AI Assistant Router (Milestone 4, backend)

Version: 1.0

Replaces the "/api/ai-assistant/status" placeholder in
placeholder_routers.py. Backed by Core/ai_assistant_web_repository.py.

session_id is keyed off the authenticated PSW user's own account
(current_user["id"]) rather than trusting a client-supplied value —
the desktop version had no user-identity concept to key off of yet
(Milestone 6 didn't exist), this web version does, so each user's AI
Assistant conversation history is naturally their own.

Requires a running Ollama server to produce real answers — see
Core/ai_assistant_web_repository.py's docstring.
"""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from Core.ai_assistant_web_repository import AiAssistantRepository
from Web.deps import require_permission

router = APIRouter(prefix="/api/ai-assistant", tags=["ai-assistant"])


class AskRequest(BaseModel):
    prompt: str
    domain: Optional[str] = None
    module: Optional[str] = None
    knowledge_name: Optional[str] = None
    version: Optional[str] = None
    document_type: Optional[str] = None
    document_version_id: Optional[int] = None
    # QA-AI-STUDIO-TASK4 (all optional; omitted = previous behaviour)
    mode: Optional[str] = None            # auto | local | global | hybrid
    model_mode: Optional[str] = None      # auto | manual
    model_provider_id: Optional[int] = None
    model_name: Optional[str] = None
    use_tools: Optional[bool] = False
    request_id: Optional[str] = None


class CancelRequest(BaseModel):
    request_id: str


# QA-AI-STUDIO-IMPACT-ANALYSIS-V1: mirrors the conceptual request shape
# given for this feature, using this project's existing naming
# convention (knowledge_name, matching AskRequest above) rather than
# introducing a differently-named field for the same concept.
class ImpactSourceScope(BaseModel):
    domain: str
    module: str
    knowledge_name: str
    version: str
    document_type: Optional[str] = None
    source_document: str


class ImpactTargetScope(BaseModel):
    domain: str
    module: Optional[str] = None


class ImpactAnalysisRequest(BaseModel):
    source: ImpactSourceScope
    target: ImpactTargetScope
    question: Optional[str] = None
    request_id: Optional[str] = None


def _session_id(current_user):

    return f"user-{current_user['id']}"


def _user_context(current_user):
    """Caller identity + resolved permissions, for per-user cache keys,
    tool RBAC and usage tracking."""

    from Core.user_repository import UserRepository

    user = dict(current_user)

    try:
        user["permissions"] = UserRepository().get_permissions_for_role(user["role_id"])
    except Exception:
        user["permissions"] = {}

    return user


@router.post("/ask")
def ask(payload: AskRequest, current_user=Depends(require_permission("ai_assistant", "view"))):

    mode = (payload.mode or "auto").lower()

    if mode not in ("auto", "local", "global", "hybrid"):
        raise HTTPException(status_code=400, detail="mode must be auto, local, global or hybrid.")

    selection = None

    if payload.model_provider_id and payload.model_name:
        selection = {"provider_id": payload.model_provider_id, "model": payload.model_name}

    request_id = (payload.request_id or "")[:64] or None

    try:
        result = AiAssistantRepository().ask(
            prompt=payload.prompt, session_id=_session_id(current_user),
            domain=payload.domain, module=payload.module,
            knowledge_name=payload.knowledge_name, version=payload.version,
            document_type=payload.document_type,document_version_id=payload.document_version_id,
            mode=mode, model_mode=payload.model_mode, model_selection=selection,
            request_id=request_id, user=_user_context(current_user),
            use_tools=bool(payload.use_tools),
        )
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error))
    return result


@router.get("/options")
def options(current_user=Depends(require_permission("ai_assistant", "view"))):
    """What the AI Assistant UI may offer: modes, enabled models and the
    current default. No URLs, keys or error details are exposed here."""

    from Core.ai_platform import runtime
    from Core.ai_platform.gateway import effective_kind

    store = runtime.get_store()
    settings = store.get_settings()
    routing = settings["routing"]
    providers = []

    # T4-013/015/018: opening the assistant loads the embedder, reranker and the local
    # model in the background, so the first question does not pay for it.
    try:
        runtime.warm_up()
    except Exception:
        pass

    for p in store.list_providers():
        if not p["enabled"]:
            continue
        models = [m for m in store.list_models(p["id"]) if m["enabled"]]
        if p["default_model"] and not any(m["model_id"] == p["default_model"] for m in models):
            models.append({"model_id": p["default_model"], "display_name": p["default_model"]})
        providers.append({
            "id": p["id"], "name": p["name"], "kind": effective_kind(p),
            "status": p["status"],
            "models": [{"id": m["model_id"], "name": m["display_name"]} for m in models],
        })

    default = {"provider_id": routing.get("default_provider_id"),
               "model": routing.get("default_model")}
    default["provider"] = next((p["name"] for p in providers if p["id"] == default["provider_id"]), None)

    tools_available = False
    try:
        tools_available = bool(settings["security"].get("ai_tools_enabled", True)) and bool(
            runtime.get_broker().catalog(
                "local", _user_context(current_user)["permissions"]))
    except Exception:
        pass

    return {
        "modes": ["auto", "local", "global", "hybrid"],
        "default_mode": routing.get("default_mode", "auto"),
        "model_mode": routing.get("model_mode", "auto"),
        "allow_manual_model_selection": routing.get("allow_manual_model_selection", True),
        "internet_search_enabled": routing.get("internet_search_enabled", True),
        "default": default, "providers": providers,
        "tools_available": tools_available,
        "enhanced_pipeline": settings["rag"].get("enhanced_pipeline", True),
    }


@router.post("/cancel")
def cancel(payload: CancelRequest, current_user=Depends(require_permission("ai_assistant", "view"))):

    from Core.ai_platform.gateway import CANCELS

    return {"cancelled": CANCELS.cancel(payload.request_id[:64], owner=current_user["id"])}


@router.get("/progress")
def progress(request_id: str, current_user=Depends(require_permission("ai_assistant", "view"))):

    from Core.ai_platform.gateway import CANCELS

    return CANCELS.progress(request_id[:64], owner=current_user["id"]) or {"stage": "unknown"}


@router.get("/history")
def history(limit: int = 10, current_user=Depends(require_permission("ai_assistant", "view"))):

    return {"history": AiAssistantRepository().history(session_id=_session_id(current_user), limit=limit)}


@router.delete("/history")
def clear_history(current_user=Depends(require_permission("ai_assistant", "view"))):

    return AiAssistantRepository().clear_history(session_id=_session_id(current_user))


# --------------------------------------------------
# QA-AI-STUDIO-IMPACT-ANALYSIS-V1
# --------------------------------------------------
#
# Same permission gate as /ask -- Impact Analysis is a new Analysis
# Type inside the existing AI Assistant page, not a separate feature
# with its own access rules. Returns the result dict as-is (HTTP 200)
# even on a controlled failure, exactly like /ask already does --
# "success"/"error_code"/"error" distinguish outcomes within the
# payload rather than via HTTP status, so the frontend's existing
# success/error-check pattern extends unchanged.

@router.post("/impact-analysis")
def impact_analysis(
    payload: ImpactAnalysisRequest,
    current_user=Depends(require_permission("ai_assistant", "view")),
):

    from Core.ai_platform.gateway import CANCELS
    request_id = (payload.request_id or "")[:64]
    event, registered = CANCELS.ensure(request_id, owner=current_user["id"]) if request_id else (None, False)
    if request_id and not registered:
        raise HTTPException(status_code=409, detail="This request ID is already active. Start a new analysis request.")

    try:

        result = AiAssistantRepository().impact_analysis(
            session_id=_session_id(current_user),
            source=payload.source.dict(),
            target=payload.target.dict(),
            question=payload.question,
            progress_callback=(lambda stage, done, total: CANCELS.stage(request_id, stage, "%d of %d" % (done, total) if total else "%d chunks read" % done)) if request_id else None,
            cancel_check=event.is_set if event else None,
        )

    except ValueError as error:

        raise HTTPException(status_code=400, detail=str(error))
    finally:
        if registered:
            CANCELS.finish(request_id)

    return result


# The existing analysis/history APIs return JSON and cannot deliver binary
# reports. This read-only export uses the same stored result and permission,
# with session ownership enforced before formatting it.
@router.get("/impact-analysis/{analysis_id}/export")
def impact_analysis_export(analysis_id: int, format: str = "xlsx", current_user=Depends(require_permission("ai_assistant", "view"))):
    from fastapi.responses import Response
    from Core.impact_analysis_repository import ImpactAnalysisRepository
    from Core.impact_analysis_exports import export_impact_analysis

    if format not in ("xlsx", "pdf"):
        raise HTTPException(status_code=400, detail="Choose Excel or PDF.")
    result = ImpactAnalysisRepository().get_for_session(analysis_id, _session_id(current_user))
    if result is None:
        raise HTTPException(status_code=404, detail="Analysis not found.")
    content, media_type = export_impact_analysis(result, format)
    return Response(content, media_type=media_type, headers={"Content-Disposition": 'attachment; filename="impact-analysis-%d.%s"' % (analysis_id, format)})


@router.get("/impact-analysis/history")
def impact_analysis_history(
    limit: int = 10,
    current_user=Depends(require_permission("ai_assistant", "view")),
):

    return {
        "history": AiAssistantRepository().impact_analysis_history(
            session_id=_session_id(current_user), limit=limit,
        )
    }
