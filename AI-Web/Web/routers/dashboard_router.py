"""Dashboard endpoints backed by the application's existing SQLite data."""

from fastapi import APIRouter, Depends, Query

from Core.dashboard_repository import DashboardRepository
from Web.deps import require_permission


router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])


@router.get("/snapshot")
def dashboard_snapshot(
    recent_limit: int = Query(20, ge=1, le=100),
    activity_limit: int = Query(20, ge=1, le=100),
    current_user=Depends(require_permission("dashboard", "view")),
):
    """Return all data necessary for the initial dashboard render."""
    return DashboardRepository().snapshot(recent_limit, activity_limit)
