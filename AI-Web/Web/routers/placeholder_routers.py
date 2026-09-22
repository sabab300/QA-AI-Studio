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

import sys
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from Web.deps import require_permission

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
from Shared.central_repository import CentralRepository, DEFAULT_ROOT

settings_router = APIRouter(prefix="/api/settings", tags=["settings"])


class CentralRepositoryRequest(BaseModel):
    path: str
    migrate: bool = True


def _not_implemented(milestone: str):

    return {"status": "not_implemented", "milestone": milestone}


@settings_router.get("/status")
def settings_status(
    current_user=Depends(require_permission("settings", "view"))
):

    return _not_implemented("Settings")


@settings_router.get("/knowledge-repository")
def get_knowledge_repository(
    current_user=Depends(require_permission("settings", "view")),
):
    root = CentralRepository.get_repository_root()
    return {"path": str(root), "exists": root.is_dir(), "default": root == DEFAULT_ROOT.resolve()}


@settings_router.put("/knowledge-repository")
def set_knowledge_repository(
    payload: CentralRepositoryRequest,
    current_user=Depends(require_permission("settings", "edit")),
):
    try:
        return CentralRepository.configure(payload.path, migrate=payload.migrate)
    except (OSError, ValueError) as error:
        raise HTTPException(status_code=422, detail=str(error))


@settings_router.post("/knowledge-repository/browse")
def browse_knowledge_repository(
    current_user=Depends(require_permission("settings", "view")),
):
    """Open the native directory chooser for this local Windows application."""
    if sys.platform != "win32":
        raise HTTPException(status_code=501, detail="Native folder browsing is available on Windows only.")
    try:
        import tkinter as tk
        from tkinter import filedialog
        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        selected = filedialog.askdirectory(
            parent=root,
            initialdir=str(CentralRepository.get_repository_root()),
            title="Select QA AI Studio Central Repository",
            mustexist=False,
        )
        root.destroy()
        return {"cancelled": not bool(selected), "path": str(Path(selected).resolve()) if selected else ""}
    except Exception as error:
        raise HTTPException(status_code=500, detail=f"Could not open the Windows folder chooser: {error}")
