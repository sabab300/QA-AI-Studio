"""AI UI/UX Testing API. Independent of Playwright replay, API Discovery and Change Impact Analysis.

All routes require a session and the Automation permission; hierarchy and design routes additionally require
Knowledge Hub permission, and anything that changes secrets requires Settings permission. Secrets are write-only.
"""
from __future__ import annotations

import json
import re
import threading
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field

from Core.uiux import build, categories as uiux_categories, quick, reports, summary as run_summary, xlsx_report
from Core.uiux.analysis import analyse as analyse_design
from Core.uiux.clickup import ClickUpTickets, TicketError
from Core.uiux.structure import Tolerances
from Core.uiux.figma_client import FigmaError
from Core.uiux.files import UploadRejected
from Core.uiux.hierarchy import HierarchyError
from Core.uiux.registry import RegistryError
from Core.uiux.repo import RepoError
from Core.uiux.security import ENVIRONMENTS, TargetError
from Web.deps import require_permission

router = APIRouter(prefix="/api/uiux", tags=["ai-uiux-testing"])

_LOCK = threading.Lock()
_SERVICES = None
MAX_UPLOAD = 150 * 1024 * 1024


def services():
    """Lazily build the service graph once; tests replace it with set_services()."""
    global _SERVICES
    with _LOCK:
        if _SERVICES is None:
            from Core.uiux.agent import UiuxAgent
            from Core.uiux.hierarchy import Hierarchy
            from Core.uiux.registry import DesignRegistry
            from Core.uiux.repo import UiuxRepo
            from Core.uiux.runner import RunManager
            from Core.uiux.schema import UiuxDatabase
            from Core.uiux.storage import Storage
            from Core.uiux.vault import Vault
            db = UiuxDatabase()
            hierarchy = Hierarchy(db.db_path)
            storage = Storage()
            vault = Vault(db)
            registry = DesignRegistry(db, hierarchy, storage, vault)
            repo = UiuxRepo(db, hierarchy, storage)
            _SERVICES = dict(db=db, hier=hierarchy, storage=storage, vault=vault, registry=registry, repo=repo, agent=UiuxAgent(), manager=RunManager(repo, registry, vault, agent=UiuxAgent()))
        return _SERVICES


def set_services(value):
    global _SERVICES
    with _LOCK:
        _SERVICES = value


def reconcile_on_startup():
    try:
        return services()["repo"].reconcile_interrupted()
    except Exception:
        return 0


# ----------------------------------------------------------------------------- helpers

def _fail(error, default=400):
    message = str(error)
    code = 404 if "does not exist" in message or "was not found" in message else default
    return HTTPException(code, message)


def guarded(fn):
    """Translate domain errors into clean HTTP errors; never leak internals."""
    import functools

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except HTTPException:
            raise
        except (RepoError, RegistryError, HierarchyError, UploadRejected, TargetError, FigmaError, ValueError) as error:
            raise _fail(error) from None
        except PermissionError as error:
            raise HTTPException(403, str(error)) from None
        except ImportError as error:
            # a library this module needs is missing: say so plainly instead of a generic server error
            raise HTTPException(503, "AI UI/UX Testing could not start: a required library is missing (" + str(error)[:160] + "). "
                                     "Run 'pip install -r requirements-web.txt' and restart QA AI Studio.") from None
    return wrapper


def _kh(user, action="view"):
    from Core.user_repository import UserRepository
    if action not in UserRepository().get_permissions_for_role(user["role_id"]).get("knowledge", set()):
        raise HTTPException(403, f"Knowledge Hub '{action}' permission is required for this action.")


def _is_admin(user) -> bool:
    return str(user.get("role_name", "")).lower() == "admin"


def _results(S):
    """The result store lives with the run manager (it writes the rows); the router only reads and exports them."""
    return S["manager"].results


def _clickup(S) -> ClickUpTickets:
    if "clickup" not in S:
        S["clickup"] = ClickUpTickets(_results(S), S["repo"])
    return S["clickup"]


def _audit_note(user, action, detail):
    try:
        from Core.user_repository import UserRepository
        UserRepository().write_audit_log(user_id=user["id"], username=user["username"], action=action, resource="automation", detail=detail[:400])
    except Exception:
        pass


def _ticket_fail(error: TicketError):
    return JSONResponse(status_code=error.status, content={"detail": str(error), **{k: v for k, v in (error.extra or {}).items()}})


def _name(user) -> str:
    return user.get("username") or str(user.get("id", ""))


def _version(vid: int) -> dict:
    return services()["hier"].identity_of_version(vid)


# ----------------------------------------------------------------------------- models

class Selection(BaseModel):
    domain: str = Field(default="", max_length=200)
    module: str = Field(default="", max_length=200)
    knowledge_name: str = Field(default="", max_length=300)
    document_type: str = Field(default="", max_length=200)
    version: str = Field(default="", max_length=100)


class FigmaLink(BaseModel):
    selection: Selection
    url: str = Field(min_length=3, max_length=2048)
    create_missing: bool = False


class FramesSelect(BaseModel):
    node_ids: list[str] = Field(max_length=200)


class TokenBody(BaseModel):
    token: str = Field(min_length=10, max_length=512)


class TargetBody(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    url: str = Field(min_length=8, max_length=2048)
    environment: Literal["Development", "QA", "UAT", "Staging", "Production", "Other"]
    internal_ok: bool = False
    extra_origins: list[str] = Field(default_factory=list, max_length=6)
    note: str = Field(default="", max_length=300)
    authorized: bool = False


class AppCredentials(BaseModel):
    type: Literal["basic", "cookie", "form"]
    username: str = Field(default="", max_length=200)
    password: str = Field(default="", max_length=500)
    name: str = Field(default="", max_length=100)
    value: str = Field(default="", max_length=4000)
    login_path: str = Field(default="/login", max_length=300)
    user_selector: str = Field(default="", max_length=200)
    pass_selector: str = Field(default="", max_length=200)
    submit_selector: str = Field(default="", max_length=200)
    failure_selector: str = Field(default="", max_length=200)


class MappingCreate(BaseModel):
    asset_id: str = Field(min_length=3, max_length=60)
    frame_id: str = Field(min_length=3, max_length=200)
    target_id: str = Field(default="", max_length=60)
    route: str = Field(default="/", max_length=300)
    viewport_w: int | None = Field(default=None, ge=200, le=3840)
    viewport_h: int | None = Field(default=None, ge=200, le=5000)


class MappingUpdate(BaseModel):
    route: str | None = Field(default=None, max_length=300)
    viewport_w: int | None = Field(default=None, ge=200, le=3840)
    viewport_h: int | None = Field(default=None, ge=200, le=5000)
    masks: list[dict] | None = Field(default=None, max_length=30)
    states: list[dict] | None = Field(default=None, max_length=10)
    target_id: str | None = Field(default=None, max_length=60)


class ElementMap(BaseModel):
    design_node_id: str = Field(min_length=1, max_length=80)
    selector: str | None = Field(default=None, max_length=300)


class SuggestBody(BaseModel):
    asset_id: str = Field(min_length=3, max_length=60)
    target_id: str = Field(min_length=3, max_length=60)
    routes: list[str] = Field(default_factory=list, max_length=12)
    discover: bool = False
    use_ai: bool = False


class ConfigBody(BaseModel):
    document_version_id: int = Field(gt=0)
    asset_id: str = Field(default="", max_length=60)
    name: str = Field(min_length=1, max_length=120)
    config: dict
    config_id: str = Field(default="", max_length=60)


class RunBody(BaseModel):
    document_version_id: int = Field(gt=0)
    asset_id: str = Field(min_length=3, max_length=60)
    config: dict
    config_id: str = Field(default="", max_length=60)


class ReviewBody(BaseModel):
    decision: Literal["Confirmed", "Not Applicable", "Needs Investigation"]
    comment: str = Field(default="", max_length=1000)


class BaselineBody(BaseModel):
    document_version_id: int = Field(gt=0)
    asset_id: str = Field(min_length=3, max_length=60)
    run_id: str = Field(min_length=5, max_length=60)
    label: str = Field(default="", max_length=120)
    notes: str = Field(default="", max_length=500)


class Transition(BaseModel):
    to: Literal["In Review", "Approved", "Active", "Rejected", "Draft"]
    note: str = Field(default="", max_length=300)


class CheckBody(BaseModel):
    name: str = Field(default="", max_length=150)


# ----------------------------------------------------------------------------- status

@router.get("/health")
@guarded
def health(user=Depends(require_permission("automation", "view"))):
    from Core.uiux.browser import check_browser
    S = services()
    ok, why = check_browser()
    token, source = S["vault"].figma_token()
    return {"browser": {"available": ok, "detail": "" if ok else why}, "figma": {"token_configured": bool(token), "source": source if token else "none", "live_verified": False},
            "tables": S["db"].table_names(), "build": build.fingerprint()}


@router.get("/build")
@guarded
def build_info(user=Depends(require_permission("automation", "view"))):
    return build.fingerprint()


# ----------------------------------------------------------------------------- hierarchy

@router.get("/hierarchy/options")
@guarded
def hierarchy_options(level: Literal["domain", "module", "knowledge_name", "document_type", "version", "source_type", "source_file"], domain: str = "", module: str = "",
                      knowledge_name: str = "", document_type: str = "", version: str = "", source_type: str = "", user=Depends(require_permission("automation", "view"))):
    _kh(user)
    selected = {k: v for k, v in dict(domain=domain, module=module, knowledge_name=knowledge_name, document_type=document_type, version=version, source_type=source_type).items() if v}
    S = services()
    if level in ("source_type", "source_file"):
        return {"level": level, "options": _source_options(S, level, selected)}
    return {"level": level, "options": S["hier"].options(level, selected)}


def _source_options(S, level, selected):
    ident = S["hier"].resolve(selected if level == "source_type" else {k: v for k, v in selected.items() if k != "source_type"})
    vid = ident["document_version_id"]
    designs = S["registry"].list_designs(vid)
    if level == "source_type":
        return sorted({d["source_type"] for d in designs}) or ["Figma"]
    return [{"value": d["source_file"], "asset_id": d["id"], "kind": d["asset_kind"]} for d in designs if not selected.get("source_type") or d["source_type"] == selected["source_type"]]


@router.post("/hierarchy/resolve")
@guarded
def hierarchy_resolve(selection: Selection, user=Depends(require_permission("automation", "view"))):
    _kh(user)
    return services()["hier"].resolve(selection.model_dump())


# ----------------------------------------------------------------------------- designs

@router.get("/designs")
@guarded
def list_designs(document_version_id: int, user=Depends(require_permission("automation", "view"))):
    _kh(user)
    designs = services()["registry"].list_designs(document_version_id)
    for d in designs:
        d["analysis"] = analyse_design(d)
    return {"designs": designs}


@router.get("/designs/{asset_id}")
@guarded
def get_design(asset_id: str, user=Depends(require_permission("automation", "view"))):
    d = services()["registry"].get(asset_id)
    d["analysis"] = analyse_design(d)
    return d


@router.post("/designs/upload")
async def upload_design(request: Request, domain: str, module: str, knowledge_name: str, document_type: str, version: str, filename: str = Query(min_length=1, max_length=200),
                        create_missing: bool = False, user=Depends(require_permission("automation", "create"))):
    _kh(user, "create")
    data = bytearray()
    async for chunk in request.stream():
        if len(data) + len(chunk) > MAX_UPLOAD:
            raise HTTPException(413, "The file exceeds the 150 MiB upload limit.")
        data.extend(chunk)
    if not data:
        raise HTTPException(400, "The uploaded file is empty.")
    sel = {"domain": domain, "module": module, "knowledge_name": knowledge_name, "document_type": document_type, "version": version}
    try:
        return services()["registry"].register_upload(sel, filename, bytes(data), _name(user), create_missing)
    except (RepoError, RegistryError, HierarchyError, UploadRejected) as error:
        raise _fail(error) from None


@router.post("/designs/figma")
@guarded
def connect_figma(body: FigmaLink, user=Depends(require_permission("automation", "create"))):
    _kh(user, "create")
    return services()["registry"].register_figma_link(body.selection.model_dump(), body.url, _name(user), body.create_missing)


@router.post("/designs/{asset_id}/frames/select")
@guarded
def select_frames(asset_id: str, body: FramesSelect, user=Depends(require_permission("automation", "edit"))):
    return services()["registry"].select_frames(asset_id, body.node_ids)


@router.post("/designs/{asset_id}/analyze")
@guarded
def analyze_design(asset_id: str, user=Depends(require_permission("automation", "execute"))):
    return services()["registry"].analyze(asset_id, _name(user))


@router.get("/frames/{frame_id}/image")
@guarded
def frame_image(frame_id: str, user=Depends(require_permission("automation", "view"))):
    data = services()["registry"].frame_image_bytes(frame_id)
    return Response(data, media_type="image/png", headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"})


@router.get("/frames/{frame_id}")
@guarded
def frame_detail(frame_id: str, user=Depends(require_permission("automation", "view"))):
    f = services()["registry"].frame(frame_id)
    nodes = f.pop("nodes")
    return {**f, "node_count": len(nodes), "nodes": [{k: n.get(k) for k in ("id", "name", "type", "role", "x", "y", "w", "h")} for n in nodes[:400]]}


@router.post("/frames/{frame_id}/image")
async def attach_frame_image(frame_id: str, request: Request, user=Depends(require_permission("automation", "edit"))):
    data = bytearray()
    async for chunk in request.stream():
        if len(data) + len(chunk) > 25 * 1024 * 1024:
            raise HTTPException(413, "The image exceeds the 25 MiB limit.")
        data.extend(chunk)
    try:
        return services()["registry"].attach_frame_image(frame_id, bytes(data), _name(user))
    except (RepoError, RegistryError, UploadRejected) as error:
        raise _fail(error) from None


# ----------------------------------------------------------------------------- secrets (write-only)

@router.get("/figma/credentials")
@guarded
def figma_status(user=Depends(require_permission("automation", "view"))):
    V = services()["vault"]
    token, source = V.figma_token()
    return {"configured": bool(token), "source": source if token else "none", **V.status(V.FIGMA)}


@router.put("/figma/credentials")
@guarded
def figma_set(body: TokenBody, user=Depends(require_permission("settings", "edit"))):
    services()["vault"].put(services()["vault"].FIGMA, body.token.strip(), _name(user))
    return {"configured": True, **services()["vault"].status(services()["vault"].FIGMA)}


@router.delete("/figma/credentials")
@guarded
def figma_clear(user=Depends(require_permission("settings", "edit"))):
    services()["vault"].delete(services()["vault"].FIGMA)
    return {"configured": False}


# ----------------------------------------------------------------------------- authorized targets

@router.get("/targets")
@guarded
def list_targets(user=Depends(require_permission("automation", "view"))):
    S = services()
    out = []
    for t in S["repo"].list_targets():
        out.append({**t, "credentials": {k: v for k, v in S["vault"].status(f"app:{t['id']}").items() if k in ("is_set", "set_at")}})
    return {"targets": out}


@router.post("/targets")
@guarded
def add_target(body: TargetBody, user=Depends(require_permission("automation", "edit"))):
    if not body.authorized:
        raise HTTPException(400, "Confirm you are authorized to test this application.")
    if body.internal_ok and not _is_admin(user):
        raise HTTPException(403, "Only an administrator can authorize an internal-network address.")
    return services()["repo"].add_target(body.name, body.url, body.environment, body.internal_ok, body.extra_origins, _name(user), body.note)


@router.delete("/targets/{target_id}")
@guarded
def remove_target(target_id: str, user=Depends(require_permission("automation", "edit"))):
    services()["repo"].deactivate_target(target_id)
    return {"removed": True}


@router.put("/targets/{target_id}/credentials")
@guarded
def set_app_credentials(target_id: str, body: AppCredentials, user=Depends(require_permission("settings", "edit"))):
    S = services()
    S["repo"].get_target(target_id)
    if body.type == "basic" and not (body.username and body.password):
        raise HTTPException(400, "Enter a user name and password.")
    if body.type == "cookie" and not (body.name and body.value):
        raise HTTPException(400, "Enter the cookie name and value.")
    if body.type == "form" and not all([body.username, body.password, body.user_selector, body.pass_selector, body.submit_selector]):
        raise HTTPException(400, "Enter the user name, password and the three form selectors.")
    S["vault"].put(f"app:{target_id}", json.dumps(body.model_dump()), _name(user))
    return {"is_set": True, "type": body.type}


@router.delete("/targets/{target_id}/credentials")
@guarded
def clear_app_credentials(target_id: str, user=Depends(require_permission("settings", "edit"))):
    services()["vault"].delete(f"app:{target_id}")
    return {"is_set": False}


# ----------------------------------------------------------------------------- mappings

@router.get("/mappings")
@guarded
def list_mappings(document_version_id: int, user=Depends(require_permission("automation", "view"))):
    return {"mappings": services()["repo"].list_mappings(document_version_id)}


@router.post("/mappings")
@guarded
def create_mapping(body: MappingCreate, user=Depends(require_permission("automation", "edit"))):
    vp = (body.viewport_w, body.viewport_h or 900) if body.viewport_w else None
    return services()["repo"].create_mapping(body.asset_id, body.frame_id, body.target_id, body.route, vp, _name(user), method="manual")


@router.patch("/mappings/{mapping_id}")
@guarded
def update_mapping(mapping_id: str, body: MappingUpdate, user=Depends(require_permission("automation", "edit"))):
    vp = (body.viewport_w, body.viewport_h or 900) if body.viewport_w else None
    return services()["repo"].update_mapping(mapping_id, _name(user), body.route, vp, body.masks, body.states, body.target_id)


@router.post("/mappings/{mapping_id}/{action}")
@guarded
def mapping_action(mapping_id: str, action: Literal["approve", "reject"], user=Depends(require_permission("automation", "edit"))):
    return services()["repo"].set_status(mapping_id, "Approved" if action == "approve" else "Rejected", _name(user))


@router.post("/mappings/{mapping_id}/element-map")
@guarded
def element_map(mapping_id: str, body: ElementMap, user=Depends(require_permission("automation", "edit"))):
    return services()["repo"].set_element_map(mapping_id, body.design_node_id, body.selector, _name(user))


@router.delete("/mappings/{mapping_id}")
@guarded
def delete_mapping(mapping_id: str, user=Depends(require_permission("automation", "edit"))):
    services()["repo"].delete_mapping(mapping_id)
    return {"removed": True}


@router.post("/mappings/suggest")
@guarded
def suggest_mappings(body: SuggestBody, user=Depends(require_permission("automation", "execute"))):
    from Core.uiux import mapping_suggest
    S = services()
    target_row = S["repo"].get_target(body.target_id)
    auth = None
    raw = S["vault"].get(f"app:{target_row['id']}")
    if raw:
        try:
            auth = json.loads(raw)
        except ValueError:
            auth = None
    routes = list(body.routes)
    discovered = []
    if body.discover:
        from Core.uiux.security import authorize
        target = authorize(target_row["origin"] + "/", target_row["environment"], S["repo"].target_records())
        discovered = mapping_suggest.discover_routes(target, auth)
        routes += [d["path"] for d in discovered if d["path"] not in routes]
    agent = S.get("agent") if body.use_ai else None
    result = mapping_suggest.suggest(S["repo"], S["registry"], body.asset_id, target_row, routes, _name(user), auth=auth, agent=agent)
    result["discovered"] = discovered
    return result


# ----------------------------------------------------------------------------- configurations

@router.get("/configs")
@guarded
def list_configs(document_version_id: int, user=Depends(require_permission("automation", "view"))):
    return {"configs": services()["repo"].list_configs(document_version_id)}


@router.post("/configs")
@guarded
def save_config(body: ConfigBody, user=Depends(require_permission("automation", "edit"))):
    return services()["repo"].save_config(body.document_version_id, body.asset_id, body.name, body.config, _name(user), body.config_id)


@router.delete("/configs/{config_id}")
@guarded
def delete_config(config_id: str, user=Depends(require_permission("automation", "edit"))):
    services()["repo"].delete_config(config_id)
    return {"removed": True}


# ----------------------------------------------------------------------------- runs

@router.post("/runs")
@guarded
def start_run(body: RunBody, user=Depends(require_permission("automation", "execute"))):
    S = services()
    run = S["repo"].create_run(body.document_version_id, body.asset_id, body.config, _name(user), config_id=body.config_id)
    S["manager"].start(run["run_id"])
    return S["repo"].get_run(run["run_id"])


@router.get("/runs")
@guarded
def list_runs(document_version_id: int, limit: int = Query(default=50, ge=1, le=200), user=Depends(require_permission("automation", "view"))):
    return {"runs": services()["repo"].list_runs(document_version_id, limit)}


@router.get("/runs/compare")
@guarded
def compare_runs(a: str, b: str, user=Depends(require_permission("automation", "view"))):
    return services()["repo"].compare_runs(a, b)


@router.get("/runs/{run_id}")
@guarded
def get_run(run_id: str, user=Depends(require_permission("automation", "view"))):
    S = services()
    run = S["repo"].get_run(run_id)
    run["evidence"] = [e for k in ("screenshot", "comparison", "design", "diff") for e in S["repo"].list_evidence(run_id, k)]
    return run


@router.post("/runs/{run_id}/cancel")
@guarded
def cancel_run(run_id: str, user=Depends(require_permission("automation", "execute"))):
    services()["manager"].cancel(run_id)
    return services()["repo"].get_run(run_id)


def _derived_run(run_id, kind, user):
    S = services()
    parent = S["repo"].get_run(run_id)
    if parent["status"] in ("Queued", "Running", "Paused"):
        raise RepoError("Wait for the run to finish or stop it first.")
    cfg = dict(parent["config"])
    units = parent["summary"].get("units", [])
    if kind == "retry":
        bad = [u["mapping_id"] for u in units if u.get("status") not in ("Tested",) and u.get("mapping_id")]
        done = {u["mapping_id"] for u in units if u.get("status") == "Tested"}
        pending = [m for m in cfg["mapping_ids"] if m not in done]
        cfg["mapping_ids"] = list(dict.fromkeys(bad + pending))
        if not cfg["mapping_ids"]:
            raise RepoError("Every screen of this run was tested; there is nothing to retry.")
    else:
        failing = S["repo"].list_findings(run_id, limit=5000)["items"]
        ids = {f["mapping_id"] for f in failing if f["verifiable"] and f["classification"] in ("Confirmed Difference", "Potential Difference", "Needs Investigation") and f["review_status"] != "Not Applicable" and f["mapping_id"]}
        cfg["mapping_ids"] = [m for m in cfg["mapping_ids"] if m in ids]
        if not cfg["mapping_ids"]:
            raise RepoError("This run has no open findings to re-check.")
    run = S["repo"].create_run(parent["document_version_id"], parent["asset_id"], cfg, _name(user), kind=kind, parent_run_id=run_id)
    S["manager"].start(run["run_id"])
    return S["repo"].get_run(run["run_id"])


@router.post("/runs/{run_id}/retry")
@guarded
def retry_run(run_id: str, user=Depends(require_permission("automation", "execute"))):
    return _derived_run(run_id, "retry", user)


@router.post("/runs/{run_id}/recheck")
@guarded
def recheck_run(run_id: str, user=Depends(require_permission("automation", "execute"))):
    return _derived_run(run_id, "recheck", user)


class ChecksRunBody(BaseModel):
    document_version_id: int = Field(gt=0)
    asset_id: str = Field(min_length=3, max_length=60)
    config: dict


@router.post("/runs/checks")
@guarded
def run_saved_checks(body: ChecksRunBody, user=Depends(require_permission("automation", "execute"))):
    S = services()
    checks = [c for c in S["repo"].list_checks(body.document_version_id) if c["active"] and c["mapping_id"]]
    if not checks:
        raise RepoError("There are no saved regression checks for this version.")
    cfg = dict(body.config)
    cfg["mapping_ids"] = list(dict.fromkeys(c["mapping_id"] for c in checks))
    run = S["repo"].create_run(body.document_version_id, body.asset_id, cfg, _name(user), kind="checks")
    S["manager"].start(run["run_id"])
    return S["repo"].get_run(run["run_id"])


# ----------------------------------------------------------------------------- findings, evidence, reviews

@router.get("/runs/{run_id}/findings")
@guarded
def run_findings(run_id: str, classification: str = "", severity: str = "", category: str = "", review_status: str = "", screen: str = "", regression_state: str = "", text: str = Query(default="", max_length=100),
                 limit: int = Query(default=300, ge=1, le=1000), offset: int = Query(default=0, ge=0), user=Depends(require_permission("automation", "view"))):
    return services()["repo"].list_findings(run_id, classification, severity, category, review_status, screen, regression_state, text, limit, offset)


@router.get("/findings/{finding_id}")
@guarded
def finding_detail(finding_id: int, user=Depends(require_permission("automation", "view"))):
    return services()["repo"].get_finding(finding_id)


@router.post("/findings/{finding_id}/review")
@guarded
def review_finding(finding_id: int, body: ReviewBody, user=Depends(require_permission("automation", "edit"))):
    return services()["repo"].review(finding_id, body.decision, body.comment, _name(user))


@router.post("/findings/{finding_id}/save-check")
@guarded
def save_check(finding_id: int, body: CheckBody, user=Depends(require_permission("automation", "edit"))):
    return services()["repo"].save_check(finding_id, body.name, _name(user))


@router.get("/evidence/{evidence_id}")
@guarded
def evidence(evidence_id: str, request: Request, user=Depends(require_permission("automation", "view"))):
    if not re.fullmatch(r"EV-[0-9a-f]{12}", evidence_id):
        raise HTTPException(404, "The evidence does not exist.")
    data, mime = services()["repo"].evidence_bytes(evidence_id)
    rng = request.headers.get("range", "")
    m = re.fullmatch(r"bytes=(\d*)-(\d*)", rng.strip()) if mime.startswith("video/") else None
    if m and (m.group(1) or m.group(2)):
        total = len(data)
        start = int(m.group(1)) if m.group(1) else max(0, total - int(m.group(2)))
        end = min(int(m.group(2)), total - 1) if (m.group(1) and m.group(2)) else total - 1
        if start >= total or start > end:
            return Response(status_code=416, headers={"Content-Range": f"bytes */{total}"})
        return Response(data[start:end + 1], status_code=206, media_type=mime, headers={"Content-Range": f"bytes {start}-{end}/{total}", "Accept-Ranges": "bytes", "Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"})
    return Response(data, media_type=mime, headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff", "Content-Security-Policy": "default-src 'none'; img-src 'self' data:"})


@router.get("/checks")
@guarded
def list_checks(document_version_id: int, user=Depends(require_permission("automation", "view"))):
    return {"checks": services()["repo"].list_checks(document_version_id)}


@router.delete("/checks/{check_id}")
@guarded
def delete_check(check_id: str, user=Depends(require_permission("automation", "edit"))):
    services()["repo"].deactivate_check(check_id)
    return {"removed": True}


# ----------------------------------------------------------------------------- baselines

@router.get("/baselines")
@guarded
def list_baselines(document_version_id: int, user=Depends(require_permission("automation", "view"))):
    return {"baselines": services()["repo"].list_baselines(document_version_id)}


@router.post("/baselines")
@guarded
def create_baseline(body: BaselineBody, user=Depends(require_permission("automation", "edit"))):
    return services()["repo"].create_baseline(body.document_version_id, body.asset_id, body.run_id, body.label, body.notes, _name(user))


@router.post("/baselines/{baseline_id}/transition")
@guarded
def baseline_transition(baseline_id: str, body: Transition, user=Depends(require_permission("automation", "edit"))):
    return services()["repo"].transition_baseline(baseline_id, body.to, _name(user), body.note)


@router.get("/baselines/{baseline_id}/compare")
@guarded
def baseline_compare(baseline_id: str, run_id: str, user=Depends(require_permission("automation", "view"))):
    return services()["repo"].compare_with_baseline(baseline_id, run_id)


# ----------------------------------------------------------------------------- reports and analytics

@router.get("/runs/{run_id}/report")
@guarded
def run_report(run_id: str, format: Literal["json", "html", "xlsx", "pdf"] = "json", user=Depends(require_permission("automation", "view"))):
    S = services()
    if format == "xlsx" and _results(S).counts(run_id)["total_rows"]:
        data, _info = xlsx_report.build(S["repo"], _results(S), S["registry"], run_id)
        base = re.sub(r"[^A-Za-z0-9_-]", "_", run_id)
        _audit_note(user, "EXPORT_UIUX_EXCEL", f"Exported Excel report for {run_id}")
        return Response(data, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", headers={"Content-Disposition": f'attachment; filename="{base}.xlsx"', "X-Content-Type-Options": "nosniff"})
    report = reports.build_report(S["repo"], S["registry"], run_id)
    if format == "json":
        return report
    images = reports.load_report_images(S["repo"], report)
    base = re.sub(r"[^A-Za-z0-9_-]", "_", run_id)
    if format == "html":
        return Response(reports.to_html(report, images), media_type="text/html; charset=utf-8", headers={"Content-Disposition": f'attachment; filename="{base}.html"', "X-Content-Type-Options": "nosniff"})
    if format == "xlsx":
        return Response(reports.to_xlsx(report, images), media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", headers={"Content-Disposition": f'attachment; filename="{base}.xlsx"'})
    return Response(reports.to_pdf(report, images), media_type="application/pdf", headers={"Content-Disposition": f'attachment; filename="{base}.pdf"'})


@router.get("/analytics")
@guarded
def analytics(document_version_id: int | None = None, user=Depends(require_permission("automation", "view"))):
    return services()["repo"].analytics(document_version_id)


# ----------------------------------------------------------------------------- one-click workflow (Design -> URL -> categories -> Run)

def _quick_fail(error: "quick.QuickError", extra=None):
    return JSONResponse(status_code=error.status, content={"detail": str(error), "code": error.code, **(extra or {})})


class QuickTargetBody(BaseModel):
    url: str = Field(min_length=3, max_length=2048)
    environment: str = Field(default="auto", max_length=20)
    authorized: bool = False


class QuickPlanBody(QuickTargetBody):
    asset_id: str = Field(min_length=3, max_length=60)
    frame_ids: list[str] = Field(default_factory=list, max_length=60)
    viewport: str = Field(default="auto", max_length=10)
    custom_viewport: dict | None = None
    auth: Literal["none", "stored"] = "none"


class QuickRunBody(QuickPlanBody):
    document_version_id: int = Field(gt=0)
    rows: list[dict] = Field(default_factory=list, max_length=60)
    categories: list[str] = Field(default_factory=list, max_length=20)
    tolerances: dict = Field(default_factory=dict)
    build_label: str = Field(default="", max_length=80)
    ai: bool = False
    colour_scheme: Literal["light", "dark"] = "light"
    visual: dict = Field(default_factory=dict)


@router.get("/categories")
@guarded
def test_categories(user=Depends(require_permission("automation", "view"))):
    """The 14 test categories, tolerance defaults and limits, viewport presets and environments for the one-click form."""
    d = Tolerances()
    return {"categories": uiux_categories.public(), "tolerances": {"defaults": {k: getattr(d, k) for k in d.__dataclass_fields__}, "limits": dict(Tolerances.LIMITS)},
            "viewports": {"auto": None, "desktop": [1440, 900], "tablet": [768, 1024], "mobile": [375, 812]}, "environments": list(ENVIRONMENTS)}


@router.post("/quick/target-check")
def quick_target_check(body: QuickTargetBody, user=Depends(require_permission("automation", "execute"))):
    S = services()
    try:
        out = quick.check_target(S["repo"], body.url, body.environment, _name(user), _is_admin(user), body.authorized, probe=True)
    except quick.QuickError as error:
        return _quick_fail(error)
    out["credentials_set"] = bool(S["vault"].status(f"app:{out['target_id']}").get("is_set"))
    return out


@router.post("/quick/prepare/{asset_id}")
@guarded
def quick_prepare(asset_id: str, user=Depends(require_permission("automation", "execute"))):
    """Make a selected design testable: a connected Figma file is analysed automatically when its frames have no data yet."""
    S = services()
    asset = S["registry"].get(asset_id)
    selected = [f for f in asset["frames"] if f["selected"]]
    analysed, notes = False, []
    if quick.needs_analysis(asset, selected):
        try:
            res = S["registry"].analyze(asset_id, _name(user))
            analysed, notes = True, res.get("notes", [])
            asset = res["asset"] if "asset" in res else S["registry"].get(asset_id)
        except RegistryError as error:
            notes.append(str(error))
    fresh = S["registry"].get(asset_id)
    fresh["analysis"] = analyse_design(fresh)
    return {"asset": fresh, "analyzed": analysed, "notes": notes,
            "capabilities": {f["id"]: quick.capabilities(f) for f in fresh["frames"]}}


@router.post("/quick/plan")
def quick_plan(body: QuickPlanBody, user=Depends(require_permission("automation", "execute"))):
    S = services()
    try:
        target = quick.check_target(S["repo"], body.url, body.environment, _name(user), _is_admin(user), body.authorized, probe=False)
        plan = quick.build_plan(S["repo"], S["registry"], body.asset_id, body.frame_ids, target["target_id"], target["route"], body.viewport, body.custom_viewport, _name(user))
    except quick.QuickError as error:
        return _quick_fail(error)
    except (RepoError, RegistryError, TargetError) as error:
        return JSONResponse(status_code=400, content={"detail": str(error)})
    return {"target": target, "plan": plan}


@router.post("/runs/quick")
def quick_run(body: QuickRunBody, user=Depends(require_permission("automation", "execute"))):
    """Everything the one-click Run needs in a single validated request."""
    S = services()
    try:
        target = quick.check_target(S["repo"], body.url, body.environment, _name(user), _is_admin(user), body.authorized, probe=False)
        asset = S["registry"].get(body.asset_id)
        if asset["document_version_id"] != body.document_version_id:
            raise quick.QuickError("The design does not belong to the selected version.", "version")
        selected = [f for f in asset["frames"] if (f["id"] in body.frame_ids if body.frame_ids else f["selected"])]
        if quick.needs_analysis(asset, selected):
            S["registry"].analyze(body.asset_id, _name(user))
        rows = body.rows
        if not rows:
            plan = quick.build_plan(S["repo"], S["registry"], body.asset_id, body.frame_ids, target["target_id"], target["route"], body.viewport, body.custom_viewport, _name(user))
            if plan["ambiguous"]:
                return JSONResponse(status_code=409, content={"detail": "Check the page matched to each frame, then run again.", "code": "needs_mapping", "plan": plan})
            rows = plan["rows"]
        mapping_ids = quick.apply_rows(S["repo"], S["registry"], body.asset_id, target["target_id"], rows, body.viewport, body.custom_viewport, _name(user))
        cfg = quick.build_config(body.model_dump(), target["target_id"], mapping_ids, target.get("environment", ""))
        run = S["repo"].create_run(body.document_version_id, body.asset_id, cfg, _name(user))
    except quick.QuickError as error:
        return _quick_fail(error)
    except HTTPException:
        raise
    except (RepoError, RegistryError, HierarchyError, TargetError) as error:
        return JSONResponse(status_code=404 if "does not exist" in str(error) else 400, content={"detail": str(error)})
    S["manager"].start(run["run_id"])
    out = S["repo"].get_run(run["run_id"])
    out["target"] = {"origin": target["origin"], "route": target["route"], "newly_authorized": target["newly_authorized"], "reachable": None}
    return out


@router.post("/runs/{run_id}/pause")
@guarded
def pause_run(run_id: str, user=Depends(require_permission("automation", "execute"))):
    S = services()
    S["manager"].pause(run_id)
    return S["repo"].get_run(run_id)


@router.post("/runs/{run_id}/resume")
@guarded
def resume_run(run_id: str, user=Depends(require_permission("automation", "execute"))):
    S = services()
    S["manager"].resume(run_id)
    return S["repo"].get_run(run_id)


@router.get("/runs/{run_id}/live")
@guarded
def run_live(run_id: str, after: int = Query(default=0, ge=0), user=Depends(require_permission("automation", "view"))):
    """Status, progress, the newest steps and what the live view should show. Polled by the Test & Run tab."""
    S = services()
    run = S["repo"].get_run(run_id)
    steps = S["repo"].list_steps(run_id, after=after, limit=200)
    live = S["manager"].live_state(run_id) or (run["progress"] or {}).get("live") or {}
    return {"run_id": run_id, "status": run["status"], "status_detail": run["status_detail"], "progress": run["progress"], "steps": steps, "live": live,
            "live_kind": "refreshed screenshots (not a video stream)", "cancel_requested": run["cancel_requested"],
            "summary": {k: run["summary"].get(k) for k in ("totals", "checks", "categories", "screens_tested")} if run["status"] not in ("Queued", "Running", "Paused") else {}}


@router.get("/runs/{run_id}/live-frame")
@guarded
def run_live_frame(run_id: str, user=Depends(require_permission("automation", "view"))):
    S = services()
    rel = S["storage"].relative(*S["repo"].run_dir(run_id), "live.jpg")
    if not S["storage"].exists(rel):
        raise HTTPException(404, "There is no live frame for this run yet.")
    return Response(S["storage"].read(rel), media_type="image/jpeg", headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})


@router.get("/runs/{run_id}/steps")
@guarded
def run_steps(run_id: str, after: int = Query(default=0, ge=0), limit: int = Query(default=500, ge=1, le=2000), user=Depends(require_permission("automation", "view"))):
    return {"steps": services()["repo"].list_steps(run_id, after, limit)}


@router.get("/runs/{run_id}/replay")
@guarded
def run_replay(run_id: str, user=Depends(require_permission("automation", "view"))):
    """Everything needed to replay a finished run from what was recorded. It never opens the website again."""
    S = services()
    run = S["repo"].get_run(run_id)
    steps = S["repo"].list_steps(run_id, limit=2000)
    videos = S["repo"].list_evidence(run_id, "video")
    shots = S["repo"].list_evidence(run_id, "screenshot")
    return {"run": {k: run[k] for k in ("run_id", "status", "status_detail", "app_url", "environment", "build_label", "started_at", "ended_at", "domain", "module", "knowledge_name", "version", "source_file")},
            "steps": steps, "videos": videos, "screenshots": shots,
            "capability": ("video" if videos else ("screenshots" if shots else "steps")),
            "note": ("A screen recording was captured for this run." if videos else
                     "No screen recording is available for this run (it was recorded before recordings existed, or the recording could not be saved). "
                     "The replay shows the recorded steps with timestamped screenshots."),
            "rerun": False}


# ----------------------------------------------------------------------------- v4: results, history, ClickUp

@router.get("/runs/{run_id}/summary")
@guarded
def run_header_summary(run_id: str, user=Depends(require_permission("automation", "view"))):
    S = services()
    return run_summary.run_header(S["repo"], S["registry"], _results(S), run_id)


@router.get("/runs/{run_id}/results")
@guarded
def run_results(run_id: str, result: str = "", category: str = "", severity: str = "", ticket_status: str = "", text: str = Query(default="", max_length=120),
                limit: int = Query(default=100, ge=1, le=500), offset: int = Query(default=0, ge=0), user=Depends(require_permission("automation", "view"))):
    S = services()
    S["repo"].get_run(run_id)
    return _results(S).list(run_id, result, category, severity, ticket_status, text, limit, offset)


@router.get("/results/{result_id}")
@guarded
def result_detail(result_id: str, user=Depends(require_permission("automation", "view"))):
    try:
        return _results(services()).get(result_id)
    except LookupError as error:
        raise HTTPException(404, str(error)) from None


@router.get("/history")
@guarded
def history(limit: int = Query(default=100, ge=1, le=500), text: str = Query(default="", max_length=120), user=Depends(require_permission("automation", "view"))):
    return {"runs": _results(services()).history(limit, text)}


@router.get("/clickup/configurations")
@guarded
def clickup_configurations(user=Depends(require_permission("automation", "view"))):
    try:
        return {"configurations": _clickup(services()).configurations()}
    except Exception:
        return {"configurations": [], "unavailable": "ClickUp configurations could not be read."}


@router.get("/clickup/discover")
def clickup_discover(config_id: int, kind: Literal["workspaces", "spaces", "folders", "lists", "folderless_lists"], parent_id: str = Query(default="", max_length=40),
                     user=Depends(require_permission("automation", "execute"))):
    try:
        return _clickup(services()).discover(config_id, kind, parent_id)
    except TicketError as error:
        return _ticket_fail(error)


@router.get("/clickup/metadata")
def clickup_metadata(config_id: int, list_id: str = Query(max_length=40), user=Depends(require_permission("automation", "execute"))):
    try:
        return _clickup(services()).metadata(config_id, list_id)
    except TicketError as error:
        return _ticket_fail(error)


@router.get("/results/{result_id}/ticket-form")
def ticket_form(result_id: str, user=Depends(require_permission("automation", "execute"))):
    try:
        return _clickup(services()).prefill(result_id)
    except TicketError as error:
        return _ticket_fail(error)


class TicketBody(BaseModel):
    config_id: int
    list_id: str = Field(default="", max_length=40)
    title: str = Field(min_length=1, max_length=250)
    description: str = Field(default="", max_length=30000)
    task_type: str = Field(default="", max_length=30)
    priority: int | None = Field(default=None, ge=1, le=4)
    status: str = Field(default="", max_length=80)
    assignees: list[str] = Field(default_factory=list, max_length=10)
    tags: list[str] = Field(default_factory=list, max_length=12)
    attach: list[Literal["design", "actual", "diff"]] = Field(default_factory=lambda: ["design", "actual", "diff"])
    attempt_token: str = Field(min_length=8, max_length=64)
    create_another: bool = False


@router.post("/results/{result_id}/ticket")
def create_ticket(result_id: str, body: TicketBody, user=Depends(require_permission("automation", "execute"))):
    """Create one ClickUp task for one result. Real tickets are only created here, on an explicit user action in the popup."""
    S = services()
    try:
        out = _clickup(S).create(result_id, body.model_dump(), _name(user))
    except TicketError as error:
        _audit_note(user, "UIUX_CLICKUP_TICKET_FAILED", f"result={result_id} status={error.status}")
        return _ticket_fail(error)
    _audit_note(user, "UIUX_CLICKUP_TICKET_" + out["status"].upper(), f"result={result_id} task={out['ticket'].get('task_id', '')}")
    return out


@router.get("/runs/{run_id}/tickets")
@guarded
def run_tickets(run_id: str, user=Depends(require_permission("automation", "view"))):
    return {"tickets": _results(services()).links_for_run(run_id)}
