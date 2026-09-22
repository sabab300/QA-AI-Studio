"""Dashboard + Reports & Analytics endpoints backed by the application's
existing SQLite data (Requirement 9 — see Core/dashboard_repository.py
for what each field means and how it's computed; nothing here is a
placeholder/demo value)."""

from fastapi import APIRouter, Depends, Query

from Core.dashboard_repository import DashboardRepository
from Web.deps import require_permission


router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])


@router.get("/snapshot")
def dashboard_snapshot(
    recent_limit: int = Query(20, ge=1, le=100),
    activity_limit: int = Query(20, ge=1, le=100),
    trend_days: int = Query(14, ge=1, le=90),
    current_user=Depends(require_permission("dashboard", "view")),
):
    """Return all data necessary for the Dashboard's five tabs
    (Overview, Test Execution, Knowledge Coverage, Recent Runs,
    Recent Activity) in one call."""
    return DashboardRepository().snapshot(recent_limit, activity_limit, trend_days)


@router.get("/filters")
def dashboard_filters(
    current_user=Depends(require_permission("dashboard", "view")),
):
    """Requirement 9 — Reports & Analytics' cascading filter option
    lists (Domain/Module/Knowledge Name/Version/Automation Type/
    Result), reusing the same shared hierarchy service every other
    screen uses rather than a dashboard-only hierarchy query."""
    return DashboardRepository().filters()
