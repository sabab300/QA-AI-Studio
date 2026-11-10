"""Append-only dispatch identity around existing engines; no executable copies."""
import json
import sqlite3
from pathlib import Path
from Core.qa_lifecycle_repository import QaLifecycleRepository, LifecycleError, digest, now


def api_definition_fingerprints(case,conn):
    value={'configuration_fingerprint':digest(json.loads(case.get('api_request_config_json') or '{}')),
           'bound_api_endpoint_id':case.get('bound_api_endpoint_id'),
           'expected_status_code':case.get('api_expected_status_code')}
    if value['bound_api_endpoint_id']:
        cursor=conn.execute('SELECT * FROM api_endpoints WHERE id=?',(value['bound_api_endpoint_id'],))
        row=cursor.fetchone()
        if row is None:raise LifecycleError('Bound API endpoint no longer exists.')
        # Raw data is read only to compute an opaque version. It is never
        # returned, serialized into pins, logged or exposed by this contract.
        raw=dict(row) if isinstance(row,(dict,sqlite3.Row)) else dict(zip([item[0] for item in cursor.description],row))
        value['endpoint_fingerprint']=digest(raw)
        dispatched=case.get('_api_endpoint_fingerprint')
        if dispatched and dispatched!=value['endpoint_fingerprint']:
            raise LifecycleError('API endpoint changed before dispatch; refresh the current configuration.')
    return value


def asset_identity(case, conn, capture_legacy_revision=False):
    kind = case.get('automation_type') or 'Manual'
    reference = 'test_cases:' + str(case['id'])
    if kind == 'API':
        value = api_definition_fingerprints(case,conn)
        reference += ':api_request_config'
    elif kind == 'Playwright':
        from Core.test_case_repository import TestCaseRepository
        source = str(case.get('active_script_source') or 'AUTO').upper()
        field = {'MANUAL': 'recorded_script', 'UPLOADED': 'uploaded_script'}.get(source, 'automation_script')
        text = case.get(field) or ''
        if not text:
            source = 'AUTO';text = case.get('automation_script') or ''
        fingerprint = TestCaseRepository.script_fingerprint(text)
        # Existing revision ownership is validated by the established lifecycle.
        from Core.qa_lifecycle_service import BorrowedConnection
        from Core.playwright_script_lifecycle import PlaywrightScriptLifecycle
        from types import SimpleNamespace
        repo = TestCaseRepository.__new__(TestCaseRepository)
        repo.db = SimpleNamespace(get_connection=lambda: BorrowedConnection(conn))
        revisions = PlaywrightScriptLifecycle(repo).list_revisions(case['id'],source).get('revisions',[])
        matches = [item for item in revisions if item['script_fingerprint'] == fingerprint]
        if not matches and text and capture_legacy_revision:
            identity=repo._playwright_identity(conn.cursor(),case)
            if identity:
                from Core.playwright_script_lifecycle import capture,revision_context
                with revision_context('BASELINE',note='Existing executable captured at dispatch; no script was replaced.'):
                    capture(repo,conn,identity[0],identity[2],source,None,text,False)
                revisions=PlaywrightScriptLifecycle(repo).list_revisions(case['id'],source).get('revisions',[])
                matches=[item for item in revisions if item['script_fingerprint']==fingerprint]
        reference = ('playwright_script_revisions:' + str(matches[0]['id'])) if matches else reference + ':' + source
        return {'kind':kind,'reference':reference,'version':fingerprint,'source':source,
                'persisted_revision':bool(matches)}
    elif kind == 'SQL':
        value = case.get('automation_script') or ''
        reference += ':inline_sql'
    else:
        value = {key:case.get(key) for key in QaLifecycleRepository.SPEC_FIELDS}
    return {'kind':kind,'reference':reference,'version':digest(value),'persisted_revision':False}


LIFECYCLE_GUIDE=('Open Reports & Analytics > QA Lifecycle, select this Test Case and record the review '
                 'decisions (In Review > Approved > Ready for Execution). Recording decisions needs QA '
                 'Engineering edit permission.')
_NEXT_STEP={'Draft':'Move it to In Review, then Approved, then Ready for Execution.',
            'In Review':'Approve it, then mark it Ready for Execution.',
            'Approved':'Mark it Ready for Execution.',
            'Retired':'Return it to Draft, then repeat the review.'}


def gate_reason(review,previous_state=None):
    """Plain-language statement of which prerequisite is missing (no secrets, no spec content)."""
    state=review.get('state') or 'Draft'
    if review.get('stale_previous_decision'):
        why=("The Test Case specification or its confirmed requirement links changed after its last review decision"
             +(" ('"+previous_state+"')" if previous_state else '')+", so that approval no longer applies and the case is back in Draft.")
    elif state=='Draft':
        why='No review decision has been recorded for the current specification, so the case is still in Draft.'
    else:
        why="The current review state is '"+state+"', not 'Ready for Execution'."
    return why+' '+_NEXT_STEP.get(state,_NEXT_STEP['Draft'])+' '+LIFECYCLE_GUIDE


def _policy_service():
    policy_path=Path(__file__).resolve().parents[1]/'Config/qa_lifecycle_policy.json'
    service=QaLifecycleRepository.__new__(QaLifecycleRepository)
    service.policy=json.loads(policy_path.read_text()) if policy_path.is_file() else {}
    return service


def gate_for_connection(conn,test_case_id):
    """Read-only view of the same rule capture_dispatch enforces. It never approves or changes anything."""
    info={'installed':False,'governed':False,'ready':True,'blocks_execution':False,'state':None,
          'message':'','test_case_id':test_case_id,'tc_number':None,'owner':None,'independent_approval':False,
          'stale_previous_decision':False,'last_decision':None}
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='qa_review_decisions'").fetchone():return info
    original_factory=conn.row_factory;conn.row_factory=sqlite3.Row
    try:
        row=conn.execute('SELECT * FROM test_cases WHERE id=?',(test_case_id,)).fetchone()
        if row is None:return info
        canonical=dict(row);service=_policy_service()
        info.update(installed=True,tc_number=canonical.get('tc_number') or canonical.get('tc_id'),
                    independent_approval=bool(service.policy.get('require_independent_approval')))
        has_review=conn.execute('SELECT 1 FROM qa_review_decisions WHERE test_case_id=?',(test_case_id,)).fetchone()
        legacy=conn.execute('SELECT 1 FROM qa_legacy_execution_cases WHERE test_case_id=?',(test_case_id,)).fetchone()
        if canonical.get('document_version_id'):
            try:
                from Shared.document_hierarchy import DocumentHierarchy
                owner=DocumentHierarchy(conn).context(canonical['document_version_id'])
                info['owner']={key:owner.get(key) for key in ('domain','module','knowledge_name','document_type','version')}
            except Exception:info['owner']=None
        if not (has_review or not legacy):
            info.update(state='Legacy — not reviewed',
                        message='Legacy Test Case: execution is not gated by lifecycle review, but it has not been approved.')
            return info
        info['governed']=True
        review=service.review(conn,canonical)
        last=conn.execute('''SELECT d.state,d.created_at,d.comments,COALESCE(NULLIF(u.full_name,''),u.username) reviewer
            FROM qa_review_decisions d LEFT JOIN users u ON u.id=d.reviewer_id WHERE d.test_case_id=? ORDER BY d.id DESC LIMIT 1''',(test_case_id,)).fetchone()
        last=dict(last) if last else None
        ready=review.get('state')=='Ready for Execution'
        info.update(state=review.get('state') or 'Draft',ready=ready,blocks_execution=not ready,
                    stale_previous_decision=bool(review.get('stale_previous_decision')),
                    last_decision=({'state':last['state'],'reviewer':last['reviewer'],'at':last['created_at']} if last else None))
        info['message']=('Approved and Ready for Execution for the current specification.' if ready
                         else gate_reason(review,last['state'] if last else None))
        return info
    finally:conn.row_factory=original_factory


def lifecycle_gate(db,test_case_id):
    conn=db.get_connection()
    try:return gate_for_connection(conn,test_case_id)
    finally:conn.close()


def capture_dispatch(conn,run_id,case,actor_id):
    """Called in the same transaction as run creation, when Task 5 is installed.

    Legacy unreviewed cases stay compatible and are explicitly NOT approved.
    Cases with lifecycle decisions must have current Ready for Execution.
    No credentials, request bodies, script text or variable values are retained.
    """
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='qa_execution_pins'").fetchone():
        return
    original_factory=conn.row_factory;conn.row_factory=sqlite3.Row
    try:
        policy_path=Path(__file__).resolve().parents[1]/'Config/qa_lifecycle_policy.json'
        service=QaLifecycleRepository.__new__(QaLifecycleRepository)
        service.policy=json.loads(policy_path.read_text()) if policy_path.is_file() else {}
        canonical=service.row(conn,'test_cases','id',case['id'])
        for field in service.SPEC_FIELDS:
            if field not in case:continue  # Older repository callers may supply only identity.
            stored=canonical.get(field);dispatched=case.get(field)
            if field=='source_knowledge_ids':
                def parsed_ids(value):
                    if isinstance(value,str):
                        try:return json.loads(value or '[]')
                        except ValueError:return []
                    return value or []
                stored=parsed_ids(stored);dispatched=parsed_ids(dispatched)
            if stored!=dispatched:
                raise LifecycleError('Test Case specification changed before dispatch; refresh and execute the current approved revision.')
        has_review=conn.execute('SELECT 1 FROM qa_review_decisions WHERE test_case_id=?',(case['id'],)).fetchone()
        legacy=conn.execute('SELECT 1 FROM qa_legacy_execution_cases WHERE test_case_id=?',(case['id'],)).fetchone()
        # Execution repositories normalize workbook/display fields. Approval
        # signatures must use the same persisted representation as review,
        # not its normalized UI projection (otherwise valid approvals go stale).
        governed=has_review or not legacy
        review=service.review(conn,canonical) if governed else {'state':'Legacy — not reviewed'}
        if governed and review['state']!='Ready for Execution':
            label=str(canonical.get('tc_number') or canonical.get('tc_id') or case['id'])
            raise LifecycleError('Current approval and Ready for Execution are required. '+label+': '+gate_reason(review))
        specification=service.specification(canonical,conn)
        identity=asset_identity(case,conn,capture_legacy_revision=True)
        conn.execute('INSERT INTO qa_execution_pins VALUES(?,?,?,?,?,?,?,?)',
                     (run_id,case['id'],case.get('test_case_version_id'),case.get('document_version_id'),
                      json.dumps(specification),json.dumps(identity),review['state'],now()))
        conn.execute('INSERT INTO qa_execution_asset_usages(run_id,asset_identity_json,reason,created_at) VALUES(?,?,?,?)',
                     (run_id,json.dumps(identity),'DISPATCH',now()))
    finally:
        conn.row_factory=original_factory


def capture_repair_usage(db,run_uuid,case,step_id):
    """Preserve the old pin and append the persisted repair's actual revision."""
    conn=db.get_connection()
    try:
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='qa_execution_asset_usages'").fetchone():return
        conn.execute('BEGIN IMMEDIATE')
        run=conn.execute('SELECT id,test_case_id,status FROM automation_runs WHERE run_uuid=?',(run_uuid,)).fetchone()
        if not run or run[1]!=case['id'] or run[2] not in ('Queued','Running'):
            raise LifecycleError('Cannot append a repair to a different or completed run.')
        identity=asset_identity(case,conn,capture_legacy_revision=True)
        if identity['kind']!='Playwright':raise LifecycleError('Interactive repair requires a Playwright asset.')
        previous=conn.execute('SELECT asset_identity_json FROM qa_execution_asset_usages WHERE run_id=? ORDER BY id DESC LIMIT 1',(run[0],)).fetchone()
        if not previous or json.loads(previous[0])!=identity:
            conn.execute('INSERT INTO qa_execution_asset_usages(run_id,asset_identity_json,reason,step_id,created_at) VALUES(?,?,?,?,?)',
                         (run[0],json.dumps(identity),'PERSISTED_REPAIR',int(step_id or 0),now()))
        conn.commit()
    except Exception:
        conn.rollback();raise
    finally:conn.close()


def validate_folder_approval(db,test_case_version_id):
    """Folder SQL preserves legacy compatibility but cannot bypass reviewed cases."""
    conn=db.get_connection();conn.row_factory=sqlite3.Row
    try:
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='qa_review_decisions'").fetchone():return
        policy_path=Path(__file__).resolve().parents[1]/'Config/qa_lifecycle_policy.json'
        service=QaLifecycleRepository.__new__(QaLifecycleRepository)
        service.policy=json.loads(policy_path.read_text()) if policy_path.is_file() else {}
        rows=conn.execute('''SELECT tc.* FROM test_cases tc WHERE tc.test_case_version_id=? AND
            (NOT EXISTS(SELECT 1 FROM qa_legacy_execution_cases legacy WHERE legacy.test_case_id=tc.id)
             OR EXISTS(SELECT 1 FROM qa_review_decisions d WHERE d.test_case_id=tc.id))''',(test_case_version_id,)).fetchall()
        for row in rows:
            review=service.review(conn,dict(row))
            if review['state']!='Ready for Execution':
                raise LifecycleError('All lifecycle-reviewed Test Cases in this SQL folder require current Ready for Execution. Not ready: '
                                     +str(row['tc_number'] if 'tc_number' in row.keys() else row['id'])+" ("+review['state']+'). '+LIFECYCLE_GUIDE)
    finally:conn.close()
