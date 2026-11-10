"""cia_* tables. Created idempotently (CREATE TABLE IF NOT EXISTS); no existing table is touched."""

TABLES = (
    '''CREATE TABLE IF NOT EXISTS cia_runs(
        id TEXT PRIMARY KEY,
        title TEXT NOT NULL,
        status TEXT NOT NULL,
        request_key TEXT NOT NULL,
        source_id INTEGER NOT NULL,
        baseline_source_id INTEGER,
        scope_json TEXT NOT NULL,
        progress_json TEXT,
        result_json TEXT,
        result_hash TEXT,
        error_code TEXT,
        error_message TEXT,
        created_by INTEGER,
        created_by_name TEXT,
        created_at TEXT NOT NULL,
        completed_at TEXT,
        engine_version TEXT)''',
    'CREATE INDEX IF NOT EXISTS idx_cia_runs_key ON cia_runs(request_key,status)',
    'CREATE INDEX IF NOT EXISTS idx_cia_runs_source ON cia_runs(source_id,created_at)',
    '''CREATE TABLE IF NOT EXISTS cia_reviews(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        run_id TEXT NOT NULL,
        item_key TEXT NOT NULL,
        decision TEXT NOT NULL,
        comment TEXT,
        reviewer_id INTEGER,
        reviewer_name TEXT,
        created_at TEXT NOT NULL)''',
    'CREATE INDEX IF NOT EXISTS idx_cia_reviews_run ON cia_reviews(run_id,item_key)',
    '''CREATE TABLE IF NOT EXISTS cia_relationships(
        id TEXT PRIMARY KEY,
        source_id INTEGER NOT NULL,
        source_sha TEXT,
        source_ref TEXT,
        source_quote TEXT,
        target_id INTEGER NOT NULL,
        target_sha TEXT,
        target_ref TEXT,
        target_quote TEXT,
        relationship TEXT,
        classification TEXT NOT NULL,
        origin TEXT NOT NULL,
        human_status TEXT,
        human_by TEXT,
        human_at TEXT,
        first_run_id TEXT,
        last_run_id TEXT,
        last_verified_at TEXT,
        stale INTEGER NOT NULL DEFAULT 0,
        stale_reason TEXT)''',
    'CREATE INDEX IF NOT EXISTS idx_cia_rel_source ON cia_relationships(source_id)',
    'CREATE INDEX IF NOT EXISTS idx_cia_rel_target ON cia_relationships(target_id)',
)


# columns added by engine 2.0 (ALTER TABLE ... ADD COLUMN; existing rows keep their values)
ADDED_COLUMNS = {
    'cia_relationships': (('engine_version', 'TEXT'), ('superseded', 'INTEGER NOT NULL DEFAULT 0'), ('supersede_note', 'TEXT'), ('item_key', 'TEXT'), ('evidence_json', 'TEXT')),
    'cia_runs': (('refine_json', 'TEXT'),),
}
CURRENT_ENGINE = '2.0'
SUPERSEDE_NOTE = ('Found by engine 1.0, which matched wording. It is kept for history and is not used as evidence by engine %s; a QA decision made on it is shown for information only.'
                  % CURRENT_ENGINE)


def ensure_schema(db):
    conn = db.get_connection()
    try:
        for sql in TABLES:
            conn.execute(sql)
        for table, columns in ADDED_COLUMNS.items():
            existing = {r[1] for r in conn.execute('PRAGMA table_info(%s)' % table)}
            for name, ddl in columns:
                if name not in existing:
                    conn.execute('ALTER TABLE %s ADD COLUMN %s %s' % (table, name, ddl))
        # relationships written before engine 2.0 are marked, never deleted
        conn.execute("UPDATE cia_relationships SET engine_version='1.0' WHERE engine_version IS NULL")
        conn.execute("UPDATE cia_relationships SET superseded=1,supersede_note=? WHERE engine_version<>? AND superseded=0", (SUPERSEDE_NOTE, CURRENT_ENGINE))
        conn.execute('CREATE INDEX IF NOT EXISTS idx_cia_rel_item ON cia_relationships(source_id,item_key)')
        conn.commit()
    finally:
        conn.close()
