# Create: AI/Web/main.py

"""
QA AI Studio — Web
FastAPI Application Entry Point

Version: 1.1

Run locally with:
    python run_web.py

(run_web.py lives alongside this Web/ folder, at the AI-Web/ root.)

Do NOT run a bare `uvicorn Web.main:app --reload --port 8000` —
without restricting which folders the reload watcher looks at, it
watches this whole working directory for *.py changes, including
Output/AutomationRuns/, where Core/playwright_runner.py writes a
fresh *.py script for every single Playwright execution. uvicorn then
sees that new script as a "source change" and restarts the entire
server mid-execution, silently killing the run that just started —
which then shows up in the UI as
"Interrupted: the server restarted while this run was in progress."
even though nothing actually crashed. run_web.py fixes this by
pointing uvicorn's reload watcher only at the real source directories
(Core/, Web/, Database/, Config/) — see its own module docstring for
the full diagnosis. If you must invoke uvicorn directly, pass the
same restriction explicitly:
    uvicorn Web.main:app --reload --port 8000 --reload-dir Core --reload-dir Web --reload-dir Database --reload-dir Config

This is the very first piece of the desktop-to-web migration: an
API server that reuses the existing Core/Database business logic
(nothing in Core/ or Database/ changes to support this) behind
HTTP endpoints instead of PySide6 widgets calling those same
classes in-process. Milestone 6 (User Management: login, roles,
permissions, audit logs) is fully wired below since every other
milestone's web endpoints will sit behind require_permission(...)
from Web/deps.py. Milestone 1 (Knowledge Hub), Milestone 3 (QA
Automation — API Collections/Git/Test Cases/Execute) and Milestone 4
(AI Assistant backend) are now real routers. Milestone 2 (QA
Engineering) and Milestone 5 (Dashboard)/Settings are still mounted
as placeholder routers (Web/routers/placeholder_routers.py) so the
full 7-menu shell stays reachable; replace one placeholder at a time
as each milestone is actually built, without touching this file's
overall shape.

CORS is wide open below for local development against the redesign
mockup / a dev frontend server on a different port. Before this is
reachable by anyone other than a single developer on localhost,
CORSMiddleware's allow_origins must be locked down to the real
frontend origin(s) — flagged again in the roadmap document.
"""

from pathlib import Path
import sqlite3

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse

from Core.user_repository import UserRepository
from Core.automation_execution_repository import AutomationExecutionRepository
from Core.qa_engineering_web_repository import QaEngineeringWeb
from Core.api_collection_repository import ApiCollectionRepository
from Core.logger import Logger
from Database.db_manager import DatabaseManager
from Web.routers.auth_router import router as auth_router
from Web.routers.users_router import (
    router as users_router,
    roles_router,
    audit_router,
)
from Web.routers.knowledge_router import router as knowledge_router
from Web.routers.automation_router import router as automation_router
from Web.routers.recorder_router import router as recorder_router
from Web.routers.api_discovery_router import router as api_discovery_router
try:  # QA-AI-STUDIO-UIUX: a missing optional dependency must never stop the rest of QA AI Studio from starting
    from Web.routers.uiux_router import router as uiux_router, reconcile_on_startup as uiux_reconcile
except Exception as _uiux_error:  # noqa: BLE001
    from fastapi import APIRouter as _UiuxRouter, HTTPException as _UiuxHTTPError
    uiux_reconcile = None
    uiux_router = _UiuxRouter(prefix="/api/uiux")
    _uiux_message = ("AI UI/UX Testing could not start: " + type(_uiux_error).__name__ + ": " + str(_uiux_error)[:200] +
                     ". Run 'pip install -r requirements-web.txt' and restart QA AI Studio.")
    print("WARNING: " + _uiux_message)

    @uiux_router.api_route("/{uiux_rest_path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
    def _uiux_unavailable(uiux_rest_path: str):
        raise _UiuxHTTPError(503, _uiux_message)
from Web.routers.interactive_execution_router import (
    router as interactive_execution_router,
)
from Web.routers.ai_assistant_router import router as ai_assistant_router
from Web.routers.ai_platform_router import router as ai_platform_router  # QA-AI-STUDIO-TASK4
from Web.routers.dashboard_router import router as dashboard_router
from Web.routers.qa_engineering_router import router as qa_engineering_router
from Web.routers.publishing_router import router as publishing_router
from Web.routers.placeholder_routers import (
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


@app.exception_handler(sqlite3.IntegrityError)
async def database_conflict(request,error):
    # Expected ownership/uniqueness/dependency failures are application errors,
    # not HTML 500 pages or SQL/credential-bearing traceback text.
    return JSONResponse(status_code=409,content={'detail':'The selected hierarchy conflicts with persisted ownership or dependent records. Refresh the hierarchy and review the selection.'})


@app.exception_handler(Exception)
async def unexpected_api_error(request,error):
    Logger.get_logger().error('Unexpected API error on %s: %s',request.url.path,type(error).__name__)
    return JSONResponse(status_code=500,content={'detail':'The server could not complete this operation. No successful result is claimed; review the server diagnostics.'})


@app.on_event("startup")
def on_startup():

    # Creates domains/modules/knowledge_items/knowledge_versions/
    # knowledge_tags/embedding_queue (Milestone 1) and the discovery/
    # API-collection tables, if they don't already exist — safe to
    # run on every startup, same as the desktop app's App/main.py.
    DatabaseManager().initialize_database()
    from Core.api_discovery.repository import DiscoveryRepository
    DiscoveryRepository().reconcile_interrupted()
    try:  # QA-AI-STUDIO-UIUX: a run still active at startup was interrupted by a restart; mark it so, never resume silently
        uiux_reconcile()
    except Exception:
        pass

    # Constructing UserRepository() runs ensure_schema(), which
    # creates the users/roles/role_permissions/audit_logs tables
    # and seeds the default Admin/QA Engineer/Viewer roles plus a
    # first admin account on a genuinely first run only.
    UserRepository()

    # QA Automation: any run row still "Queued"/"Running" at this
    # exact moment is provably orphaned — no background thread from a
    # previous process can still be alive in a freshly-started
    # process. Without this, a run interrupted by a server
    # restart/crash would show as "Running" forever (see
    # Core/automation_execution_repository.py's module docstring).
    # Safe to call on every startup, same as initialize_database().
    AutomationExecutionRepository().reconcile_stale_running_on_startup()

    # Generation history is durable; jobs left running by a prior process
    # are marked interrupted instead of remaining misleadingly active.
    QaEngineeringWeb().reconcile_stale_jobs()
    # QA-AI-STUDIO-TASK6: additive ci_* tables; any analysis a previous process left running is marked interrupted.
    try:
        from Core.change_impact.repository import ChangeImpactRepository
        ChangeImpactRepository().reconcile_stale()
    except Exception:
        Logger.get_logger().exception('Change Impact schema check failed; the module is unavailable but the app continues.')

    # QA-AI-STUDIO-CIA: additive cia_* tables; any analysis a previous process left running is marked interrupted.
    try:
        from Core.cia.service import CiaService
        CiaService().reconcile_stale()
    except Exception:
        Logger.get_logger().exception('Change Impact Analysis setup failed; the module is unavailable but the app continues.')

    # QA-AI-STUDIO-FINAL-ARCHITECTURE-UI-AUTOMATION-COMPLETION-V4:
    # a prior pass (QA-AI-STUDIO-LIVE-CORRECTION-PASS-V2 item 10) added
    # a startup call here that migrated legacy API Collections into a
    # Knowledge Hub "ST-API Collection" folder. That direction is now
    # explicitly WRONG per the V4 master task — API Collections are an
    # automation asset filed under a Test Cases (TC-*) folder as
    # "API-TC-*", never a Knowledge Hub source type/folder. That old
    # migration was NEVER RUN (this review happened before any restart),
    # so no data was migrated into the wrong shape. The call is removed
    # here rather than left disabled, so a later edit can't accidentally
    # re-enable the wrong migration. See
    # ApiCollectionRepository.migrate_unresolved_legacy_collections() for
    # the corrected, TC-folder-aware replacement, wired in once the new
    # storage model lands.
    try:
        ApiCollectionRepository().migrate_unresolved_legacy_collections()
    except Exception:
        Logger.get_logger().exception(
            "API Collection legacy-association audit failed — the "
            "server is starting anyway; existing collections are left "
            "untouched either way (this step never migrates data, only "
            "marks/reports association status)."
        )


@app.get("/api/health")
def health():

    return {"status": "ok", "service": "qa-ai-studio-api"}


@app.on_event("shutdown")
def stop_api_discovery():
    from Core.api_discovery.capture import CAPTURES
    from Core.api_discovery.execution import REPLAYS
    CAPTURES.shutdown()
    REPLAYS.shutdown()


app.include_router(auth_router)
app.include_router(users_router)
app.include_router(roles_router)
app.include_router(audit_router)
app.include_router(knowledge_router)
app.include_router(qa_engineering_router)
app.include_router(automation_router)
app.include_router(recorder_router)
app.include_router(api_discovery_router)
app.include_router(uiux_router)
app.include_router(interactive_execution_router)
app.include_router(ai_assistant_router)
app.include_router(ai_platform_router)  # QA-AI-STUDIO-TASK4: Settings -> LLM
app.include_router(dashboard_router)
app.include_router(publishing_router)
from Web.routers.change_impact_router import router as change_impact_router  # QA-AI-STUDIO-TASK6
app.include_router(change_impact_router)
from Web.routers.cia_router import router as cia_router  # QA-AI-STUDIO-CIA: merged Change Impact Analysis
app.include_router(cia_router)
app.include_router(settings_router)

# The redesign frontend is deliberately served by the same local process as
# the API.  Keeping this mount last ensures every /api/... route wins first.
frontend_dir = Path(__file__).resolve().parents[1] / "Frontend"
if frontend_dir.is_dir():
    app.mount("/", StaticFiles(directory=str(frontend_dir), html=True), name="frontend")
