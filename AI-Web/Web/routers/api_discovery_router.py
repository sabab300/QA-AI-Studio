"""AI API Discovery: distinct from Knowledge URL discovery and test replay."""
import json
import sqlite3
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import Response
from fastapi.routing import APIRoute
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, Field

from Core.api_discovery import artifacts
from Core.api_discovery.capture import CAPTURES, resolve_target
from Core.api_discovery.evidence import DiscoveryError, MAX_UPLOAD, parse_har, safe_url
from Core.api_discovery.repository import DiscoveryRepository
from Web.deps import require_permission

class DiscoveryRoute(APIRoute):
    def get_route_handler(self):
        handler=super().get_route_handler()
        async def bounded(request):
            if request.method in {'POST','PUT','PATCH'}:
                limit=MAX_UPLOAD if request.url.path.endswith('/har') or '/collection-import/' in request.url.path or '/preview/' in request.url.path else 2*1024*1024
                data=bytearray()
                async for chunk in request.stream():
                    if len(data)+len(chunk)>limit:raise HTTPException(413,'API Discovery request exceeds its size limit.')
                    data.extend(chunk)
                request._body=bytes(data)
            try:return await handler(request)
            except RequestValidationError:
                # Pydantic's default input echo can include submitted secrets.
                raise HTTPException(422,'Invalid API Discovery request. Check required fields, value types and size limits.') from None
        return bounded


router = APIRouter(prefix="/api/api-discovery", tags=["api-discovery"],route_class=DiscoveryRoute)


def collection_repository():
    from Core.api_discovery.collections import CollectionRepository
    return CollectionRepository()


def collection_error(operation):
    """Shared safe mapping; never expose database paths or Python tracebacks."""
    try:return operation()
    except KeyError:raise HTTPException(404,'Collection or execution was not found for the current user.') from None
    except DiscoveryError as error:raise HTTPException(409,str(error)) from None
    except ValueError:raise HTTPException(400,'Invalid structured collection configuration.') from None
    except (sqlite3.Error,OSError):raise HTTPException(409,'Managed storage is unavailable or changed. No historical collection was overwritten.') from None


def repository():
    return DiscoveryRepository()


def get_session(identity, user):
    try:
        return repository().get(identity, user["id"])
    except KeyError:
        raise HTTPException(404, "Discovery session was not found for the current user.") from None


class StartRequest(BaseModel):
    name: str = Field(min_length=1, max_length=150)
    website: str = Field(min_length=8, max_length=2048)
    authorized_target: bool = False
    duration_seconds: int = Field(default=300, ge=30, le=900)
    mode: Literal["Manual", "AI Agent"] = "Manual"
    instructions: str = Field(default="", max_length=4000)
    allow_local_target: bool = False
    source_id: int | None = Field(default=None,gt=0)
    capture_samples: bool = False
    resource_origins: list[str] = Field(default_factory=list,max_length=8)
    environment: str = Field(default='',max_length=100)


class AnalysisRequest(BaseModel):
    provider_id: int | None = None
    model: str = Field(default="", max_length=200)


class DecisionRequest(BaseModel):
    action_id: str = Field(min_length=1,max_length=64)
    approved: bool
    control_id: str | None = Field(default=None,max_length=64)


class SelectionRequest(AnalysisRequest):
    keys: list[str] = Field(min_length=1,max_length=50)
    request_id: str = Field(default="",max_length=64,pattern=r"^[a-zA-Z0-9_-]*$")


class SaveCasesRequest(BaseModel):
    reviewed: bool = False
    source_knowledge_id: int = Field(gt=0)
    rows: list[dict] = Field(min_length=1,max_length=50)


class KnowledgeRequest(SelectionRequest):
    reviewed: bool = False
    domain: str = Field(min_length=1,max_length=200)
    module: str = Field(min_length=1,max_length=200)
    knowledge_name: str = Field(min_length=1,max_length=300)
    document_type: Literal["Technical Document","Integration Document","Other"] = "Technical Document"
    version: str = Field(default="1.0",min_length=1,max_length=100)


class TransferRequest(BaseModel):
    reviewed: bool = False
    output_id: str = Field(min_length=1,max_length=64)


class CollectionSaveRequest(BaseModel):
    source_id: int = Field(gt=0)
    name: str = Field(min_length=1,max_length=150)
    document: dict
    expected_version: int = Field(default=0,ge=0)
    reviewed: bool = False


class ArtifactSaveRequest(CollectionSaveRequest):
    identity: str | None = Field(default=None,pattern='^[0-9a-f]{32}$')


class ReplayRequest(BaseModel):
    authorized: bool = False
    allowed_origins: list[str] = Field(min_length=1,max_length=10)
    environment: str = Field(default='',max_length=100)
    variables: dict = Field(default_factory=dict,max_length=100)
    secure_variable_names: list[str] = Field(default_factory=list,max_length=50)
    timeout_seconds: int = Field(default=20,ge=1,le=60)
    delay_seconds: float = Field(default=0,ge=0,le=5)
    duration_seconds: int = Field(default=300,ge=1,le=600)
    confirm_state_changes: bool = False
    stop_on_failure: bool = False
    retry_count: int = Field(default=0,ge=0,le=2)
    confirm_retries: bool = False
    allow_local_target: bool = False
    acknowledge_import_warnings: bool = False
    version: int | None = Field(default=None,ge=1)
    selected_request_ids: list[str] | None = Field(default=None,max_length=100)


def replay_configuration(payload,user):
    from Core.api_discovery.policy import approved
    config=payload.model_dump();config['allow_local_target']=False
    for origin in payload.allowed_origins:
        decision=collection_error(lambda:approved(collection_repository(),origin,payload.environment,user))
        config['allow_local_target']=config['allow_local_target'] or decision['allow_local']
    config['target_check']=lambda url:approved(collection_repository(),url,payload.environment,user)
    config['executed_by_name']=user['username']
    if payload.secure_variable_names:
        from Core.test_environment_config import TestEnvironmentConfig
        saved=TestEnvironmentConfig().load().get('api_variables',{})
        for name in payload.secure_variable_names:
            if name not in saved:raise HTTPException(400,'A selected environment variable is unavailable.')
            config['variables'][name]=saved[name]
    return config


@router.get('/targets')
def approved_targets(user=Depends(require_permission('automation','view'))):
    from Core.api_discovery.policy import targets
    return collection_error(lambda:{'targets':targets(collection_repository())})


@router.get('/collections')
def collections_list(source_id: int | None = Query(default=None,gt=0),offset: int=Query(default=0,ge=0),limit: int=Query(default=25,ge=1,le=100),search: str=Query(default='',max_length=150),sort: str='updated_at',user=Depends(require_permission('automation','view'))):
    return collection_error(lambda:collection_repository().page(user['id'],source_id,offset,limit,search,sort) if source_id else {'collections':collection_repository().list(user['id'])})


@router.post('/collections')
def collections_create(payload: CollectionSaveRequest,user=Depends(require_permission('automation','create')),
                       knowledge_user=Depends(require_permission('knowledge','view'))):
    if not payload.reviewed:raise HTTPException(400,'Review the selected requests and source hierarchy before saving.')
    if payload.expected_version!=0:raise HTTPException(400,'New collections start at version 1.')
    return collection_error(lambda:collection_repository().save(user['id'],payload.source_id,payload.name,payload.document))


@router.get('/collections/{identity}')
def collections_get(identity: str,version: int | None = Query(default=None,ge=1),user=Depends(require_permission('automation','view'))):
    return collection_error(lambda:collection_repository().get(identity,user['id'],version))


@router.put('/collections/{identity}')
def collections_update(identity: str,payload: CollectionSaveRequest,user=Depends(require_permission('automation','edit')),
                       knowledge_user=Depends(require_permission('knowledge','view'))):
    if not payload.reviewed:raise HTTPException(400,'Explicit review is required before saving a new immutable version.')
    repo=collection_repository();collection_error(lambda:repo.get(identity,user['id']))
    return collection_error(lambda:repo.save(user['id'],payload.source_id,payload.name,payload.document,identity,payload.expected_version))


@router.post('/collections/{identity}/archive')
def collections_archive(identity: str,user=Depends(require_permission('automation','edit'))):
    return collection_error(lambda:collection_repository().archive(identity,user['id']) or {'archived':True})


@router.post('/collection-import/{format}')
async def collections_import(format: Literal['har','postman','native'],request: Request,
                             source_id: int = Query(gt=0),authorized: bool = False,
                             user=Depends(require_permission('automation','create')),
                             knowledge_user=Depends(require_permission('knowledge','view'))):
    if not authorized:raise HTTPException(400,'Confirm authorization to import this traffic.')
    repo=collection_repository()
    def validate_source():
        with repo.connection() as connection:repo.source(connection,source_id)
    collection_error(validate_source)
    data=bytearray()
    async for chunk in request.stream():
        if len(data)+len(chunk)>MAX_UPLOAD:raise HTTPException(413,'Import exceeds 10 MiB.')
        data.extend(chunk)
    from Core.api_discovery.interchange import parse
    def result():
        document=parse(data,format)
        return {'preview':document,'duplicates':repo.duplicates(user['id'],source_id,document),'source_id':source_id,'saved':False,'executed':False}
    return collection_error(result)


@router.get('/source-artifacts/{source_id}')
def source_artifacts(source_id: int,offset: int=Query(default=0,ge=0),search: str='',sort: str='updated_at',user=Depends(require_permission('knowledge','view'))):
    repo=collection_repository()
    def operation():
        with repo.connection() as connection:repo.source(connection,source_id)
        return repo.page(user['id'],source_id,offset,25,search,sort)
    return collection_error(operation)


@router.post('/source-artifacts/{source_id}/preview/{format}')
async def artifact_preview(source_id: int,format: Literal['har','postman','native'],request: Request,user=Depends(require_permission('knowledge','edit'))):
    return await collections_import(format,request,source_id,True,user,user)


@router.post('/source-artifacts/{source_id}')
def artifact_save(source_id: int,payload: ArtifactSaveRequest,user=Depends(require_permission('knowledge','edit'))):
    if not payload.reviewed or source_id!=payload.source_id:raise HTTPException(400,'Review the collection and selected source before saving the artifact.')
    return collection_error(lambda:collection_repository().save(user['id'],source_id,payload.name,payload.document,payload.identity,payload.expected_version))


@router.get('/source-artifacts/{source_id}/{identity}')
def artifact_get(source_id: int,identity: str,version: int | None=Query(default=None,ge=1),user=Depends(require_permission('knowledge','view'))):
    value=collection_error(lambda:collection_repository().get(identity,user['id'],version))
    if value['source_id']!=source_id:raise HTTPException(404,'Collection is not associated with this source.')
    return value


@router.get('/source-artifacts/{source_id}/{identity}/export/{format}')
def artifact_export(source_id: int,identity: str,format: Literal['native','postman'],user=Depends(require_permission('knowledge','view'))):
    from Core.api_discovery.interchange import postman
    document=artifact_get(source_id,identity,None,user)['document']
    return Response(json.dumps(postman(document) if format=='postman' else document),media_type='application/json',headers={'Content-Disposition':'attachment; filename="api-collection-'+format+'.json"'})


@router.post('/sessions/{identity}/collection-preview')
def captured_collection_preview(identity: str,payload: SelectionRequest,
                                user=Depends(require_permission('automation','create'))):
    session=get_session(identity,user)
    selected=set(payload.keys)
    if not selected<={item['key'] for item in session['endpoints']}:
        raise HTTPException(400,'A selected API is outside this discovery session.')
    requests=[];warnings=[]
    # Preserve execution order and repeated operations. Grouped review rows
    # are not a license to discard create/update/invalid variants of one URL.
    with repository().connection() as connection:
        records=connection.execute('SELECT id,payload_json FROM api_discovery_evidence WHERE session_id=? ORDER BY id',(identity,)).fetchall()
    for record in sorted(records,key=lambda row:json.loads(row['payload_json']).get('request_order',row['id'])):
        item=json.loads(record['payload_json'])
        if item['key'] not in selected:continue
        if len(requests)>=100:raise HTTPException(400,'Select at most 100 captured occurrences per collection.')
        request={'id':'api_'+str(len(requests)+1),'name':item['method']+' '+item['endpoint'],'method':item['method'],
                 'url':item['observed_url'].split('?',1)[0],'headers':item.get('request_headers',{}),'assertions':[],'evidence_ids':[record['id']]}
        request['requires_configuration']=bool(item.get('requires_configuration',False) or (item['query_parameters'] and 'query_sample' not in item) or (item['authentication'] and not item.get('request_headers')) or (item['method'] in {'POST','PUT','PATCH'} and 'request_sample' not in item))
        if 'request_sample' in item:
            request.update(body=item['request_sample'],body_mode=item.get('request_body_mode','json'))
        if item.get('query_sample'):
            from urllib.parse import urlencode
            request['url']+='?'+urlencode([(pair['name'],pair['value']) for pair in item['query_sample']],safe='{}')
        if item['method'] in {'POST','PUT','PATCH','DELETE'} or item['query_parameters'] or item['authentication']:
            warnings.append('Captured field values/authentication were omitted; review bodies, query values and secure authentication references before execution.')
        requests.append(request)
    return {'document':{'name':session['name'],'requests':requests,'variables':{},'evidence_session_id':identity,'compatibility':sorted(set(warnings))}}


@router.get('/collections/{identity}/export/{format}')
def collections_export(identity: str,format: Literal['postman','native'],user=Depends(require_permission('automation','view'))):
    from Core.api_discovery.interchange import postman
    document=collection_error(lambda:collection_repository().get(identity,user['id'])['document'])
    output=postman(document) if format=='postman' else document
    return Response(json.dumps(output,indent=2),media_type='application/json',headers={'Content-Disposition':'attachment; filename="api-collection-'+format+'.json"'})


@router.post('/collections/{identity}/execute')
def collections_execute(identity: str,payload: ReplayRequest,user=Depends(require_permission('automation','execute'))):
    from Core.api_discovery.execution import REPLAYS
    repo=collection_repository();collection=collection_error(lambda:repo.get(identity,user['id'],payload.version))
    if not payload.authorized:raise HTTPException(400,'Authorize this environment and replay scope.')
    if collection['document'].get('compatibility') and not payload.acknowledge_import_warnings:
        raise HTTPException(400,'Review and acknowledge import limitations before executing.')
    if any(item['method'] not in {'GET','HEAD','OPTIONS'} for item in collection['document']['requests']) and not payload.confirm_state_changes:
        raise HTTPException(400,'Confirm state-changing requests before execution.')
    config=replay_configuration(payload,user)
    from Core.api_discovery.replay import prepare
    collection_error(lambda:prepare(collection['document'],config))
    run=collection_error(lambda:REPLAYS.start(repo,identity,user['id'],config,payload.version))
    return {'run_id':run,'status':'QUEUED'}


@router.post('/collections/{identity}/validate')
def collections_validate(identity: str,payload: ReplayRequest,user=Depends(require_permission('automation','execute'))):
    from Core.api_discovery.replay import preview
    collection=collection_error(lambda:collection_repository().get(identity,user['id'],payload.version))
    config=replay_configuration(payload,user)
    return collection_error(lambda:preview(collection['document'],config))


@router.get('/collections/{identity}/runs')
def collections_runs(identity: str,user=Depends(require_permission('automation','view'))):
    return collection_error(lambda:{'runs':collection_repository().runs(identity,user['id'])})


@router.get('/runs/{run_id}')
def execution_detail(run_id: str,user=Depends(require_permission('automation','view'))):
    return collection_error(lambda:collection_repository().get_run(run_id,user['id']))


@router.post('/runs/{run_id}/cancel')
def execution_cancel(run_id: str,user=Depends(require_permission('automation','execute'))):
    from Core.api_discovery.execution import REPLAYS
    collection_error(lambda:collection_repository().get_run(run_id,user['id']))
    return {'cancelled':REPLAYS.cancel(run_id,user['id'])}


@router.get('/runs/{run_id}/report/{format}')
def execution_report(run_id: str,format: Literal['html','pdf','xlsx','json'],user=Depends(require_permission('automation','view'))):
    from Core.api_discovery.execution_reports import render
    run=collection_error(lambda:collection_repository().get_run(run_id,user['id']))
    try:data,mime=render(run['result'],format)
    except ImportError:raise HTTPException(409,'The configured report renderer is unavailable.') from None
    return Response(data,media_type=mime,headers={'Content-Disposition':'attachment; filename="api-execution.'+format+'"'})


@router.get('/runs/{run_id}/compare/{baseline_id}')
def execution_compare(run_id: str,baseline_id: str,user=Depends(require_permission('automation','view'))):
    from Core.api_discovery.execution_reports import compare
    repo=collection_repository()
    current=collection_error(lambda:repo.get_run(run_id,user['id']));baseline=collection_error(lambda:repo.get_run(baseline_id,user['id']))
    if current['collection_id']!=baseline['collection_id']:raise HTTPException(409,'Compare runs belonging to the same collection.')
    return compare(baseline['result'],current['result'])


@router.get("/sessions")
def list_sessions(source_id: int | None=Query(default=None,gt=0),offset: int=Query(default=0,ge=0),search: str=Query(default='',max_length=150),status: str='',legacy: bool=False,user=Depends(require_permission("automation", "view"))):
    return repository().page(user['id'],source_id,offset,25,search,status,legacy) if source_id else {"sessions": repository().list(user["id"])}


@router.get('/sessions/{identity}/evidence/{evidence_id}')
def evidence_detail(identity: str,evidence_id: int,user=Depends(require_permission('automation','view'))):
    try:return repository().evidence(identity,user['id'],evidence_id)
    except KeyError:raise HTTPException(404,'Evidence not found for the current user.') from None


@router.get("/sessions/{identity}")
def detail(identity: str, user=Depends(require_permission("automation", "view"))):
    return get_session(identity, user)


@router.post("/har")
async def upload_har(request: Request, name: str = Query(min_length=1, max_length=150),
                     authorized_target: bool = False,
                     source_id: int | None = Query(default=None,gt=0),
                     user=Depends(require_permission("automation", "create")),
                     knowledge_user=Depends(require_permission('knowledge','view'))):
    if not authorized_target:
        raise HTTPException(400, "Confirm that you are authorized to analyze this traffic.")
    data = bytearray()
    async for chunk in request.stream():
        if len(data) + len(chunk) > MAX_UPLOAD:
            raise HTTPException(413, "HAR exceeds the 10 MiB limit.")
        data.extend(chunk)
    try:
        if source_id is not None:
            repo=collection_repository()
            with repo.connection() as connection:repo.source(connection,source_id)
        evidence = parse_har(data)
        identity = repository().create(user["id"], name.strip(), "HAR upload (sanitized evidence only)", "HAR", evidence,source_id=source_id)
        return get_session(identity, user)
    except DiscoveryError as error:
        raise HTTPException(400, str(error)) from None


@router.post("/sessions")
def start(payload: StartRequest, user=Depends(require_permission("automation", "create")),
          knowledge_user=Depends(require_permission('knowledge','view'))):
    if not payload.authorized_target:
        raise HTTPException(400, "Confirm target authorization before launching a browser.")
    try:
        if payload.source_id is not None:
            repo=collection_repository()
            with repo.connection() as connection:repo.source(connection,payload.source_id)
        from Core.api_discovery.policy import approved
        decision=approved(collection_repository(),payload.website,payload.environment,user)
        allow_local=decision['allow_local'];host,address=decision['host'],decision['address']
        from urllib.parse import urlsplit
        resources=[]
        for origin in payload.resource_origins:
            if origin.rstrip('/') not in [value.rstrip('/') for value in decision['resource_origins']]:raise DiscoveryError('Resource origin is not approved for this target/environment.')
            parts=urlsplit(origin)
            if parts.path not in {'','/'} or parts.query or parts.fragment or parts.username or parts.password:
                raise DiscoveryError('Approved resource origins must contain only scheme, host and optional port.')
            resource_host,resource_address=resolve_target(origin,allow_local=allow_local)
            resources.append({'host':resource_host,'address':resource_address,'scheme':parts.scheme,'port':parts.port or (443 if parts.scheme=='https' else 80)})
        planner = None
        if payload.mode == "AI Agent":
            if not payload.instructions.strip():
                raise DiscoveryError("Enter the workflow goal for AI Agent discovery.")
            from Core.user_repository import UserRepository
            if "view" not in UserRepository().get_permissions_for_role(user["role_id"]).get("ai_assistant", set()):
                raise HTTPException(403, "AI Assistant permission is required for agent planning.")
            from Core.api_discovery.agent import AgentPlanner
            planner = AgentPlanner(payload.instructions, user["username"],allow_approved_cloud=True)
        repo = repository()
        identity = repo.create(user["id"], payload.name.strip(), safe_url(payload.website), payload.mode,source_id=payload.source_id)
        try:
            CAPTURES.start(repo, identity, user["id"], payload.website, host, address, payload.duration_seconds, planner=planner,capture_samples=payload.capture_samples,resource_origins=resources,target_check=lambda url:approved(collection_repository(),url,payload.environment,user))
        except Exception:
            repo.update(identity, user["id"], "failed", "Browser could not be started. Check active capture limits and browser installation.")
            raise
        return repo.get(identity, user["id"])
    except DiscoveryError as error:
        raise HTTPException(409, str(error)) from None


@router.post("/sessions/{identity}/{action}")
def control(identity: str, action: Literal["pause", "resume", "stop", "cancel"],
            user=Depends(require_permission("automation", "create"))):
    get_session(identity, user)
    try:
        CAPTURES.control(identity, user["id"], action)
        return {"requested": action, "message": "Stop completes after browser resources are released."}
    except DiscoveryError as error:
        raise HTTPException(409, str(error)) from None


@router.post("/sessions/{identity}/analysis/run")
def analyze_session(identity: str, payload: AnalysisRequest,
                    user=Depends(require_permission("automation", "create")),
                    ai_user=Depends(require_permission("ai_assistant", "view"))):
    from Core.api_discovery.analysis import analyze
    session = get_session(identity, user)
    if session["status"] not in {"completed", "failed", "interrupted"}:
        raise HTTPException(409, "Stop browser collection before AI analysis.")
    try:
        selection = {"provider_id": payload.provider_id, "model": payload.model} if payload.provider_id and payload.model else None
        result = analyze(session, user["username"], selection)
        repository().update(identity, user["id"], session["status"], session["error"], result)
        return result
    except DiscoveryError as error:
        raise HTTPException(400, str(error)) from None


@router.get("/sessions/{identity}/export/{format}")
def export(identity: str, format: Literal["json", "markdown", "openapi", "python", "javascript", "postman", "xlsx"],
           user=Depends(require_permission("automation", "view"))):
    from Core.api_discovery.reporting import report
    session = report(get_session(identity, user),repository().db_path)
    media = "text/plain; charset=utf-8"
    extension = {"markdown": "md", "python": "py", "javascript": "js", "postman": "postman.json", "openapi": "openapi.json"}.get(format, format)
    try:
        if format == "markdown":
            body = artifacts.markdown(session)
        elif format == "python":
            body = artifacts.python_client(session)
        elif format == "javascript":
            body = artifacts.javascript_client(session)
        elif format == "xlsx":
            from io import BytesIO
            from openpyxl import Workbook
            workbook = Workbook()
            sheet = workbook.active
            sheet.title = "Discovered API Coverage"
            sheet.append(["Scope: discovered endpoints only, not complete application API coverage"])
            sheet.append(["Method", "Endpoint", "Captured requests", "Observed statuses", "Evidence IDs"])
            for endpoint in session["endpoints"]:
                sheet.append([endpoint["method"], endpoint["endpoint"], endpoint["occurrences"],
                              str(endpoint["observed_statuses"]), str(endpoint["evidence_ids"])])
            coverage=workbook.create_sheet("Coverage and execution")
            for key,value in session["coverage"].items():
                coverage.append([key,str(value)])
            coverage.append(["Recorded execution","No result is inferred when execution history is absent"])
            for run in session["execution_history"]:
                coverage.append([str(run.get("test_case_id")),str(run.get("status")),str(run.get("started_at",""))])
            buffer = BytesIO()
            workbook.save(buffer)
            body = buffer.getvalue()
            media = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        else:
            value = artifacts.openapi(session) if format == "openapi" else artifacts.postman(session) if format == "postman" else artifacts.documentation(session)
            body = json.dumps(value, indent=2)
            media = "application/json"
    except ValueError as error:
        raise HTTPException(400, str(error)) from None
    return Response(body, media_type=media, headers={"Content-Disposition": 'attachment; filename="api-discovery-' + identity[:32] + '.' + extension + '"', "Cache-Control": "no-store"})


@router.get("/sessions/{identity}/report/detail")
def report_detail(identity: str,user=Depends(require_permission("automation","view"))):
    from Core.api_discovery.reporting import report
    return report(get_session(identity,user),repository().db_path)


@router.delete("/sessions/{identity}")
def delete(identity: str, user=Depends(require_permission("automation", "edit"))):
    session = get_session(identity, user)
    if session["status"] in {"starting", "recording", "paused","stopping"}:
        raise HTTPException(409, "Stop the browser and wait for completion before deleting its discovery history.")
    try:
        repository().delete(identity, user["id"])
    except DiscoveryError as error:
        raise HTTPException(409, str(error)) from None
    return {"deleted": True}


@router.post("/sessions/{identity}/agent/decision")
def decide(identity: str, payload: DecisionRequest, user=Depends(require_permission("automation","create")),
           ai_user=Depends(require_permission("ai_assistant","view"))):
    get_session(identity,user)
    try:
        CAPTURES.decide(identity,user["id"],payload.action_id,payload.approved,payload.control_id)
        return {"accepted":True}
    except DiscoveryError as error:
        raise HTTPException(409,str(error)) from None


@router.post("/sessions/{identity}/test-cases/generate")
def drafts(identity: str,payload: SelectionRequest,
           user=Depends(require_permission("qa_engineering","create")),
           automation_user=Depends(require_permission("automation","view")),
           ai_user=Depends(require_permission("ai_assistant","view"))):
    from Core.api_discovery.integrations import generate_drafts
    from Core.ai_platform.gateway import CANCELS
    import uuid
    request_id="discovery-drafts:"+str(user['id'])+":"+identity+":"+(payload.request_id or uuid.uuid4().hex)
    _,created=CANCELS.ensure(request_id,owner=user['id'])
    if not created:
        raise HTTPException(409,"This generation request is already running.")
    try:
        selection={"provider_id":payload.provider_id,"model":payload.model} if payload.provider_id and payload.model else None
        return generate_drafts(get_session(identity,user),payload.keys,user["username"],selection,request_id=request_id)
    except DiscoveryError as error:
        raise HTTPException(400,str(error)) from None
    finally:
        CANCELS.finish(request_id)


@router.post("/sessions/{identity}/test-cases/cancel")
def cancel_drafts(identity: str,payload: SelectionRequest,
                  user=Depends(require_permission("qa_engineering","create"))):
    from Core.ai_platform.gateway import CANCELS
    get_session(identity,user)
    request_id="discovery-drafts:"+str(user['id'])+":"+identity+":"+payload.request_id
    return {"cancelled":CANCELS.cancel(request_id,owner=user['id'])}


@router.post("/sessions/{identity}/test-cases/save")
def save_cases(identity: str,payload: SaveCasesRequest,
               user=Depends(require_permission("qa_engineering","create")),
               automation_user=Depends(require_permission("automation","view")),
               knowledge_user=Depends(require_permission("knowledge","view"))):
    from Core.api_discovery.integrations import DiscoveryIntegrations
    if not payload.reviewed:
        raise HTTPException(400,"Review and explicitly confirm the canonical test cases before saving.")
    try:
        return DiscoveryIntegrations(repository()).save_cases(get_session(identity,user),user["id"],payload.source_knowledge_id,payload.rows)
    except (ValueError,FileNotFoundError) as error:
        # Existing canonical validators return useful business messages; do not
        # leak filesystem locations from a missing source/workbook exception.
        message=str(error) if isinstance(error,DiscoveryError) else "Canonical save could not validate the selected source, hierarchy or reviewed rows. No automatic retry was attempted."
        raise HTTPException(409,message) from None


@router.post("/sessions/{identity}/knowledge/save")
def knowledge(identity: str,payload: KnowledgeRequest,
              user=Depends(require_permission("knowledge","create")),
              automation_user=Depends(require_permission("automation","view"))):
    from Core.api_discovery.integrations import DiscoveryIntegrations
    if not payload.reviewed:
        raise HTTPException(400,"Review the destination and confirm documentation save.")
    try:
        return DiscoveryIntegrations(repository()).save_knowledge(get_session(identity,user),user["id"],payload.keys,
                   {k:getattr(payload,k) for k in ("domain","module","knowledge_name","document_type","version")})
    except (ValueError,FileNotFoundError):
        raise HTTPException(409,"Documentation ingestion could not complete. Inspect its persisted processing state before retrying; no successful indexing is claimed.") from None


@router.post("/sessions/{identity}/automation/transfer")
def transfer(identity: str,payload: TransferRequest,
             user=Depends(require_permission("automation","create")),
             qa_user=Depends(require_permission("qa_engineering","view"))):
    from Core.api_discovery.integrations import DiscoveryIntegrations
    if not payload.reviewed:
        raise HTTPException(400,"Confirm transfer of reviewed test cases; execution is not automatic.")
    try:
        return DiscoveryIntegrations(repository()).transfer(get_session(identity,user),user["id"],payload.output_id)
    except (ValueError,FileNotFoundError):
        raise HTTPException(409,"Transfer requires review of endpoint/source ownership. Existing canonical records were preserved; no automatic retry or execution occurred.") from None
