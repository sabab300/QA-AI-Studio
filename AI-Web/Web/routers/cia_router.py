"""Change Impact Analysis (merged module) - /api/cia. Same bearer auth and role permissions as the earlier Change Impact module.

* options / history / results / report ... qa_engineering:view AND knowledge:view (results quote documents)
* start / cancel an analysis ............. qa_engineering:create AND knowledge:view
* QA review decisions .................... qa_engineering:edit AND knowledge:view
"""
import threading
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field

from Core.change_impact.util import ImpactError
from Core.cia.service import CiaService
from Core.user_repository import UserRepository
from Web.routers.change_impact_router import require_all, _may_view_documents

router = APIRouter(prefix='/api/cia', tags=['change-impact-analysis'])

_service = None
_lock = threading.Lock()


def get_service():
    global _service
    with _lock:
        if _service is None:
            _service = CiaService(access=_may_view_documents)
        return _service


def set_service(service):
    """Test hook: inject a service built on disposable data."""
    global _service
    with _lock:
        _service = service


class Strict(BaseModel):
    model_config = ConfigDict(extra='forbid')


class Scope(Strict):
    domain: Optional[str] = Field(default=None, max_length=300)
    module: Optional[str] = Field(default=None, max_length=300)
    knowledge_name: Optional[str] = Field(default=None, max_length=300)
    document_type: Optional[str] = Field(default=None, max_length=300)
    version: Optional[str] = Field(default=None, max_length=300)
    source_id: Optional[int] = Field(default=None, gt=0)


class StartRequest(Strict):
    source_id: int = Field(gt=0)
    scope: Scope = Field(default_factory=Scope)
    use_ai: bool = False
    force: bool = False


class ReviewRequest(Strict):
    item_key: str = Field(min_length=1, max_length=80)
    decision: Literal['Confirmed', 'Not Applicable', 'Needs Investigation']
    comment: str = Field(default='', max_length=2000)


def invoke(action, *args, **kwargs):
    try:
        return action(*args, **kwargs)
    except ImpactError as error:
        raise HTTPException(error.status, {'message': error.message, 'code': error.code})
    except ValueError as error:
        raise HTTPException(400, str(error))


view_all = require_all(('qa_engineering', 'view'), ('knowledge', 'view'))
create_all = require_all(('qa_engineering', 'create'), ('knowledge', 'view'))
edit_perm = require_all(('qa_engineering', 'edit'), ('knowledge', 'view'))


def _audit(user, action, detail):
    try:
        UserRepository().write_audit_log(user_id=user['id'], username=user['username'], action=action, resource='qa_engineering', detail=str(detail)[:500])
    except Exception:
        pass


@router.get('/options')
def options(user=Depends(view_all)):
    permissions = UserRepository().get_permissions_for_role(user['role_id'])
    data = invoke(get_service().options)
    data['can'] = {'start': 'create' in permissions.get('qa_engineering', set()), 'review': 'edit' in permissions.get('qa_engineering', set())}
    return data


@router.post('/analyses', status_code=202)
def start(body: StartRequest, user=Depends(create_all)):
    payload = body.model_dump()
    payload['scope'] = {k: v for k, v in payload['scope'].items() if v not in (None, '')}
    header = invoke(get_service().start, payload, user)
    _audit(user, 'CIA_START', '%s source %s' % (header['id'], body.source_id))
    return header


@router.get('/analyses')
def history(limit: int = 50, source_id: Optional[int] = None, user=Depends(view_all)):
    return {'items': invoke(get_service().history, limit, source_id)}


@router.get('/analyses/{run_id}')
def header(run_id: str, user=Depends(view_all)):
    return invoke(get_service().header, run_id)


@router.get('/analyses/{run_id}/result')
def result(run_id: str, user=Depends(view_all)):
    return invoke(get_service().result, run_id)


@router.post('/analyses/{run_id}/cancel')
def cancel(run_id: str, user=Depends(create_all)):
    service = get_service()
    head = invoke(service.header, run_id)
    permissions = UserRepository().get_permissions_for_role(user['role_id'])
    if head['created_by_name'] != user['username'] and 'delete' not in permissions.get('qa_engineering', set()):
        raise HTTPException(403, 'Only the person who started an analysis, or an administrator, can cancel it.')
    out = invoke(service.cancel, run_id)
    _audit(user, 'CIA_CANCEL', run_id)
    return out


@router.post('/analyses/{run_id}/review')
def review(run_id: str, body: ReviewRequest, user=Depends(edit_perm)):
    out = invoke(get_service().review, run_id, body.item_key, body.decision, body.comment, user)
    _audit(user, 'CIA_REVIEW', '%s %s %s' % (run_id, body.item_key, body.decision))
    return out


@router.get('/analyses/{run_id}/report.xlsx')
def report(run_id: str, user=Depends(view_all)):
    data, name, mime = invoke(get_service().export, run_id)
    _audit(user, 'CIA_EXPORT', run_id)
    return Response(content=data, media_type=mime, headers={'Content-Disposition': 'attachment; filename="%s"' % name, 'Cache-Control': 'no-store'})


@router.get('/relationships')
def relationships(source_id: Optional[int] = None, include_stale: bool = True, user=Depends(view_all)):
    return {'items': get_service().relationships.list(source_id, include_stale)}


@router.post('/relationships/refresh')
def refresh_relationships(user=Depends(edit_perm)):
    out = get_service().relationships.refresh()
    _audit(user, 'CIA_REFRESH_RELATIONSHIPS', out)
    return out
