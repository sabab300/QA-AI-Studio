"""Reports > Test Cases Publish and reusable integration configuration APIs."""

from io import BytesIO
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from Core.test_case_publishing import PublishingRepository, PublishingService, TARGETS
from Core.user_repository import UserRepository
from Web.deps import require_permission

router = APIRouter(prefix="/api/publishing", tags=["publishing"])


def _data(model): return model.model_dump() if hasattr(model, "model_dump") else model.dict()
def _audit(user, action, detail): UserRepository().write_audit_log(user_id=user["id"],username=user["username"],action=action,resource="reports",detail=detail)


class ConfigurationRequest(BaseModel):
    target: str
    name: str
    base_url: str = ""
    auth_type: str = "TOKEN"
    secret: str = ""
    settings: Dict[str, Any] = Field(default_factory=dict)
    mappings: Dict[str, Any] = Field(default_factory=dict)
    is_default: bool = False


class DuplicateRequest(BaseModel): name: str
class ClickUpSessionRequest(BaseModel):
    configuration_id: Optional[int] = None
    base_url: str = "https://api.clickup.com/api/v2"
    secret: str = ""
    kind: str = "workspaces"
    parent_id: str = ""
    workspace_id: str = ""
    space_id: str = ""
class WorkspaceRowsRequest(BaseModel):
    target: str = ""
    configuration_id: Optional[int] = None
    test_case_version_id: int
    test_case_file_id: int
    rows: List[Dict[str, Any]]
    validate_destination: bool = False


class PublishRequest(WorkspaceRowsRequest):
    allow_republish: bool = False


@router.get("/configurations")
def list_configurations(target: str | None = None,current_user=Depends(require_permission("settings","view"))):
    normalized=target.upper() if target else None
    if normalized and normalized not in TARGETS: raise HTTPException(422,"Unsupported integration target.")
    return {"configurations":PublishingRepository().list_configs(normalized)}


@router.post("/configurations")
def create_configuration(payload:ConfigurationRequest,current_user=Depends(require_permission("settings","edit"))):
    try: result=PublishingRepository().save_config(_data(payload))
    except ValueError as error: raise HTTPException(422,str(error))
    _audit(current_user,"CREATE_PUBLISHING_CONFIGURATION",f"Created {result['target']} configuration '{result['name']}'")
    return result


@router.put("/configurations/{config_id}")
def update_configuration(config_id:int,payload:ConfigurationRequest,current_user=Depends(require_permission("settings","edit"))):
    try: result=PublishingRepository().save_config(_data(payload),config_id)
    except ValueError as error: raise HTTPException(422,str(error))
    _audit(current_user,"UPDATE_PUBLISHING_CONFIGURATION",f"Updated configuration id={config_id}")
    return result


@router.post("/configurations/{config_id}/duplicate")
def duplicate_configuration(config_id:int,payload:DuplicateRequest,current_user=Depends(require_permission("settings","edit"))):
    try: result=PublishingRepository().duplicate_config(config_id,payload.name)
    except ValueError as error: raise HTTPException(422,str(error))
    _audit(current_user,"DUPLICATE_PUBLISHING_CONFIGURATION",f"Duplicated configuration id={config_id}")
    return result


@router.delete("/configurations/{config_id}")
def delete_configuration(config_id:int,current_user=Depends(require_permission("settings","edit"))):
    try: PublishingRepository().delete_config(config_id)
    except ValueError as error: raise HTTPException(409,str(error))
    _audit(current_user,"DELETE_PUBLISHING_CONFIGURATION",f"Deleted configuration id={config_id}")
    return {"status":"deleted"}


@router.post("/configurations/{config_id}/test")
def test_configuration(config_id:int,current_user=Depends(require_permission("settings","edit"))):
    try: return PublishingService().test_connection(config_id)
    except ValueError as error: raise HTTPException(422,str(error))


@router.get("/configurations/{config_id}/clickup-discovery")
def clickup_discovery(config_id:int,kind:str,parent_id:str="",current_user=Depends(require_permission("settings","view"))):
    try: return PublishingService().discover_clickup(config_id,kind,parent_id)
    except ValueError as error: raise HTTPException(422,str(error))


@router.post("/clickup/test")
def test_clickup_session(payload:ClickUpSessionRequest,current_user=Depends(require_permission("settings","edit"))):
    try: return PublishingService().test_clickup_values(_data(payload))
    except ValueError as error: raise HTTPException(422,str(error))


@router.post("/clickup/discovery")
def discover_clickup_session(payload:ClickUpSessionRequest,current_user=Depends(require_permission("settings","view"))):
    try: return PublishingService().discover_clickup_values(_data(payload),payload.kind,payload.parent_id)
    except ValueError as error: raise HTTPException(422,str(error))


@router.get("/mapping-source-fields")
def mapping_source_fields(current_user=Depends(require_permission("settings","view"))):
    from Core.test_case_workbook import COLUMNS, DEFAULT_CLICKUP_DESCRIPTION_TEMPLATE, TEMPLATE_TOKEN_ALIASES
    return {"fields":[{"id":field,"name":label} for field,label in COLUMNS],"aliases":TEMPLATE_TOKEN_ALIASES,"description_template":DEFAULT_CLICKUP_DESCRIPTION_TEMPLATE}


@router.get("/workspace")
def load_workspace(test_case_version_id:int,test_case_file_id:int,current_user=Depends(require_permission("dashboard","view"))):
    try: return PublishingService().load_workspace(test_case_version_id,test_case_file_id)
    except ValueError as error: raise HTTPException(404,str(error))


@router.post("/preview")
def preview(payload:WorkspaceRowsRequest,current_user=Depends(require_permission("dashboard","view"))):
    if not payload.configuration_id: raise HTTPException(422,"A saved configuration is required.")
    try: return PublishingService().preview(payload.target,payload.configuration_id,payload.rows,payload.validate_destination)
    except ValueError as error: raise HTTPException(422,str(error))


@router.post("/validate")
def validate(payload:WorkspaceRowsRequest,current_user=Depends(require_permission("dashboard","view"))):
    errors=PublishingService().validate_rows(payload.rows)
    return {"valid":not errors,"errors":errors}


@router.post("/export.xlsx")
def export_xlsx(payload:WorkspaceRowsRequest,current_user=Depends(require_permission("dashboard","view"))):
    try: data=PublishingService().export(payload.rows,payload.target,payload.configuration_id)
    except ValueError as error: raise HTTPException(422,str(error))
    filename=f"TestManager_Final_{payload.test_case_version_id}_{payload.test_case_file_id}.xlsx" if payload.target.upper()=="TESTMANAGER" else f"QA_AI_Studio_Final_{payload.test_case_version_id}_{payload.test_case_file_id}.xlsx"
    _audit(current_user,"EXPORT_PUBLISHING_WORKBOOK",f"Exported {len(payload.rows)} publishing rows")
    return StreamingResponse(BytesIO(data),media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",headers={"Content-Disposition":f'attachment; filename="{filename}"'})


@router.post("/publish")
def publish(payload:PublishRequest,current_user=Depends(require_permission("dashboard","view"))):
    if not payload.configuration_id: raise HTTPException(422,"A saved configuration is required.")
    try: result=PublishingService().publish(payload.target,payload.configuration_id,payload.test_case_version_id,payload.test_case_file_id,payload.rows,current_user["username"],payload.allow_republish)
    except ValueError as error: raise HTTPException(422,str(error))
    _audit(current_user,"PUBLISH_TEST_CASES",f"{payload.target}: {result['success']} succeeded, {result['failed']} failed, {result['skipped']} skipped")
    return result


@router.get("/history")
def history(current_user=Depends(require_permission("dashboard","view"))):
    return {"runs":PublishingRepository().history()}
