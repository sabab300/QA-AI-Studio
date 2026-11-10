"""Additive, idempotent ``ci_*`` schema for Task 6.

Nothing here alters or drops an existing table. History is protected by
triggers: a finished analysis (and every child row, review event, explanation
and audit event) cannot be updated or deleted through SQL.
"""

MIGRATION_1 = (
    'CREATE TABLE IF NOT EXISTS ci_migrations(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)',
    '''CREATE TABLE IF NOT EXISTS ci_analyses(
        id TEXT PRIMARY KEY,
        title TEXT NOT NULL,
        mode TEXT NOT NULL CHECK(mode IN ('VERSION_COMPARE','SINGLE_DOCUMENT')),
        status TEXT NOT NULL CHECK(status IN ('Queued','Running','Completed','Failed','Cancelled')),
        changed_source_id INTEGER NOT NULL,
        baseline_source_id INTEGER,
        changed_document_version_id INTEGER,
        baseline_document_version_id INTEGER,
        request_key TEXT NOT NULL,
        config_json TEXT NOT NULL,
        config_hash TEXT NOT NULL,
        ai_requested INTEGER NOT NULL DEFAULT 0,
        ai_provider TEXT NOT NULL DEFAULT '',
        ai_model TEXT NOT NULL DEFAULT '',
        ai_status TEXT NOT NULL DEFAULT 'Not requested',
        progress_json TEXT NOT NULL DEFAULT '{}',
        error_code TEXT NOT NULL DEFAULT '',
        error_message TEXT NOT NULL DEFAULT '',
        snapshot_json TEXT,
        snapshot_hash TEXT,
        created_by INTEGER,
        created_by_name TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL,
        completed_at TEXT)''',
    '''CREATE TABLE IF NOT EXISTS ci_changes(
        analysis_id TEXT NOT NULL REFERENCES ci_analyses(id),
        change_id TEXT NOT NULL,
        category TEXT NOT NULL,
        change_kind TEXT NOT NULL CHECK(change_kind IN ('ACTUAL_DIFF','PROPOSED_CRF')),
        change_type TEXT NOT NULL,
        description TEXT NOT NULL,
        original_text TEXT,
        modified_text TEXT,
        source_document TEXT NOT NULL,
        source_version TEXT NOT NULL,
        evidence_json TEXT NOT NULL,
        status TEXT NOT NULL,
        PRIMARY KEY(analysis_id, change_id))''',
    '''CREATE TABLE IF NOT EXISTS ci_findings(
        analysis_id TEXT NOT NULL REFERENCES ci_analyses(id),
        finding_id TEXT NOT NULL,
        kind TEXT NOT NULL,
        change_id TEXT NOT NULL DEFAULT '',
        title TEXT NOT NULL,
        impact_class TEXT NOT NULL DEFAULT '',
        severity TEXT NOT NULL DEFAULT '',
        assurance TEXT NOT NULL,
        verification_status TEXT NOT NULL DEFAULT 'Unverified',
        payload_json TEXT NOT NULL,
        evidence_json TEXT NOT NULL,
        PRIMARY KEY(analysis_id, finding_id))''',
    '''CREATE TABLE IF NOT EXISTS ci_review_events(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        analysis_id TEXT NOT NULL REFERENCES ci_analyses(id),
        finding_id TEXT NOT NULL DEFAULT '',
        event_type TEXT NOT NULL CHECK(event_type IN ('FINDING_REVIEW','DEPENDENCY_CONFIRM','REGRESSION_APPROVAL','COMMENT')),
        decision TEXT NOT NULL DEFAULT '',
        comment TEXT NOT NULL DEFAULT '',
        actor_id INTEGER,
        actor_name TEXT NOT NULL DEFAULT '',
        detail_json TEXT NOT NULL DEFAULT '{}',
        created_at TEXT NOT NULL)''',
    '''CREATE TABLE IF NOT EXISTS ci_explanations(
        id TEXT PRIMARY KEY,
        analysis_id TEXT NOT NULL REFERENCES ci_analyses(id),
        subject_type TEXT NOT NULL,
        subject_id TEXT NOT NULL,
        mode TEXT NOT NULL CHECK(mode IN ('AI','RULE_BASED')),
        text TEXT NOT NULL,
        source_refs_json TEXT NOT NULL,
        insufficient_evidence INTEGER NOT NULL DEFAULT 0,
        provider TEXT NOT NULL DEFAULT '',
        model TEXT NOT NULL DEFAULT '',
        status TEXT NOT NULL DEFAULT 'Pending review',
        created_by INTEGER,
        created_at TEXT NOT NULL)''',
    '''CREATE TABLE IF NOT EXISTS ci_events(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        analysis_id TEXT NOT NULL DEFAULT '',
        action TEXT NOT NULL,
        actor_id INTEGER,
        actor_name TEXT NOT NULL DEFAULT '',
        detail_json TEXT NOT NULL DEFAULT '{}',
        created_at TEXT NOT NULL)''',
    'CREATE INDEX IF NOT EXISTS ix_ci_analyses_created ON ci_analyses(created_at)',
    'CREATE INDEX IF NOT EXISTS ix_ci_analyses_request ON ci_analyses(request_key, status)',
    'CREATE INDEX IF NOT EXISTS ix_ci_findings_kind ON ci_findings(analysis_id, kind)',
    'CREATE INDEX IF NOT EXISTS ix_ci_review_analysis ON ci_review_events(analysis_id, id)',
    # A queued or running analysis may progress; once terminal, nothing changes.
    '''CREATE TRIGGER IF NOT EXISTS ci_analysis_terminal_immutable BEFORE UPDATE ON ci_analyses
       WHEN OLD.status IN ('Completed','Failed','Cancelled')
       BEGIN SELECT RAISE(ABORT,'Analysis history is immutable'); END''',
    '''CREATE TRIGGER IF NOT EXISTS ci_analysis_no_delete BEFORE DELETE ON ci_analyses
       BEGIN SELECT RAISE(ABORT,'Analysis history is immutable'); END''',
    '''CREATE TRIGGER IF NOT EXISTS ci_analysis_identity_immutable BEFORE UPDATE ON ci_analyses
       WHEN NEW.id IS NOT OLD.id OR NEW.config_json IS NOT OLD.config_json OR NEW.config_hash IS NOT OLD.config_hash
         OR NEW.changed_source_id IS NOT OLD.changed_source_id OR NEW.baseline_source_id IS NOT OLD.baseline_source_id
         OR NEW.created_by IS NOT OLD.created_by OR NEW.created_at IS NOT OLD.created_at OR NEW.mode IS NOT OLD.mode
       BEGIN SELECT RAISE(ABORT,'Analysis identity and configuration are immutable'); END''',
)

_APPEND_ONLY = ('ci_changes', 'ci_findings', 'ci_review_events', 'ci_explanations', 'ci_events')
MIGRATION_1 = MIGRATION_1 + tuple(
    'CREATE TRIGGER IF NOT EXISTS ci_%s_no_%s BEFORE %s ON %s BEGIN SELECT RAISE(ABORT,\'Analysis history is immutable\'); END'
    % (table, action.lower(), action, table)
    for table in _APPEND_ONLY for action in ('UPDATE', 'DELETE'))

EXPECTED_TABLES = frozenset(('ci_migrations', 'ci_analyses', 'ci_changes', 'ci_findings',
                             'ci_review_events', 'ci_explanations', 'ci_events'))
