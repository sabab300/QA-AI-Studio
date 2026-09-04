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

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel

from Core.automation_web_repository import (
    ApiCollectionsWeb,
    EnvironmentConfigWeb,
    GitAutomationWeb,
    TestCasesWeb,
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
    status: Optional[str] = None,
    automation_type: Optional[str] = None,
    q: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
    current_user=Depends(require_permission("automation", "view")),
):

    return TestCasesWeb().list_workspace(
        domain=domain, module=module, knowledge_name=knowledge_name,
        status=status, automation_type=automation_type, q=q,
        limit=min(max(limit, 1), 200), offset=max(offset, 0),
    )


@router.get("/scope")
def scope(current_user=Depends(require_permission("automation", "view"))):

    return {"scopes": TestCasesWeb().list_scopes()}


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


@router.get("/test-cases")
def list_test_cases(
    domain: str, module: str, knowledge_name: str,
    current_user=Depends(require_permission("automation", "view")),
):

    return {"test_cases": TestCasesWeb().list_test_cases(domain, module, knowledge_name)}


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
    current_user=Depends(require_permission("automation", "create")),
):

    file_bytes = file.file.read()

    try:

        result = TestCasesWeb().import_from_excel(
            file_bytes, file.filename, domain, module, knowledge_name, version,
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
def validate_test_case(test_case_id: int, current_user=Depends(require_permission("automation", "view"))):

    try:

        return TestCasesWeb().validate_for_execution(test_case_id)

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

    return TestCasesWeb().set_active_script(test_case_id, payload.source)


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
def execute_api(test_case_id: int, current_user=Depends(require_permission("automation", "execute"))):

    try:

        result = TestCasesWeb().execute_api(test_case_id)

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
    test_case_id: Optional[int] = None,
    status: Optional[str] = None,
    q: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
    current_user=Depends(require_permission("automation", "view")),
):

    return TestCasesWeb().list_runs(
        domain=domain, module=module, knowledge_name=knowledge_name,
        test_case_id=test_case_id, status=status, q=q,
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
