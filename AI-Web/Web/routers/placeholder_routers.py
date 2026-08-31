# Create: AI/Web/routers/placeholder_routers.py

"""
QA AI Studio — Web
Placeholder Routers for Milestones 2-5

Version: 1.1

These exist so the full 7-menu shell (matching the Production
Roadmap's Login/Knowledge/QA Engineering/Automation/AI
Assistant/Dashboard/Settings structure) runs end-to-end today,
with real permission checks and a real "not yet implemented"
response, rather than the frontend hitting a 404 for screens that
haven't been built yet. Each one gets replaced by its own real
router as that milestone is implemented — this file should shrink,
never grow.

v1.1: Milestone 1 (Knowledge Hub) is real now — see
Web/routers/knowledge_router.py — so its placeholder is removed
from here.

v1.2: Milestone 3 (QA Automation — API Collections/Git/Test Cases/
Execute) is real now — see Web/routers/automation_router.py.
Milestone 4 (AI Assistant backend) is real now — see
Web/routers/ai_assistant_router.py. Both placeholders removed from
here. QA Engineering (AI-generated test case creation) and Dashboard/
Settings remain placeholders.
"""

from fastapi import APIRouter, Depends

from Web.deps import require_permission

qa_engineering_router = APIRouter(prefix="/api/qa-engineering", tags=["qa-engineering"])
dashboard_router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])
settings_router = APIRouter(prefix="/api/settings", tags=["settings"])


def _not_implemented(milestone: str):

    return {"status": "not_implemented", "milestone": milestone}


@qa_engineering_router.get("/status")
def qa_engineering_status(
    current_user=Depends(require_permission("qa_engineering", "view"))
):

    return _not_implemented("Milestone 2 - QA Engineering")


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
