"""Task 5-owned lifecycle APIs reusing existing auth and permissions."""
from typing import Literal
from fastapi import APIRouter,Depends,HTTPException
from pydantic import BaseModel,Field,ConfigDict
from Web.deps import require_permission
from Core.qa_lifecycle_service import QaLifecycleService
from Core.qa_lifecycle_repository import LifecycleError
from Core.qa_lifecycle_reports import QaLifecycleReports
router=APIRouter(prefix='/api/qa-lifecycle',tags=['qa-lifecycle'])
class StrictModel(BaseModel):model_config=ConfigDict(extra='forbid')
class ReviewRequest(StrictModel):state:str;comments:str=Field(min_length=1,max_length=4000)
class RequirementReview(StrictModel):status:Literal['Confirmed','Retired']
class LinkRequest(StrictModel):test_case_id:int;status:Literal['PROPOSED','CONFIRMED','REJECTED'];rationale:str=Field(default='',max_length=4000)
class AssetRequest(StrictModel):kind:Literal['Manual','Playwright','API','SQL'];reference:str=Field(default='',max_length=250)
class ManualRequest(StrictModel):outcome:Literal['Pass','Fail'];environment:str=Field(min_length=1,max_length=200);evidence:list[str]=Field(default_factory=list,max_length=20);comments:str=Field(min_length=1,max_length=4000)
class FinalizeRequest(StrictModel):asset_link_id:str|None=None;environment:str=Field(min_length=1,max_length=200);evidence:list[str]=Field(default_factory=list,max_length=20)
class DefectRequest(StrictModel):run_id:int;title:str=Field(min_length=1,max_length=250);description:str=Field(min_length=1,max_length=8000);severity:Literal['Critical','High','Medium','Low']='Medium';priority:Literal['Urgent','High','Normal','Low']='Normal';assigned_to:int|None=None
class DefectTransition(StrictModel):state:str;resolution:str=Field(default='',max_length=4000)
class RetestRequest(StrictModel):run_id:int
class ClickupLink(StrictModel):external_id:str=Field(min_length=1,max_length=100)
class AssignmentRequest(StrictModel):user_id:int|None=None
class RecommendationRequest(StrictModel):
    requirement_id:str=Field(min_length=1,max_length=100)
    provider_reference:str=Field(min_length=1,max_length=250)
    rationale:str=Field(min_length=1,max_length=4000)
    content_hash:str=Field(min_length=64,max_length=64)
class RecommendationDecision(StrictModel):
    decision:Literal['APPLIED','REJECTED']
    comments:str=Field(min_length=1,max_length=4000)
def invoke(action,*args,**kwargs):
    try:return action(*args,**kwargs)
    except LifecycleError as error:raise HTTPException(error.status,str(error))
    except ValueError as error:raise HTTPException(400,str(error))
@router.get('/reports')
def reports(domain:str|None=None,module:str|None=None,knowledge_name:str|None=None,document_type:str|None=None,version:str|None=None,document_version_id:int|None=None,execution_type:str|None=None,status:str|None=None,date_from:str|None=None,date_to:str|None=None,user=Depends(require_permission('dashboard','view'))):
    filters={key:value for key,value in locals().items() if key!='user' and value is not None}
    return invoke(QaLifecycleReports(QaLifecycleService()).snapshot,filters)
@router.get('/requirements')
def requirements(document_version_id:int,user=Depends(require_permission('qa_engineering','view'))):
    with QaLifecycleService().connection() as conn:return {'requirements':[dict(row) for row in conn.execute('SELECT * FROM qa_requirements WHERE document_version_id=? ORDER BY anchor',(document_version_id,))]}
@router.get('/actors')
def actors(user=Depends(require_permission('automation','edit'))):
    with QaLifecycleService().connection() as conn:return {'actors':[dict(row) for row in conn.execute("SELECT id,COALESCE(NULLIF(full_name,''),username) display_name FROM users WHERE is_active=1 ORDER BY username")]}
@router.get('/policy')
def policy(user=Depends(require_permission('qa_engineering','view'))):
    service=QaLifecycleService();return {'review_transitions':service.transitions,'defect_transitions':{key:sorted(values) for key,values in service.DEFECT_TRANSITIONS.items()},'independent_approval':bool(service.policy.get('require_independent_approval'))}
@router.post('/sources/{source_id}/extract')
def extract(source_id:int,user=Depends(require_permission('qa_engineering','create'))):return invoke(QaLifecycleService().extract_requirements,source_id,user)
@router.patch('/requirements/{requirement_id}/review')
def review_requirement(requirement_id:str,body:RequirementReview,user=Depends(require_permission('qa_engineering','edit'))):return invoke(QaLifecycleService().confirm_requirement,requirement_id,body.status,user)
@router.put('/requirements/{requirement_id}/links')
def requirement_link(requirement_id:str,body:LinkRequest,user=Depends(require_permission('qa_engineering','edit'))):return invoke(QaLifecycleService().link_requirement,requirement_id,body.test_case_id,body.status,body.rationale,user)
@router.get('/cases/{case_id}/trace')
def trace(case_id:int,user=Depends(require_permission('qa_engineering','view'))):return invoke(QaLifecycleService().trace,case_id)
@router.get('/cases/{case_id}/recommendation-context')
def recommendation_context(case_id:int,user=Depends(require_permission('qa_engineering','view'))):
    return invoke(QaLifecycleService().recommendation_context,case_id)
@router.post('/cases/{case_id}/recommendations')
def recommendation(case_id:int,body:RecommendationRequest,user=Depends(require_permission('qa_engineering','edit'))):
    return invoke(QaLifecycleService().propose_requirement_link,case_id,body.requirement_id,body.provider_reference,body.rationale,body.content_hash,user)
@router.post('/recommendations/{recommendation_id}/decision')
def recommendation_decision(recommendation_id:str,body:RecommendationDecision,user=Depends(require_permission('qa_engineering','edit'))):
    return invoke(QaLifecycleService().decide_recommendation,recommendation_id,body.decision,body.comments,user)
@router.post('/cases/{case_id}/review')
def decide(case_id:int,body:ReviewRequest,user=Depends(require_permission('qa_engineering','edit'))):return invoke(QaLifecycleService().decide_review,case_id,body.state,body.comments,user)
@router.post('/cases/{case_id}/assets')
def asset(case_id:int,body:AssetRequest,user=Depends(require_permission('automation','edit'))):return invoke(QaLifecycleService().register_asset,case_id,body.kind,body.reference,user)
@router.get('/cases/{case_id}/execution-gate')
def execution_gate(case_id:int,user=Depends(require_permission('automation','view'))):
    """Read-only: current approval / Ready for Execution status and the missing prerequisite. Never changes state."""
    from Core.qa_lifecycle_execution_contract import lifecycle_gate
    return invoke(lifecycle_gate,QaLifecycleService().db,case_id)
@router.get('/cases/{case_id}/asset-options')
def asset_options(case_id:int,user=Depends(require_permission('automation','view'))):return invoke(QaLifecycleService().asset_options,case_id)
@router.post('/cases/{case_id}/manual-results')
def manual(case_id:int,body:ManualRequest,user=Depends(require_permission('automation','execute'))):return invoke(QaLifecycleService().record_manual,case_id,body.outcome,body.environment,body.evidence,body.comments,user)
@router.post('/runs/{run_id}/finalize')
def finalize(run_id:int,body:FinalizeRequest,user=Depends(require_permission('automation','edit'))):return invoke(QaLifecycleService().finalize_run,run_id,body.asset_link_id,body.environment,body.evidence,user)
@router.post('/defects')
def create_defect(body:DefectRequest,user=Depends(require_permission('automation','edit'))):return invoke(QaLifecycleService().create_defect,body.run_id,body.title,body.description,body.severity,body.priority,body.assigned_to,user)
@router.patch('/defects/{defect_id}')
def defect_state(defect_id:str,body:DefectTransition,user=Depends(require_permission('automation','edit'))):return invoke(QaLifecycleService().transition_defect,defect_id,body.state,body.resolution,user)
@router.post('/defects/{defect_id}/retests')
def retest(defect_id:str,body:RetestRequest,user=Depends(require_permission('automation','execute'))):return invoke(QaLifecycleService().record_retest,defect_id,body.run_id,user)
@router.put('/defects/{defect_id}/assignment')
def assign(defect_id:str,body:AssignmentRequest,user=Depends(require_permission('automation','edit'))):return invoke(QaLifecycleService().assign_defect,defect_id,body.user_id,user)
@router.put('/defects/{defect_id}/clickup-link')
def clickup(defect_id:str,body:ClickupLink,user=Depends(require_permission('automation','edit'))):return invoke(QaLifecycleService().link_clickup,defect_id,body.external_id,user)
