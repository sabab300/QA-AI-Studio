"""Additive persistence for AI UI/UX Testing (tables prefixed ``uiux_``).

Nothing here alters or drops an existing table. Knowledge Hub tables (knowledge_items,
knowledge_entities, knowledge_document_types, document_versions) remain the owner of
design assets; the rows below only reference them by ``knowledge_id`` and
``document_version_id``.
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path

SCHEMA_VERSION = 3

TABLES = [
    """CREATE TABLE IF NOT EXISTS uiux_meta(key TEXT PRIMARY KEY, value TEXT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS uiux_design_assets(
        id TEXT PRIMARY KEY,
        knowledge_id INTEGER UNIQUE,
        document_version_id INTEGER,
        domain TEXT NOT NULL, module TEXT NOT NULL, knowledge_name TEXT NOT NULL,
        document_type TEXT NOT NULL, version TEXT NOT NULL, source_type TEXT NOT NULL, source_file TEXT NOT NULL DEFAULT '',
        asset_kind TEXT NOT NULL,
        file_name TEXT NOT NULL DEFAULT '', content_hash TEXT NOT NULL DEFAULT '',
        figma_file_key TEXT NOT NULL DEFAULT '', figma_url TEXT NOT NULL DEFAULT '',
        extraction_status TEXT NOT NULL DEFAULT 'Not analysed', extraction_note TEXT NOT NULL DEFAULT '',
        structural_available INTEGER NOT NULL DEFAULT 0,
        status TEXT NOT NULL DEFAULT 'Registered',
        created_by TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL,
        last_analysis_at TEXT, last_refresh_at TEXT,
        metadata_json TEXT NOT NULL DEFAULT '{}'
    )""",
    """CREATE INDEX IF NOT EXISTS idx_uiux_assets_ver ON uiux_design_assets(document_version_id)""",
    """CREATE INDEX IF NOT EXISTS idx_uiux_assets_hash ON uiux_design_assets(content_hash)""",
    """CREATE TABLE IF NOT EXISTS uiux_frames(
        id TEXT PRIMARY KEY,
        asset_id TEXT NOT NULL REFERENCES uiux_design_assets(id) ON DELETE CASCADE,
        node_id TEXT NOT NULL, page_name TEXT NOT NULL DEFAULT '', name TEXT NOT NULL,
        width REAL NOT NULL DEFAULT 0, height REAL NOT NULL DEFAULT 0,
        viewport_class TEXT NOT NULL DEFAULT 'desktop',
        image_path TEXT NOT NULL DEFAULT '', image_hash TEXT NOT NULL DEFAULT '', image_source TEXT NOT NULL DEFAULT '',
        nodes_json TEXT NOT NULL DEFAULT '[]', variables_json TEXT NOT NULL DEFAULT '{}',
        selected INTEGER NOT NULL DEFAULT 1, updated_at TEXT NOT NULL,
        UNIQUE(asset_id,node_id)
    )""",
    """CREATE TABLE IF NOT EXISTS uiux_targets(
        id TEXT PRIMARY KEY, name TEXT NOT NULL, origin TEXT NOT NULL, environment TEXT NOT NULL,
        internal_ok INTEGER NOT NULL DEFAULT 0, extra_origins_json TEXT NOT NULL DEFAULT '[]',
        authorized_by TEXT NOT NULL, authorized_at TEXT NOT NULL, note TEXT NOT NULL DEFAULT '',
        active INTEGER NOT NULL DEFAULT 1, UNIQUE(origin,environment)
    )""",
    """CREATE TABLE IF NOT EXISTS uiux_mappings(
        id TEXT PRIMARY KEY,
        document_version_id INTEGER NOT NULL, asset_id TEXT NOT NULL REFERENCES uiux_design_assets(id) ON DELETE CASCADE,
        frame_id TEXT NOT NULL REFERENCES uiux_frames(id) ON DELETE CASCADE,
        target_id TEXT NOT NULL DEFAULT '', route TEXT NOT NULL DEFAULT '/',
        viewport_w INTEGER NOT NULL, viewport_h INTEGER NOT NULL, viewport_name TEXT NOT NULL DEFAULT 'desktop',
        confidence REAL NOT NULL DEFAULT 0, status TEXT NOT NULL DEFAULT 'Suggested', method TEXT NOT NULL DEFAULT 'auto',
        evidence_json TEXT NOT NULL DEFAULT '{}', element_map_json TEXT NOT NULL DEFAULT '{}',
        masks_json TEXT NOT NULL DEFAULT '[]', states_json TEXT NOT NULL DEFAULT '[]',
        created_by TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL,
        approved_by TEXT NOT NULL DEFAULT '', approved_at TEXT, updated_at TEXT NOT NULL
    )""",
    """CREATE INDEX IF NOT EXISTS idx_uiux_map_ver ON uiux_mappings(document_version_id,status)""",
    """CREATE TABLE IF NOT EXISTS uiux_configs(
        id TEXT PRIMARY KEY, document_version_id INTEGER NOT NULL, asset_id TEXT NOT NULL DEFAULT '',
        name TEXT NOT NULL, config_json TEXT NOT NULL, created_by TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL, updated_at TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS uiux_runs(
        run_id TEXT PRIMARY KEY,
        document_version_id INTEGER NOT NULL,
        domain TEXT NOT NULL DEFAULT '', module TEXT NOT NULL DEFAULT '', knowledge_name TEXT NOT NULL DEFAULT '',
        document_type TEXT NOT NULL DEFAULT '', version TEXT NOT NULL DEFAULT '', source_type TEXT NOT NULL DEFAULT '', source_file TEXT NOT NULL DEFAULT '',
        asset_id TEXT NOT NULL DEFAULT '', baseline_id TEXT NOT NULL DEFAULT '', config_id TEXT NOT NULL DEFAULT '',
        app_url TEXT NOT NULL DEFAULT '', environment TEXT NOT NULL DEFAULT '', build_label TEXT NOT NULL DEFAULT '',
        browser TEXT NOT NULL DEFAULT 'chromium', viewports_json TEXT NOT NULL DEFAULT '[]',
        kind TEXT NOT NULL DEFAULT 'full', parent_run_id TEXT NOT NULL DEFAULT '',
        status TEXT NOT NULL, status_detail TEXT NOT NULL DEFAULT '',
        started_at TEXT, ended_at TEXT, created_at TEXT NOT NULL, created_by TEXT NOT NULL DEFAULT '',
        config_json TEXT NOT NULL DEFAULT '{}', summary_json TEXT NOT NULL DEFAULT '{}', progress_json TEXT NOT NULL DEFAULT '{}',
        ai_status TEXT NOT NULL DEFAULT 'Not requested', ai_note TEXT NOT NULL DEFAULT '',
        cancel_requested INTEGER NOT NULL DEFAULT 0, error TEXT NOT NULL DEFAULT '', engine_version TEXT NOT NULL DEFAULT ''
    )""",
    """CREATE INDEX IF NOT EXISTS idx_uiux_runs_ver ON uiux_runs(document_version_id,created_at)""",
    """CREATE TABLE IF NOT EXISTS uiux_findings(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        defect_id TEXT NOT NULL UNIQUE,
        run_id TEXT NOT NULL REFERENCES uiux_runs(run_id) ON DELETE CASCADE,
        document_version_id INTEGER NOT NULL, fingerprint TEXT NOT NULL,
        mapping_id TEXT NOT NULL DEFAULT '', frame_id TEXT NOT NULL DEFAULT '',
        screen TEXT NOT NULL DEFAULT '', component TEXT NOT NULL DEFAULT '', category TEXT NOT NULL,
        title TEXT NOT NULL, description TEXT NOT NULL DEFAULT '', expected TEXT NOT NULL DEFAULT '', actual TEXT NOT NULL DEFAULT '',
        difference TEXT NOT NULL DEFAULT '', classification TEXT NOT NULL, severity TEXT NOT NULL, confidence REAL NOT NULL DEFAULT 0,
        design_evidence_json TEXT NOT NULL DEFAULT '{}', browser_evidence_json TEXT NOT NULL DEFAULT '{}',
        screenshot_evidence_id TEXT NOT NULL DEFAULT '',
        suggestion TEXT NOT NULL DEFAULT '', suggestion_source TEXT NOT NULL DEFAULT 'rule', ai_note TEXT NOT NULL DEFAULT '',
        verifiable INTEGER NOT NULL DEFAULT 1, review_status TEXT NOT NULL DEFAULT 'Unreviewed', needs_reassessment INTEGER NOT NULL DEFAULT 0,
        regression_state TEXT NOT NULL DEFAULT '', application_build TEXT NOT NULL DEFAULT '', design_version TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS uiux_run_steps(
        id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL REFERENCES uiux_runs(run_id) ON DELETE CASCADE,
        seq INTEGER NOT NULL, at TEXT NOT NULL, unit TEXT NOT NULL DEFAULT '', category TEXT NOT NULL DEFAULT '',
        kind TEXT NOT NULL DEFAULT 'step', status TEXT NOT NULL DEFAULT 'info', message TEXT NOT NULL,
        page_url TEXT NOT NULL DEFAULT '', evidence_id TEXT NOT NULL DEFAULT '', meta_json TEXT NOT NULL DEFAULT '{}',
        UNIQUE(run_id,seq)
    )""",
    """CREATE INDEX IF NOT EXISTS idx_uiux_steps_run ON uiux_run_steps(run_id,seq)""",
    """CREATE INDEX IF NOT EXISTS idx_uiux_find_run ON uiux_findings(run_id)""",
    """CREATE INDEX IF NOT EXISTS idx_uiux_find_fp ON uiux_findings(document_version_id,fingerprint)""",
    """CREATE TABLE IF NOT EXISTS uiux_evidence(
        id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES uiux_runs(run_id) ON DELETE CASCADE,
        finding_id INTEGER, kind TEXT NOT NULL, path TEXT NOT NULL, mime TEXT NOT NULL DEFAULT 'image/png',
        sha256 TEXT NOT NULL DEFAULT '', width INTEGER NOT NULL DEFAULT 0, height INTEGER NOT NULL DEFAULT 0,
        label TEXT NOT NULL DEFAULT '', meta_json TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL
    )""",
    """CREATE INDEX IF NOT EXISTS idx_uiux_evid_run ON uiux_evidence(run_id)""",
    """CREATE TABLE IF NOT EXISTS uiux_reviews(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        fingerprint TEXT NOT NULL, document_version_id INTEGER NOT NULL, finding_id INTEGER, run_id TEXT NOT NULL DEFAULT '',
        decision TEXT NOT NULL, comment TEXT NOT NULL DEFAULT '', reviewer TEXT NOT NULL, reviewed_at TEXT NOT NULL,
        actual_snapshot TEXT NOT NULL DEFAULT ''
    )""",
    """CREATE INDEX IF NOT EXISTS idx_uiux_rev_fp ON uiux_reviews(document_version_id,fingerprint)""",
    """CREATE TABLE IF NOT EXISTS uiux_baselines(
        id TEXT PRIMARY KEY, document_version_id INTEGER NOT NULL, asset_id TEXT NOT NULL DEFAULT '',
        label TEXT NOT NULL, design_version TEXT NOT NULL DEFAULT '', build_label TEXT NOT NULL DEFAULT '',
        status TEXT NOT NULL, source_run_id TEXT NOT NULL DEFAULT '', snapshot_json TEXT NOT NULL DEFAULT '{}',
        notes TEXT NOT NULL DEFAULT '', created_by TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL,
        reviewed_by TEXT NOT NULL DEFAULT '', reviewed_at TEXT, approved_by TEXT NOT NULL DEFAULT '', approved_at TEXT,
        activated_at TEXT, superseded_at TEXT, superseded_by TEXT NOT NULL DEFAULT ''
    )""",
    """CREATE INDEX IF NOT EXISTS idx_uiux_base_ver ON uiux_baselines(document_version_id,status)""",
    """CREATE TABLE IF NOT EXISTS uiux_secrets(
        name TEXT PRIMARY KEY, blob TEXT NOT NULL, set_by TEXT NOT NULL DEFAULT '', set_at TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS uiux_checks(
        id TEXT PRIMARY KEY, document_version_id INTEGER NOT NULL, mapping_id TEXT NOT NULL DEFAULT '',
        name TEXT NOT NULL, category TEXT NOT NULL, spec_json TEXT NOT NULL, source_finding_fingerprint TEXT NOT NULL DEFAULT '',
        active INTEGER NOT NULL DEFAULT 1, qa_test_case_ref TEXT NOT NULL DEFAULT '',
        created_by TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL, last_result TEXT NOT NULL DEFAULT '', last_run_id TEXT NOT NULL DEFAULT ''
    )""",
    """CREATE TABLE IF NOT EXISTS uiux_results(
        id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES uiux_runs(run_id) ON DELETE CASCADE,
        seq INTEGER NOT NULL, test_id TEXT NOT NULL,
        user_category TEXT NOT NULL DEFAULT '', category_label TEXT NOT NULL DEFAULT '',
        name TEXT NOT NULL, expected TEXT NOT NULL DEFAULT '', actual TEXT NOT NULL DEFAULT '',
        result TEXT NOT NULL, reason TEXT NOT NULL DEFAULT '', severity TEXT NOT NULL DEFAULT '', confidence REAL NOT NULL DEFAULT 0,
        source TEXT NOT NULL DEFAULT '', screen TEXT NOT NULL DEFAULT '', viewport TEXT NOT NULL DEFAULT '',
        member_count INTEGER NOT NULL DEFAULT 1, members_json TEXT NOT NULL DEFAULT '[]',
        evidence_json TEXT NOT NULL DEFAULT '{}', finding_fingerprint TEXT NOT NULL DEFAULT '',
        design_name TEXT NOT NULL DEFAULT '', design_version TEXT NOT NULL DEFAULT '', target_url TEXT NOT NULL DEFAULT '',
        executed_at TEXT NOT NULL,
        UNIQUE(run_id,seq), UNIQUE(run_id,test_id)
    )""",
    """CREATE INDEX IF NOT EXISTS idx_uiux_results_run ON uiux_results(run_id,result)""",
    """CREATE TABLE IF NOT EXISTS uiux_clickup_links(
        id TEXT PRIMARY KEY, run_id TEXT NOT NULL, result_id TEXT NOT NULL, idempotency_key TEXT NOT NULL UNIQUE,
        config_id INTEGER NOT NULL DEFAULT 0, list_id TEXT NOT NULL DEFAULT '', task_type TEXT NOT NULL DEFAULT '',
        task_id TEXT NOT NULL DEFAULT '', task_url TEXT NOT NULL DEFAULT '', task_title TEXT NOT NULL DEFAULT '', task_status TEXT NOT NULL DEFAULT '',
        outcome TEXT NOT NULL, error TEXT NOT NULL DEFAULT '', attachments_json TEXT NOT NULL DEFAULT '[]',
        created_by TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL
    )""",
    """CREATE INDEX IF NOT EXISTS idx_uiux_cu_result ON uiux_clickup_links(result_id,outcome)""",
    """CREATE INDEX IF NOT EXISTS idx_uiux_cu_run ON uiux_clickup_links(run_id)""",
]


class UiuxDatabase:
    """Connection factory. ``db_path`` is explicit in tests; otherwise the metadata database."""

    def __init__(self, db_path=None):
        if db_path is None:
            from Database.db_manager import DatabaseManager
            db_path = DatabaseManager().db_path
        self.db_path = Path(db_path)
        self.ensure_schema()

    @contextmanager
    def connect(self):
        conn = sqlite3.connect(self.db_path, timeout=15)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            with conn:  # commit on success, rollback on error (transaction-safe)
                yield conn
        finally:
            conn.close()

    def _backup_before_first_migration(self):
        """The first run of this module adds tables to a live database: take a consistent copy first (once)."""
        if not self.db_path.exists() or self.db_path.stat().st_size == 0:
            return None
        src = sqlite3.connect(self.db_path, timeout=15)
        try:
            has_meta = src.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='uiux_meta'").fetchone()
            has_steps = src.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='uiux_run_steps'").fetchone()
            has_results = src.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='uiux_results'").fetchone()
            if has_meta and has_steps and has_results:
                return None
            from datetime import datetime
            label = ("PRE_UIUX_V3" if has_steps else "PRE_UIUX_V2") if has_meta else "PRE_UIUX"   # v2 adds the run-step table, v3 the result and ClickUp-link tables; a copy is taken once before each
            target = self.db_path.with_name(f"{self.db_path.name}.{label}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.bak")
            dst = sqlite3.connect(target)
            try:
                src.backup(dst)
            finally:
                dst.close()
            self.backup_path = target
            return target
        finally:
            src.close()

    backup_path = None

    def ensure_schema(self):
        self._backup_before_first_migration()
        # One explicit transaction for the whole migration: SQLite DDL is transactional, so a failure part-way leaves no new tables.
        conn = sqlite3.connect(self.db_path, timeout=15, isolation_level=None)
        try:
            conn.execute("BEGIN IMMEDIATE")
            try:
                for statement in TABLES:
                    conn.execute(statement)
                conn.execute("INSERT OR IGNORE INTO uiux_meta(key,value) VALUES('schema_version',?)", (str(SCHEMA_VERSION),))
                conn.execute("INSERT OR REPLACE INTO uiux_meta(key,value) VALUES('schema_version',?)", (str(SCHEMA_VERSION),))
                conn.execute("COMMIT")
            except BaseException:
                conn.execute("ROLLBACK")
                raise
        finally:
            conn.close()

    def table_names(self):
        with self.connect() as c:
            return [r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'uiux_%' ORDER BY name")]
