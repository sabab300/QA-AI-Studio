"""Lifecycle views for the existing Reports area; all metrics use stored facts."""
from datetime import datetime
import json
from Core.qa_lifecycle_repository import LifecycleError

class QaLifecycleReports:
    FILTERS={'domain':'d.name','module':'m.name','knowledge_name':'k.name','document_type':'t.name','version':'v.version','document_version_id':'v.id'}
    def __init__(self,service):self.service=service
    def snapshot(self,filters=None):
        filters=filters or {};where=[];parameters=[]
        for key,column in self.FILTERS.items():
            if filters.get(key) not in (None,''):where.append(column+'=?');parameters.append(filters[key])
        for key in ('date_from','date_to'):
            if filters.get(key):
                try:datetime.fromisoformat(filters[key].replace('Z','+00:00'))
                except ValueError:raise LifecycleError('Use an ISO date/time filter.',400)
        clause=' AND '.join(where) or '1=1'
        joins=' FROM document_versions v JOIN knowledge_document_types t ON t.id=v.document_type_id JOIN knowledge_entities k ON k.id=t.knowledge_id JOIN modules m ON m.id=k.module_id JOIN domains d ON d.id=m.domain_id '
        with self.service.connection() as conn:
            owners=[row[0] for row in conn.execute('SELECT v.id'+joins+'WHERE '+clause,parameters)]
            if not owners:return self.empty()
            marks=','.join('?' for _ in owners)
            cases=[dict(row) for row in conn.execute('SELECT * FROM test_cases WHERE document_version_id IN ('+marks+') ORDER BY id LIMIT 1000',owners)]
            case_ids={case['id'] for case in cases}
            requirements=[dict(row) for row in conn.execute('SELECT * FROM qa_requirements WHERE document_version_id IN ('+marks+')',owners)]
            links=[dict(row) for row in conn.execute('SELECT l.* FROM qa_requirement_links l JOIN qa_requirements r ON r.id=l.requirement_id WHERE r.document_version_id IN ('+marks+')',owners)]
            confirmed={item['id'] for item in requirements if item['status']=='Confirmed'}
            covered={link['requirement_id'] for link in links if link['status']=='CONFIRMED' and link['test_case_id'] in case_ids}
            linked_cases={link['test_case_id'] for link in links if link['status']=='CONFIRMED'}
            reviews=[{'test_case_id':case['id'],**self.service.review(conn,case)} for case in cases]
            version_numbers={row[0]:row[1] for row in conn.execute('SELECT id,version_number FROM test_case_versions')}
            assets=[dict(row) for row in conn.execute('SELECT a.* FROM qa_asset_links a JOIN test_cases c ON c.id=a.test_case_id WHERE c.document_version_id IN ('+marks+')',owners)]
            executions=[dict(row) for row in conn.execute('SELECT * FROM qa_execution_annotations WHERE document_version_id IN ('+marks+') ORDER BY created_at',owners)]
            def in_dates(row):return (not filters.get('date_from') or row['created_at']>=filters['date_from']) and (not filters.get('date_to') or row['created_at']<=filters['date_to'])
            executions=[row for row in executions if in_dates(row)]
            for row in executions:row['result']=json.loads(row['result_json'])
            if filters.get('execution_type'):executions=[row for row in executions if conn.execute('SELECT automation_type FROM automation_runs WHERE id=?',(row['run_id'],)).fetchone()[0]==filters['execution_type']]
            if filters.get('status'):executions=[row for row in executions if row['result']['outcome']==filters['status']]
            defects=[dict(row) for row in conn.execute('SELECT f.* FROM qa_defects f JOIN test_cases c ON c.id=f.test_case_id WHERE c.document_version_id IN ('+marks+')',owners)]
            defects=[row for row in defects if in_dates(row)]
            retests=[dict(row) for row in conn.execute('SELECT r.* FROM qa_retests r JOIN qa_defects f ON f.id=r.defect_id JOIN test_cases c ON c.id=f.test_case_id WHERE c.document_version_id IN ('+marks+')',owners)]
            retests=[row for row in retests if in_dates(row)]
            sources=[row[0] for row in conn.execute('SELECT id FROM knowledge_items WHERE document_version_id IN ('+marks+')',owners)]
            unanalyzed=set(sources)-{row['source_id'] for row in requirements}
            impacts=[dict(row) for row in conn.execute('SELECT logical_key,COUNT(DISTINCT quote_hash) different_quotes,COUNT(DISTINCT document_version_id) document_versions FROM qa_requirements WHERE logical_key IN (SELECT logical_key FROM qa_requirements WHERE document_version_id IN ('+marks+')) GROUP BY logical_key HAVING COUNT(DISTINCT quote_hash)>1',owners)]
            trends={}
            for row in executions:
                day=row['created_at'][:10];entry=trends.setdefault(day,{'Pass':0,'Fail':0,'Other':0});outcome=row['result']['outcome'];entry[outcome if outcome in ('Pass','Fail') else 'Other']+=1
            return {
              'requirement_coverage':{'confirmed':len(confirmed),'covered':len(confirmed&covered),'uncovered':sorted(confirmed-covered),'coverage_percent':round(100*len(confirmed&covered)/len(confirmed),2) if confirmed else None,'unanalysed_source_ids':sorted(unanalyzed),'basis':'Confirmed extracted requirements only; not a claim of full document coverage'},
              'review_approval':reviews,
              'automation_coverage':{'linked_assets':len(assets),'linked_test_case_revisions':len({row['test_case_id'] for row in assets}),'total_test_case_revisions':len(cases),'legacy_assets_not_yet_linked':True},
              'execution_summary':{'total':len(executions),'Pass':sum(row['result']['outcome']=='Pass' for row in executions),'Fail':sum(row['result']['outcome']=='Fail' for row in executions),'legacy_unannotated_runs_not_counted':True},
              'pass_fail_trends':trends,'defect_summary':defects,'retest_status':retests,
              'requirement_to_defect':[{'requirement_id':link['requirement_id'],'test_case_id':link['test_case_id'],'defect_ids':[d['id'] for d in defects if d['test_case_id']==link['test_case_id']]} for link in links if link['status']=='CONFIRMED'],
              'release_quality':{'assessment':'Not a release approval','uncovered_requirements':len(confirmed-covered),'unapproved_revisions':sum(row['state'] not in ('Approved','Ready for Execution') for row in reviews),'open_defects':sum(row['state']!='Closed' for row in defects),'cases_without_confirmed_requirements':sorted(case_ids-linked_cases)},
              'lifecycle_traceability':[{'test_case_id':case['id'],'tc_number':case['tc_number'],'test_case_version_id':case.get('test_case_version_id'),'revision_number':version_numbers.get(case.get('test_case_version_id')),'document_version_id':case['document_version_id']} for case in cases],
              'document_revision_impacts':impacts,'case_limit':1000,'case_results_may_be_truncated':len(cases)==1000,
              'date_filter_basis':'Dates apply to execution/defect/retest events, not lifetime requirement totals.'}
    @staticmethod
    def empty():return {'requirement_coverage':{'confirmed':0,'covered':0,'uncovered':[],'coverage_percent':None},'review_approval':[],'automation_coverage':{'linked_assets':0},'execution_summary':{'total':0,'Pass':0,'Fail':0},'pass_fail_trends':{},'defect_summary':[],'retest_status':[],'requirement_to_defect':[],'release_quality':{'assessment':'No scoped data'},'lifecycle_traceability':[],'document_revision_impacts':[]}
