"""QA Engineering web API: grounded generation, review, and persistence."""

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from Core.qa_engineering_export_service import QaEngineeringExportService
from Core.qa_engineering_web_repository import QaEngineeringWeb
from Core.test_case_generator import ALL_TEST_TYPES
from Core.test_case_repository import TestCaseRepository
from Core.user_repository import UserRepository
from Web.deps import require_permission


router = APIRouter(prefix="/api/qa-engineering", tags=["qa-engineering"])
OUTPUT_FORMATS = {"Excel", "Word", "PDF"}
EXPORT_FORMATS = {"xlsx", "csv", "pdf", "html"}


class GenerateRequest(BaseModel):
    domain: str = Field(min_length=1, max_length=200)
    module: str = Field(min_length=1, max_length=200)
    knowledge_name: str = Field(min_length=1, max_length=300)
    version: str | None = Field(default=None, max_length=100)
    document_type: str = Field(min_length=1, max_length=200)
    knowledge_source_ids: list[int] = Field(min_length=1)
    test_case_document_name: str | None = Field(default=None, max_length=300)
    requirement: str | None = Field(default=None, max_length=10_000)
    number_of_cases: int | Literal["all"] = "all"
    test_types: list[str] = Field(min_length=1)
    output_formats: list[str] = Field(min_length=1)


class TestCaseDraft(BaseModel):
    scenario: str = ""
    importance: str = ""
    test_type: str = ""
    test_types: list[str] = Field(default_factory=list)
    test_case: str = Field(min_length=1, max_length=1000)
    pre_conditions: str = ""
    steps: str = ""
    expected_result: str = ""
    execution_type: str = "Manual"
    execution_tool: str = ""
    source_knowledge_ids: list[int] = Field(default_factory=list)


class SaveJobRequest(BaseModel):
    rows: list[TestCaseDraft] = Field(min_length=1)


class ReviewOperation(BaseModel):
    action: Literal["insert", "update", "delete"]
    id: int | None = None
    values: TestCaseDraft | None = None


class ReviewSaveRequest(BaseModel):
    domain: str = Field(min_length=1)
    module: str = Field(min_length=1)
    knowledge_name: str = Field(min_length=1)
    version: str = Field(min_length=1)
    document_type: str = Field(min_length=1)
    source_knowledge_ids: list[int] = Field(min_length=1)
    test_case_document_name: str = ""
    reviewed_file_name: str = Field(min_length=1, max_length=220)
    operations: list[ReviewOperation] = Field(min_length=1)


class UpdateTestCaseRequest(TestCaseDraft):
    pass


class ExportRequest(BaseModel):
    domain: str = Field(min_length=1, max_length=200)
    module: str = Field(min_length=1, max_length=200)
    knowledge_name: str = Field(min_length=1, max_length=300)
    version: str | None = Field(default=None, max_length=100)
    document_type: str | None = Field(default=None, max_length=200)
    file_name: str | None = Field(default=None, max_length=220)
    format: str
    rows: list[dict] = Field(min_length=1)


def _audit(current_user, action, detail):
    UserRepository().write_audit_log(
        current_user["id"], current_user["username"], action,
        resource="qa_engineering", detail=detail,
    )


def _validate_test_types(values):
    selected = values.get("test_types") or []
    invalid = [value for value in selected if value not in ALL_TEST_TYPES]
    if invalid:
        raise HTTPException(
            status_code=422,
            detail="Unsupported Test Types: " + ", ".join(invalid),
        )
    execution_type = values.get("execution_type") or "Manual"
    execution_tool = values.get("execution_tool") or ""
    if execution_type not in {"Manual", "Automatable"}:
        raise HTTPException(status_code=422, detail="Unsupported Execution Type.")
    if execution_type == "Manual" and execution_tool:
        raise HTTPException(status_code=422, detail="Manual Test Cases cannot specify an Execution Tool.")
    allowed_tools = {item["label"] for item in TestCaseRepository().list_execution_tools()}
    if execution_tool not in ({""} | allowed_tools):
        raise HTTPException(status_code=422, detail="Unsupported Execution Tool.")


def _validate_required_case_fields(values):
    required = (
        ("Importance", "importance"), ("Execution Type", "execution_type"),
        ("Test Types", "test_types"), ("Scenario", "scenario"),
        ("Preconditions", "pre_conditions"), ("Test Case", "test_case"),
        ("Steps", "steps"), ("Expected Result", "expected_result"),
    )
    missing = [label for label, key in required if not values.get(key) or (isinstance(values.get(key), str) and not values[key].strip())]
    if missing:
        raise HTTPException(status_code=422, detail="Required: " + ", ".join(missing) + ".")


@router.get("/execution-tools")
def execution_tools(current_user=Depends(require_permission("qa_engineering", "view"))):
    return {"tools": TestCaseRepository().list_execution_tools()}


@router.get("/scope")
def scope(
    domain: str | None = None,
    module: str | None = None,
    current_user=Depends(require_permission("qa_engineering", "view")),
):
    """Return the cascading domain/module/knowledge selection data."""
    items = QaEngineeringWeb().list_scope(domain, module)
    domains = sorted({item.get("domain") for item in items if item.get("domain")})
    modules = sorted({item.get("module") for item in items if item.get("module")})
    knowledge = [
        {
            "id": item.get("id"), "knowledge_name": item.get("knowledge_name"),
            "version": item.get("version"), "domain": item.get("domain"),
            "module": item.get("module"),
            "document_type": item.get("document_type"),
            "source_type": item.get("source_type"),
            "file_name": item.get("file_name"),
        }
        for item in items
    ]
    return {"domains": domains, "modules": modules, "knowledge_items": knowledge,
            "test_types": ALL_TEST_TYPES, "output_formats": sorted(OUTPUT_FORMATS)}


@router.post("/generate")
def generate(
    payload: GenerateRequest,
    current_user=Depends(require_permission("qa_engineering", "create")),
):
    if payload.number_of_cases != "all" and not 1 <= payload.number_of_cases <= 100:
        raise HTTPException(status_code=422, detail="Number of cases must be between 1 and 100, or 'all'.")
    invalid_types = sorted(set(payload.test_types) - set(ALL_TEST_TYPES))
    invalid_formats = sorted(set(payload.output_formats) - OUTPUT_FORMATS)
    if invalid_types or invalid_formats:
        detail = []
        if invalid_types:
            detail.append("Unsupported test types: " + ", ".join(invalid_types))
        if invalid_formats:
            detail.append("Unsupported output formats: " + ", ".join(invalid_formats))
        raise HTTPException(status_code=422, detail="; ".join(detail))
    if len(payload.knowledge_source_ids) != len(set(payload.knowledge_source_ids)):
        raise HTTPException(status_code=422, detail="Duplicate Knowledge sources are not allowed.")

    matching_items = QaEngineeringWeb().list_scope(payload.domain, payload.module)
    matching_items = [
        item for item in matching_items
        if item.get("knowledge_name") == payload.knowledge_name
        and (not payload.version or item.get("version") == payload.version)
        and item.get("document_type") == payload.document_type
        and item.get("id") in set(payload.knowledge_source_ids)
    ]
    if len(matching_items) != len(set(payload.knowledge_source_ids)):
        raise HTTPException(
            status_code=422,
            detail="Select a knowledge item that exists in the selected domain and module.",
        )

    # Supports both Pydantic v1 and v2, since the project does not pin it yet.
    payload_data = payload.model_dump() if hasattr(payload, "model_dump") else payload.dict()
    job = QaEngineeringWeb().start_generation(payload_data)
    _audit(current_user, "GENERATE_TEST_CASES",
           f"Started generation for {payload.domain} / {payload.module} / {payload.knowledge_name}")
    return job


@router.get("/jobs/{job_id}")
def get_job(
    job_id: str,
    current_user=Depends(require_permission("qa_engineering", "view")),
):
    job = QaEngineeringWeb().get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Generation job not found.")
    return job


@router.get("/jobs")
def list_jobs(limit: int = 20, offset: int = 0, q: str | None = None,
              started_from: str | None = None, started_to: str | None = None,
              current_user=Depends(require_permission("qa_engineering", "view"))):
    return QaEngineeringWeb().list_jobs(limit, offset, q, started_from, started_to)


@router.post("/jobs/{job_id}/save")
def save_job(job_id: str, payload: SaveJobRequest,
             current_user=Depends(require_permission("qa_engineering", "create"))):
    rows = [row.model_dump() if hasattr(row, "model_dump") else row.dict() for row in payload.rows]
    for row in rows:
        _validate_test_types(row)
        _validate_required_case_fields(row)
    try:
        ids = QaEngineeringWeb().save_job(job_id, rows)
    except ValueError as error:
        raise HTTPException(status_code=409 if "saved" in str(error).lower() else 422, detail=str(error))
    _audit(current_user, "SAVE_GENERATED_TEST_CASES", f"Saved {len(ids)} reviewed test case(s)")
    return {"saved_ids": ids, "count": len(ids)}


@router.post("/review-save")
def review_save(payload: ReviewSaveRequest,
                current_user=Depends(require_permission("qa_engineering", "create"))):
    if len(payload.source_knowledge_ids) != len(set(payload.source_knowledge_ids)):
        raise HTTPException(status_code=422, detail="Duplicate Knowledge/source relationships are not allowed.")
    if any(operation.action == "delete" for operation in payload.operations):
        permissions = UserRepository().get_permissions_for_role(current_user["role_id"])
        if "delete" not in permissions.get("qa_engineering", set()):
            raise HTTPException(status_code=403, detail="Delete permission is required to finalize pending Test Case deletions.")
    operations = []
    for index, operation in enumerate(payload.operations, start=1):
        values = None
        if operation.values is not None:
            values = operation.values.model_dump() if hasattr(operation.values, "model_dump") else operation.values.dict()
            try:
                _validate_test_types(values)
                _validate_required_case_fields(values)
                row_sources = set(values.get("source_knowledge_ids") or [])
                selected_sources = set(payload.source_knowledge_ids)
                valid_sources = row_sources == selected_sources if operation.action == "insert" else selected_sources.issubset(row_sources)
                if not valid_sources:
                    raise HTTPException(status_code=422, detail="Selected Knowledge/source relationships do not match the reviewed hierarchy context.")
            except HTTPException as error:
                raise HTTPException(status_code=422, detail=f"Row {index}: {error.detail}")
        if operation.action != "insert" and not operation.id:
            raise HTTPException(status_code=422, detail=f"Row {index}: persisted record ID is required.")
        operations.append({"action": operation.action, "id": operation.id, "values": values or {}})
    scope = payload.model_dump(exclude={"operations", "reviewed_file_name"}) if hasattr(payload, "model_dump") else payload.dict(exclude={"operations", "reviewed_file_name"})
    try:
        repository = TestCaseRepository()
        result = repository.review_save(scope, operations)
        persisted_rows = repository.list_context_test_cases(scope)
        reviewed_file = QaEngineeringExportService().export(
            persisted_rows, "xlsx", scope, file_name=payload.reviewed_file_name,
            allow_empty=True,
        )
        repository.set_reviewed_workbook([row["id"] for row in persisted_rows], reviewed_file["relative_path"])
        result["items"] = persisted_rows
        result["reviewed_file"] = reviewed_file
    except (OSError, RuntimeError, ValueError) as error:
        raise HTTPException(status_code=422, detail=str(error))
    _audit(current_user, "REVIEW_SAVE_TEST_CASES", f"Applied {len(operations)} selected Test Case change(s)")
    return result


@router.get("/test-cases")
def list_test_cases(domain: str | None = None, module: str | None = None,
                    knowledge_name: str | None = None, version: str | None = None,
                    q: str | None = None,
                    current_user=Depends(require_permission("qa_engineering", "view"))):
    return TestCaseRepository().list_all_test_cases(
        domain=domain, module=module, knowledge_name=knowledge_name, version=version, q=q,
    )


@router.get("/test-cases/{test_case_id}")
def get_test_case(test_case_id: int,
                  current_user=Depends(require_permission("qa_engineering", "view"))):
    item = TestCaseRepository().get_test_case(test_case_id)
    if not item:
        raise HTTPException(status_code=404, detail="Test case not found.")
    return item


@router.put("/test-cases/{test_case_id}")
def update_test_case(test_case_id: int, payload: UpdateTestCaseRequest,
                     current_user=Depends(require_permission("qa_engineering", "edit"))):
    values = payload.model_dump() if hasattr(payload, "model_dump") else payload.dict()
    _validate_test_types(values)
    _validate_required_case_fields(values)
    item = TestCaseRepository().update_test_case(test_case_id, values)
    if not item:
        raise HTTPException(status_code=404, detail="Test case not found.")
    _audit(current_user, "UPDATE_TEST_CASE", f"Updated test case id={test_case_id}")
    return item


@router.delete("/test-cases/{test_case_id}")
def delete_test_case(test_case_id: int,
                     current_user=Depends(require_permission("qa_engineering", "delete"))):
    if not TestCaseRepository().delete_test_case(test_case_id):
        raise HTTPException(status_code=404, detail="Test case not found.")
    _audit(current_user, "DELETE_TEST_CASE", f"Deleted test case id={test_case_id}")
    return {"status": "deleted", "id": test_case_id}


@router.get("/exports/config")
def get_export_config(current_user=Depends(require_permission("qa_engineering", "view"))):
    service = QaEngineeringExportService()
    return {"path": str(service.get_location()), "locations": service.list_locations(),
            "formats": sorted(EXPORT_FORMATS)}


@router.post("/exports")
def create_export(payload: ExportRequest,
                  current_user=Depends(require_permission("qa_engineering", "view"))):
    fmt = payload.format.lower()
    if fmt not in EXPORT_FORMATS:
        raise HTTPException(status_code=422, detail="Unsupported export format.")
    rows = payload.rows
    for index, row in enumerate(rows, start=1):
        try:
            _validate_test_types(row)
            _validate_required_case_fields(row)
        except HTTPException as error:
            raise HTTPException(status_code=422, detail=f"Row {index}: {error.detail}")
    try:
        exported = QaEngineeringExportService().export(
            rows, fmt,
            {"domain": payload.domain, "module": payload.module,
             "knowledge_name": payload.knowledge_name, "version": payload.version,
             "document_type": payload.document_type},
            file_name=payload.file_name,
        )
    except (OSError, RuntimeError, ValueError) as error:
        raise HTTPException(status_code=422, detail=str(error))
    _audit(current_user, "EXPORT_TEST_CASES", f"Exported {exported['count']} test cases as {fmt}")
    return exported


@router.get("/exports/download/file")
def download_export_relative(relative_path: str,
                             current_user=Depends(require_permission("qa_engineering", "view"))):
    try:
        path = QaEngineeringExportService().resolve_relative_download(relative_path)
    except ValueError as error:
        raise HTTPException(status_code=404, detail=str(error))
    return FileResponse(path, filename=path.name)


@router.get("/exports/{file_name}")
def download_export(file_name: str,
                    current_user=Depends(require_permission("qa_engineering", "view"))):
    try:
        path = QaEngineeringExportService().resolve_download(file_name)
    except ValueError as error:
        raise HTTPException(status_code=404, detail=str(error))
    return FileResponse(path, filename=path.name)


@router.get("/export/test-cases.xlsx")
def legacy_export_test_cases(domain: str, module: str, knowledge_name: str,
                             current_user=Depends(require_permission("qa_engineering", "view"))):
    """Backward-compatible endpoint retained for older Web clients."""
    result = TestCaseRepository().list_all_test_cases(
        domain=domain, module=module, knowledge_name=knowledge_name, limit=10000,
    )
    try:
        exported = QaEngineeringExportService().export(
            result["test_cases"], "xlsx",
            {"domain": domain, "module": module, "knowledge_name": knowledge_name},
        )
        path = QaEngineeringExportService().resolve_download(exported["file_name"])
    except (OSError, RuntimeError, ValueError) as error:
        raise HTTPException(status_code=422, detail=str(error))
    return FileResponse(path, filename=path.name)
