"""Canonical Knowledge -> Document Type -> Version ownership.

Shared by Desktop/Web; callers provide their own SQLite connection.
Labels on source rows remain compatibility aliases, not ownership keys.
Schema/data migration is explicit and never opens the active runtime itself.
"""
from datetime import datetime
import sqlite3


SCHEMA = (
    '''CREATE TABLE IF NOT EXISTS knowledge_entities (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        module_id INTEGER NOT NULL REFERENCES modules(id) ON DELETE RESTRICT,
        name TEXT NOT NULL CHECK(length(trim(name))>0),
        created_date TEXT NOT NULL,
        UNIQUE(module_id,name)
    )''',
    '''CREATE TABLE IF NOT EXISTS knowledge_document_types (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        knowledge_id INTEGER NOT NULL REFERENCES knowledge_entities(id) ON DELETE RESTRICT,
        name TEXT NOT NULL CHECK(length(trim(name))>0),
        created_date TEXT NOT NULL,
        UNIQUE(knowledge_id,name)
    )''',
    '''CREATE TABLE IF NOT EXISTS document_versions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        document_type_id INTEGER NOT NULL REFERENCES knowledge_document_types(id) ON DELETE RESTRICT,
        version TEXT NOT NULL CHECK(length(trim(version))>0),
        created_date TEXT NOT NULL,
        UNIQUE(document_type_id,version)
    )''',
)


class HierarchyConflict(ValueError):
    pass


class DocumentHierarchy:
    def __init__(self, connection):
        self.connection = connection

    def ensure_schema(self):
        cursor=self.connection.cursor();cursor.row_factory=None
        for sql in SCHEMA:
            cursor.execute(sql)
        tables = {r[0] for r in cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        for table in ('knowledge_items','knowledge_versions','test_case_versions','test_cases',
                      'api_collections','qa_generation_jobs','automation_runs','sql_scripts'):
            if table not in tables:
                continue
            columns = {r[1] for r in cursor.execute('PRAGMA table_info('+table+')')}
            if 'document_version_id' not in columns:
                cursor.execute('ALTER TABLE '+table+' ADD COLUMN document_version_id INTEGER REFERENCES document_versions(id) ON DELETE RESTRICT')
            cursor.execute('CREATE INDEX IF NOT EXISTS ix_'+table+'_document_version ON '+table+'(document_version_id)')
        self._ownership_triggers(cursor,tables)

    def _ownership_triggers(self,cursor,tables):
        """Protect every writer, including legacy/Desktop direct SQL paths."""
        if 'knowledge_items' in tables:
            match='''SELECT v.id FROM document_versions v JOIN knowledge_document_types t ON t.id=v.document_type_id
                JOIN knowledge_entities k ON k.id=t.knowledge_id JOIN modules m ON m.id=k.module_id
                JOIN domains d ON d.id=m.domain_id WHERE d.name=NEW.domain AND m.name=NEW.module
                AND k.name=NEW.knowledge_name AND t.name=NEW.document_type AND v.version=NEW.version
                AND (NEW.domain_id IS NULL OR NEW.domain_id=d.id) AND (NEW.module_id IS NULL OR NEW.module_id=m.id)'''
            cursor.execute('''CREATE TRIGGER IF NOT EXISTS kh_bind_source_insert AFTER INSERT ON knowledge_items BEGIN
                INSERT INTO knowledge_entities(module_id,name,created_date)
                SELECT m.id,NEW.knowledge_name,datetime('now') FROM modules m JOIN domains d ON d.id=m.domain_id
                WHERE d.name=NEW.domain AND m.name=NEW.module
                AND (NEW.domain_id IS NULL OR NEW.domain_id=d.id) AND (NEW.module_id IS NULL OR NEW.module_id=m.id)
                ON CONFLICT(module_id,name) DO NOTHING;
                INSERT INTO knowledge_document_types(knowledge_id,name,created_date)
                SELECT k.id,NEW.document_type,datetime('now') FROM knowledge_entities k
                JOIN modules m ON m.id=k.module_id JOIN domains d ON d.id=m.domain_id
                WHERE d.name=NEW.domain AND m.name=NEW.module AND k.name=NEW.knowledge_name
                ON CONFLICT(knowledge_id,name) DO NOTHING;
                INSERT INTO document_versions(document_type_id,version,created_date)
                SELECT t.id,NEW.version,datetime('now') FROM knowledge_document_types t
                JOIN knowledge_entities k ON k.id=t.knowledge_id JOIN modules m ON m.id=k.module_id
                JOIN domains d ON d.id=m.domain_id WHERE d.name=NEW.domain AND m.name=NEW.module
                AND k.name=NEW.knowledge_name AND t.name=NEW.document_type
                ON CONFLICT(document_type_id,version) DO NOTHING;
                SELECT CASE WHEN ('''+match+''') IS NULL OR (NEW.document_version_id IS NOT NULL AND NEW.document_version_id<>('''+match+'''))
                    THEN RAISE(ABORT,'Source hierarchy ownership conflict') END;
                UPDATE knowledge_items SET document_version_id=('''+match+''') WHERE id=NEW.id;
                END''')
            cursor.execute('''CREATE TRIGGER IF NOT EXISTS kh_guard_source_update BEFORE UPDATE OF domain,module,knowledge_name,document_type,version,domain_id,module_id,document_version_id ON knowledge_items
                WHEN NEW.document_version_id IS NOT NULL AND NEW.document_version_id IS NOT ('''+match+''')
                BEGIN SELECT RAISE(ABORT,'Source hierarchy ownership conflict'); END''')
        definitions={
            'test_case_versions':('source_knowledge_id','SELECT document_version_id FROM knowledge_items WHERE id=NEW.source_knowledge_id'),
            'test_cases':('test_case_version_id','SELECT document_version_id FROM test_case_versions WHERE id=NEW.test_case_version_id'),
            'sql_scripts':('test_case_version_id','SELECT document_version_id FROM test_case_versions WHERE id=NEW.test_case_version_id'),
            'api_collections':('test_case_version_id,linked_knowledge_item_id','SELECT COALESCE((SELECT document_version_id FROM test_case_versions WHERE id=NEW.test_case_version_id),(SELECT document_version_id FROM knowledge_items WHERE id=NEW.linked_knowledge_item_id))'),
            'automation_runs':('test_case_version_id,test_case_id','SELECT COALESCE((SELECT document_version_id FROM test_case_versions WHERE id=NEW.test_case_version_id),(SELECT document_version_id FROM test_cases WHERE id=NEW.test_case_id))'),
        }
        for table,(fields,owner) in definitions.items():
            columns={row[1] for row in cursor.execute('PRAGMA table_info('+table+')')}
            if not set(fields.split(','))|{'id','document_version_id'} <= columns:continue
            # Lazy-created tables must exist before their ownership is read.
            if 'test_case_versions' in owner and 'test_case_versions' not in tables:continue
            if table=='api_collections':
                cursor.execute('''CREATE TRIGGER IF NOT EXISTS kh_guard_api_source_insert BEFORE INSERT ON api_collections
                    WHEN NEW.test_case_version_id IS NOT NULL AND NEW.linked_knowledge_item_id IS NOT NULL
                        AND NEW.linked_knowledge_item_id IS NOT (SELECT source_knowledge_id FROM test_case_versions WHERE id=NEW.test_case_version_id)
                    BEGIN SELECT RAISE(ABORT,'API collection source ownership conflict'); END''')
                cursor.execute('''CREATE TRIGGER IF NOT EXISTS kh_guard_api_source_update BEFORE UPDATE OF test_case_version_id,linked_knowledge_item_id ON api_collections
                    WHEN NEW.test_case_version_id IS NOT NULL AND NEW.linked_knowledge_item_id IS NOT NULL
                        AND NEW.linked_knowledge_item_id IS NOT (SELECT source_knowledge_id FROM test_case_versions WHERE id=NEW.test_case_version_id)
                    BEGIN SELECT RAISE(ABORT,'API collection source ownership conflict'); END''')
            references={'test_case_versions': [('source_knowledge_id','knowledge_items')],
                'test_cases':[('test_case_version_id','test_case_versions')],
                'sql_scripts':[('test_case_version_id','test_case_versions')],
                'api_collections':[('test_case_version_id','test_case_versions'),('linked_knowledge_item_id','knowledge_items')],
                'automation_runs':[('test_case_id','test_cases'),('test_case_version_id','test_case_versions')]}
            for field,parent in references.get(table,[]):
                if parent not in tables:continue
                cursor.execute('CREATE TRIGGER IF NOT EXISTS kh_guard_'+table+'_'+field+'_insert BEFORE INSERT ON '+table+
                    ' WHEN NEW.'+field+' IS NOT NULL AND NOT EXISTS(SELECT 1 FROM '+parent+' WHERE id=NEW.'+field+
                    ") BEGIN SELECT RAISE(ABORT,'Selected hierarchy owner is absent'); END")
            for operation in ('INSERT','UPDATE OF '+fields+',document_version_id'):
                suffix='insert' if operation=='INSERT' else 'update'
                cursor.execute('CREATE TRIGGER IF NOT EXISTS kh_bind_'+table+'_'+suffix+' AFTER '+operation+' ON '+table+
                    ' WHEN ('+owner+') IS NOT NULL AND NEW.document_version_id IS NOT ('+owner+') BEGIN SELECT CASE WHEN NEW.document_version_id IS NOT NULL AND NEW.document_version_id<>('+owner+
                    ") THEN RAISE(ABORT,'Dependent document version ownership conflict') END; UPDATE "+table+' SET document_version_id=('+owner+') WHERE id=NEW.id; END')
        if 'test_case_sources' in tables and 'test_cases' in tables:
            columns={row[1] for row in cursor.execute('PRAGMA table_info(test_case_sources)')}
            if {'test_case_id','knowledge_source_id'}<=columns:
                cursor.execute('''CREATE TRIGGER IF NOT EXISTS kh_bind_case_source AFTER INSERT ON test_case_sources BEGIN
                    SELECT CASE WHEN (SELECT document_version_id FROM test_cases WHERE id=NEW.test_case_id) IS NOT NULL
                        AND (SELECT document_version_id FROM test_cases WHERE id=NEW.test_case_id) IS NOT
                            (SELECT document_version_id FROM knowledge_items WHERE id=NEW.knowledge_source_id)
                        THEN RAISE(ABORT,'Test case sources span different document versions') END;
                    UPDATE test_cases SET document_version_id=(SELECT document_version_id FROM knowledge_items WHERE id=NEW.knowledge_source_id)
                        WHERE id=NEW.test_case_id;
                    END''')

    @staticmethod
    def _required(value,label):
        value = str(value or '').strip()
        if not value:
            raise HierarchyConflict(label+' is required.')
        return value

    def ensure_context(self, domain_id, module_id, knowledge_name, document_type, version):
        knowledge_name = self._required(knowledge_name,'Knowledge')
        document_type = self._required(document_type,'Document Type')
        version = self._required(version,'Version')
        module = self.connection.execute('SELECT domain_id FROM modules WHERE id=?',(int(module_id),)).fetchone()
        if not module or int(module[0]) != int(domain_id):
            raise HierarchyConflict('Module does not belong to the selected Domain.')
        now = datetime.now().isoformat()
        self.connection.execute('INSERT INTO knowledge_entities(module_id,name,created_date) VALUES(?,?,?) ON CONFLICT(module_id,name) DO NOTHING',(module_id,knowledge_name,now))
        knowledge_id = self.connection.execute('SELECT id FROM knowledge_entities WHERE module_id=? AND name=?',(module_id,knowledge_name)).fetchone()[0]
        self.connection.execute('INSERT INTO knowledge_document_types(knowledge_id,name,created_date) VALUES(?,?,?) ON CONFLICT(knowledge_id,name) DO NOTHING',(knowledge_id,document_type,now))
        document_type_id = self.connection.execute('SELECT id FROM knowledge_document_types WHERE knowledge_id=? AND name=?',(knowledge_id,document_type)).fetchone()[0]
        self.connection.execute('INSERT INTO document_versions(document_type_id,version,created_date) VALUES(?,?,?) ON CONFLICT(document_type_id,version) DO NOTHING',(document_type_id,version,now))
        version_id = self.connection.execute('SELECT id FROM document_versions WHERE document_type_id=? AND version=?',(document_type_id,version)).fetchone()[0]
        return {'knowledge_entity_id':knowledge_id,'document_type_id':document_type_id,'document_version_id':version_id}

    def context(self, version_id):
        cursor=self.connection.execute('''SELECT d.id AS domain_id,d.name AS domain,m.id AS module_id,m.name AS module,
            k.id AS knowledge_entity_id,k.name AS knowledge_name,t.id AS document_type_id,t.name AS document_type,
            v.id AS document_version_id,v.version
            FROM document_versions v JOIN knowledge_document_types t ON t.id=v.document_type_id
            JOIN knowledge_entities k ON k.id=t.knowledge_id JOIN modules m ON m.id=k.module_id
            JOIN domains d ON d.id=m.domain_id WHERE v.id=?''',(int(version_id),))
        row=cursor.fetchone()
        if not row:
            raise HierarchyConflict('Selected document version is absent.')
        return dict(zip((c[0] for c in cursor.description),row))

    def versions(self, document_type_id):
        cursor=self.connection.execute('SELECT id AS document_version_id,version,created_date FROM document_versions WHERE document_type_id=? ORDER BY id',(int(document_type_id),))
        return [dict(zip((c[0] for c in cursor.description),row)) for row in cursor.fetchall()]

    def resolve_source(self, source_id):
        """Resolve legacy aliases safely, or validate an existing stable owner.

        Runs in the caller's transaction. Never infer a Document Type from a
        version label, and support repositories using dictionary row factories.
        """
        factory = self.connection.row_factory
        try:
            self.connection.row_factory = None
            self.ensure_schema()
            row = self.connection.execute('''SELECT domain_id,module_id,domain,module,
                knowledge_name,document_type,version,document_version_id
                FROM knowledge_items WHERE id=?''', (int(source_id),)).fetchone()
            if not row:
                raise HierarchyConflict('Selected source is absent.')
            domain_id,module_id,domain,module,knowledge,dtype,version,owner_id = row
            parent = self.connection.execute('''SELECT d.id,m.id FROM domains d
                JOIN modules m ON m.domain_id=d.id WHERE d.name=? AND m.name=?''',
                (domain,module)).fetchall()
            if len(parent)!=1 or (domain_id and domain_id!=parent[0][0]) or (module_id and module_id!=parent[0][1]):
                raise HierarchyConflict('Selected source has conflicting hierarchy parents.')
            if owner_id:
                owner=self.context(owner_id)
                actual=(owner['domain'],owner['module'],owner['knowledge_name'],owner['document_type'],owner['version'])
                if actual!=(domain,module,knowledge,dtype,version):
                    raise HierarchyConflict('Selected source aliases conflict with its document version owner.')
            else:
                identity=self.ensure_context(parent[0][0],parent[0][1],knowledge,dtype,version)
                owner=self.context(identity['document_version_id'])
                self.connection.execute('UPDATE knowledge_items SET document_version_id=?,domain_id=?,module_id=? WHERE id=?',
                    (owner['document_version_id'],parent[0][0],parent[0][1],int(source_id)))
            return owner
        finally:
            self.connection.row_factory=factory

    def catalog(self, **filters):
        """Include empty managed nodes; existing source-item APIs cannot."""
        columns={'domain':'d.name','module':'m.name','knowledge_name':'k.name','document_type':'t.name',
                 'document_type_id':'t.id','document_version_id':'v.id'}
        conditions=[];values=[]
        for key,column in columns.items():
            if filters.get(key) not in (None,''):
                conditions.append(column+'=?');values.append(filters[key])
        sql='''SELECT d.id AS domain_id,d.name AS domain,m.id AS module_id,m.name AS module,
             k.id AS knowledge_entity_id,k.name AS knowledge_name,t.id AS document_type_id,t.name AS document_type,
             v.id AS document_version_id,v.version,v.created_date
             FROM domains d LEFT JOIN modules m ON m.domain_id=d.id
             LEFT JOIN knowledge_entities k ON k.module_id=m.id
             LEFT JOIN knowledge_document_types t ON t.knowledge_id=k.id
             LEFT JOIN document_versions v ON v.document_type_id=t.id'''
        if conditions: sql+=' WHERE '+' AND '.join(conditions)
        sql+=' ORDER BY d.name,m.name,k.name,t.name,v.id'
        cursor=self.connection.execute(sql,values)
        return [dict(zip((c[0] for c in cursor.description),row)) for row in cursor.fetchall()]

    def create_node(self, kind, context, name):
        name=self._required(name,kind)
        module=self.connection.execute('''SELECT m.id FROM modules m JOIN domains d ON d.id=m.domain_id
            WHERE d.name=? AND m.name=?''',(context.get('domain'),context.get('module'))).fetchone()
        if not module: raise HierarchyConflict('Choose an existing Domain and Module.')
        now=datetime.now().isoformat()
        if kind=='knowledge':
            table,parent,parent_id,column='knowledge_entities','module_id',module[0],'name'
        else:
            knowledge=self.connection.execute('SELECT id FROM knowledge_entities WHERE module_id=? AND name=?',(module[0],context.get('knowledge_name'))).fetchone()
            if not knowledge: raise HierarchyConflict('Choose an existing Knowledge entity.')
            if kind=='document_type':
                table,parent,parent_id,column='knowledge_document_types','knowledge_id',knowledge[0],'name'
            elif kind=='version':
                dtype=self.connection.execute('SELECT id FROM knowledge_document_types WHERE knowledge_id=? AND name=?',(knowledge[0],context.get('document_type'))).fetchone()
                if not dtype: raise HierarchyConflict('Choose an existing Document Type before Version.')
                table,parent,parent_id,column='document_versions','document_type_id',dtype[0],'version'
            else: raise HierarchyConflict('Unsupported hierarchy creation level.')
        if self.connection.execute('SELECT id FROM '+table+' WHERE '+parent+'=? AND '+column+'=?',(parent_id,name)).fetchone():
            raise HierarchyConflict('This hierarchy value already exists under the selected parent.')
        cursor=self.connection.execute('INSERT INTO '+table+'('+parent+','+column+',created_date) VALUES(?,?,?)',(parent_id,name,now))
        return {'id':cursor.lastrowid,'type':kind,'name':name}

    def migrate_sources(self):
        """Preserve every ID; missing/mismatched parents abort the transaction."""
        self.ensure_schema()
        cursor=self.connection.execute('SELECT id,domain,module,domain_id,module_id,knowledge_name,document_type,version FROM knowledge_items ORDER BY id')
        rows=[dict(zip((c[0] for c in cursor.description),row)) for row in cursor.fetchall()]
        mapping={}
        for row in rows:
            domain_id=row.get('domain_id')
            module_id=row.get('module_id')
            domain=self.connection.execute('SELECT id,name FROM domains WHERE id=?',(domain_id,)).fetchone() if domain_id else None
            if not domain:
                matches=self.connection.execute('SELECT id,name FROM domains WHERE name=?',(row['domain'],)).fetchall()
                if len(matches)!=1:
                    raise HierarchyConflict('Source '+str(row['id'])+' has an unresolved Domain.')
                domain=matches[0];domain_id=domain[0]
            if domain[1]!=row['domain']:
                raise HierarchyConflict('Source '+str(row['id'])+' has conflicting Domain metadata.')
            module=self.connection.execute('SELECT id,domain_id,name FROM modules WHERE id=?',(module_id,)).fetchone() if module_id else None
            if not module:
                matches=self.connection.execute('SELECT id,domain_id,name FROM modules WHERE domain_id=? AND name=?',(domain_id,row['module'])).fetchall()
                if len(matches)!=1:
                    raise HierarchyConflict('Source '+str(row['id'])+' has an unresolved Module.')
                module=matches[0];module_id=module[0]
            if module[1]!=domain_id or module[2]!=row['module']:
                raise HierarchyConflict('Source '+str(row['id'])+' has conflicting Module metadata.')
            identity=self.ensure_context(domain_id,module_id,row['knowledge_name'],row['document_type'],row['version'])
            version_id=identity['document_version_id'];mapping[row['id']]=version_id
            self.connection.execute('UPDATE knowledge_items SET document_version_id=?,domain_id=?,module_id=? WHERE id=?',(version_id,domain_id,module_id,row['id']))
        # Historical version rows remain tied to their source's Document Type,
        # but keep their own version label and row ID.
        cursor=self.connection.execute('SELECT id,knowledge_item_id,version FROM knowledge_versions')
        for record_id,source_id,label in cursor.fetchall():
            if source_id not in mapping:
                raise HierarchyConflict('Historical version has an absent source owner.')
            owner=self.context(mapping[source_id])
            identity=self.ensure_context(owner['domain_id'],owner['module_id'],owner['knowledge_name'],owner['document_type'],label)
            self.connection.execute('UPDATE knowledge_versions SET document_version_id=? WHERE id=?',(identity['document_version_id'],record_id))
        return mapping

    def move(self, kind, node_id, parent_id):
        """A stable-ID move never upserts/merges the selected entity."""
        definitions={'knowledge':('knowledge_entities','module_id','modules'),
                     'document_type':('knowledge_document_types','knowledge_id','knowledge_entities'),
                     'version':('document_versions','document_type_id','knowledge_document_types')}
        if kind not in definitions:
            raise HierarchyConflict('Unsupported canonical hierarchy move.')
        table,parent_column,parent_table=definitions[kind]
        if not self.connection.execute('SELECT id FROM '+parent_table+' WHERE id=?',(parent_id,)).fetchone():
            raise HierarchyConflict('Move destination is absent.')
        if not self.connection.execute('SELECT id FROM '+table+' WHERE id=?',(node_id,)).fetchone():
            raise HierarchyConflict('Move source is absent.')
        try:
            self.connection.execute('UPDATE '+table+' SET '+parent_column+'=? WHERE id=?',(parent_id,node_id))
        except sqlite3.IntegrityError as ex:
            raise HierarchyConflict('Destination already contains this hierarchy value; move cannot merge identities.') from ex
