"""Persistence for Task 6. Accepts an injectable ``db`` (anything exposing
``get_connection()``) so tests run on disposable databases and the web layer
uses the normal DatabaseManager."""
import logging
import sqlite3
from contextlib import contextmanager

from Core.change_impact import schema
from Core.change_impact.util import ImpactError, dumps, loads, now, digest


def integrity_failure(error):
    """Translate a SQLite constraint failure into a precise, safe message.

    SQLite reports every constraint failure (UNIQUE/PRIMARY KEY, NOT NULL, CHECK, FOREIGN KEY and the RAISE(ABORT)
    of the history-protection triggers) as ``IntegrityError``. The message names only table and column, never data.
    """
    detail = str(error)
    low = detail.lower()
    where = detail.split(':', 1)[1].strip() if ':' in detail else ''
    logging.getLogger('QA_AI_STUDIO').warning('Change Impact persistence conflict: %s', detail)
    if 'immutable' in low:
        return ImpactError('Analysis history is immutable and cannot be modified.', 409, 'IMMUTABLE_HISTORY')
    if 'unique constraint' in low or 'primary key' in low:
        return ImpactError('The analysis result could not be saved because two records share the same identifier%s. Nothing was changed.'
                           % (' (%s)' % where if where else ''), 409, 'DUPLICATE_RECORD')
    if 'not null constraint' in low:
        return ImpactError('The analysis result could not be saved because a required value was missing%s. Nothing was changed.'
                           % (' (%s)' % where if where else ''), 409, 'MISSING_VALUE')
    if 'foreign key' in low:
        return ImpactError('The analysis result could not be saved because it refers to a record that does not exist. Nothing was changed.',
                           409, 'RELATED_RECORD_MISSING')
    if 'check constraint' in low:
        return ImpactError('The analysis result could not be saved because a value is not allowed%s. Nothing was changed.'
                           % (' (%s)' % where if where else ''), 409, 'INVALID_VALUE')
    return ImpactError('Analysis record conflicts with an immutable or related record.', 409, 'CONFLICT')


class ChangeImpactRepository:

    def __init__(self, db=None):
        if db is None:
            from Database.db_manager import DatabaseManager
            db = DatabaseManager()
        self.db = db
        self.ensure_schema()

    # ------------------------------------------------------------------ plumbing
    @contextmanager
    def connection(self, write=False):
        conn = self.db.get_connection()
        conn.row_factory = sqlite3.Row
        try:
            conn.execute('PRAGMA foreign_keys=ON')
            if write:
                conn.execute('BEGIN IMMEDIATE')
            yield conn
            if write:
                conn.commit()
        except sqlite3.IntegrityError as error:
            conn.rollback()
            raise integrity_failure(error) from error
        except sqlite3.DatabaseError as error:
            conn.rollback()
            if 'immutable' in str(error).lower():
                raise ImpactError('Analysis history is immutable and cannot be modified.', 409, 'IMMUTABLE_HISTORY') from error
            raise
        except Exception:
            if write:
                conn.rollback()
            raise
        finally:
            conn.close()

    def ensure_schema(self):
        """Additive and idempotent. Re-running is a no-op; no table is altered or dropped."""
        with self.connection() as conn:
            exists = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='ci_migrations'").fetchone()
            applied = bool(exists and conn.execute('SELECT 1 FROM ci_migrations WHERE version=1').fetchone())
            tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if applied and schema.EXPECTED_TABLES <= tables:
                return {'applied': False, 'version': 1}
            for statement in schema.MIGRATION_1:
                conn.execute(statement)
            conn.execute('INSERT OR IGNORE INTO ci_migrations(version,applied_at) VALUES(1,?)', (now(),))
            conn.commit()
        return {'applied': True, 'version': 1}

    # ------------------------------------------------------------------ analyses
    def create_analysis(self, record):
        """Insert a Queued analysis. Refuses a duplicate submission that is still active."""
        with self.connection(True) as conn:
            duplicate = conn.execute(
                "SELECT id FROM ci_analyses WHERE request_key=? AND status IN ('Queued','Running')",
                (record['request_key'],)).fetchone()
            if duplicate:
                raise ImpactError('An identical analysis is already running (%s).' % duplicate['id'], 409, 'DUPLICATE_SUBMISSION')
            conn.execute(
                '''INSERT INTO ci_analyses(id,title,mode,status,changed_source_id,baseline_source_id,
                   changed_document_version_id,baseline_document_version_id,request_key,config_json,config_hash,
                   ai_requested,ai_status,progress_json,created_by,created_by_name,created_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                (record['id'], record['title'], record['mode'], 'Queued', record['changed_source_id'],
                 record.get('baseline_source_id'), record.get('changed_document_version_id'),
                 record.get('baseline_document_version_id'), record['request_key'], dumps(record['config']),
                 digest(record['config']), 1 if record.get('ai_requested') else 0,
                 'Requested' if record.get('ai_requested') else 'Not requested',
                 dumps({'steps': [], 'current': 'Queued'}), record.get('created_by'),
                 record.get('created_by_name', ''), now()))
        return record['id']

    def set_progress(self, analysis_id, progress, status=None):
        with self.connection(True) as conn:
            if status:
                conn.execute("UPDATE ci_analyses SET progress_json=?,status=? WHERE id=? AND status IN ('Queued','Running')",
                             (dumps(progress), status, analysis_id))
            else:
                conn.execute("UPDATE ci_analyses SET progress_json=? WHERE id=? AND status IN ('Queued','Running')",
                             (dumps(progress), analysis_id))

    def set_ai_info(self, analysis_id, provider, model, status):
        with self.connection(True) as conn:
            conn.execute("UPDATE ci_analyses SET ai_provider=?,ai_model=?,ai_status=? WHERE id=? AND status IN ('Queued','Running')",
                         (provider or '', model or '', status, analysis_id))

    def finalize(self, analysis_id, snapshot, changes, findings, progress):
        """Freeze the snapshot and normalised rows in one transaction."""
        snapshot_text = dumps(snapshot)
        with self.connection(True) as conn:
            row = conn.execute('SELECT status FROM ci_analyses WHERE id=?', (analysis_id,)).fetchone()
            if not row:
                raise ImpactError('Analysis not found.', 404, 'NOT_FOUND')
            if row['status'] not in ('Queued', 'Running'):
                raise ImpactError('Analysis is already finished.', 409, 'IMMUTABLE_HISTORY')
            for change in changes:
                conn.execute(
                    '''INSERT INTO ci_changes(analysis_id,change_id,category,change_kind,change_type,description,original_text,
                       modified_text,source_document,source_version,evidence_json,status) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)''',
                    (analysis_id, change['change_id'], change['category'], change['change_kind'], change['change_type'],
                     change['description'], change.get('original_text'), change.get('modified_text'),
                     change['source_document'], change['source_version'], dumps(change['evidence']), change['status']))
            for finding in findings:
                conn.execute(
                    '''INSERT INTO ci_findings(analysis_id,finding_id,kind,change_id,title,impact_class,severity,assurance,
                       verification_status,payload_json,evidence_json) VALUES(?,?,?,?,?,?,?,?,?,?,?)''',
                    (analysis_id, finding['finding_id'], finding['kind'], finding.get('change_id', ''), finding['title'],
                     finding.get('impact_class', ''), finding.get('severity', ''), finding['assurance'],
                     finding.get('verification_status', 'Unverified'), dumps(finding.get('payload', {})),
                     dumps(finding.get('evidence', []))))
            conn.execute(
                "UPDATE ci_analyses SET status='Completed',snapshot_json=?,snapshot_hash=?,progress_json=?,completed_at=? WHERE id=?",
                (snapshot_text, digest(snapshot_text), dumps(progress), now(), analysis_id))
        return digest(snapshot_text)

    def terminate(self, analysis_id, status, code, message, progress=None):
        if status not in ('Failed', 'Cancelled'):
            raise ValueError('terminate() accepts Failed or Cancelled only')
        with self.connection(True) as conn:
            conn.execute(
                "UPDATE ci_analyses SET status=?,error_code=?,error_message=?,progress_json=COALESCE(?,progress_json),completed_at=? "
                "WHERE id=? AND status IN ('Queued','Running')",
                (status, code, str(message)[:1000], dumps(progress) if progress is not None else None, now(), analysis_id))

    def reconcile_stale(self):
        """A process restart provably orphaned any Queued/Running analysis."""
        with self.connection(True) as conn:
            cursor = conn.execute(
                "UPDATE ci_analyses SET status='Failed',error_code='INTERRUPTED',"
                "error_message='The server restarted before this analysis finished. Start it again.',completed_at=? "
                "WHERE status IN ('Queued','Running')", (now(),))
            return cursor.rowcount

    def get(self, analysis_id):
        with self.connection() as conn:
            row = conn.execute('SELECT * FROM ci_analyses WHERE id=?', (analysis_id,)).fetchone()
        if not row:
            raise ImpactError('Analysis not found.', 404, 'NOT_FOUND')
        return dict(row)

    def header(self, analysis_id):
        record = self.get(analysis_id)
        return self.to_header(record)

    @staticmethod
    def to_header(record):
        return {
            'id': record['id'], 'title': record['title'], 'mode': record['mode'], 'status': record['status'],
            'changed_source_id': record['changed_source_id'], 'baseline_source_id': record['baseline_source_id'],
            'changed_document_version_id': record['changed_document_version_id'],
            'baseline_document_version_id': record['baseline_document_version_id'],
            'ai_requested': bool(record['ai_requested']), 'ai_provider': record['ai_provider'],
            'ai_model': record['ai_model'], 'ai_status': record['ai_status'],
            'progress': loads(record['progress_json'], {}), 'error_code': record['error_code'],
            'error_message': record['error_message'], 'created_by_name': record['created_by_name'],
            'created_at': record['created_at'], 'completed_at': record['completed_at'],
            'snapshot_hash': record['snapshot_hash'], 'config': loads(record['config_json'], {}),
        }

    def list(self, limit=100, offset=0, status=None, search=None, created_by=None):
        limit = max(1, min(int(limit), 500))
        sql = ('SELECT id,title,mode,status,created_by_name,created_at,completed_at,ai_provider,ai_model,ai_status,'
               'changed_source_id,baseline_source_id,snapshot_json FROM ci_analyses')
        conditions, values = [], []
        if status:
            conditions.append('status=?'); values.append(status)
        if created_by:
            conditions.append('created_by=?'); values.append(int(created_by))
        if search:
            conditions.append('(title LIKE ? OR id LIKE ? OR created_by_name LIKE ?)')
            like = '%' + str(search).replace('%', '').replace('_', '') + '%'
            values += [like, like, like]
        if conditions:
            sql += ' WHERE ' + ' AND '.join(conditions)
        sql += ' ORDER BY created_at DESC, id DESC LIMIT ? OFFSET ?'
        values += [limit, max(0, int(offset))]
        with self.connection() as conn:
            rows = conn.execute(sql, values).fetchall()
        result = []
        for row in rows:
            snapshot = loads(row['snapshot_json'], {}) or {}
            totals = (snapshot.get('dashboard') or {}).get('totals', {})
            result.append({
                'id': row['id'], 'title': row['title'], 'mode': row['mode'], 'status': row['status'],
                'created_by_name': row['created_by_name'], 'created_at': row['created_at'],
                'completed_at': row['completed_at'], 'ai_provider': row['ai_provider'], 'ai_model': row['ai_model'],
                'ai_status': row['ai_status'], 'changed_source_id': row['changed_source_id'],
                'baseline_source_id': row['baseline_source_id'],
                'sources': [d.get('file_name') for d in snapshot.get('documents', [])],
                'totals': totals})
        return result

    def snapshot(self, analysis_id):
        record = self.get(analysis_id)
        snapshot = loads(record['snapshot_json'])
        if snapshot is None:
            raise ImpactError('This analysis has no completed result (status: %s).' % record['status'], 409, 'NOT_COMPLETED')
        if digest(record['snapshot_json']) != record['snapshot_hash']:
            raise ImpactError('The stored analysis snapshot failed its integrity check.', 500, 'SNAPSHOT_CORRUPT')
        return snapshot

    # ------------------------------------------------------------------ review / audit
    def add_review_event(self, analysis_id, event_type, finding_id, decision, comment, actor, detail=None):
        with self.connection(True) as conn:
            row = conn.execute('SELECT status FROM ci_analyses WHERE id=?', (analysis_id,)).fetchone()
            if not row:
                raise ImpactError('Analysis not found.', 404, 'NOT_FOUND')
            if row['status'] != 'Completed':
                raise ImpactError('Only a completed analysis can be reviewed.', 409, 'NOT_COMPLETED')
            cursor = conn.execute(
                '''INSERT INTO ci_review_events(analysis_id,finding_id,event_type,decision,comment,actor_id,actor_name,detail_json,created_at)
                   VALUES(?,?,?,?,?,?,?,?,?)''',
                (analysis_id, finding_id or '', event_type, decision or '', comment or '', actor.get('id'),
                 actor.get('username') or actor.get('full_name') or '', dumps(detail or {}), now()))
            return cursor.lastrowid

    def review_events(self, analysis_id):
        with self.connection() as conn:
            rows = conn.execute('SELECT * FROM ci_review_events WHERE analysis_id=? ORDER BY id', (analysis_id,)).fetchall()
        return [dict(r) | {'detail': loads(r['detail_json'], {})} for r in rows]

    def finding_ids(self, analysis_id):
        with self.connection() as conn:
            return {r[0]: dict(r) for r in conn.execute(
                'SELECT finding_id,kind,assurance,change_id,payload_json FROM ci_findings WHERE analysis_id=?', (analysis_id,))}

    def add_explanation(self, record):
        with self.connection(True) as conn:
            conn.execute(
                '''INSERT INTO ci_explanations(id,analysis_id,subject_type,subject_id,mode,text,source_refs_json,
                   insufficient_evidence,provider,model,status,created_by,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                (record['id'], record['analysis_id'], record['subject_type'], record['subject_id'], record['mode'],
                 record['text'], dumps(record['source_refs']), 1 if record.get('insufficient_evidence') else 0,
                 record.get('provider', ''), record.get('model', ''), record.get('status', 'Pending review'),
                 record.get('created_by'), now()))

    def explanations(self, analysis_id):
        with self.connection() as conn:
            rows = conn.execute('SELECT * FROM ci_explanations WHERE analysis_id=? ORDER BY created_at,id', (analysis_id,)).fetchall()
        return [dict(r) | {'source_refs': loads(r['source_refs_json'], []),
                           'insufficient_evidence': bool(r['insufficient_evidence'])} for r in rows]

    def log_event(self, analysis_id, action, actor, detail=None):
        with self.connection(True) as conn:
            conn.execute('INSERT INTO ci_events(analysis_id,action,actor_id,actor_name,detail_json,created_at) VALUES(?,?,?,?,?,?)',
                         (analysis_id or '', action, (actor or {}).get('id'),
                          (actor or {}).get('username') or (actor or {}).get('full_name') or '', dumps(detail or {}), now()))

    def events(self, analysis_id):
        with self.connection() as conn:
            rows = conn.execute('SELECT * FROM ci_events WHERE analysis_id=? ORDER BY id', (analysis_id,)).fetchall()
        return [dict(r) | {'detail': loads(r['detail_json'], {})} for r in rows]
