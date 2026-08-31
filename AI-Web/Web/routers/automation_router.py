# Create: AI/Web/routers/automation_router.py

"""
QA AI Studio — Web
QA Automation Router (Milestone 3, partial)

Version: 1.0

Replaces the "/api/automation/status" placeholder in
placeholder_routers.py. Backed by Core/automation_web_repository.py.

Covers: API Collections (Postman import/browse/edit — grounds AI
script generation the same way it does on the desktop), Git
Automation (status/commit/pull/push/merge/compare/conflicts), Test
Case management (list/import from Excel/status), AI-generated script
creation, and Execute as a background job (start + poll, since a
real browser run can take up to ~2 minutes).

NOT covered (see Core/automation_web_repository.py's module
docstring): the Playwright codegen recorder and the interactive
locator-repair run both need a live, human-drivable browser window —
a WebSocket-based remote-control bridge, not a REST wrapper. Building
those without that bridge would just silently not work, so they're
left out rather than faked.
"""

from typing import List, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel

from Core.automation_web_repository import ApiCollectionsWeb, GitAutomationWeb, TestCasesWeb
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
# Test Cases + Execute
# ============================================================


class ImportExcelForm(BaseModel):
    pass


class SetStatusRequest(BaseModel):
    status: str


class GenerateAutomationRequest(BaseModel):
    automation_type: str
    domain: str
    module: str
    knowledge_name: str
    version: Optional[str] = None


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


@router.post("/test-cases/{test_case_id}/execute")
def start_execution(test_case_id: int, current_user=Depends(require_permission("automation", "execute"))):

    try:

        job = TestCasesWeb().start_execution(test_case_id)

    except ValueError as error:

        raise HTTPException(status_code=400, detail=str(error))

    _audit(current_user, "EXECUTE_TEST_CASE", f"Started execution job {job['job_id']} for test_case_id={test_case_id}")

    return job


@router.get("/runs/{job_id}")
def get_run(job_id: str, current_user=Depends(require_permission("automation", "view"))):

    job = TestCasesWeb().get_job(job_id)

    if job is None:

        raise HTTPException(status_code=404, detail="Run not found.")

    return job
