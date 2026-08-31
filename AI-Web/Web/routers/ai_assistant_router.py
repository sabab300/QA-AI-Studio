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


def _session_id(current_user):

    return f"user-{current_user['id']}"


@router.post("/ask")
def ask(payload: AskRequest, current_user=Depends(require_permission("ai_assistant", "view"))):

    try:

        result = AiAssistantRepository().ask(
            prompt=payload.prompt, session_id=_session_id(current_user),
            domain=payload.domain, module=payload.module,
            knowledge_name=payload.knowledge_name, version=payload.version,
        )

    except ValueError as error:

        raise HTTPException(status_code=400, detail=str(error))

    return result


@router.get("/history")
def history(limit: int = 10, current_user=Depends(require_permission("ai_assistant", "view"))):

    return {"history": AiAssistantRepository().history(session_id=_session_id(current_user), limit=limit)}


@router.delete("/history")
def clear_history(current_user=Depends(require_permission("ai_assistant", "view"))):

    return AiAssistantRepository().clear_history(session_id=_session_id(current_user))
