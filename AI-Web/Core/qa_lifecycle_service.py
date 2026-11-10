"""Authorized lifecycle actions using the existing execution history store."""
import json
import re
import uuid
from types import SimpleNamespace
from Core.qa_lifecycle_repository import QaLifecycleRepository,LifecycleError,now,digest
from Shared.document_hierarchy import DocumentHierarchy

class BorrowedConnection:
    """Reuse legacy repository primitives inside one outer SQLite transaction."""
    def __init__(self,conn):self.conn=conn;self.row_factory=None
    def cursor(self):
        cursor=self.conn.cursor();cursor.row_factory=self.row_factory;return cursor
    def execute(self,*args):return self.cursor().execute(*args)
    def commit(self):pass
    def close(self):pass

class QaLifecycleService(QaLifecycleRepository):
    DEFECT_TRANSITIONS={'New':{'Open'},'Open':{'In Progress','Fixed'},'In Progress':{'Fixed'},'Fixed':{'Retest','Reopened'},'Retest':{'Closed','Reopened'},'Closed':{'Reopened'},'Reopened':{'Open','In Progress','Fixed'}}
    def recommendation_context(self,case_id):
        """Task 4 consumes facts; provider execution remains outside Task 5."""
        with self.connection() as conn:
            case=self.case(conn,case_id)
            return {'contract_version':1,'test_case_id':case['id'],
                    'document_version_id':case['document_version_id'],
                    'content_hash':digest(self.specification(case,conn)),
                    'review_state':self.review(conn,case)['state'],
                    'requirement_ids':[row[0] for row in conn.execute(
                        "SELECT id FROM qa_requirements WHERE document_version_id=? AND status='Confirmed' ORDER BY id",
                        (case['document_version_id'],))],
                    'automatic_approval_allowed':False}
    def propose_requirement_link(self,case_id,requirement_id,provider_reference,rationale,content_hash,actor):
        if not str(provider_reference).strip() or len(str(provider_reference))>250 or not str(rationale).strip() or len(str(rationale))>4000:
            raise LifecycleError('A bounded provider reference and rationale are required.',400)
        with self.connection(True) as conn:
            case=self.case(conn,case_id);requirement=self.row(conn,'qa_requirements','id',requirement_id)
            if requirement['document_version_id']!=case['document_version_id']:
                raise LifecycleError('Recommendation belongs to another document version.')
            if content_hash!=digest(self.specification(case,conn)):
                raise LifecycleError('Recommendation context is stale; request fresh facts.')
            identifier='REC-'+uuid.uuid4().hex
            conn.execute('INSERT INTO qa_ai_recommendations VALUES(?,?,?,?,?,?,?,?)',
                         (identifier,case_id,requirement_id,provider_reference,rationale,content_hash,self.actor(conn,actor)['id'],now()))
            self.event(conn,actor,'recommendation',identifier,'AI_PROPOSED',{'test_case_id':case_id})
            return {'id':identifier,'status':'PROPOSED','applied':False}
    def decide_recommendation(self,identifier,decision,comments,actor):
        if decision not in ('APPLIED','REJECTED') or not str(comments).strip() or len(str(comments))>4000:
            raise LifecycleError('An explicit decision and bounded review comment are required.',400)
        with self.connection(True) as conn:
            proposal=self.row(conn,'qa_ai_recommendations','id',identifier)
            if conn.execute('SELECT 1 FROM qa_ai_recommendation_decisions WHERE recommendation_id=?',(identifier,)).fetchone():
                raise LifecycleError('Recommendation was already reviewed.')
            if decision=='APPLIED':
                case=self.case(conn,proposal['test_case_id']);req=self.row(conn,'qa_requirements','id',proposal['requirement_id'])
                if proposal['content_hash']!=digest(self.specification(case,conn)):
                    raise LifecycleError('Recommendation context is stale; no changes were applied.')
                if req['status']!='Confirmed' or req['document_version_id']!=case['document_version_id']:
                    raise LifecycleError('Only a confirmed requirement in the same document version may be linked.')
                conn.execute('''INSERT INTO qa_requirement_links VALUES(?,?,?,?,?,?)
                    ON CONFLICT(requirement_id,test_case_id) DO UPDATE SET status=excluded.status,
                    rationale=excluded.rationale,actor_id=excluded.actor_id,updated_at=excluded.updated_at''',
                    (req['id'],case['id'],'CONFIRMED',proposal['rationale'],self.actor(conn,actor)['id'],now()))
            conn.execute('INSERT INTO qa_ai_recommendation_decisions VALUES(?,?,?,?,?)',
                         (identifier,decision,comments,self.actor(conn,actor)['id'],now()))
            self.event(conn,actor,'recommendation',identifier,'AI_REVIEWED',{'decision':decision})
            return {'id':identifier,'status':decision,'automatic_approval':False}
    def _asset(self,conn,case_id,kind,reference,actor):
        case=self.case(conn,case_id);sql_id=None;revision_id=None
        if kind=='Manual':
            definition={key:case.get(key) for key in self.SPEC_FIELDS};reference='test_cases:'+str(case_id);validation='Not applicable'
        elif kind=='API':
            if reference not in ('test_cases:'+str(case_id),'test_cases:'+str(case_id)+':api_request_config') or not (case.get('api_request_config_json') or case.get('bound_api_endpoint_id')):raise LifecycleError('Select the actual saved API configuration.')
            reference='test_cases:'+str(case_id)+':api_request_config'
            from Core.qa_lifecycle_execution_contract import api_definition_fingerprints
            definition=api_definition_fingerprints(case,conn);validation=case.get('status') or 'Draft'
        elif kind=='Playwright':
            from Core.test_case_repository import TestCaseRepository
            from Core.playwright_script_lifecycle import PlaywrightScriptLifecycle
            table,key=reference.split(':',1)
            if table!='playwright_script_revisions':raise LifecycleError('Select a persisted Playwright script revision.',400)
            repo=TestCaseRepository.__new__(TestCaseRepository);repo.db=SimpleNamespace(get_connection=lambda:BorrowedConnection(conn))
            try:definition=PlaywrightScriptLifecycle(repo).get_revision(case_id,int(key))
            except ValueError as error:raise LifecycleError(str(error)) from error
            revision_id=int(key);validation=case.get('status') or 'Draft'
        elif kind=='SQL':
            if reference=='test_cases:'+str(case_id)+':inline_sql':
                if case.get('automation_type')!='SQL' or not case.get('automation_script'):raise LifecycleError('No saved inline SQL asset exists.')
                definition=case['automation_script'];validation=case.get('status') or 'Draft'
            else:
                table,key=reference.split(':',1)
                if table!='sql_scripts':raise LifecycleError('Select a persisted SQL script.',400)
                definition=self.row(conn,table,'id',int(key));sql_id=int(key)
                if definition.get('document_version_id')!=case['document_version_id']:raise LifecycleError('SQL asset belongs to another document version.')
                if definition.get('test_case_version_id')!=case.get('test_case_version_id'):raise LifecycleError('SQL asset belongs to another Test Case revision.')
                path=self.central.resolve_managed(definition['repository_path'])
                if not path.is_file():raise LifecycleError('Managed SQL file is unavailable.')
                validation=definition.get('status') or 'Draft'
                definition={'sql_script_id':sql_id,'test_case_version_id':definition.get('test_case_version_id'),
                            'document_version_id':definition.get('document_version_id'),
                            'content_digest':digest(path.read_text(encoding='utf-8'))}
        else:raise LifecycleError('Unsupported automation type.',400)
        version=definition['script_fingerprint'] if kind=='Playwright' else digest(definition)
        identifier='ASSET-'+uuid.uuid5(uuid.NAMESPACE_URL,f'{case_id}:{kind}:{reference}:{version}').hex
        conn.execute('INSERT OR IGNORE INTO qa_asset_links VALUES(?,?,?,?,?,?,?,?,?,?)',(identifier,case_id,kind,reference,version,validation,self.actor(conn,actor)['id'],now(),sql_id,revision_id))
        self.event(conn,actor,'case',case_id,'ASSET_LINK',{'asset_link_id':identifier,'kind':kind})
        return self.row(conn,'qa_asset_links','id',identifier)
    def register_asset(self,case_id,kind,reference,actor):
        with self.connection(True) as conn:return self._asset(conn,case_id,kind,reference,actor)
    def asset_options(self,case_id):
        from Core.test_case_repository import TestCaseRepository
        from Core.playwright_script_lifecycle import PlaywrightScriptLifecycle
        with self.connection() as conn:
            case=self.case(conn,case_id);rows=[{'kind':'Manual','reference':'','label':'Manual Test Case specification'}]
            if case.get('api_request_config_json') or case.get('bound_api_endpoint_id'):rows.append({'kind':'API','reference':'test_cases:'+str(case_id),'label':'Bound API endpoint / saved request configuration'})
            if case.get('automation_type')=='SQL' and case.get('automation_script'):rows.append({'kind':'SQL','reference':'test_cases:'+str(case_id)+':inline_sql','label':'Saved inline SQL configuration'})
            repo=TestCaseRepository.__new__(TestCaseRepository);repo.db=SimpleNamespace(get_connection=lambda:BorrowedConnection(conn))
            for item in PlaywrightScriptLifecycle(repo).list_revisions(case_id).get('revisions',[]):rows.append({'kind':'Playwright','reference':'playwright_script_revisions:'+str(item['id']),'label':item['source']+' revision '+str(item['revision_number'])})
            for item in conn.execute('SELECT id,display_name FROM sql_scripts WHERE document_version_id=? AND test_case_version_id=?',(case['document_version_id'],case.get('test_case_version_id'))):rows.append({'kind':'SQL','reference':'sql_scripts:'+str(item['id']),'label':item['display_name']})
            return {'assets':rows}
    def _finalize(self,conn,run_id,asset_link_id,environment,evidence,actor):
        run=self.row(conn,'automation_runs','id',int(run_id))
        if not run.get('test_case_id'):
            raise LifecycleError('Folder-scoped SQL runs have no individual Test Case verdict. Use case-scoped execution or an explicit manual observation for defect/retest evidence.')
        case=self.case(conn,run['test_case_id'])
        if run['status'] not in ('Passed','Failed','Error','Cancelled','Stopped'):raise LifecycleError('Execution is not terminal.')
        if asset_link_id:
            asset=self.row(conn,'qa_asset_links','id',asset_link_id)
            if asset['test_case_id']!=case['id'] or asset['kind']!=run['automation_type']:raise LifecycleError('Asset does not match the executed Test Case revision/type.')
        if not isinstance(evidence,list) or len(evidence)>20 or any(not isinstance(item,str) or len(item)>1000 for item in evidence):raise LifecycleError('Evidence must be up to 20 bounded reference strings.',400)
        snapshot={key:run.get(key) for key in ('run_uuid','status','outcome','success','duration_seconds','started_at','finished_at','script_path','screenshot_path','executed_by_user_id')}
        snapshot['asset_execution_verified']=run['automation_type']=='Manual'
        snapshot['test_case_snapshot']={key:case.get(key) for key in self.SPEC_FIELDS}
        snapshot['test_case_snapshot_verified']=run['automation_type']=='Manual'
        snapshot['review_at_finalization']=self.review(conn,case)['state']
        pin=conn.execute('SELECT * FROM qa_execution_pins WHERE run_id=?',(run_id,)).fetchone()
        if pin:
            snapshot['dispatch_asset']=json.loads(pin['asset_identity_json'])
            if asset_link_id and (asset['asset_reference']!=snapshot['dispatch_asset']['reference'] or asset['asset_version']!=snapshot['dispatch_asset']['version']):
                raise LifecycleError('Selected asset version differs from the immutable dispatch pin.')
            snapshot['test_case_snapshot']=json.loads(pin['specification_json'])
            snapshot['test_case_snapshot_verified']=True
            snapshot['review_at_dispatch']=pin['review_state']
            snapshot['asset_usages']=[dict(item) for item in conn.execute(
                'SELECT asset_identity_json,reason,step_id,created_at FROM qa_execution_asset_usages WHERE run_id=? ORDER BY id',(run_id,))]
            # Dispatch identity is evidence, not proof that an arbitrary legacy
            # engine actually executed it. Keep the existing honest verification flag.
        if run['automation_type']=='Manual':snapshot['manual_observation']=run.get('final_message') or ''
        conn.execute('INSERT INTO qa_execution_annotations VALUES(?,?,?,?,?,?,?,?,?,?)',(run_id,case['id'],case.get('test_case_version_id'),case['document_version_id'],asset_link_id,str(environment)[:200],json.dumps(snapshot),json.dumps(evidence),self.actor(conn,actor)['id'],now()))
        self.event(conn,actor,'execution',run_id,'FINALIZE_EVIDENCE',{'test_case_id':case['id'],'outcome':run.get('outcome')})
        return self.row(conn,'qa_execution_annotations','run_id',run_id)
    def finalize_run(self,run_id,asset_link_id,environment,evidence,actor):
        with self.connection(True) as conn:return self._finalize(conn,run_id,asset_link_id,environment,evidence,actor)
    def record_manual(self,case_id,outcome,environment,evidence,comments,actor):
        if outcome not in ('Pass','Fail'):raise LifecycleError('Manual result must be Pass or Fail.',400)
        if not str(comments).strip():raise LifecycleError('Manual execution comments are required.',400)
        from Core.automation_execution_repository import AutomationExecutionRepository
        from Core.logger import Logger
        with self.connection(True) as conn:
            case=self.case(conn,case_id)
            if self.review(conn,case)['state']!='Ready for Execution':raise LifecycleError('Current approval and Ready for Execution are required.')
            self.actor(conn,actor);asset=self._asset(conn,case_id,'Manual','',actor)
            runs=AutomationExecutionRepository.__new__(AutomationExecutionRepository)
            runs.db=SimpleNamespace(get_connection=lambda:BorrowedConnection(conn));runs.logger=Logger.get_logger()
            run=runs.create_run(dict(case,automation_type='Manual'),actor['id'],actor.get('username'))
            runs.mark_running(run['run_uuid'])
            finished=runs.mark_finished(run['run_uuid'],{'success':outcome=='Pass','final_message':comments,'stdout':'Authorized manual result'})
            # Existing store columns already exist; bind the known owner in this
            # new manual operation without changing automated-engine contracts.
            conn.execute('UPDATE automation_runs SET document_version_id=?,test_case_version_id=?,environment_name=? WHERE id=?',(case['document_version_id'],case.get('test_case_version_id'),str(environment)[:200],finished['id']))
            from Core.test_case_repository import TestCaseRepository
            cases=TestCaseRepository.__new__(TestCaseRepository);cases.db=runs.db;cases.logger=runs.logger
            cases.update_result(case_id,outcome)
            conn.execute('UPDATE test_cases SET actual_result=? WHERE id=?',(comments,case_id))
            return self._finalize(conn,finished['id'],asset['id'],environment,evidence,actor)
    def create_defect(self,run_id,title,description,severity,priority,assigned_to,actor):
        if not str(title).strip() or not str(description).strip():raise LifecycleError('Defect title and description required.',400)
        if severity not in ('Critical','High','Medium','Low') or priority not in ('Urgent','High','Normal','Low'):raise LifecycleError('Invalid severity or priority.',400)
        with self.connection(True) as conn:
            run=self.row(conn,'qa_execution_annotations','run_id',int(run_id))
            if json.loads(run['result_json'])['outcome']!='Fail':raise LifecycleError('A defect requires a failed finalized execution.')
            if assigned_to:self.actor(conn,{'id':assigned_to})
            identifier='DEF-'+uuid.uuid4().hex;stamp=now()
            conn.execute('INSERT INTO qa_defects VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)',(identifier,run['test_case_id'],run_id,title,description,severity,priority,'New',assigned_to,'','',self.actor(conn,actor)['id'],stamp,stamp))
            self.event(conn,actor,'defect',identifier,'CREATE_DEFECT',{'failed_run_id':run_id});return self.row(conn,'qa_defects','id',identifier)
    def transition_defect(self,defect_id,state,resolution,actor):
        with self.connection(True) as conn:
            defect=self.row(conn,'qa_defects','id',defect_id)
            if state not in self.DEFECT_TRANSITIONS.get(defect['state'],set()):raise LifecycleError('Invalid defect transition.')
            if state=='Fixed' and not str(resolution).strip():raise LifecycleError('Resolution is required before Fixed.',400)
            if state=='Closed':
                latest=conn.execute('SELECT a.result_json FROM qa_retests t JOIN qa_execution_annotations a ON a.run_id=t.run_id WHERE t.defect_id=? ORDER BY t.created_at DESC LIMIT 1',(defect_id,)).fetchone()
                if not latest or json.loads(latest[0])['outcome']!='Pass':raise LifecycleError('Closure requires a passing latest retest.')
            conn.execute('UPDATE qa_defects SET state=?,resolution=?,updated_at=? WHERE id=?',(state,resolution or defect['resolution'],now(),defect_id))
            self.event(conn,actor,'defect',defect_id,'DEFECT_TRANSITION',{'from':defect['state'],'to':state});return self.row(conn,'qa_defects','id',defect_id)
    def assign_defect(self,defect_id,user_id,actor):
        with self.connection(True) as conn:
            self.row(conn,'qa_defects','id',defect_id)
            if user_id:self.actor(conn,{'id':user_id})
            conn.execute('UPDATE qa_defects SET assigned_to=?,updated_at=? WHERE id=?',(user_id,now(),defect_id))
            self.event(conn,actor,'defect',defect_id,'ASSIGN_DEFECT',{'assigned_to':user_id})
            return self.row(conn,'qa_defects','id',defect_id)
    def record_retest(self,defect_id,run_id,actor):
        with self.connection(True) as conn:
            defect=self.row(conn,'qa_defects','id',defect_id);run=self.row(conn,'qa_execution_annotations','run_id',int(run_id))
            if defect['state'] not in ('Fixed','Retest'):raise LifecycleError('Defect must be Fixed or awaiting Retest.')
            original=self.case(conn,defect['test_case_id']);current=self.case(conn,run['test_case_id'])
            if (original.get('tc_id') or original['tc_number'])!=(current.get('tc_id') or current['tc_number']):raise LifecycleError('Retest belongs to another logical Test Case.')
            owners=[DocumentHierarchy(conn).context(item['document_version_id']) for item in (original,current)]
            if owners[0]['document_type_id']!=owners[1]['document_type_id'] or int(run_id)==defect['failed_run_id']:raise LifecycleError('Retest must be a new execution of the same owned requirement.')
            outcome=json.loads(run['result_json'])['outcome']
            if outcome not in ('Pass','Fail'):raise LifecycleError('Retest requires a verified Pass or Fail.')
            original_source=conn.execute('SELECT source_knowledge_id FROM test_case_versions WHERE id=?',(original.get('test_case_version_id'),)).fetchone()
            current_source=conn.execute('SELECT source_knowledge_id FROM test_case_versions WHERE id=?',(current.get('test_case_version_id'),)).fetchone()
            if not original_source or not current_source or original_source[0]!=current_source[0]:
                raise LifecycleError('Retest belongs to another canonical source/Test Case identity.')
            pin=conn.execute('SELECT * FROM qa_execution_pins WHERE run_id=?',(run_id,)).fetchone()
            fixed=next((event for event in conn.execute("SELECT created_at,detail_json FROM qa_lifecycle_events WHERE entity_type='defect' AND entity_id=? AND action='DEFECT_TRANSITION' ORDER BY id DESC",(defect_id,)) if json.loads(event['detail_json']).get('to')=='Fixed'),None)
            if not pin or pin['review_state']!='Ready for Execution' or not fixed or pin['created_at']<=fixed['created_at']:
                raise LifecycleError('Retest requires a newly approved execution dispatched after the recorded fix; historical results remain unchanged.')
            if digest(json.loads(pin['specification_json']))!=digest(self.specification(current,conn)):
                raise LifecycleError('Retest specification changed since execution; review and execute the current revision.')
            identifier=uuid.uuid4().hex;conn.execute('INSERT INTO qa_retests VALUES(?,?,?,?,?)',(identifier,defect_id,run_id,self.actor(conn,actor)['id'],now()))
            conn.execute('UPDATE qa_defects SET state=?,updated_at=? WHERE id=?',('Retest' if outcome=='Pass' else 'Reopened',now(),defect_id))
            self.event(conn,actor,'defect',defect_id,'RETEST',{'run_id':run_id,'outcome':outcome});return self.row(conn,'qa_retests','id',identifier)
    def link_clickup(self,defect_id,external_id,actor):
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,100}',str(external_id)):raise LifecycleError('Enter an existing ClickUp task ID.',400)
        with self.connection(True) as conn:
            self.row(conn,'qa_defects','id',defect_id);conn.execute('UPDATE qa_defects SET clickup_id=?,updated_at=? WHERE id=?',(external_id,now(),defect_id))
            self.event(conn,actor,'defect',defect_id,'LINK_EXISTING_CLICKUP',{'external_record_created':False})
            return {'id':defect_id,'clickup_id':external_id,'external_verified':False,'external_record_created':False}
