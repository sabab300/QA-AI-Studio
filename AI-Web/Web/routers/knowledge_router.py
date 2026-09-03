# Create: AI/Web/routers/knowledge_router.py

"""
QA AI Studio — Web
Knowledge Hub Router (Milestone 1)

Version: 1.0

Replaces the "/api/knowledge/status" placeholder in
placeholder_routers.py. Backed by Core/knowledge_web_repository.py,
which itself wraps the same MetadataManager/RepositoryManager/
UploadPipeline/VectorStore classes the desktop app's Knowledge Hub
already uses — nothing in Core/ changes to support this.

Scope (v2): browse the Domain -> Module -> Knowledge Name -> Version
tree, search, upload a single document with manual Domain/Module/
Knowledge Name/Version tagging, view/edit a knowledge item's
metadata, list an item's versions, delete one version, AI Smart
Upload's auto-classification (suggest, does not save), Merge
Knowledge (preview-only, matches the desktop backend — there is no
"save merged result" write path there either) and Compare Versions.
Bulk/folder upload and URL Knowledge guided capture remain out of
scope for this router — the latter needs a stateful, remotely-
drivable browser session (see the delivery notes) and is not a fit
for a stateless request/response endpoint.
"""

from typing import List, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel

from Core.knowledge_web_repository import KnowledgeRepository
from Core.user_repository import UserRepository
from Web.deps import require_permission

router = APIRouter(prefix="/api/knowledge", tags=["knowledge"])


class UpdateKnowledgeItemRequest(BaseModel):
    domain: Optional[str] = None
    module: Optional[str] = None
    knowledge_name: Optional[str] = None
    version: Optional[str] = None
    document_type: Optional[str] = None
    summary: Optional[str] = None
    tags: Optional[List[str]] = None


class MergeItemsRequest(BaseModel):
    knowledge_ids: List[int]


class CompareVersionsRequest(BaseModel):
    knowledge_id_a: int
    knowledge_id_b: int


class CreateDomainRequest(BaseModel):
    name: str


class CreateModuleRequest(BaseModel):
    name: str


def _audit(current_user, action, detail):

    UserRepository().write_audit_log(
        user_id=current_user["id"],
        username=current_user["username"],
        action=action,
        resource="knowledge",
        detail=detail,
    )


@router.get("/tree")
def get_tree(current_user=Depends(require_permission("knowledge", "view"))):

    return {"tree": KnowledgeRepository().get_tree()}


@router.get("/domains")
def list_domains(current_user=Depends(require_permission("knowledge", "view"))):

    return {"domains": KnowledgeRepository().list_domains()}


@router.post("/domains")
def create_domain(
    payload: CreateDomainRequest,
    current_user=Depends(require_permission("knowledge", "create")),
):
    try:
        domain_id = KnowledgeRepository().create_domain(payload.name)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error))

    _audit(current_user, "CREATE_DOMAIN", f"Created domain '{payload.name}'")
    return {"status": "success", "domain": payload.name, "domain_id": domain_id}


@router.get("/domains/{domain}/modules")
def list_modules(
    domain: str, current_user=Depends(require_permission("knowledge", "view"))
):

    return {"modules": KnowledgeRepository().list_modules(domain)}


@router.post("/domains/{domain}/modules")
def create_module(
    domain: str,
    payload: CreateModuleRequest,
    current_user=Depends(require_permission("knowledge", "create")),
):
    try:
        module_id = KnowledgeRepository().create_module(domain, payload.name)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error))

    _audit(current_user, "CREATE_MODULE", f"Created module '{payload.name}' under domain '{domain}'")
    return {"status": "success", "domain": domain, "module": payload.name, "module_id": module_id}


@router.get("/items")
def list_items(
    search: Optional[str] = None,
    current_user=Depends(require_permission("knowledge", "view")),
):

    return {"items": KnowledgeRepository().list_items(search=search)}


@router.get("/items/{knowledge_id}")
def get_item(
    knowledge_id: int,
    current_user=Depends(require_permission("knowledge", "view")),
):

    item = KnowledgeRepository().get_item_details(knowledge_id)

    if item is None:

        raise HTTPException(status_code=404, detail="Knowledge item not found.")

    return item


@router.get("/items/{knowledge_id}/versions")
def get_item_versions(
    knowledge_id: int,
    current_user=Depends(require_permission("knowledge", "view")),
):

    return {"versions": KnowledgeRepository().get_item_versions(knowledge_id)}


@router.patch("/items/{knowledge_id}")
def update_item(
    knowledge_id: int,
    payload: UpdateKnowledgeItemRequest,
    current_user=Depends(require_permission("knowledge", "edit")),
):

    repository = KnowledgeRepository()

    existing = repository.get_item(knowledge_id)

    if existing is None:

        raise HTTPException(status_code=404, detail="Knowledge item not found.")

    fields = {k: v for k, v in payload.dict().items() if v is not None}

    if not fields:

        raise HTTPException(status_code=400, detail="Nothing to update.")

    try:
        updated = repository.update_item(knowledge_id, **fields)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error))

    _audit(
        current_user,
        "UPDATE_KNOWLEDGE_ITEM",
        f"Updated knowledge_id={knowledge_id} "
        f"('{existing['knowledge_name']}' v{existing['version']})",
    )

    return updated


@router.delete("/items/{knowledge_id}")
def delete_item(
    knowledge_id: int,
    current_user=Depends(require_permission("knowledge", "delete")),
):

    repository = KnowledgeRepository()

    result = repository.delete_item(knowledge_id)

    if result is None:

        raise HTTPException(status_code=404, detail="Knowledge item not found.")

    item = result["item"]

    _audit(
        current_user,
        "DELETE_KNOWLEDGE_ITEM",
        f"Deleted '{item['knowledge_name']}' v{item['version']} "
        f"({item['domain']} / {item['module']}, id={knowledge_id})",
    )

    return {"status": "deleted", **{k: v for k, v in result.items() if k != "item"}}


@router.post("/upload")
def upload(
    file: UploadFile = File(...),
    domain: str = Form(...),
    module: str = Form(...),
    knowledge_name: str = Form(...),
    version: str = Form("1.0"),
    document_type: str = Form(""),
    current_user=Depends(require_permission("knowledge", "create")),
):

    file_bytes = file.file.read()

    if not file_bytes:

        raise HTTPException(status_code=400, detail="The uploaded file is empty.")

    try:

        result = KnowledgeRepository().upload(
            file_bytes=file_bytes,
            original_filename=file.filename,
            domain=domain,
            module=module,
            knowledge_name=knowledge_name,
            version=version,
            document_type=document_type,
        )

    except ValueError as error:

        raise HTTPException(status_code=400, detail=str(error))

    _audit(
        current_user,
        "UPLOAD_KNOWLEDGE_ITEM",
        f"Uploaded '{file.filename}' to {domain} / {module} / "
        f"{knowledge_name} v{version}",
    )

    return result


@router.post("/suggest-classification")
def suggest_classification(
    file: UploadFile = File(...),
    current_user=Depends(require_permission("knowledge", "create")),
):

    file_bytes = file.file.read()

    if not file_bytes:

        raise HTTPException(status_code=400, detail="The uploaded file is empty.")

    try:

        result = KnowledgeRepository().suggest_classification(
            file_bytes=file_bytes,
            original_filename=file.filename,
        )

    except ValueError as error:

        raise HTTPException(status_code=400, detail=str(error))

    if not result.get("success"):

        raise HTTPException(
            status_code=422,
            detail=result.get("error", "Could not analyze this file."),
        )

    return result


@router.post("/merge")
def merge_items(
    payload: MergeItemsRequest,
    current_user=Depends(require_permission("knowledge", "view")),
):

    try:

        result = KnowledgeRepository().merge_items(payload.knowledge_ids)

    except ValueError as error:

        raise HTTPException(status_code=400, detail=str(error))

    _audit(
        current_user,
        "MERGE_KNOWLEDGE_PREVIEW",
        f"Previewed merge of knowledge_ids={payload.knowledge_ids}",
    )

    return result


@router.post("/compare")
def compare_versions(
    payload: CompareVersionsRequest,
    current_user=Depends(require_permission("knowledge", "view")),
):

    try:

        result = KnowledgeRepository().compare_versions(
            payload.knowledge_id_a, payload.knowledge_id_b
        )

    except ValueError as error:

        raise HTTPException(status_code=400, detail=str(error))

    return result
