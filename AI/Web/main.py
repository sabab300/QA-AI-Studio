# Create: AI/Web/main.py

"""
QA AI Studio — Web
FastAPI Application Entry Point

Version: 1.0

Run locally with:
    uvicorn Web.main:app --reload --port 8000

This is the very first piece of the desktop-to-web migration: an
API server that reuses the existing Core/Database business logic
(nothing in Core/ or Database/ changes to support this) behind
HTTP endpoints instead of PySide6 widgets calling those same
classes in-process. Milestone 6 (User Management: login, roles,
permissions, audit logs) is fully wired below since every other
milestone's web endpoints will sit behind require_permission(...)
from Web/deps.py. Milestones 1-5 are mounted as placeholder routers
(Web/routers/placeholder_routers.py) so the full 7-menu shell is
reachable today; replace one placeholder at a time as each
milestone is actually built, without touching this file's overall
shape.

CORS is wide open below for local development against the redesign
mockup / a dev frontend server on a different port. Before this is
reachable by anyone other than a single developer on localhost,
CORSMiddleware's allow_origins must be locked down to the real
frontend origin(s) — flagged again in the roadmap document.
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from Core.user_repository import UserRepository
from Web.routers.auth_router import router as auth_router
from Web.routers.users_router import (
    router as users_router,
    roles_router,
    audit_router,
)
from Web.routers.placeholder_routers import (
    knowledge_router,
    qa_engineering_router,
    automation_router,
    ai_assistant_router,
    dashboard_router,
    settings_router,
)

app = FastAPI(
    title="QA AI Studio API",
    version="1.0.0",
    description=(
        "Web API for QA AI Studio — Knowledge Hub, QA Engineering, "
        "QA Automation, AI Assistant, Dashboard, Settings and User "
        "Management, backed by the same Core/Database layer as the "
        "desktop application."
    ),
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # TODO: restrict to real frontend origin(s) before shared/production use
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def on_startup():

    # Constructing UserRepository() runs ensure_schema(), which
    # creates the users/roles/role_permissions/audit_logs tables
    # and seeds the default Admin/QA Engineer/Viewer roles plus a
    # first admin account on a genuinely first run only.
    UserRepository()


@app.get("/api/health")
def health():

    return {"status": "ok", "service": "qa-ai-studio-api"}


app.include_router(auth_router)
app.include_router(users_router)
app.include_router(roles_router)
app.include_router(audit_router)
app.include_router(knowledge_router)
app.include_router(qa_engineering_router)
app.include_router(automation_router)
app.include_router(ai_assistant_router)
app.include_router(dashboard_router)
app.include_router(settings_router)