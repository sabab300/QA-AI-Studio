"""Task 6 - Change Impact Analysis APIs. Reuses the existing bearer auth and role permissions.

Permissions (no new permission resource is introduced, so existing role seeds keep working):
* read / history / reports ......... qa_engineering:view  AND knowledge:view (results quote documents)
* start / cancel an analysis ....... qa_engineering:create AND knowledge:view
* review findings, confirm deps .... qa_engineering:edit
* approve regression selection ..... qa_engineering:execute
* AI explanations .................. qa_engineering:view AND ai_assistant:view
"""
import sqlite3
import threading
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field

from Core.change_impact.service import ChangeImpactService, STEPS
from Core.change_impact.util import ImpactError
from Core.user_repository import UserRepository
from Web.deps import get_current_user, require_permission

router = APIRouter(prefix='/api/change-impact', tags=['change-impact'])

_service = None
_lock = threading.Lock()


def _may_view_documents(actor, owner=None):
    """No per-document ACL exists in QA AI Studio; document access is the role's knowledge:view right."""
    try:
        return 'view' in UserRepository().get_permissions_for_role(actor['role_id']).get('knowledge', set())
    except Exception:
        return False


def get_service():
    global _service
    with _lock:
        if _service is None:
            _service = ChangeImpactService(access=_may_view_documents)
        return _service


def set_service(service):
    """Test hook: inject a service built on disposable data."""
    global _service
    with _lock:
        _service = service


class Strict(BaseModel):
    model_config = ConfigDict(extra='forbid')


class StartRequest(Strict):
    mode: Literal['VERSION_COMPARE', 'SINGLE_DOCUMENT']
    changed_source_id: int = Field(gt=0)
    baseline_source_id: Optional[int] = Field(default=None, gt=0)
    title: str = Field(default='', max_length=160)
    options: dict = Field(default_factory=dict)


class ReviewRequest(Strict):
    finding_id: str = Field(min_length=1, max_length=80)
    decision: Literal['Accepted', 'Rejected', 'Investigate']
    comment: str = Field(default='', max_length=2000)


class DependencyRequest(Strict):
    finding_id: str = Field(min_length=1, max_length=80)
    decision: Literal['Confirmed', 'Rejected']
    comment: str = Field(default='', max_length=2000)


class ApprovalRequest(Strict):
    test_case_ids: list[int] = Field(min_length=1, max_length=500)
    comment: str = Field(default='', max_length=2000)


class CommentRequest(Strict):
    finding_id: str = Field(default='', max_length=80)
    comment: str = Field(min_length=3, max_length=2000)


class ExplainRequest(Strict):
    subject_type: Literal['finding', 'change', 'risk']
    subject_id: str = Field(min_length=1, max_length=80)
    refresh: bool = False


def _audit(user, action, detail):
    try:
        UserRepository().write_audit_log(user_id=user['id'], username=user['username'], action=action, resource='qa_engineering', detail=str(detail)[:500])
    except Exception:
        pass  # the change-impact audit trail (ci_events) is authoritative; never fail a request over the mirror


def invoke(action, *args, **kwargs):
    try:
        return action(*args, **kwargs)
    except ImpactError as error:
        raise HTTPException(error.status, {'message': error.message, 'code': error.code})
    except ValueError as error:
        raise HTTPException(400, str(error))


def require_all(*pairs):
    """Dependency requiring every (resource, action) pair."""
    async def _dependency(request: Request, user=Depends(get_current_user)):
        permissions = UserRepository().get_permissions_for_role(user['role_id'])
        for resource, action in pairs:
            if action not in permissions.get(resource, set()):
                raise HTTPException(403, "Your role ('%s') does not have '%s' access to '%s'." % (user.get('role_name'), action, resource))
        return user
    return _dependency


view_all = require_all(('qa_engineering', 'view'), ('knowledge', 'view'))
create_all = require_all(('qa_engineering', 'create'), ('knowledge', 'view'))
edit_perm = require_all(('qa_engineering', 'edit'), ('knowledge', 'view'))
approve_perm = require_all(('qa_engineering', 'execute'), ('knowledge', 'view'))
explain_perm = require_all(('qa_engineering', 'view'), ('knowledge', 'view'), ('ai_assistant', 'view'))


@router.get('/config')
def config(user=Depends(view_all)):
    service = get_service()
    ok, reason = service.ai.available()
    permissions = UserRepository().get_permissions_for_role(user['role_id'])
    can = lambda r, a: a in permissions.get(r, set())
    limits = service.policy['limits']
    return {'steps': list(STEPS), 'modes': ['VERSION_COMPARE', 'SINGLE_DOCUMENT'],
            'ai': {'available': bool(ok), 'reason': reason}, 'limits': {k: limits[k] for k in ('max_changes', 'max_dependency_depth', 'max_regression_items', 'max_graph_nodes')},
            'risk_weights': service.policy['risk_weights'], 'risk_levels': service.policy['risk_levels'], 'priority_rules': service.policy['priority_rules'],
            'can': {'start': can('qa_engineering', 'create'), 'review': can('qa_engineering', 'edit'), 'approve': can('qa_engineering', 'execute'),
                    'explain': can('ai_assistant', 'view'), 'generate_tests': can('qa_engineering', 'create')}}


@router.get('/sources')
def sources(domain: Optional[str] = None, module: Optional[str] = None, knowledge_name: Optional[str] = None, document_type: Optional[str] = None,
            user=Depends(view_all)):
    service = get_service()
    conn = service.db.get_connection()
    conn.row_factory = sqlite3.Row
    try:
        sql = ('SELECT id,file_name,source_type,document_version_id,domain,module,knowledge_name,document_type,version FROM knowledge_items '
               "WHERE document_version_id IS NOT NULL AND COALESCE(status,'') NOT IN ('Deleted','Retired','Archived')")
        values = []
        for column, value in (('domain', domain), ('module', module), ('knowledge_name', knowledge_name), ('document_type', document_type)):
            if value:
                sql += ' AND %s=?' % column
                values.append(value)
        sql += ' ORDER BY domain,module,knowledge_name,document_type,version,file_name LIMIT 3000'
        return {'items': [dict(r) for r in conn.execute(sql, values)]}
    finally:
        conn.close()


@router.post('/analyses', status_code=202)
def start(body: StartRequest, user=Depends(create_all)):
    header = invoke(get_service().start, body.model_dump(), user)
    _audit(user, 'CHANGE_IMPACT_START', '%s %s' % (header['id'], body.mode))
    return header


@router.get('/analyses')
def history(limit: int = 100, offset: int = 0, status: Optional[str] = None, search: Optional[str] = None, mine: bool = False, user=Depends(view_all)):
    return {'items': get_service().repo.list(limit, offset, status, search, user['id'] if mine else None)}


@router.get('/analyses/{analysis_id}')
def header(analysis_id: str, user=Depends(view_all)):
    return invoke(get_service().repo.header, analysis_id)


@router.get('/analyses/{analysis_id}/result')
def result(analysis_id: str, user=Depends(view_all)):
    return invoke(get_service().result, analysis_id)


@router.post('/analyses/{analysis_id}/cancel')
def cancel(analysis_id: str, user=Depends(create_all)):
    service = get_service()
    head = invoke(service.repo.header, analysis_id)
    permissions = UserRepository().get_permissions_for_role(user['role_id'])
    if head['created_by_name'] != user['username'] and 'delete' not in permissions.get('qa_engineering', set()):
        raise HTTPException(403, 'Only the person who started an analysis, or an administrator, can cancel it.')
    out = invoke(service.cancel, analysis_id, user)
    _audit(user, 'CHANGE_IMPACT_CANCEL', analysis_id)
    return out


@router.post('/analyses/{analysis_id}/review')
def review(analysis_id: str, body: ReviewRequest, user=Depends(edit_perm)):
    out = invoke(get_service().review_finding, analysis_id, body.finding_id, body.decision, body.comment, user)
    _audit(user, 'CHANGE_IMPACT_REVIEW', '%s %s %s' % (analysis_id, body.finding_id, body.decision))
    return out


@router.post('/analyses/{analysis_id}/dependencies')
def dependency(analysis_id: str, body: DependencyRequest, user=Depends(edit_perm)):
    out = invoke(get_service().confirm_dependency, analysis_id, body.finding_id, body.decision, body.comment, user)
    _audit(user, 'CHANGE_IMPACT_DEPENDENCY', '%s %s %s' % (analysis_id, body.finding_id, body.decision))
    return out


@router.post('/analyses/{analysis_id}/regression/approve')
def approve(analysis_id: str, body: ApprovalRequest, user=Depends(approve_perm)):
    out = invoke(get_service().approve_regression, analysis_id, body.test_case_ids, body.comment, user)
    _audit(user, 'CHANGE_IMPACT_APPROVE_REGRESSION', '%s %d tests' % (analysis_id, out['approved']))
    return out


@router.post('/analyses/{analysis_id}/comments')
def comment(analysis_id: str, body: CommentRequest, user=Depends(edit_perm)):
    return invoke(get_service().add_comment, analysis_id, body.finding_id, body.comment, user)


@router.post('/analyses/{analysis_id}/explain')
def explain(analysis_id: str, body: ExplainRequest, user=Depends(explain_perm)):
    return invoke(get_service().explain, analysis_id, body.subject_type, body.subject_id, user, body.refresh)


@router.post('/analyses/{analysis_id}/gaps/{finding_id}/handoff')
def handoff(analysis_id: str, finding_id: str, user=Depends(create_all)):
    service = get_service()
    snapshot = invoke(service.repo.snapshot, analysis_id)
    gap = next((f for f in snapshot['findings'] if f['finding_id'] == finding_id and f['kind'] == 'GAP'), None)
    if not gap:
        raise HTTPException(404, {'message': 'That coverage gap does not belong to this analysis.', 'code': 'FINDING_NOT_FOUND'})
    service.repo.log_event(analysis_id, 'GAP_HANDOFF', user, {'finding_id': finding_id})
    return {'handoff': gap['payload']['handoff'], 'notice': 'Nothing was created. Review the evidence in QA Engineering and generate tests there.'}


@router.get('/analyses/{analysis_id}/report')
def report_json(analysis_id: str, user=Depends(view_all)):
    return invoke(get_service().report, analysis_id)


@router.get('/analyses/{analysis_id}/report.{fmt}')
def report_file(analysis_id: str, fmt: Literal['xlsx', 'pdf'], user=Depends(view_all)):
    data, name, mime = invoke(get_service().export, analysis_id, fmt, user)
    _audit(user, 'CHANGE_IMPACT_EXPORT', '%s %s' % (analysis_id, fmt))
    return Response(content=data, media_type=mime, headers={'Content-Disposition': 'attachment; filename="%s"' % name, 'Cache-Control': 'no-store'})


@router.get('/analyses/{analysis_id}/events')
def events(analysis_id: str, user=Depends(view_all)):
    service = get_service()
    invoke(service.repo.header, analysis_id)
    return {'events': service.repo.events(analysis_id)}
