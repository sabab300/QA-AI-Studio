"""Read-only access to the existing Knowledge Hub, QA Engineering test case and QA Automation tables.

Every query is parameterised and bounded. Nothing here writes. Missing optional
tables yield empty results instead of an error.
"""
import sqlite3


def _chunks(values, size=400):
    values = list(values)
    for i in range(0, len(values), size):
        yield values[i:i + size]


class ScopeReader:

    def __init__(self, db, limits=None):
        self.db = db
        self.limits = limits or {}
        self.notes = []

    # ------------------------------------------------------------------ helpers
    def _conn(self):
        conn = self.db.get_connection()
        conn.row_factory = sqlite3.Row
        return conn

    def _tables(self, conn):
        return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}

    def _columns(self, conn, table):
        return {r[1] for r in conn.execute('PRAGMA table_info(%s)' % table)}

    def _note(self, text):
        if text not in self.notes:
            self.notes.append(text)

    def _in(self, conn, sql, values, extra=()):
        rows = []
        for part in _chunks(values):
            marks = ','.join('?' * len(part))
            rows += [dict(r) for r in conn.execute(sql.replace('(?)', '(%s)' % marks), tuple(part) + tuple(extra))]
        return rows

    # ------------------------------------------------------------------ knowledge scope
    def knowledge_versions(self, knowledge_entity_id):
        conn = self._conn()
        try:
            if not knowledge_entity_id or 'document_versions' not in self._tables(conn):
                return []
            return [dict(r) for r in conn.execute(
                'SELECT v.id AS document_version_id,v.version,t.id AS document_type_id,t.name AS document_type '
                'FROM document_versions v JOIN knowledge_document_types t ON t.id=v.document_type_id '
                'WHERE t.knowledge_id=? ORDER BY t.name,v.id', (int(knowledge_entity_id),))]
        finally:
            conn.close()

    # ------------------------------------------------------------------ recorded requirement records and links (read-only, optional)
    def requirements(self, version_ids):
        conn = self._conn()
        try:
            if 'qa_requirements' not in self._tables(conn):
                return []
            if not version_ids:
                return []
            return self._in(conn, 'SELECT id,logical_key,source_id,document_version_id,source_reference,description,anchor,status '
                                  'FROM qa_requirements WHERE document_version_id IN (?)', version_ids)
        finally:
            conn.close()

    def requirement_links(self, requirement_ids):
        conn = self._conn()
        try:
            if 'qa_requirement_links' not in self._tables(conn) or not requirement_ids:
                return []
            return self._in(conn, 'SELECT requirement_id,test_case_id,status,rationale,updated_at FROM qa_requirement_links '
                                  "WHERE status!='REJECTED' AND requirement_id IN (?)", requirement_ids)
        finally:
            conn.close()

    def latest_review_state(self, test_ids):
        conn = self._conn()
        try:
            if 'qa_review_decisions' not in self._tables(conn) or not test_ids:
                return {}
            rows = self._in(conn, 'SELECT test_case_id,state,id FROM qa_review_decisions WHERE test_case_id IN (?) ORDER BY id', test_ids)
            return {r['test_case_id']: r['state'] for r in rows}
        finally:
            conn.close()

    # ------------------------------------------------------------------ test cases
    def test_cases(self, version_ids=None, test_ids=None, module=None, domain=None, limit=None):
        limit = int(limit or self.limits.get('max_tests_considered', 5000))
        conn = self._conn()
        try:
            if 'test_cases' not in self._tables(conn):
                return []
            columns = self._columns(conn, 'test_cases')
            wanted = ['id', 'tc_number', 'tc_id', 'domain', 'module', 'knowledge_name', 'version', 'scenario', 'importance',
                      'test_type', 'test_case', 'pre_conditions', 'steps', 'expected_result', 'status', 'automation_type',
                      'last_result', 'last_run_date', 'document_version_id', 'test_case_version_id',
                      'active_script_source', 'bound_api_endpoint_id', 'sql_script_path', 'api_request_config_json']
            select = ','.join('t.' + c if c in columns else 'NULL AS ' + c for c in wanted)
            version_join = ''
            version_col = 'NULL AS version_number'
            if 'test_case_versions' in self._tables(conn) and 'test_case_version_id' in columns:
                version_join = ' LEFT JOIN test_case_versions v ON v.id=t.test_case_version_id'
                version_col = 'v.version_number AS version_number'
            base = 'SELECT %s,%s FROM test_cases t%s' % (select, version_col, version_join)
            if test_ids is not None:
                return [dict(r) for r in self._in(conn, base + ' WHERE t.id IN (?)', test_ids)]
            if version_ids is not None and 'document_version_id' in columns:
                if not version_ids:
                    return []
                rows = self._in(conn, base + ' WHERE t.document_version_id IN (?)', version_ids)
                return rows[:limit]
            if module is not None:
                sql = base + ' WHERE t.module=?' + (' AND t.domain=?' if domain else '') + ' ORDER BY t.id LIMIT ?'
                values = (module,) + ((domain,) if domain else ()) + (limit,)
                return [dict(r) for r in conn.execute(sql, values)]
            return []
        finally:
            conn.close()

    def tests_for_sources(self, source_ids, limit=None):
        """Test cases generated from, or linked to, the given Knowledge Hub source documents (the source relationship)."""
        limit = int(limit or self.limits.get('max_tests_considered', 5000))
        source_ids = sorted({int(s) for s in source_ids if s})
        conn = self._conn()
        try:
            if 'test_case_sources' not in self._tables(conn) or not source_ids:
                return {}
            out = {}
            for r in self._in(conn, 'SELECT test_case_id,knowledge_source_id FROM test_case_sources WHERE knowledge_source_id IN (?)', source_ids)[:limit]:
                out.setdefault(r['test_case_id'], set()).add(r['knowledge_source_id'])
            return out
        finally:
            conn.close()

    def test_sources(self, test_ids):
        conn = self._conn()
        try:
            if 'test_case_sources' not in self._tables(conn) or not test_ids:
                return {}
            out = {}
            for r in self._in(conn, 'SELECT test_case_id,knowledge_source_id FROM test_case_sources WHERE test_case_id IN (?)', test_ids):
                out.setdefault(r['test_case_id'], []).append(r['knowledge_source_id'])
            return out
        finally:
            conn.close()

    # ------------------------------------------------------------------ automation
    def asset_links(self, test_ids):
        conn = self._conn()
        try:
            if 'qa_asset_links' not in self._tables(conn) or not test_ids:
                return {}
            out = {}
            for r in self._in(conn, 'SELECT test_case_id,kind,asset_reference,asset_version,validation_state,sql_script_id,playwright_revision_id '
                                    'FROM qa_asset_links WHERE test_case_id IN (?) ORDER BY created_at', test_ids):
                out.setdefault(r['test_case_id'], []).append(r)
            return out
        finally:
            conn.close()

    def playwright_assets(self, tc_ids, source_ids):
        conn = self._conn()
        try:
            if 'playwright_automation_assets' not in self._tables(conn) or not tc_ids:
                return []
            cap = int(self.limits.get('max_script_characters', 200000))
            modified = 'modified_date,' if 'modified_date' in self._columns(conn, 'playwright_automation_assets') else 'NULL AS modified_date,'
            rows = self._in(conn, 'SELECT id,stable_scope_key,source_knowledge_id,tc_id,active_script_source,lifecycle_status,' + modified +
                                  'substr(COALESCE(automation_script,\'\'),1,%d) AS automation_script,'
                                  'substr(COALESCE(uploaded_script,\'\'),1,%d) AS uploaded_script,'
                                  'substr(COALESCE(recorded_script,\'\'),1,%d) AS recorded_script '
                                  'FROM playwright_automation_assets WHERE tc_id IN (?)' % (cap, cap, cap), tc_ids)
            if source_ids:
                allowed = set(source_ids)
                rows = [r for r in rows if r['source_knowledge_id'] in allowed or r['source_knowledge_id'] is None]
            return rows
        finally:
            conn.close()

    def test_case_versions(self, source_ids=None, version_ids=None):
        """Workbook version records (id, source, version number, document version, created date) for the given Knowledge sources and/or version ids."""
        conn = self._conn()
        try:
            if 'test_case_versions' not in self._tables(conn):
                return []
            cols = self._columns(conn, 'test_case_versions')
            select = ','.join(c if c in cols else 'NULL AS ' + c for c in ('id', 'source_knowledge_id', 'version_number', 'document_version_id', 'created_date'))
            found = {}
            if source_ids:
                for r in self._in(conn, 'SELECT %s FROM test_case_versions WHERE source_knowledge_id IN (?)' % select, sorted(source_ids)):
                    found[r['id']] = r
            if version_ids:
                for r in self._in(conn, 'SELECT %s FROM test_case_versions WHERE id IN (?)' % select, sorted(version_ids)):
                    found[r['id']] = r
            return list(found.values())
        finally:
            conn.close()

    def api_endpoints(self, test_case_version_ids, limit=2000):
        conn = self._conn()
        try:
            tables = self._tables(conn)
            if not {'api_collections', 'api_endpoints'} <= tables or not test_case_version_ids:
                return []
            if 'test_case_version_id' not in self._columns(conn, 'api_collections'):
                return []
            rows = self._in(conn, 'SELECT c.id AS collection_id,c.name AS collection,c.test_case_version_id,e.id AS endpoint_id,e.name,e.method,'
                                  'e.url_raw,e.headers_json,e.query_params_json,substr(COALESCE(e.body_raw,\'\'),1,20000) AS body_raw '
                                  "FROM api_collections c JOIN api_endpoints e ON e.collection_id=c.id WHERE COALESCE(c.status,'Active')='Active' "
                                  "AND c.test_case_version_id IN (?)", test_case_version_ids)
            return rows[:limit]
        finally:
            conn.close()

    def sql_scripts(self, test_case_version_ids):
        conn = self._conn()
        try:
            if 'sql_scripts' not in self._tables(conn) or not test_case_version_ids:
                return []
            if 'test_case_version_id' not in self._columns(conn, 'sql_scripts'):
                return []
            return self._in(conn, 'SELECT id,test_case_version_id,display_name,original_filename,repository_path,status FROM sql_scripts '
                                  "WHERE COALESCE(status,'Active')='Active' AND test_case_version_id IN (?)", test_case_version_ids)
        finally:
            conn.close()

    def run_history(self, test_ids, per_test=20):
        """Final outcomes of earlier runs. Only terminal results are counted."""
        conn = self._conn()
        try:
            if 'automation_runs' not in self._tables(conn) or not test_ids:
                return {}
            out = {}
            for r in self._in(conn, "SELECT test_case_id,status,outcome,created_date FROM automation_runs WHERE test_case_id IN (?) "
                                    "AND status IN ('Passed','Failed','Error') ORDER BY id DESC", test_ids):
                bucket = out.setdefault(r['test_case_id'], [])
                if len(bucket) < per_test:
                    bucket.append(r)
            return out
        finally:
            conn.close()
