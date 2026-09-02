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
Execute), Milestone 4 (AI Assistant), Milestone 5 (Dashboard), and
Milestone 2 (QA Engineering) are real routers. Settings remains a
placeholder.
"""

from fastapi import APIRouter, Depends

from Web.deps import require_permission

settings_router = APIRouter(prefix="/api/settings", tags=["settings"])


def _not_implemented(milestone: str):

    return {"status": "not_implemented", "milestone": milestone}


@settings_router.get("/status")
def settings_status(
    current_user=Depends(require_permission("settings", "view"))
):

    return _not_implemented("Settings")
