"""Lifecycle relationships around canonical records, never a second TC store."""
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import re
import sqlite3
import uuid
from pathlib import Path
from Database.db_manager import DatabaseManager
from Shared.central_repository import CentralRepository
from Shared.document_hierarchy import DocumentHierarchy

class LifecycleError(ValueError):
    def __init__(self,message,status=409):super().__init__(message);self.status=status
def now():return datetime.now(timezone.utc).isoformat()
def digest(value):return hashlib.sha256(json.dumps(value,sort_keys=True,default=str).encode()).hexdigest()

class QaLifecycleRepository:
    REVIEW_TRANSITIONS={'Draft':{'In Review','Retired'},'In Review':{'Draft','Approved','Retired'},'Approved':{'Ready for Execution','In Review','Retired'},'Ready for Execution':{'In Review','Retired'},'Retired':{'Draft'}}
    SPEC_FIELDS=('tc_number','tc_id','scenario','test_case','pre_conditions','steps','expected_result','test_type','source_knowledge_ids','document_version_id','test_case_version_id')
    def __init__(self,db=None,central=None,policy=None):
        self.db=db or DatabaseManager();self.central=central or CentralRepository()
        path=Path(__file__).resolve().parents[1]/'Config/qa_lifecycle_policy.json'
        self.policy=policy if policy is not None else (json.loads(path.read_text()) if path.is_file() else {})
        if self.policy.get('version',1)!=1 or not isinstance(self.policy.get('require_independent_approval',False),bool) or not isinstance(self.policy.get('min_review_comment_length',1),int) or not 1<=self.policy.get('min_review_comment_length',1)<=4000:raise LifecycleError('Invalid lifecycle review policy configuration.',400)
        self.transitions=self.policy.get('review_transitions') or {key:sorted(values) for key,values in self.REVIEW_TRANSITIONS.items()}
        if any(key not in self.REVIEW_TRANSITIONS or not isinstance(values,list) or not set(values)<=self.REVIEW_TRANSITIONS[key] for key,values in self.transitions.items()):raise LifecycleError('Lifecycle policy may restrict, not bypass, the canonical review workflow.',400)
        self.ensure_schema()
    def specification(self,case,conn):
        links=[dict(row) for row in conn.execute("SELECT r.id,r.quote_hash,r.status,r.document_version_id FROM qa_requirement_links l JOIN qa_requirements r ON r.id=l.requirement_id WHERE l.test_case_id=? AND l.status='CONFIRMED' ORDER BY r.id",(case['id'],))]
        return {key:case.get(key) for key in self.SPEC_FIELDS}|{'review_policy':digest(self.policy),'confirmed_requirements':links}
    @contextmanager
    def connection(self,write=False):
        conn=self.db.get_connection();conn.row_factory=sqlite3.Row;conn.execute('PRAGMA foreign_keys=ON')
        try:
            if write:conn.execute('BEGIN IMMEDIATE')
            yield conn
            if write:conn.commit()
        except sqlite3.IntegrityError as error:
            conn.rollback();raise LifecycleError('Lifecycle dependency, duplicate or immutable-history conflict.') from error
        except Exception:conn.rollback();raise
        finally:conn.close()
    def ensure_schema(self):
        from Core.qa_lifecycle_schema import MIGRATION_1,MIGRATION_2,MIGRATION_3
        with self.connection(True) as conn:
            exists=conn.execute("SELECT 1 FROM sqlite_master WHERE name='qa_lifecycle_migrations'").fetchone()
            if exists and conn.execute('SELECT 1 FROM qa_lifecycle_migrations WHERE version=1').fetchone():
                expected={'qa_requirements','qa_requirement_links','qa_review_decisions','qa_asset_links','qa_execution_annotations','qa_defects','qa_retests','qa_lifecycle_events'}
                tables={row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                if not expected<=tables:raise LifecycleError('Incomplete lifecycle schema; restore or repair its migration before continuing.')
            else:
                for statement in MIGRATION_1:conn.execute(statement)
                conn.execute('INSERT INTO qa_lifecycle_migrations VALUES(1,?)',(now(),))
            if not conn.execute('SELECT 1 FROM qa_lifecycle_migrations WHERE version=2').fetchone():
                for statement in MIGRATION_2:conn.execute(statement)
                conn.execute('INSERT INTO qa_lifecycle_migrations VALUES(2,?)',(now(),))
            expected_v2={'qa_execution_pins','qa_execution_asset_usages','qa_ai_recommendations','qa_ai_recommendation_decisions'}
            tables={row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if not expected_v2<=tables:raise LifecycleError('Incomplete lifecycle contract schema; restore or repair migration 2 before continuing.')
            if not conn.execute('SELECT 1 FROM qa_lifecycle_migrations WHERE version=3').fetchone():
                for statement in MIGRATION_3:conn.execute(statement)
                conn.execute('INSERT INTO qa_lifecycle_migrations VALUES(3,?)',(now(),))
            if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='qa_legacy_execution_cases'").fetchone():raise LifecycleError('Incomplete legacy compatibility registry.')
            if conn.execute('PRAGMA foreign_key_check').fetchall():raise LifecycleError('Lifecycle migration foreign-key check failed; rolled back.')
    @staticmethod
    def row(conn,table,key,value):
        row=conn.execute('SELECT * FROM '+table+' WHERE '+key+'=?',(value,)).fetchone()
        if row is None:raise LifecycleError('Selected '+table+' record was not found.',404)
        return dict(row)
    def actor(self,conn,actor):
        user=self.row(conn,'users','id',int(actor['id']))
        if not user['is_active']:raise LifecycleError('Inactive user cannot perform lifecycle actions.',403)
        return user
    def event(self,conn,actor,entity,key,action,detail):
        user=self.actor(conn,actor);stamp=now()
        conn.execute('INSERT INTO qa_lifecycle_events(entity_type,entity_id,action,actor_id,detail_json,created_at) VALUES(?,?,?,?,?,?)',(entity,str(key),action,user['id'],json.dumps(detail),stamp))
        conn.execute('INSERT INTO audit_logs(user_id,username,action,resource,detail,created_date) VALUES(?,?,?,?,?,?)',(user['id'],user['username'],'QA_LIFECYCLE_'+action,'qa_engineering',json.dumps({'entity':entity,'id':str(key)}),stamp))
    def case(self,conn,case_id):
        case=self.row(conn,'test_cases','id',int(case_id))
        if not case.get('document_version_id'):raise LifecycleError('Resolve legacy Test Case document-version ownership first.')
        DocumentHierarchy(conn).context(case['document_version_id']);return case
    def review(self,conn,case):
        decision=conn.execute('SELECT * FROM qa_review_decisions WHERE test_case_id=? ORDER BY id DESC LIMIT 1',(case['id'],)).fetchone()
        signature=digest(self.specification(case,conn))
        if decision and decision['content_hash']==signature:return dict(decision)
        return {'state':'Draft','content_hash':signature,'stale_previous_decision':bool(decision)}
    def decide_review(self,case_id,state,comments,actor):
        with self.connection(True) as conn:
            case=self.case(conn,case_id);previous=self.review(conn,case)
            if state not in self.transitions.get(previous['state'],[]):raise LifecycleError('Invalid review transition.')
            if len(str(comments).strip())<int(self.policy.get('min_review_comment_length',1)):raise LifecycleError('Review comments do not meet the configured minimum length.',400)
            if state=='Approved' and self.policy.get('require_independent_approval') and previous.get('reviewer_id')==actor['id']:raise LifecycleError('Approval must be recorded by a different reviewer from the review submitter.')
            snapshot=self.specification(case,conn)
            if state in ('Approved','Ready for Execution') and any(row['document_version_id']!=case['document_version_id'] or row['status']!='Confirmed' for row in snapshot['confirmed_requirements']):raise LifecycleError('Review requirement ownership/status before approving this Test Case.')
            conn.execute('INSERT INTO qa_review_decisions(test_case_id,state,content_hash,case_snapshot_json,reviewer_id,comments,created_at) VALUES(?,?,?,?,?,?,?)',(case_id,state,digest(snapshot),json.dumps(snapshot),self.actor(conn,actor)['id'],comments,now()))
            self.event(conn,actor,'case',case_id,'REVIEW_DECISION',{'from':previous['state'],'to':state});return self.review(conn,case)
    def extract_requirements(self,source_id,actor):
        from Core.text_extractor import TextExtractor
        with self.connection() as conn:
            source=self.row(conn,'knowledge_items','id',int(source_id))
            if not source.get('document_version_id'):raise LifecycleError('Resolve the source document-version owner before analysis.')
            owner=DocumentHierarchy(conn).context(source['document_version_id'])
        path=self.central.resolve_managed(source.get('repository_path'),source.get('repository_relative_path') or '')
        if not path.is_file() or path.stat().st_size>32*1024*1024:raise LifecycleError('Source unavailable or exceeds the 32 MiB analysis limit.',400)
        text=TextExtractor().extract_text(str(path))
        if not text.strip():raise LifecycleError('No extractable requirement text.',400)
        rows=[];offset=0;limit=150000
        with self.connection(True) as conn:
            for line in text[:limit].splitlines(keepends=True):
                quote=line.strip();anchor=offset;offset+=len(line)
                if not quote or len(quote)>10000 or not re.search(r'\b(shall|must|required)\b|\b(?:REQ|FR|NFR|BR)[-_ ]?\d+',quote,re.I):continue
                match=re.search(r'\b(?:REQ|FR|NFR|BR)[-_ ]?\d+\b',quote,re.I);ref=match.group(0) if match else ''
                quote_hash=digest(quote);logical=f"dt:{owner['document_type_id']}:{ref.upper()}" if ref else f'source:{source_id}:anchor:{anchor}'
                identifier='REQ-'+uuid.uuid5(uuid.NAMESPACE_URL,f'{logical}:{source_id}:{anchor}:{quote_hash}').hex
                conn.execute('INSERT OR IGNORE INTO qa_requirements VALUES(?,?,?,?,?,?,?,?,?,?,?)',(identifier,logical,source_id,source['document_version_id'],ref or identifier,quote,anchor,quote_hash,'Needs Review',self.actor(conn,actor)['id'],now()))
                rows.append(identifier)
            self.event(conn,actor,'source',source_id,'EXTRACT_REQUIREMENTS',{'candidates':len(rows),'bounded':len(text)>limit})
        return {'requirement_ids':rows,'analyzed_characters':min(len(text),limit),'total_characters':len(text),'bounded':len(text)>limit,'verified':False}
    def confirm_requirement(self,requirement_id,status,actor):
        if status not in ('Confirmed','Retired'):raise LifecycleError('Invalid requirement state.',400)
        with self.connection(True) as conn:
            item=self.row(conn,'qa_requirements','id',requirement_id);conn.execute('UPDATE qa_requirements SET status=? WHERE id=?',(status,requirement_id))
            self.event(conn,actor,'requirement',requirement_id,'REQUIREMENT_REVIEW',{'from':item['status'],'to':status});return self.row(conn,'qa_requirements','id',requirement_id)
    def link_requirement(self,requirement_id,case_id,status,rationale,actor):
        if status not in ('PROPOSED','CONFIRMED','REJECTED'):raise LifecycleError('Invalid relationship state.',400)
        with self.connection(True) as conn:
            requirement=self.row(conn,'qa_requirements','id',requirement_id);case=self.case(conn,case_id)
            if status!='REJECTED' and requirement['document_version_id']!=case['document_version_id']:raise LifecycleError('Requirement and Test Case belong to different document versions.')
            if status=='CONFIRMED' and requirement['status']!='Confirmed':raise LifecycleError('Review the requirement before confirming its relationship.')
            conn.execute('INSERT INTO qa_requirement_links VALUES(?,?,?,?,?,?) ON CONFLICT(requirement_id,test_case_id) DO UPDATE SET status=excluded.status,rationale=excluded.rationale,actor_id=excluded.actor_id,updated_at=excluded.updated_at',(requirement_id,case_id,status,str(rationale),self.actor(conn,actor)['id'],now()))
            self.event(conn,actor,'case',case_id,'REQUIREMENT_LINK',{'requirement_id':requirement_id,'status':status});return {'requirement_id':requirement_id,'test_case_id':case_id,'status':status}
    def trace(self,case_id):
        with self.connection() as conn:
            case=self.case(conn,case_id);owner=DocumentHierarchy(conn).context(case['document_version_id'])
            requirements=[dict(row) for row in conn.execute('SELECT r.*,l.status link_status,l.rationale FROM qa_requirement_links l JOIN qa_requirements r ON r.id=l.requirement_id WHERE l.test_case_id=?',(case_id,))]
            assets=[dict(row) for row in conn.execute('SELECT * FROM qa_asset_links WHERE test_case_id=?',(case_id,))]
            runs=[dict(row) for row in conn.execute('SELECT * FROM qa_execution_annotations WHERE test_case_id=?',(case_id,))]
            dispatches=[dict(row) for row in conn.execute('''SELECT p.*,r.run_uuid,r.status,r.outcome
                FROM qa_execution_pins p JOIN automation_runs r ON r.id=p.run_id
                WHERE p.test_case_id=? ORDER BY r.id''',(case_id,))]
            for dispatch in dispatches:
                dispatch['asset_usages']=[dict(row) for row in conn.execute(
                    'SELECT asset_identity_json,reason,step_id,created_at FROM qa_execution_asset_usages WHERE run_id=? ORDER BY id',(dispatch['run_id'],))]
            recommendations=[dict(row) for row in conn.execute('''SELECT p.*,COALESCE(d.decision,'PROPOSED') status,
                d.actor_id reviewer_id,d.comments review_comments,d.created_at reviewed_at
                FROM qa_ai_recommendations p LEFT JOIN qa_ai_recommendation_decisions d ON d.recommendation_id=p.id
                WHERE p.test_case_id=? ORDER BY p.created_at''',(case_id,))]
            defects=[dict(row) for row in conn.execute('SELECT * FROM qa_defects WHERE test_case_id=?',(case_id,))]
            for defect in defects:
                defect['retests']=[dict(row) for row in conn.execute('SELECT * FROM qa_retests WHERE defect_id=?',(defect['id'],))]
                user=conn.execute('SELECT full_name,username FROM users WHERE id=?',(defect['assigned_to'],)).fetchone()
                defect['assigned_name']=(user[0] or user[1]) if user else None
            decisions=[dict(row) for row in conn.execute('SELECT r.*,COALESCE(NULLIF(u.full_name,\'\'),u.username) reviewer_name FROM qa_review_decisions r JOIN users u ON u.id=r.reviewer_id WHERE r.test_case_id=? ORDER BY r.id',(case_id,))]
            return {'test_case':{key:case.get(key) for key in self.SPEC_FIELDS},'test_case_row_id':case_id,'owner':owner,'review':self.review(conn,case),'decisions':decisions,'requirements':requirements,'assets':assets,'executions':runs,'dispatches':dispatches,'recommendations':recommendations,'defects':defects}
