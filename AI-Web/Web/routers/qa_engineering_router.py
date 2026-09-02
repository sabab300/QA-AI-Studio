"""QA Engineering web API: knowledge scope selection and AI test generation."""

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from Core.qa_engineering_web_repository import QaEngineeringWeb
from Core.test_case_generator import ALL_TEST_TYPES
from Core.user_repository import UserRepository
from Web.deps import require_permission


router = APIRouter(prefix="/api/qa-engineering", tags=["qa-engineering"])
OUTPUT_FORMATS = {"Excel", "Word", "PDF"}


class GenerateRequest(BaseModel):
    domain: str = Field(min_length=1, max_length=200)
    module: str = Field(min_length=1, max_length=200)
    knowledge_name: str = Field(min_length=1, max_length=300)
    version: str | None = Field(default=None, max_length=100)
    requirement: str | None = Field(default=None, max_length=10_000)
    number_of_cases: int | Literal["all"] = "all"
    test_types: list[str] = Field(min_length=1)
    output_formats: list[str] = Field(min_length=1)


def _audit(current_user, action, detail):
    UserRepository().write_audit_log(
        current_user["id"], current_user["username"], action,
        resource="qa_engineering", detail=detail,
    )


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
    invalid_types = sorted(set(payload.test_types) - set(ALL_TEST_TYPES))
    invalid_formats = sorted(set(payload.output_formats) - OUTPUT_FORMATS)
    if invalid_types or invalid_formats:
        detail = []
        if invalid_types:
            detail.append("Unsupported test types: " + ", ".join(invalid_types))
        if invalid_formats:
            detail.append("Unsupported output formats: " + ", ".join(invalid_formats))
        raise HTTPException(status_code=422, detail="; ".join(detail))

    matching_items = QaEngineeringWeb().list_scope(payload.domain, payload.module)
    matching_items = [
        item for item in matching_items
        if item.get("knowledge_name") == payload.knowledge_name
        and (not payload.version or item.get("version") == payload.version)
    ]
    if not matching_items:
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
