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

import json
import shutil
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field

from Core.knowledge_web_repository import KnowledgeRepository
from Core.api_collection_repository import ApiCollectionRepository
from Core.api_automation_runner import ApiAutomationRunner
from Core.knowledge_discovery_sessions import KnowledgeDiscoverySessions
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


class UpdateApiEndpointRequest(BaseModel):
    method: Optional[str] = None
    name: Optional[str] = None
    url_raw: Optional[str] = None
    url_resolved: Optional[str] = None
    headers: Optional[List[Dict[str, str]]] = None
    body_mode: Optional[str] = None
    body_raw: Optional[str] = None


class RunApiEndpointRequest(BaseModel):
    variables: Dict[str, str] = Field(default_factory=dict)
    base_url_override: str = ""
    timeout_seconds: float = 30
    verify_ssl: bool = True


class CreateDiscoverySessionRequest(BaseModel):
    url: str
    authentication_type: str = "NONE"


class AuthenticateDiscoveryRequest(BaseModel):
    credentials: Dict[str, str] = Field(default_factory=dict)
    analysis: Dict[str, Any] = Field(default_factory=dict)


class NavigateDiscoveryRequest(BaseModel):
    url: str


class ScanDiscoveryRequest(BaseModel):
    label: str = ""


class SaveDiscoveryRequest(BaseModel):
    steps: List[Dict[str, Any]]
    hierarchy: Dict[str, str]


def _audit(current_user, action, detail):

    UserRepository().write_audit_log(
        user_id=current_user["id"],
        username=current_user["username"],
        action=action,
        resource="knowledge",
        detail=detail,
    )


_SENSITIVE_HEADER_NAMES = {
    "authorization", "proxy-authorization", "x-api-key", "api-key", "cookie", "set-cookie"
}


def _mask_headers(headers):
    if isinstance(headers, str):
        try:
            headers = json.loads(headers or "[]")
        except json.JSONDecodeError:
            return []
    if isinstance(headers, list):
        return [
            {**item, "value": "••••••••"}
            if str((item or {}).get("key", "")).lower() in _SENSITIVE_HEADER_NAMES
            else item
            for item in headers
        ]
    return {
        key: ("••••••••" if str(key).lower() in _SENSITIVE_HEADER_NAMES else value)
        for key, value in (headers or {}).items()
    }


def _safe_endpoint(endpoint):
    if endpoint is None:
        return None
    result = dict(endpoint)
    result["headers"] = _mask_headers(result.pop("headers_json", "[]"))
    result.pop("auth_details_json", None)
    return result


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
    source_type: str = Form("FILE"),
    reviewed_summary: Optional[str] = Form(None),
    reviewed_tags: Optional[str] = Form(None),
    reviewed_confidence: Optional[float] = Form(None),
    current_user=Depends(require_permission("knowledge", "create")),
):

    file_bytes = file.file.read()

    if not file_bytes:

        raise HTTPException(status_code=400, detail="The uploaded file is empty.")

    try:

        reviewed_analysis = None
        if reviewed_summary is not None or reviewed_tags is not None:
            try:
                tags = json.loads(reviewed_tags or "[]")
            except (TypeError, json.JSONDecodeError):
                tags = [tag.strip() for tag in (reviewed_tags or "").split(",") if tag.strip()]
            reviewed_analysis = {
                "summary": reviewed_summary or "",
                "tags": tags if isinstance(tags, list) else [],
                "confidence": reviewed_confidence or 0,
                "document_type": document_type,
            }

        result = KnowledgeRepository().upload(
            file_bytes=file_bytes,
            original_filename=file.filename,
            domain=domain,
            module=module,
            knowledge_name=knowledge_name,
            version=version,
            document_type=document_type,
            source_type=source_type,
            reviewed_analysis=reviewed_analysis,
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


@router.post("/api-collections/import")
def import_api_collection(
    file: UploadFile = File(...),
    domain: Optional[str] = Form(None),
    module: Optional[str] = Form(None),
    knowledge_name: Optional[str] = Form(None),
    version: Optional[str] = Form("1.0"),
    current_user=Depends(require_permission("knowledge", "create")),
):
    if Path(file.filename or "").suffix.lower() != ".json":
        raise HTTPException(status_code=400, detail="A Postman Collection JSON file is required.")
    temp_dir = tempfile.mkdtemp(prefix="qaais_postman_")
    temp_path = Path(temp_dir) / (Path(file.filename or "collection.json").name or "collection.json")
    try:
        temp_path.write_bytes(file.file.read())
        if not temp_path.stat().st_size:
            raise HTTPException(status_code=400, detail="The uploaded collection is empty.")
        result = ApiCollectionRepository().import_postman_collection(
            str(temp_path), domain, module, knowledge_name, version
        )
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)
    if not result.get("success"):
        raise HTTPException(status_code=422, detail=result.get("error") or "Collection import failed.")
    _audit(current_user, "IMPORT_API_COLLECTION", f"Imported collection_id={result.get('collection_id')}")
    return result


@router.get("/api-collections/{collection_id}")
def get_api_collection(collection_id: int, current_user=Depends(require_permission("knowledge", "view"))):
    repository = ApiCollectionRepository()
    collection = repository.get_collection(collection_id)
    if not collection:
        raise HTTPException(status_code=404, detail="API collection not found.")
    result = dict(collection)
    result.pop("variables_json", None)
    result["endpoints"] = [_safe_endpoint(row) for row in repository.list_endpoints(collection_id)]
    return result


@router.get("/items/{knowledge_id}/api-collections")
def get_api_collections_for_knowledge(knowledge_id: int, current_user=Depends(require_permission("knowledge", "view"))):
    repository = ApiCollectionRepository()
    collections = [
        dict(row) for row in repository.list_collections()
        if row.get("linked_knowledge_item_id") == knowledge_id
    ]
    for collection in collections:
        collection.pop("variables_json", None)
        collection["endpoints"] = [_safe_endpoint(row) for row in repository.list_endpoints(collection["id"])]
    return {"collections": collections}


@router.get("/api-endpoints/{endpoint_id}")
def get_api_endpoint(endpoint_id: int, current_user=Depends(require_permission("knowledge", "view"))):
    endpoint = ApiCollectionRepository().get_endpoint(endpoint_id)
    if not endpoint:
        raise HTTPException(status_code=404, detail="API endpoint not found.")
    return _safe_endpoint(endpoint)


@router.patch("/api-endpoints/{endpoint_id}")
def update_api_endpoint(endpoint_id: int, payload: UpdateApiEndpointRequest, current_user=Depends(require_permission("knowledge", "edit"))):
    repository = ApiCollectionRepository()
    if not repository.get_endpoint(endpoint_id):
        raise HTTPException(status_code=404, detail="API endpoint not found.")
    fields = {key: value for key, value in payload.dict().items() if value is not None}
    headers = fields.pop("headers", None)
    if headers is not None:
        existing_headers = {}
        try:
            existing_headers = {
                str(row.get("key", "")): row.get("value", "")
                for row in json.loads(repository.get_endpoint(endpoint_id).get("headers_json") or "[]")
            }
        except (TypeError, json.JSONDecodeError):
            existing_headers = {}
        for row in headers:
            if row.get("value") == "••••••••":
                row["value"] = existing_headers.get(str(row.get("key", "")), "")
        fields["headers_json"] = json.dumps(headers)
    if not fields or not repository.update_endpoint(endpoint_id, **fields):
        raise HTTPException(status_code=400, detail="Nothing to update.")
    _audit(current_user, "UPDATE_API_ENDPOINT", f"Updated endpoint_id={endpoint_id}")
    return _safe_endpoint(repository.get_endpoint(endpoint_id))


@router.post("/api-endpoints/{endpoint_id}/run")
def run_api_endpoint(endpoint_id: int, payload: RunApiEndpointRequest, current_user=Depends(require_permission("knowledge", "view"))):
    endpoint = ApiCollectionRepository().get_endpoint(endpoint_id)
    if not endpoint:
        raise HTTPException(status_code=404, detail="API endpoint not found.")
    result = ApiAutomationRunner().send(endpoint, {
        "api_variables": payload.variables,
        "api_base_url_override": payload.base_url_override,
        "api_timeout_seconds": payload.timeout_seconds,
        "api_verify_ssl": payload.verify_ssl,
    })
    if result.get("request"):
        result["request"]["headers"] = _mask_headers(result["request"].get("headers", {}))
        data = result["request"].get("data")
        if isinstance(data, bytes):
            result["request"]["data"] = data.decode("utf-8", errors="replace")[:8000]
    result["response_headers"] = _mask_headers(result.get("response_headers", {}))
    _audit(current_user, "RUN_API_ENDPOINT", f"Ran endpoint_id={endpoint_id}; status={result.get('status_code')}")
    return result


@router.post("/discovery/sessions")
def create_discovery_session(payload: CreateDiscoverySessionRequest, current_user=Depends(require_permission("knowledge", "create"))):
    try:
        result = KnowledgeDiscoverySessions.create(payload.url, payload.authentication_type)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error))
    _audit(current_user, "CREATE_DISCOVERY_SESSION", "Created a URL discovery session")
    return result


@router.post("/discovery/sessions/{session_id}/authenticate")
def authenticate_discovery_session(session_id: str, payload: AuthenticateDiscoveryRequest, current_user=Depends(require_permission("knowledge", "create"))):
    try:
        return KnowledgeDiscoverySessions.authenticate(session_id, payload.credentials, payload.analysis)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error))


@router.post("/discovery/sessions/{session_id}/navigate")
def navigate_discovery_session(session_id: str, payload: NavigateDiscoveryRequest, current_user=Depends(require_permission("knowledge", "create"))):
    try:
        return KnowledgeDiscoverySessions.navigate(session_id, payload.url)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error))


@router.post("/discovery/sessions/{session_id}/scan")
def scan_discovery_session(session_id: str, payload: ScanDiscoveryRequest, current_user=Depends(require_permission("knowledge", "create"))):
    try:
        return KnowledgeDiscoverySessions.scan(session_id, payload.label)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error))


@router.post("/discovery/sessions/{session_id}/save")
def save_discovery_session(session_id: str, payload: SaveDiscoveryRequest, current_user=Depends(require_permission("knowledge", "create"))):
    try:
        result = KnowledgeDiscoverySessions.save(session_id, payload.steps, payload.hierarchy)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error))
    if not result.get("success"):
        raise HTTPException(status_code=422, detail=result.get("error") or "Discovery could not be saved.")
    _audit(current_user, "SAVE_DISCOVERY_FLOW", f"Saved knowledge_id={result.get('knowledge_item_id')}")
    return result


@router.delete("/discovery/sessions/{session_id}")
def close_discovery_session(session_id: str, current_user=Depends(require_permission("knowledge", "create"))):
    return {"closed": KnowledgeDiscoverySessions.close(session_id)}
