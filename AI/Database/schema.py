# Replace: AI/Database/schema.py

"""
QA AI Studio
Database Schema

Version: 3.0  (Unified — Phase 1 Consistency)

This is now the ONLY schema file. schema_v3.py is deleted — it had
a second, incompatible knowledge_items definition that never
actually took effect (knowledge_items already existed by the time
it ran), plus six enterprise auth tables (roles/users/permissions/
audit_logs/user_sessions/knowledge_relationships) that nothing in
the app ever used. Keeping two schema files that disagreed with
each other was exactly the kind of drift this pass is fixing.

CRITICAL FIX: previously, NOTHING in the running app ever called an
initializer — main.py never did, and the one wrapper that would
(SchemaManager) was never instantiated anywhere. The app only
worked because Database/metadata.db already had tables in it from
some earlier, since-removed code path. If that file were ever
deleted, the app would crash immediately with "no such table"
errors. This is now fixed — see App/main.py, which calls
DatabaseManager().initialize_database() before creating the
window.
"""

# ==========================================================
# Domains  (must be created before knowledge_items/modules,
# since modules has a FK to this table)
# ==========================================================

DOMAINS_TABLE = """
CREATE TABLE IF NOT EXISTS domains
(
    id INTEGER PRIMARY KEY AUTOINCREMENT,

    name TEXT NOT NULL UNIQUE,

    description TEXT,

    status TEXT DEFAULT 'Active',

    created_date TEXT,

    modified_date TEXT
);
"""


# ==========================================================
# Modules
# ==========================================================
# NOTE: UNIQUE(domain_id, name) is enforced here for fresh installs.
# Your EXISTING database needs migrate_phase1_consistency.py to add
# this constraint, since SQLite can't ALTER TABLE to add a
# constraint to an already-existing table — it requires a rebuild,
# which that script does safely (de-duplicating first).

MODULES_TABLE = """
CREATE TABLE IF NOT EXISTS modules
(
    id INTEGER PRIMARY KEY AUTOINCREMENT,

    domain_id INTEGER NOT NULL,

    name TEXT NOT NULL,

    description TEXT,

    status TEXT DEFAULT 'Active',

    created_date TEXT,

    modified_date TEXT,

    UNIQUE(domain_id, name),

    FOREIGN KEY(domain_id)
        REFERENCES domains(id)
        ON DELETE CASCADE
);
"""


# ==========================================================
# Knowledge Items
# ==========================================================
# domain/module TEXT columns are kept (a lot of existing queries
# read them directly) but are now always written FROM the resolved
# domain_id/module_id at save time — see
# Core/metadata_manager.py save_knowledge_item(). The text columns
# become a synced display cache instead of the source of truth;
# domain_id/module_id are the source of truth going forward.

KNOWLEDGE_ITEMS_TABLE = """
CREATE TABLE IF NOT EXISTS knowledge_items
(

    id INTEGER PRIMARY KEY AUTOINCREMENT,

    domain TEXT NOT NULL,

    module TEXT NOT NULL,

    knowledge_name TEXT NOT NULL,

    version TEXT NOT NULL,

    knowledge_path TEXT NOT NULL,

    file_name TEXT NOT NULL,

    original_path TEXT,

    repository_path TEXT NOT NULL,

    sha256 TEXT NOT NULL,

    file_size INTEGER,

    extension TEXT,

    knowledge_type TEXT,

    source_type TEXT,

    summary TEXT,

    tags TEXT,

    confidence REAL,

    status TEXT,

    created_date TEXT,

    modified_date TEXT,

    last_embedded TEXT

);
"""

# Applied on every startup — sqlite3.OperationalError (column
# already exists) is caught and ignored, so this is safe to run
# repeatedly. This is how existing databases pick up new columns
# without a destructive migration.
KNOWLEDGE_ITEMS_MIGRATION = [

    """
    ALTER TABLE knowledge_items
    ADD COLUMN platform TEXT
    """,

    """
    ALTER TABLE knowledge_items
    ADD COLUMN category TEXT
    """,

    """
    ALTER TABLE knowledge_items
    ADD COLUMN business_process TEXT
    """,

    """
    ALTER TABLE knowledge_items
    ADD COLUMN document_type TEXT
    """,

    # New in Phase 1 — the real relationship. Nullable so this is a
    # safe additive change; migrate_phase1_consistency.py backfills
    # them for rows that existed before this column did.
    """
    ALTER TABLE knowledge_items
    ADD COLUMN domain_id INTEGER
    """,

    """
    ALTER TABLE knowledge_items
    ADD COLUMN module_id INTEGER
    """,
]


# ==========================================================
# Knowledge Versions
# ==========================================================

KNOWLEDGE_VERSIONS_TABLE = """
CREATE TABLE IF NOT EXISTS knowledge_versions
(

    id INTEGER PRIMARY KEY AUTOINCREMENT,

    knowledge_item_id INTEGER NOT NULL,

    version TEXT NOT NULL,

    sha256 TEXT,

    repository_path TEXT,

    created_date TEXT,

    FOREIGN KEY (knowledge_item_id)
        REFERENCES knowledge_items(id)

);
"""


# ==========================================================
# Knowledge Tags
# ==========================================================

KNOWLEDGE_TAGS_TABLE = """
CREATE TABLE IF NOT EXISTS knowledge_tags
(

    id INTEGER PRIMARY KEY AUTOINCREMENT,

    knowledge_id INTEGER NOT NULL,

    tag TEXT NOT NULL,

    FOREIGN KEY (knowledge_id)
        REFERENCES knowledge_items(id)

);
"""


# ==========================================================
# Embedding Queue
# ==========================================================

EMBEDDING_QUEUE_TABLE = """
CREATE TABLE IF NOT EXISTS embedding_queue
(

    id INTEGER PRIMARY KEY AUTOINCREMENT,

    knowledge_id INTEGER NOT NULL,

    status TEXT,

    priority INTEGER DEFAULT 1,

    created_date TEXT,

    completed_date TEXT,

    FOREIGN KEY (knowledge_id)
        REFERENCES knowledge_items(id)

);
"""


# ==========================================================
# Table creation order matters: domains before modules (FK),
# both before knowledge_items (referenced by domain_id/module_id
# once populated).
# ==========================================================

ALL_TABLES = [
    DOMAINS_TABLE,
    MODULES_TABLE,
    KNOWLEDGE_ITEMS_TABLE,
    KNOWLEDGE_VERSIONS_TABLE,
    KNOWLEDGE_TAGS_TABLE,
    EMBEDDING_QUEUE_TABLE,
]