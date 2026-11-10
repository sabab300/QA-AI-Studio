"""Requirement-owned discovery collections, independent of TC automation assets.

Uses canonical source IDs/paths, immutable version files and owner-scoped rows.
No legacy api_collections rows are repurposed or migrated into new ownership.
"""
import json
import os
import re
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime,timezone
from pathlib import Path
from urllib.parse import urlsplit,parse_qsl
from .assertions import validate as validate_assertions
from .evidence import DiscoveryError,METHODS

REFERENCE=re.compile(r'^(?:Bearer |Basic )?\{\{[A-Za-z_][A-Za-z0-9_-]{0,63}\}\}$')
NAME=re.compile(r'^[A-Za-z_][A-Za-z0-9_-]{0,63}$')


def reviewed_document(document):
    from Core.secret_masking import is_sensitive_key
    if not isinstance(document,dict):raise DiscoveryError('Collection must be an object.')
    if len(json.dumps(document))>1024*1024:raise DiscoveryError('Collection exceeds 1 MiB.')
    requests=document.get('requests')
    if not isinstance(requests,list) or not 1<=len(requests)<=100:raise DiscoveryError('Review between 1 and 100 API requests.')
    def secrets(value,key='',sensitive_parent=False):
        sensitive=sensitive_parent or is_sensitive_key(key)
        if sensitive and value not in (None,'') and not isinstance(value,(dict,list)) and not REFERENCE.fullmatch(str(value)):
            raise DiscoveryError('Sensitive request fields require a secure variable reference: '+key[:80])
        if isinstance(value,dict):
            for k,v in value.items():secrets(v,str(k),sensitive)
        elif isinstance(value,list):
            for v in value:secrets(v,key,sensitive)
    identities=set()
    clean=json.loads(json.dumps(document))
    allowed={'requests','variables','name','description','evidence_session_id','format','format_version','collection_id','version','source_id','hierarchy','compatibility','provenance'}
    if set(clean)-allowed:raise DiscoveryError('Unsupported collection fields: '+', '.join(sorted(set(clean)-allowed)))
    for field,limit in (('name',150),('description',2000)):
        if field in clean and (not isinstance(clean[field],str) or len(clean[field])>limit):raise DiscoveryError('Invalid collection '+field+'.')
    compatibility=clean.get('compatibility',[])
    if not isinstance(compatibility,list) or len(compatibility)>50 or any(not isinstance(value,str) or len(value)>500 for value in compatibility):raise DiscoveryError('Invalid compatibility report.')
    provenance=clean.get('provenance',{})
    if not isinstance(provenance,dict) or set(provenance)-{'format','collection_id','version'}:raise DiscoveryError('Invalid import provenance.')
    if provenance.get('collection_id') and not re.fullmatch('[0-9a-f]{32}',str(provenance['collection_id'])):raise DiscoveryError('Invalid imported collection identity.')
    if provenance.get('format') and provenance['format'] not in {'native','postman','har'}:raise DiscoveryError('Invalid provenance format.')
    if 'version' in provenance and (type(provenance['version']) is not int or not 1<=provenance['version']<=1000000):raise DiscoveryError('Invalid provenance version.')
    for item in clean['requests']:
        if not isinstance(item,dict):raise DiscoveryError('Request must be an object.')
        if set(item)-{'id','name','method','url','headers','body','body_mode','assertions','extract','depends_on','evidence_ids','folder','auth','requires_configuration'}:
            raise DiscoveryError('Unsupported request configuration fields.')
        identity=str(item.get('id',''))
        if not NAME.fullmatch(identity) or identity in identities:raise DiscoveryError('Each API requires a distinct stable identifier.')
        identities.add(identity)
        if not isinstance(item.get('requires_configuration',False),bool):raise DiscoveryError('Request configuration state must be boolean.')
        if 'name' in item and (not isinstance(item['name'],str) or len(item['name'])>250):raise DiscoveryError('Request name exceeds 250 characters or is invalid.')
        folder=item.get('folder','')
        if not isinstance(folder,str) or len(folder)>500 or len(folder.split(' > '))>8:raise DiscoveryError('Invalid request folder breadcrumb.')
        evidence=item.get('evidence_ids',[])
        if not isinstance(evidence,list) or len(evidence)>2000 or any(type(value) is not int or value<1 for value in evidence):raise DiscoveryError('Invalid source evidence references.')
        if item.get('method') not in METHODS:raise DiscoveryError('Unsupported HTTP method.')
        item.setdefault('headers',{});item.setdefault('assertions',[])
        if item.get('body_mode','json') not in {'json','text','urlencoded'}:raise DiscoveryError('Unsupported request body encoding.')
        if item.get('body_mode')=='urlencoded' and not isinstance(item.get('body'),dict):raise DiscoveryError('URL-encoded bodies require a field/value object.')
        url=str(item.get('url',''));parts=urlsplit(url.replace('{{baseUrl}}','https://example.invalid'))
        if parts.scheme not in {'http','https'} or not parts.hostname or parts.username or parts.password or parts.fragment:
            raise DiscoveryError('Use an HTTP(S) URL without embedded credentials or fragments.')
        for key,value in parse_qsl(parts.query):secrets(value,key)
        headers=item.get('headers',{})
        if not isinstance(headers,dict) or len(headers)>60:raise DiscoveryError('Headers must be a bounded name/value object.')
        if len({name.lower() for name in headers})!=len(headers):raise DiscoveryError('Repeated header names are not supported.')
        for key,value in headers.items():
            if not isinstance(value,str):raise DiscoveryError('Header values must be text or variable references.')
            if '\r' in str(key)+str(value) or '\n' in str(key)+str(value):raise DiscoveryError('Header newlines are not allowed.')
            if str(key).lower() in {'host','content-length','transfer-encoding','connection','proxy-authorization'}:raise DiscoveryError('Transport-managed header cannot be configured.')
        secrets(headers);secrets(item.get('body'))
        if isinstance(item.get('body'),str):
            try:parsed_body=json.loads(item['body'])
            except (ValueError,TypeError):
                if is_sensitive_key(item['body']):raise DiscoveryError('Use structured JSON and variable references for sensitive request bodies; unsafe raw credentials are not saved.')
            else:secrets(parsed_body)
        auth=item.get('auth',{})
        if not isinstance(auth,dict) or set(auth)-{'type','variable','username_variable','password_variable'}:raise DiscoveryError('Invalid authentication references.')
        if auth.get('type','none') not in {'none','bearer','basic'}:raise DiscoveryError('Use header variable references for API keys; unsupported authentication method.')
        for key,value in auth.items():
            if key!='type' and not NAME.fullmatch(str(value)):raise DiscoveryError('Authentication must name a secure environment variable.')
        validate_assertions(item.get('assertions',[]))
        for assertion in item.get('assertions',[]):
            if assertion['type']!='schema':secrets(assertion.get('expected'))
        extracts=item.get('extract',[])
        if not isinstance(extracts,list) or len(extracts)>20:raise DiscoveryError('At most 20 extraction rules per API.')
        from .assertions import resolve
        for extraction in extracts:
            if not isinstance(extraction,dict) or set(extraction)-{'variable','path'}:raise DiscoveryError('Unsupported extraction rule fields.')
            if not isinstance(extraction,dict) or not NAME.fullmatch(str(extraction.get('variable',''))):raise DiscoveryError('Extraction requires a valid variable name.')
            resolve({},extraction.get('path',''))
        if not isinstance(item.get('depends_on',[]),list):raise DiscoveryError('Dependencies must be an ordered list.')
        if len(item.get('depends_on',[]))>100 or any(not isinstance(value,str) or not NAME.fullmatch(value) for value in item.get('depends_on',[])):raise DiscoveryError('Invalid dependency references.')
    previous=set()
    for item in clean['requests']:
        if not set(item.get('depends_on',[]))<=previous:raise DiscoveryError('Dependencies must reference an earlier API in this collection.')
        previous.add(item['id'])
    variables=clean.setdefault('variables',{})
    if not isinstance(variables,dict) or len(variables)>100 or any(not NAME.fullmatch(k) for k in variables):raise DiscoveryError('Invalid variable definitions.')
    if any(isinstance(value,(dict,list)) or len(str(value))>8000 for value in variables.values()):raise DiscoveryError('Variables must contain bounded scalar values or secure references.')
    secrets(variables)
    clean['format']='qa-ai-studio-api-collection';clean['format_version']=1
    return clean


class CollectionRepository:
    def __init__(self,db_path=None,central=None):
        if db_path is None:
            from Database.db_manager import DatabaseManager
            db_path=DatabaseManager().db_path
        if central is None:
            from Shared.central_repository import CentralRepository
            central=CentralRepository()
        self.db_path=Path(db_path);self.central=central
        with self.connection() as connection:
            connection.executescript('''
                CREATE TABLE IF NOT EXISTS api_discovery_collections(
                    id TEXT PRIMARY KEY,owner_id INTEGER NOT NULL,source_id INTEGER NOT NULL REFERENCES knowledge_items(id) ON DELETE RESTRICT,
                    name TEXT NOT NULL,description TEXT NOT NULL DEFAULT '',status TEXT NOT NULL DEFAULT 'Active',
                    current_version INTEGER NOT NULL DEFAULT 0,created_at TEXT NOT NULL,updated_at TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS idx_ad_collections_owner_source ON api_discovery_collections(owner_id,source_id);
                CREATE TABLE IF NOT EXISTS api_discovery_collection_versions(
                    collection_id TEXT NOT NULL REFERENCES api_discovery_collections(id) ON DELETE RESTRICT,
                    version INTEGER NOT NULL,document_json TEXT NOT NULL,relative_path TEXT NOT NULL,created_at TEXT NOT NULL,
                    PRIMARY KEY(collection_id,version));
                CREATE TABLE IF NOT EXISTS api_discovery_runs(
                    id TEXT PRIMARY KEY,collection_id TEXT NOT NULL,collection_version INTEGER NOT NULL,
                    owner_id INTEGER NOT NULL,status TEXT NOT NULL,created_at TEXT NOT NULL,updated_at TEXT NOT NULL,
                    result_json TEXT NOT NULL DEFAULT '{}',relative_path TEXT NOT NULL DEFAULT '',
                    FOREIGN KEY(collection_id,collection_version) REFERENCES api_discovery_collection_versions(collection_id,version) ON DELETE RESTRICT);
                CREATE INDEX IF NOT EXISTS idx_ad_runs_owner ON api_discovery_runs(owner_id,created_at);
                CREATE TABLE IF NOT EXISTS api_discovery_collection_audit(
                    id INTEGER PRIMARY KEY,collection_id TEXT NOT NULL REFERENCES api_discovery_collections(id) ON DELETE RESTRICT,
                    owner_id INTEGER NOT NULL,action TEXT NOT NULL,created_at TEXT NOT NULL,version INTEGER NOT NULL);
            ''')

    @contextmanager
    def connection(self):
        connection=sqlite3.connect(self.db_path,timeout=15);connection.row_factory=sqlite3.Row
        connection.execute('PRAGMA foreign_keys=ON')
        try:
            with connection:yield connection
        finally:connection.close()

    def source(self,connection,source_id):
        from Shared.document_hierarchy import DocumentHierarchy
        source=connection.execute('SELECT * FROM knowledge_items WHERE id=?',(source_id,)).fetchone()
        if not source:raise DiscoveryError('Selected source document no longer exists.')
        source=dict(source);owner=DocumentHierarchy(connection).resolve_source(source_id)
        path=self.central.resolve_managed(source.get('repository_path') or source.get('stored_path') or '',source.get('repository_relative_path') or '')
        if not path.is_relative_to(self.central.root.resolve()) or not path.is_file():
            raise DiscoveryError('Managed source document cannot be resolved safely. Collection was not saved.')
        return source,owner,path.parent

    def save(self,owner_id,source_id,name,document,identity=None,expected_version=0):
        document=reviewed_document(document);name=str(name).strip()
        if not name or len(name)>150:raise DiscoveryError('Collection name is required (150 characters maximum).')
        now=datetime.now(timezone.utc).isoformat();identity=identity or uuid.uuid4().hex;created_path=None
        if not re.fullmatch(r'[0-9a-f]{32}',identity):raise DiscoveryError('Invalid collection identity.')
        try:
            with self.connection() as connection:
                connection.execute('BEGIN IMMEDIATE')
                existing=connection.execute('SELECT * FROM api_discovery_collections WHERE id=?',(identity,)).fetchone()
                if existing and (existing['owner_id']!=owner_id or existing['source_id']!=source_id):raise DiscoveryError('Collection ownership cannot be changed.')
                current=existing['current_version'] if existing else 0
                if expected_version!=current:raise DiscoveryError('Collection changed since it was opened. Refresh before saving.')
                source,hierarchy,parent=self.source(connection,source_id)
                hierarchy=dict(hierarchy,source_type=source.get('source_type',''),source_document=source.get('file_name',''),source_id=source_id)
                if document.get('evidence_session_id'):
                    evidence=connection.execute('SELECT * FROM api_discovery_sessions WHERE id=?',(document['evidence_session_id'],)).fetchone()
                    if not evidence or evidence['owner_id']!=owner_id:raise DiscoveryError('Captured evidence is not owned by the current user.')
                    if 'source_id' in evidence.keys() and evidence['source_id'] not in (None,source_id):raise DiscoveryError('Captured evidence belongs to a different source document.')
                version=current+1
                document.update(name=name,collection_id=identity,version=version,source_id=source_id,hierarchy=hierarchy)
                destination=parent/'Recorded APIs'/('C-'+identity[:12])/'versions'/('V-'+str(version))/'collection.json'
                if not destination.resolve().is_relative_to(self.central.root.resolve()):raise DiscoveryError('Managed collection location escaped repository ownership.')
                if len(str(destination))>240:raise DiscoveryError('Managed collection path exceeds the supported path budget.')
                if destination.exists():raise DiscoveryError('Collection artifact already exists; reconcile before retrying.')
                destination.parent.mkdir(parents=True,exist_ok=True)
                encoded=json.dumps(document,ensure_ascii=False,indent=2)
                temporary=destination.with_suffix('.'+uuid.uuid4().hex+'.tmp')
                created_path=temporary
                with temporary.open('x',encoding='utf-8') as stream:
                    stream.write(encoded);stream.flush();os.fsync(stream.fileno())
                os.replace(temporary,destination);created_path=destination
                if json.loads(destination.read_text(encoding='utf-8'))!=document:raise DiscoveryError('Collection artifact verification failed.')
                if not existing:
                    connection.execute('INSERT INTO api_discovery_collections(id,owner_id,source_id,name,created_at,updated_at) VALUES(?,?,?,?,?,?)',(identity,owner_id,source_id,name,now,now))
                connection.execute('INSERT INTO api_discovery_collection_versions VALUES(?,?,?,?,?)',(identity,version,encoded,self.central.relative(destination),now))
                connection.execute('UPDATE api_discovery_collections SET name=?,current_version=?,updated_at=? WHERE id=?',(name,version,now,identity))
                connection.execute('INSERT INTO api_discovery_collection_audit(collection_id,owner_id,action,created_at,version) VALUES(?,?,?,?,?)',(identity,owner_id,'save_version',now,version))
                if document.get('evidence_session_id'):
                    connection.execute('INSERT INTO api_discovery_outputs(id,session_id,kind,state,result_json) VALUES(?,?,?,?,?)',
                        ('collection:'+identity+':'+str(version),document['evidence_session_id'],'collection','completed',json.dumps({'collection_id':identity,'version':version,'source_id':source_id})))
            return self.get(identity,owner_id)
        except BaseException:
            if created_path and created_path.is_file():
                created_path.unlink()
                parent=created_path.parent
                # Prune ONLY the newly-created empty version directory.
                if parent.name.startswith('V-') and not any(parent.iterdir()):parent.rmdir()
            raise

    def get(self,identity,owner_id,version=None):
        with self.connection() as connection:
            row=connection.execute('SELECT * FROM api_discovery_collections WHERE id=? AND owner_id=?',(identity,owner_id)).fetchone()
            if not row:raise KeyError('Collection not found for current user.')
            result=dict(row)
            record=connection.execute('SELECT * FROM api_discovery_collection_versions WHERE collection_id=? AND version=?',(identity,version or row['current_version'])).fetchone()
            if not record:raise KeyError('Collection version was not found.')
            result.update(document=json.loads(record['document_json']),version=record['version'],relative_path=record['relative_path'])
            artifact=self.central.resolve_managed('',record['relative_path'])
            if not artifact.is_relative_to(self.central.root.resolve()) or not artifact.is_file():
                raise DiscoveryError('Managed collection artifact is unavailable; historical metadata was preserved.')
            if artifact.stat().st_size>2*1024*1024 or json.loads(artifact.read_text(encoding='utf-8'))!=result['document']:
                raise DiscoveryError('Collection artifact and metadata disagree; restore or reconcile before replay.')
            source=connection.execute('SELECT repository_path,repository_relative_path FROM knowledge_items WHERE id=?',(row['source_id'],)).fetchone()
            if not source or not artifact.is_relative_to(self.central.resolve_managed(source[0] or '',source[1] or '').parent):
                raise DiscoveryError('Collection requires controlled legacy path adoption before replay.')
            return result

    def list(self,owner_id):
        with self.connection() as connection:
            return [dict(row) for row in connection.execute('SELECT * FROM api_discovery_collections WHERE owner_id=? ORDER BY updated_at DESC LIMIT 200',(owner_id,))]

    def page(self,owner_id,source_id,offset=0,limit=25,search='',sort='updated_at'):
        order={'name':'c.name COLLATE NOCASE','updated_at':'c.updated_at DESC','version':'c.current_version DESC'}.get(sort,'c.updated_at DESC')
        with self.connection() as connection:
            args=(owner_id,source_id,'%'+search[:150]+'%')
            total=connection.execute('SELECT COUNT(*) FROM api_discovery_collections WHERE owner_id=? AND source_id=? AND name LIKE ?',args).fetchone()[0]
            rows=connection.execute('SELECT c.*,json_array_length(v.document_json,\'$.requests\') AS api_count FROM api_discovery_collections c JOIN api_discovery_collection_versions v ON v.collection_id=c.id AND v.version=c.current_version WHERE c.owner_id=? AND c.source_id=? AND c.name LIKE ? ORDER BY '+order+' LIMIT ? OFFSET ?',args+(min(100,max(1,limit)),max(0,offset))).fetchall()
            return {'collections':[dict(row) for row in rows],'total':total}

    def duplicates(self,owner_id,source_id,document):
        shape=lambda value:json.dumps({'requests':value.get('requests'),'variables':value.get('variables',{})},sort_keys=True,separators=(',',':'))
        expected=shape(reviewed_document(document));result=[]
        with self.connection() as connection:
            rows=connection.execute('SELECT c.id,c.name,v.document_json FROM api_discovery_collections c JOIN api_discovery_collection_versions v ON v.collection_id=c.id AND v.version=c.current_version WHERE c.owner_id=? AND c.source_id=?',(owner_id,source_id))
            for row in rows:
                if shape(json.loads(row['document_json']))==expected:result.append({'id':row['id'],'name':row['name']})
        return result

    def archive(self,identity,owner_id):
        with self.connection() as connection:
            row=connection.execute('SELECT current_version FROM api_discovery_collections WHERE id=? AND owner_id=?',(identity,owner_id)).fetchone()
            if not row:raise KeyError('Collection not found for current user.')
            now=datetime.now(timezone.utc).isoformat()
            connection.execute("UPDATE api_discovery_collections SET status='Archived',updated_at=? WHERE id=?",(now,identity))
            connection.execute('INSERT INTO api_discovery_collection_audit(collection_id,owner_id,action,created_at,version) VALUES(?,?,?,?,?)',(identity,owner_id,'archive',now,row[0]))

    def new_run(self,identity,owner_id,version=None):
        collection=self.get(identity,owner_id,version)
        if collection['status']=='Archived':raise DiscoveryError('Archived collections cannot execute.')
        run_id=uuid.uuid4().hex;now=datetime.now(timezone.utc).isoformat()
        with self.connection() as connection:
            connection.execute('INSERT INTO api_discovery_runs(id,collection_id,collection_version,owner_id,status,created_at,updated_at) VALUES(?,?,?,?,?,?,?)',
                (run_id,identity,collection['version'],owner_id,'QUEUED',now,now))
            connection.execute('INSERT INTO api_discovery_collection_audit(collection_id,owner_id,action,created_at,version) VALUES(?,?,?,?,?)',
                (identity,owner_id,'execute_version',now,collection['version']))
        return run_id,collection

    def update_run(self,run_id,owner_id,result,final=False):
        now=datetime.now(timezone.utc).isoformat();created=None
        try:
            with self.connection() as connection:
                row=connection.execute('SELECT r.*,c.source_id FROM api_discovery_runs r JOIN api_discovery_collections c ON c.id=r.collection_id WHERE r.id=? AND r.owner_id=?',(run_id,owner_id)).fetchone()
                if not row:raise KeyError('Run not found for current user.')
                relative=''
                if final:
                    source,_,parent=self.source(connection,row['source_id'])
                    directory=parent/'Recorded APIs'/('C-'+row['collection_id'][:12])/'executions'/('RUN-'+run_id[:12])
                    if not directory.resolve().is_relative_to(self.central.root.resolve()):raise DiscoveryError('Execution location escaped repository ownership.')
                    if directory.exists():raise DiscoveryError('Run artifact already exists; historical execution was not overwritten.')
                    directory.mkdir(parents=True);created=directory
                    payload=dict(result,run_id=run_id,collection_id=row['collection_id'],version=row['collection_version'],executed_by=owner_id,executed_at=row['created_at'])
                    version=connection.execute('SELECT document_json FROM api_discovery_collection_versions WHERE collection_id=? AND version=?',(row['collection_id'],row['collection_version'])).fetchone()
                    version=json.loads(version[0]);payload.update(hierarchy=version.get('hierarchy',{}),collection_name=version.get('name',''))
                    destination=directory/'results.json'
                    with destination.open('x',encoding='utf-8') as stream:
                        json.dump(payload,stream,ensure_ascii=False,indent=2);stream.flush();os.fsync(stream.fileno())
                    from .execution_reports import render
                    for format in ('html','pdf','xlsx'):
                        data,_=render(payload,format)
                        with (directory/('report.'+format)).open('xb') as stream:
                            stream.write(data);stream.flush();os.fsync(stream.fileno())
                    relative=self.central.relative(destination);result=payload
                connection.execute('UPDATE api_discovery_runs SET status=?,result_json=?,relative_path=CASE WHEN ?<>\'\' THEN ? ELSE relative_path END,updated_at=? WHERE id=?',
                    (result['status'],json.dumps(result),relative,relative,now,run_id))
        except BaseException:
            if created:
                for path in created.iterdir():
                    if path.is_file() and path.name in {'results.json','report.html','report.pdf','report.xlsx'}:path.unlink()
                if not any(created.iterdir()):created.rmdir()
            raise

    def get_run(self,run_id,owner_id):
        with self.connection() as connection:
            row=connection.execute('SELECT * FROM api_discovery_runs WHERE id=? AND owner_id=?',(run_id,owner_id)).fetchone()
            if not row:raise KeyError('Run not found for current user.')
            result=dict(row);result['result']=json.loads(result.pop('result_json'));return result

    def runs(self,identity,owner_id):
        self.get(identity,owner_id)
        with self.connection() as connection:
            return [dict(row) for row in connection.execute('SELECT id,status,collection_version,created_at,updated_at FROM api_discovery_runs WHERE collection_id=? AND owner_id=? ORDER BY created_at DESC LIMIT 100',(identity,owner_id))]
