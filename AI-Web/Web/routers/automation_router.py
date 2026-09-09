# Create: AI/Web/routers/automation_router.py

"""
QA AI Studio — Web
QA Automation Router

Version: 2.0

Backed by Core/automation_web_repository.py. Covers: API Collections
(Postman import/browse/edit — grounds AI script generation the same
way it does on the desktop), Test Environment Settings (execution
configuration — Base URL, timeouts, API auth — previously not exposed
to the web port at all), the Automation Workspace (a cross-scope
landing page with each asset's latest run attached), Test Case /
script management (view/edit/save/validate/AI-suggest-type),
AI-generated script creation, real Execute for both Playwright
(background job — a real browser run can take up to ~2 minutes — with
persisted history, genuine Cancel, and Re-run) and API (a real HTTP
request against the actual imported endpoint, synchronous since it's
fast), Execution History, and Git Automation
(status/commit/pull/push/merge/compare/conflicts).

NOT covered (see Core/automation_web_repository.py's module
docstring): the Playwright codegen recorder and the interactive
locator-repair run both need a live, human-drivable browser window —
a WebSocket-based remote-control bridge, not a REST wrapper. Building
those without that bridge would just silently not work, so they're
left out rather than faked.
"""

from typing import Any, Dict, List, Literal, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel

from Core.automation_web_repository import (
    ApiCollectionsWeb,
    EnvironmentConfigWeb,
    GitAutomationWeb,
    TestCasesWeb,
    SqlAutomationWeb,
)
from Core.user_repository import UserRepository
from Web.deps import require_permission

router = APIRouter(prefix="/api/automation", tags=["automation"])


def _audit(current_user, action, detail):

    UserRepository().write_audit_log(
        user_id=current_user["id"],
        username=current_user["username"],
        action=action,
        resource="automation",
        detail=detail,
    )


def _value_error_to_400(fn, *args, **kwargs):

    try:

        return fn(*args, **kwargs)

    except ValueError as error:

        raise HTTPException(status_code=400, detail=str(error))


# ============================================================
# API Collections
# ============================================================


class UpdateEndpointRequest(BaseModel):
    method: Optional[str] = None
    name: Optional[str] = None
    url_raw: Optional[str] = None
    url_resolved: Optional[str] = None
    headers: Optional[dict] = None
    body_mode: Optional[str] = None
    body_raw: Optional[str] = None


@router.post("/api-collections/import")
def import_api_collection(
    file: UploadFile = File(...),
    domain: Optional[str] = Form(None),
    module: Optional[str] = Form(None),
    knowledge_name: Optional[str] = Form(None),
    version: Optional[str] = Form(None),
    current_user=Depends(require_permission("automation", "create")),
):

    file_bytes = file.file.read()

    try:

        result = ApiCollectionsWeb().import_collection(
            file_bytes=file_bytes, original_filename=file.filename,
            domain=domain, module=module, knowledge_name=knowledge_name,
            version=version,
        )

    except ValueError as error:

        raise HTTPException(status_code=400, detail=str(error))

    _audit(current_user, "IMPORT_API_COLLECTION", f"Imported '{file.filename}' ({result.get('endpoints_saved', 0)} endpoints)")

    return result


@router.get("/api-collections")
def list_api_collections(current_user=Depends(require_permission("automation", "view"))):

    return {"collections": ApiCollectionsWeb().list_collections()}


@router.get("/api-collections/{collection_id}")
def get_api_collection(collection_id: int, current_user=Depends(require_permission("automation", "view"))):

    collection = ApiCollectionsWeb().get_collection(collection_id)

    if collection is None:

        raise HTTPException(status_code=404, detail="Collection not found.")

    return collection


@router.get("/api-endpoints/{endpoint_id}")
def get_api_endpoint(endpoint_id: int, current_user=Depends(require_permission("automation", "view"))):

    endpoint = ApiCollectionsWeb().get_endpoint(endpoint_id)

    if endpoint is None:

        raise HTTPException(status_code=404, detail="Endpoint not found.")

    return endpoint


@router.patch("/api-endpoints/{endpoint_id}")
def update_api_endpoint(
    endpoint_id: int,
    payload: UpdateEndpointRequest,
    current_user=Depends(require_permission("automation", "edit")),
):

    fields = {k: v for k, v in payload.dict().items() if v is not None}

    if not fields:

        raise HTTPException(status_code=400, detail="Nothing to update.")

    try:

        updated = ApiCollectionsWeb().update_endpoint(endpoint_id, **fields)

    except ValueError as error:

        raise HTTPException(status_code=400, detail=str(error))

    if updated is None:

        raise HTTPException(status_code=404, detail="Endpoint not found.")

    _audit(current_user, "UPDATE_API_ENDPOINT", f"Updated endpoint id={endpoint_id}")

    return updated


# ============================================================
# Test Environment Settings (execution configuration)
# ============================================================


class EnvironmentUpdateRequest(BaseModel):
    base_url: Optional[str] = None
    username: Optional[str] = None
    password: Optional[str] = None
    notes: Optional[str] = None
    slow_mo_ms: Optional[str] = None
    default_timeout_ms: Optional[str] = None
    api_auth_type: Optional[str] = None
    api_auth_token: Optional[str] = None
    api_auth_header_name: Optional[str] = None
    api_auth_header_value: Optional[str] = None
    api_username: Optional[str] = None
    api_password: Optional[str] = None
    api_extra_headers: Optional[Dict[str, str]] = None
    api_timeout_seconds: Optional[str] = None
    api_verify_ssl: Optional[bool] = None
    api_base_url_override: Optional[str] = None


class RememberVariablesRequest(BaseModel):
    variables: Dict[str, str]


@router.get("/environment")
def get_environment(current_user=Depends(require_permission("automation", "view"))):

    return EnvironmentConfigWeb().get()


@router.put("/environment")
def update_environment(
    payload: EnvironmentUpdateRequest,
    current_user=Depends(require_permission("automation", "edit")),
):

    fields = payload.dict(exclude_unset=True)

    updated = EnvironmentConfigWeb().update(fields)

    _audit(current_user, "UPDATE_TEST_ENVIRONMENT", "Updated Test Environment Settings")

    return updated


@router.post("/environment/variables")
def remember_variables(
    payload: RememberVariablesRequest,
    current_user=Depends(require_permission("automation", "edit")),
):

    return EnvironmentConfigWeb().remember_variables(payload.variables)


# ============================================================
# Automation Workspace (cross-scope landing page)
# ============================================================


@router.get("/workspace")
def workspace(
    domain: Optional[str] = None,
    module: Optional[str] = None,
    knowledge_name: Optional[str] = None,
    version: Optional[str] = None,
    document_type: Optional[str] = None,
    source_knowledge_ids: Optional[List[int]] = Query(None),
    test_case_document: Optional[str] = None,
    status: Optional[str] = None,
    automation_type: Optional[str] = None,
    q: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
    current_user=Depends(require_permission("automation", "view")),
):

    return TestCasesWeb().list_workspace(
        domain=domain, module=module, knowledge_name=knowledge_name,
        version=version, document_type=document_type,
        source_knowledge_ids=source_knowledge_ids, test_case_document=test_case_document,
        status=status, automation_type=automation_type, q=q,
        limit=min(max(limit, 1), 200), offset=max(offset, 0),
    )


@router.get("/scope")
def scope(current_user=Depends(require_permission("automation", "view"))):
    return TestCasesWeb().list_scopes()


# ============================================================
# Test Cases + Scripts
# ============================================================


class ImportExcelForm(BaseModel):
    pass


class SetStatusRequest(BaseModel):
    status: str


class RecordResultRequest(BaseModel):
    result: Literal["Pass", "Fail", "Blocked"]


class GenerateAutomationRequest(BaseModel):
    automation_type: str
    domain: str
    module: str
    knowledge_name: str
    version: Optional[str] = None


class GenerateFromCollectionRequest(BaseModel):
    domain: str
    module: str
    knowledge_name: str
    version: Optional[str] = None
    automation_type: str = "API"
    endpoint_ids: Optional[List[int]] = None


class UpdateScriptRequest(BaseModel):
    script_text: str


class ActiveScriptRequest(BaseModel):
    source: Literal["AUTO", "MANUAL"]


class ExecuteApiRequest(BaseModel):
    endpoint_id: Optional[int] = None


@router.get("/test-cases")
def list_test_cases(
    domain: str, module: str, knowledge_name: str, automation_type: Optional[str] = None,
    current_user=Depends(require_permission("automation", "view")),
):

    return {"test_cases": TestCasesWeb().list_test_cases(domain, module, knowledge_name, automation_type)}


@router.get("/test-cases/sample-template")
def sample_template(current_user=Depends(require_permission("automation", "view"))):
    """
    IMPORTANT: registered BEFORE '/test-cases/{test_case_id}' —
    FastAPI matches path routes in registration order, and
    'sample-template' would otherwise match that int-typed path
    param first and fail with a 422 before ever reaching this route.

    Excel column headers/order match TestExecutionManager's OWN
    `_IMPORT_FIELD_ALIASES` exactly (the first alias for each field) —
    generated from that same source rather than hand-typed separately,
    so this can never silently drift out of sync with what the
    importer actually accepts.
    """

    import io

    from openpyxl import Workbook

    from Core.test_execution_manager import TestExecutionManager

    aliases = TestExecutionManager._IMPORT_FIELD_ALIASES

    header_labels = {
        "scenario": "Scenario",
        "importance": "Importance",
        "test_type": "Test Type",
        "test_case": "Test Case",
        "pre_conditions": "Pre-Conditions",
        "steps": "Steps",
        "expected_result": "Expected Result",
        "execution_type": "Execution Type",
        "execution_tool": "Execution Tool",
    }

    headers = [header_labels[field] for field in aliases.keys()]

    example_row = [
        "Login with valid credentials",
        "High",
        "Functional",
        "Verify a user can log in with a valid username and password",
        "User account exists and is active",
        "1. Open the login page\n2. Enter valid username/password\n3. Click Login",
        "User is redirected to the dashboard and sees their name in the header",
        "Automatable",
        "Playwright",
    ]

    workbook = Workbook()

    sheet = workbook.active

    sheet.title = "Test Cases"

    sheet.append(headers)

    sheet.append(example_row)

    for col_index in range(1, len(headers) + 1):

        sheet.column_dimensions[sheet.cell(row=1, column=col_index).column_letter].width = 28

    buffer = io.BytesIO()

    workbook.save(buffer)

    buffer.seek(0)

    from fastapi.responses import StreamingResponse

    return StreamingResponse(
        buffer,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=qa_automation_test_case_template.xlsx"},
    )


@router.get("/test-cases/{test_case_id}")
def get_test_case(test_case_id: int, current_user=Depends(require_permission("automation", "view"))):

    test_case = TestCasesWeb().get_test_case(test_case_id)

    if test_case is None:

        raise HTTPException(status_code=404, detail="Test case not found.")

    return test_case


@router.delete("/test-cases/{test_case_id}")
def delete_test_case(test_case_id: int, current_user=Depends(require_permission("automation", "delete"))):

    deleted = TestCasesWeb().delete_test_case(test_case_id)

    if not deleted:

        raise HTTPException(status_code=404, detail="Test case not found.")

    _audit(current_user, "DELETE_TEST_CASE", f"Deleted test_case_id={test_case_id}")

    return {"deleted": True}


@router.post("/test-cases/import")
def import_test_cases(
    file: UploadFile = File(...),
    domain: str = Form(...),
    module: str = Form(...),
    knowledge_name: str = Form(...),
    version: Optional[str] = Form(None),
    document_type: str = Form(...),
    source_knowledge_ids: List[int] = Form(...),
    current_user=Depends(require_permission("automation", "create")),
):

    file_bytes = file.file.read()

    try:

        result = TestCasesWeb().import_from_excel(
            file_bytes, file.filename, domain, module, knowledge_name, version,
            document_type, source_knowledge_ids,
        )

    except ValueError as error:

        raise HTTPException(status_code=400, detail=str(error))

    _audit(current_user, "IMPORT_TEST_CASES", f"Imported {result.get('imported', 0)} test case(s) from '{file.filename}'")

    return result


@router.patch("/test-cases/{test_case_id}/status")
def set_test_case_status(
    test_case_id: int, payload: SetStatusRequest,
    current_user=Depends(require_permission("automation", "edit")),
):

    updated = TestCasesWeb().set_status(test_case_id, payload.status)

    if updated is None:

        raise HTTPException(status_code=404, detail="Test case not found.")

    return updated


@router.post("/test-cases/{test_case_id}/result")
def record_result(
    test_case_id: int, payload: RecordResultRequest,
    current_user=Depends(require_permission("automation", "edit")),
):

    try:

        updated = TestCasesWeb().record_result(test_case_id, payload.result)

    except ValueError as error:

        raise HTTPException(status_code=400, detail=str(error))

    _audit(current_user, "RECORD_TEST_RESULT", f"test_case_id={test_case_id} -> {payload.result}")

    return updated


@router.get("/test-cases/{test_case_id}/validate")
def validate_test_case(
    test_case_id: int,
    source: Optional[str] = Query(
        None,
        description=(
            "Optional — 'AUTO' or 'MANUAL'. When given, validates that "
            "specific saved script source (the Validate button's "
            "'validate selected source' behavior, item 6) instead of "
            "the currently Active one (the default 'execute readiness' "
            "behavior used by the Execute tab / Execute Selected, item "
            "8)."
        ),
    ),
    current_user=Depends(require_permission("automation", "view")),
):

    try:

        return TestCasesWeb().validate_for_execution(test_case_id, source=source)

    except ValueError as error:

        raise HTTPException(status_code=404, detail=str(error))


@router.post("/test-cases/{test_case_id}/suggest-type")
def suggest_type(test_case_id: int, current_user=Depends(require_permission("automation", "create"))):

    try:

        return TestCasesWeb().suggest_type(test_case_id)

    except ValueError as error:

        raise HTTPException(status_code=400, detail=str(error))

    except RuntimeError as error:

        raise HTTPException(status_code=422, detail=str(error))


@router.put("/test-cases/{test_case_id}/script")
def update_script(
    test_case_id: int, payload: UpdateScriptRequest,
    current_user=Depends(require_permission("automation", "edit")),
):

    try:

        return TestCasesWeb().update_script(test_case_id, payload.script_text)

    except ValueError as error:

        raise HTTPException(status_code=404, detail=str(error))


@router.put("/test-cases/{test_case_id}/recorded-script")
def update_recorded_script(
    test_case_id: int, payload: UpdateScriptRequest,
    current_user=Depends(require_permission("automation", "edit")),
):

    return TestCasesWeb().update_recorded_script(test_case_id, payload.script_text)


@router.patch("/test-cases/{test_case_id}/active-script")
def set_active_script(
    test_case_id: int, payload: ActiveScriptRequest,
    current_user=Depends(require_permission("automation", "edit")),
):

    # QA-AUTOMATION-FINAL-ARCHITECTURE-04 hidden-bug fix: this
    # endpoint had no try/except at all, unlike every sibling endpoint
    # in this router — set_active_script() now legitimately raises
    # ValueError (invalid test case, or a syntactically broken script
    # being rejected for Active), which would otherwise surface as an
    # unhandled 500 instead of a clean 400 with a real message.
    try:

        return TestCasesWeb().set_active_script(test_case_id, payload.source)

    except ValueError as error:

        raise HTTPException(status_code=400, detail=str(error))


@router.post("/test-cases/{test_case_id}/generate-automation")
def generate_automation(
    test_case_id: int, payload: GenerateAutomationRequest,
    current_user=Depends(require_permission("automation", "create")),
):

    try:

        result = TestCasesWeb().generate_automation(
            test_case_id, payload.automation_type, payload.domain,
            payload.module, payload.knowledge_name, payload.version,
        )

    except ValueError as error:

        raise HTTPException(status_code=400, detail=str(error))

    except RuntimeError as error:

        # generate_automation() raises this when the AI's own output
        # can't be repaired into valid Python after retrying — a real,
        # user-facing outcome (bad/unlucky model output), not a server
        # bug, so it's a 422 with the same message the desktop app
        # would show, not an unhandled 500.
        raise HTTPException(status_code=422, detail=str(error))

    _audit(current_user, "GENERATE_AUTOMATION", f"Generated {payload.automation_type} script for test_case_id={test_case_id}")

    return result


@router.post("/generate-from-collection")
def generate_from_collection(
    payload: GenerateFromCollectionRequest,
    current_user=Depends(require_permission("automation", "create")),
):

    result = TestCasesWeb().generate_from_collection(
        payload.domain, payload.module, payload.knowledge_name,
        payload.version, payload.automation_type, payload.endpoint_ids,
    )

    if not result.get("success"):

        raise HTTPException(status_code=422, detail=result.get("error") or "Generation failed for every endpoint.")

    _audit(
        current_user, "GENERATE_AUTOMATION_FROM_COLLECTION",
        f"{payload.domain}/{payload.module}/{payload.knowledge_name}: "
        f"generated {result.get('generated', 0)}, skipped {result.get('skipped', 0)}",
    )

    return result


# ============================================================
# Execute — Playwright (background job) + API (real HTTP, synchronous)
# ============================================================


@router.post("/test-cases/{test_case_id}/execute")
def start_execution(test_case_id: int, current_user=Depends(require_permission("automation", "execute"))):

    try:

        job = TestCasesWeb().start_execution(
            test_case_id, executed_by_user_id=current_user["id"],
            executed_by_username=current_user["username"],
        )

    except ValueError as error:

        raise HTTPException(status_code=400, detail=str(error))

    _audit(current_user, "EXECUTE_TEST_CASE", f"Started execution job {job['job_id']} for test_case_id={test_case_id}")

    return job


@router.post("/test-cases/{test_case_id}/execute-api")
def execute_api(
    test_case_id: int, payload: Optional[ExecuteApiRequest] = None,
    current_user=Depends(require_permission("automation", "execute")),
):

    try:

        result = TestCasesWeb().execute_api(
            test_case_id, endpoint_id=payload.endpoint_id if payload else None,
            executed_by_user_id=current_user["id"],
            executed_by_username=current_user["username"],
        )

    except ValueError as error:

        raise HTTPException(status_code=400, detail=str(error))

    _audit(
        current_user, "EXECUTE_API_TEST_CASE",
        f"test_case_id={test_case_id}, executed={result.get('executed')}",
    )

    return result


@router.get("/runs")
def list_runs(
    domain: Optional[str] = None,
    module: Optional[str] = None,
    knowledge_name: Optional[str] = None,
    version: Optional[str] = None,
    document_type: Optional[str] = None,
    source_knowledge_ids: Optional[List[int]] = Query(None),
    test_case_document: Optional[str] = None,
    test_case_id: Optional[int] = None,
    status: Optional[str] = None,
    automation_type: Optional[str] = None,
    q: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
    current_user=Depends(require_permission("automation", "view")),
):

    return TestCasesWeb().list_runs(
        domain=domain, module=module, knowledge_name=knowledge_name,
        version=version, document_type=document_type,
        source_knowledge_ids=source_knowledge_ids, test_case_document=test_case_document,
        test_case_id=test_case_id, status=status, q=q,
        automation_type=automation_type,
        limit=min(max(limit, 1), 200), offset=max(offset, 0),
    )


@router.get("/runs/{run_id}")
def get_run(run_id: str, current_user=Depends(require_permission("automation", "view"))):

    job = TestCasesWeb().get_job(run_id)

    if job is None:

        raise HTTPException(status_code=404, detail="Run not found.")

    return job


@router.get("/runs/{run_id}/evidence")
def get_run_evidence(run_id: str, current_user=Depends(require_permission("automation", "view"))):

    path = TestCasesWeb().get_evidence_path(run_id)

    if path is None:

        raise HTTPException(status_code=404, detail="No evidence available for this run.")

    return FileResponse(str(path), media_type="image/png")


@router.post("/runs/{run_id}/cancel")
def cancel_run(run_id: str, current_user=Depends(require_permission("automation", "execute"))):

    cancelled = TestCasesWeb().cancel_execution(run_id)

    if not cancelled:

        raise HTTPException(
            status_code=409,
            detail=(
                "Nothing to cancel — this run has already finished, or "
                "the server restarted since it started."
            ),
        )

    _audit(current_user, "CANCEL_EXECUTION", f"run_uuid={run_id}")

    return {"cancelled": True}


@router.post("/runs/{run_id}/rerun")
def rerun_run(run_id: str, current_user=Depends(require_permission("automation", "execute"))):

    try:

        job = TestCasesWeb().rerun(
            run_id, executed_by_user_id=current_user["id"],
            executed_by_username=current_user["username"],
        )

    except ValueError as error:

        raise HTTPException(status_code=404, detail=str(error))

    _audit(current_user, "RERUN_EXECUTION", f"re_run_of={run_id} -> job {job['job_id']}")

    return job


# ============================================================
# Git Automation
# ============================================================


class GitRepoRequest(BaseModel):
    remote_url: str
    branch: str = "main"
    username: str = ""
    token: str = ""


class GitCommitRequest(GitRepoRequest):
    files: List[str]
    message: str


class GitMergeRequest(GitRepoRequest):
    source_branch: str


class GitCompareRequest(GitRepoRequest):
    ref_a: str
    ref_b: str


class GitResolveConflictRequest(GitRepoRequest):
    file_path: str
    strategy: str


@router.post("/git/open")
def git_open(payload: GitRepoRequest, current_user=Depends(require_permission("automation", "view"))):

    try:

        return GitAutomationWeb().open_or_clone(payload.remote_url, payload.branch, payload.username, payload.token)

    except Exception as error:

        raise HTTPException(status_code=400, detail=str(error))


@router.post("/git/status")
def git_status(payload: GitRepoRequest, current_user=Depends(require_permission("automation", "view"))):

    try:

        return GitAutomationWeb().status(payload.remote_url, payload.branch, payload.username, payload.token)

    except Exception as error:

        raise HTTPException(status_code=400, detail=str(error))


@router.post("/git/commit")
def git_commit(payload: GitCommitRequest, current_user=Depends(require_permission("automation", "edit"))):

    try:

        result = GitAutomationWeb().add_and_commit(
            payload.remote_url, payload.files, payload.message,
            payload.branch, payload.username, payload.token,
        )

    except Exception as error:

        raise HTTPException(status_code=400, detail=str(error))

    _audit(current_user, "GIT_COMMIT", f"Committed {len(payload.files)} file(s) to {payload.remote_url}")

    return result


@router.post("/git/pull")
def git_pull(payload: GitRepoRequest, current_user=Depends(require_permission("automation", "edit"))):

    try:

        return GitAutomationWeb().pull(payload.remote_url, payload.branch, payload.username, payload.token)

    except Exception as error:

        raise HTTPException(status_code=400, detail=str(error))


@router.post("/git/push")
def git_push(payload: GitRepoRequest, current_user=Depends(require_permission("automation", "edit"))):

    try:

        result = GitAutomationWeb().push(payload.remote_url, payload.branch, payload.username, payload.token)

    except Exception as error:

        raise HTTPException(status_code=400, detail=str(error))

    _audit(current_user, "GIT_PUSH", f"Pushed {payload.branch} to {payload.remote_url}")

    return result


@router.post("/git/merge")
def git_merge(payload: GitMergeRequest, current_user=Depends(require_permission("automation", "edit"))):

    try:

        return GitAutomationWeb().merge(
            payload.remote_url, payload.source_branch, payload.branch, payload.username, payload.token,
        )

    except Exception as error:

        raise HTTPException(status_code=400, detail=str(error))


@router.post("/git/compare")
def git_compare(payload: GitCompareRequest, current_user=Depends(require_permission("automation", "view"))):

    try:

        diff = GitAutomationWeb().compare(
            payload.remote_url, payload.ref_a, payload.ref_b, payload.branch, payload.username, payload.token,
        )

    except Exception as error:

        raise HTTPException(status_code=400, detail=str(error))

    # GitService.compare() returns a raw diff string (fine to hand a
    # PySide6 text widget directly) — wrap it in an object so the web
    # response shape is consistent with every other endpoint here.
    return {"diff": diff}


@router.post("/git/conflicts")
def git_conflicts(payload: GitRepoRequest, current_user=Depends(require_permission("automation", "view"))):

    try:

        return {"conflicts": GitAutomationWeb().list_conflicts(payload.remote_url, payload.branch, payload.username, payload.token)}

    except Exception as error:

        raise HTTPException(status_code=400, detail=str(error))


@router.post("/git/resolve-conflict")
def git_resolve_conflict(payload: GitResolveConflictRequest, current_user=Depends(require_permission("automation", "edit"))):

    try:

        return GitAutomationWeb().resolve_conflict(
            payload.remote_url, payload.file_path, payload.strategy, payload.branch, payload.username, payload.token,
        )

    except Exception as error:

        raise HTTPException(status_code=400, detail=str(error))


@router.post("/git/generate-commit-message")
def git_generate_commit_message(payload: GitRepoRequest, current_user=Depends(require_permission("automation", "edit"))):

    try:

        return GitAutomationWeb().generate_commit_message(payload.remote_url, payload.branch, payload.username, payload.token)

    except Exception as error:

        raise HTTPException(status_code=400, detail=str(error))


# ============================================================
# SQL Automation (new — QA-AUTOMATION-FINAL-ARCHITECTURE-04)
# ============================================================
# "SQL" existed as a per-row Automation Type choice before this task
# but had no runner (see Core/automation_web_repository.py's
# SqlAutomationWeb docstring). Everything here follows the same
# shape as the Playwright/API endpoints above; the one addition is
# that Execute here ALWAYS re-validates the saved SQL through the
# read-only safety gate immediately before running it (see
# SqlAutomationWeb.execute()) — never trusts a status flag alone.


class SqlEnvironmentUpdateRequest(BaseModel):
    sql_db_type: Optional[str] = None
    sql_sqlite_path: Optional[str] = None
    sql_host: Optional[str] = None
    sql_port: Optional[str] = None
    sql_database: Optional[str] = None
    sql_username: Optional[str] = None
    sql_password: Optional[str] = None
    sql_extra_params: Optional[Dict[str, str]] = None
    sql_timeout_seconds: Optional[str] = None
    sql_max_rows: Optional[str] = None
    sql_notes: Optional[str] = None


class SqlGenerateRequest(BaseModel):
    database_schema: Optional[str] = None


class SqlSaveScriptRequest(BaseModel):
    sql: str
    assertion_type: str = "row_exists"
    assertion_value: Optional[str] = None
    assertion_column: Optional[str] = None


class SqlActiveRequest(BaseModel):
    active: bool


@router.get("/sql/environment")
def get_sql_environment(current_user=Depends(require_permission("automation", "view"))):

    return SqlAutomationWeb().get_environment()


@router.put("/sql/environment")
def update_sql_environment(
    payload: SqlEnvironmentUpdateRequest,
    current_user=Depends(require_permission("automation", "edit")),
):

    fields = payload.dict(exclude_unset=True)

    updated = SqlAutomationWeb().update_environment(fields)

    _audit(current_user, "UPDATE_SQL_ENVIRONMENT", "Updated SQL Automation Test Environment Setting")

    return updated


@router.post("/sql/environment/test-connection")
def test_sql_connection(current_user=Depends(require_permission("automation", "view"))):

    return SqlAutomationWeb().test_connection()


@router.post("/test-cases/{test_case_id}/generate-sql")
def generate_sql(
    test_case_id: int, payload: SqlGenerateRequest,
    current_user=Depends(require_permission("automation", "create")),
):

    try:

        result = SqlAutomationWeb().generate_sql(test_case_id, payload.database_schema)

    except ValueError as error:

        raise HTTPException(status_code=400, detail=str(error))

    _audit(current_user, "GENERATE_SQL_AUTOMATION", f"Generated SQL draft for test_case_id={test_case_id}")

    return result


@router.put("/test-cases/{test_case_id}/sql-script")
def save_sql_script(
    test_case_id: int, payload: SqlSaveScriptRequest,
    current_user=Depends(require_permission("automation", "edit")),
):

    try:

        return SqlAutomationWeb().save_script(
            test_case_id, payload.sql, payload.assertion_type,
            payload.assertion_value, payload.assertion_column,
        )

    except ValueError as error:

        raise HTTPException(status_code=404, detail=str(error))


@router.get("/test-cases/{test_case_id}/validate-sql")
def validate_sql(test_case_id: int, current_user=Depends(require_permission("automation", "view"))):

    try:

        return SqlAutomationWeb().validate_sql(test_case_id)

    except ValueError as error:

        raise HTTPException(status_code=404, detail=str(error))


@router.patch("/test-cases/{test_case_id}/sql-active")
def set_sql_active(
    test_case_id: int, payload: SqlActiveRequest,
    current_user=Depends(require_permission("automation", "edit")),
):

    try:

        return SqlAutomationWeb().set_active(test_case_id, payload.active)

    except ValueError as error:

        raise HTTPException(status_code=422, detail=str(error))


@router.post("/test-cases/{test_case_id}/execute-sql")
def execute_sql(test_case_id: int, current_user=Depends(require_permission("automation", "execute"))):

    try:

        result = SqlAutomationWeb().execute(
            test_case_id, executed_by_user_id=current_user["id"],
            executed_by_username=current_user["username"],
        )

    except ValueError as error:

        raise HTTPException(status_code=400, detail=str(error))

    _audit(current_user, "EXECUTE_SQL_TEST_CASE", f"Executed SQL test_case_id={test_case_id}")

    return result


# ============================================================
# ClickUp — contextual "create bug for a failed test" action only
# (new — QA-AUTOMATION-FINAL-ARCHITECTURE-04). NOT a standalone tab.
# ============================================================


class ClickUpConfigRequest(BaseModel):
    clickup_api_token: Optional[str] = None
    clickup_list_id: Optional[str] = None


@router.get("/clickup/status")
def clickup_status(current_user=Depends(require_permission("automation", "view"))):
    return {
        "available": False,
        "configured": False,
        "clickup_api_token_is_set": False,
        "clickup_list_id": "",
        "message": "ClickUp integration is unavailable in this Web build.",
    }


@router.put("/clickup/config")
def update_clickup_config(
    payload: ClickUpConfigRequest,
    current_user=Depends(require_permission("automation", "edit")),
):

    raise HTTPException(
        status_code=503,
        detail="ClickUp integration is unavailable in this Web build.",
    )


@router.post("/test-cases/{test_case_id}/clickup-bug")
def create_clickup_bug(
    test_case_id: int,
    current_user=Depends(require_permission("automation", "edit")),
):
    raise HTTPException(
        status_code=503,
        detail="ClickUp integration is unavailable in this Web build.",
    )
