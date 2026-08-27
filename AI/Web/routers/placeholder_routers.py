# Create: AI/Web/routers/placeholder_routers.py

"""
QA AI Studio — Web
Placeholder Routers for Milestones 1-5

Version: 1.0

These exist so the full 7-menu shell (matching the Production
Roadmap's Login/Knowledge/QA Engineering/Automation/AI
Assistant/Dashboard/Settings structure) runs end-to-end today,
with real permission checks and a real "not yet implemented"
response, rather than the frontend hitting a 404 for screens that
haven't been built yet. Each one gets replaced by its own real
router as that milestone is implemented — this file should shrink,
never grow.
"""

from fastapi import APIRouter, Depends

from Web.deps import require_permission

knowledge_router = APIRouter(prefix="/api/knowledge", tags=["knowledge"])
qa_engineering_router = APIRouter(prefix="/api/qa-engineering", tags=["qa-engineering"])
automation_router = APIRouter(prefix="/api/automation", tags=["automation"])
ai_assistant_router = APIRouter(prefix="/api/ai-assistant", tags=["ai-assistant"])
dashboard_router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])
settings_router = APIRouter(prefix="/api/settings", tags=["settings"])


def _not_implemented(milestone: str):

    return {"status": "not_implemented", "milestone": milestone}


@knowledge_router.get("/status")
def knowledge_status(current_user=Depends(require_permission("knowledge", "view"))):

    return _not_implemented("Milestone 1 - Knowledge Hub")


@qa_engineering_router.get("/status")
def qa_engineering_status(
    current_user=Depends(require_permission("qa_engineering", "view"))
):

    return _not_implemented("Milestone 2 - QA Engineering")


@automation_router.get("/status")
def automation_status(
    current_user=Depends(require_permission("automation", "view"))
):

    return _not_implemented("Milestone 3 - QA Automation")


@ai_assistant_router.get("/status")
def ai_assistant_status(
    current_user=Depends(require_permission("ai_assistant", "view"))
):

    return _not_implemented("Milestone 4 - AI Assistant")


@dashboard_router.get("/status")
def dashboard_status(
    current_user=Depends(require_permission("dashboard", "view"))
):

    return _not_implemented("Milestone 5 - Dashboard")


@settings_router.get("/status")
def settings_status(
    current_user=Depends(require_permission("settings", "view"))
):

    return _not_implemented("Settings")