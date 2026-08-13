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
# URL System Discovery
# ==========================================================
#
# Separate hierarchy from Domain -> Module -> Knowledge Name, per
# your explicit requirement — document knowledge and discovered
# system/UI knowledge are structurally different things.
#
# Hierarchy:
#   Application -> Business Process -> Variant -> Page -> Tab
#     -> [Section] -> Element
#   Workflow Step is a separate table that references Pages/Tabs
#   to record sequence and dependency, not another nesting level.
#
# Credentials used during discovery are NEVER stored here — same
# pattern as Git/Test Environment settings (local file, in-memory
# only during the discovery run).
# ==========================================================

DISCOVERY_APPLICATIONS_TABLE = """
CREATE TABLE IF NOT EXISTS discovery_applications
(
    id INTEGER PRIMARY KEY AUTOINCREMENT,

    name TEXT NOT NULL UNIQUE,

    base_url TEXT,

    description TEXT,

    status TEXT DEFAULT 'Active',

    created_date TEXT,

    modified_date TEXT
);
"""


DISCOVERY_BUSINESS_PROCESSES_TABLE = """
CREATE TABLE IF NOT EXISTS discovery_business_processes
(
    id INTEGER PRIMARY KEY AUTOINCREMENT,

    application_id INTEGER NOT NULL,

    name TEXT NOT NULL,

    description TEXT,

    -- Optional bridge to your existing document knowledge, for the
    -- "Knowledge Chain" combination (CRF/SRS + discovered system
    -- knowledge). NULL until you explicitly link them.
    linked_domain_id INTEGER,

    linked_module_id INTEGER,

    status TEXT DEFAULT 'Active',

    created_date TEXT,

    modified_date TEXT,

    UNIQUE(application_id, name),

    FOREIGN KEY(application_id)
        REFERENCES discovery_applications(id)
        ON DELETE CASCADE,

    FOREIGN KEY(linked_domain_id)
        REFERENCES domains(id)
        ON DELETE SET NULL,

    FOREIGN KEY(linked_module_id)
        REFERENCES modules(id)
        ON DELETE SET NULL
);
"""


DISCOVERY_VARIANTS_TABLE = """
CREATE TABLE IF NOT EXISTS discovery_variants
(
    id INTEGER PRIMARY KEY AUTOINCREMENT,

    business_process_id INTEGER NOT NULL,

    name TEXT NOT NULL,

    description TEXT,

    status TEXT DEFAULT 'Active',

    created_date TEXT,

    modified_date TEXT,

    UNIQUE(business_process_id, name),

    FOREIGN KEY(business_process_id)
        REFERENCES discovery_business_processes(id)
        ON DELETE CASCADE
);
"""


DISCOVERY_PAGES_TABLE = """
CREATE TABLE IF NOT EXISTS discovery_pages
(
    id INTEGER PRIMARY KEY AUTOINCREMENT,

    variant_id INTEGER NOT NULL,

    name TEXT NOT NULL,

    url TEXT NOT NULL,

    page_title TEXT,

    status TEXT DEFAULT 'Active',

    created_date TEXT,

    modified_date TEXT,

    UNIQUE(variant_id, url),

    FOREIGN KEY(variant_id)
        REFERENCES discovery_variants(id)
        ON DELETE CASCADE
);
"""


DISCOVERY_TABS_TABLE = """
CREATE TABLE IF NOT EXISTS discovery_tabs
(
    id INTEGER PRIMARY KEY AUTOINCREMENT,

    page_id INTEGER NOT NULL,

    name TEXT NOT NULL,

    tab_order INTEGER DEFAULT 0,

    status TEXT DEFAULT 'Active',

    created_date TEXT,

    modified_date TEXT,

    UNIQUE(page_id, name),

    FOREIGN KEY(page_id)
        REFERENCES discovery_pages(id)
        ON DELETE CASCADE
);
"""


DISCOVERY_SECTIONS_TABLE = """
CREATE TABLE IF NOT EXISTS discovery_sections
(
    id INTEGER PRIMARY KEY AUTOINCREMENT,

    tab_id INTEGER NOT NULL,

    name TEXT NOT NULL,

    section_order INTEGER DEFAULT 0,

    status TEXT DEFAULT 'Active',

    created_date TEXT,

    modified_date TEXT,

    UNIQUE(tab_id, name),

    FOREIGN KEY(tab_id)
        REFERENCES discovery_tabs(id)
        ON DELETE CASCADE
);
"""


DISCOVERY_ELEMENTS_TABLE = """
CREATE TABLE IF NOT EXISTS discovery_elements
(
    id INTEGER PRIMARY KEY AUTOINCREMENT,

    tab_id INTEGER NOT NULL,

    -- Nullable: only set when the page genuinely has a meaningful
    -- section grouping. Most elements will just reference tab_id.
    section_id INTEGER,

    name TEXT,

    element_type TEXT,

    locator TEXT NOT NULL,

    locator_strategy TEXT,

    -- JSON-encoded list of fallback locators, most to least
    -- preferred (e.g. data-testid, then id, then css path) — for
    -- automation scripts to try if the primary one stops matching.
    alternate_locators TEXT,

    placeholder TEXT,

    validation_message TEXT,

    is_required INTEGER DEFAULT 0,

    status TEXT DEFAULT 'Active',

    created_date TEXT,

    modified_date TEXT,

    FOREIGN KEY(tab_id)
        REFERENCES discovery_tabs(id)
        ON DELETE CASCADE,

    FOREIGN KEY(section_id)
        REFERENCES discovery_sections(id)
        ON DELETE CASCADE
);
"""


DISCOVERY_WORKFLOW_STEPS_TABLE = """
CREATE TABLE IF NOT EXISTS discovery_workflow_steps
(
    id INTEGER PRIMARY KEY AUTOINCREMENT,

    variant_id INTEGER NOT NULL,

    step_order INTEGER NOT NULL,

    page_id INTEGER NOT NULL,

    tab_id INTEGER,

    step_name TEXT,

    depends_on_step_id INTEGER,

    is_end_step INTEGER DEFAULT 0,

    created_date TEXT,

    modified_date TEXT,

    FOREIGN KEY(variant_id)
        REFERENCES discovery_variants(id)
        ON DELETE CASCADE,

    FOREIGN KEY(page_id)
        REFERENCES discovery_pages(id)
        ON DELETE CASCADE,

    FOREIGN KEY(tab_id)
        REFERENCES discovery_tabs(id)
        ON DELETE CASCADE,

    FOREIGN KEY(depends_on_step_id)
        REFERENCES discovery_workflow_steps(id)
        ON DELETE SET NULL
);
"""


DISCOVERY_SESSIONS_TABLE = """
CREATE TABLE IF NOT EXISTS discovery_sessions
(
    id INTEGER PRIMARY KEY AUTOINCREMENT,

    application_id INTEGER,

    url TEXT NOT NULL,

    source_area TEXT,

    auth_type TEXT,

    status TEXT DEFAULT 'Pending',

    pages_discovered_count INTEGER DEFAULT 0,

    elements_discovered_count INTEGER DEFAULT 0,

    error_message TEXT,

    started_date TEXT,

    completed_date TEXT,

    FOREIGN KEY(application_id)
        REFERENCES discovery_applications(id)
        ON DELETE SET NULL
);
"""


# ==========================================================
# Table creation order matters: domains before modules (FK),
# both before knowledge_items (referenced by domain_id/module_id
# once populated). Discovery tables follow their own dependency
# order — applications, then each level down, sessions last since
# it optionally references applications.
# ==========================================================

ALL_TABLES = [
    DOMAINS_TABLE,
    MODULES_TABLE,
    KNOWLEDGE_ITEMS_TABLE,
    KNOWLEDGE_VERSIONS_TABLE,
    KNOWLEDGE_TAGS_TABLE,
    EMBEDDING_QUEUE_TABLE,
    DISCOVERY_APPLICATIONS_TABLE,
    DISCOVERY_BUSINESS_PROCESSES_TABLE,
    DISCOVERY_VARIANTS_TABLE,
    DISCOVERY_PAGES_TABLE,
    DISCOVERY_TABS_TABLE,
    DISCOVERY_SECTIONS_TABLE,
    DISCOVERY_ELEMENTS_TABLE,
    DISCOVERY_WORKFLOW_STEPS_TABLE,
    DISCOVERY_SESSIONS_TABLE,
]

# AI/Database/schema.py (Additions for Hierarchy & Workflow Store)

CREATE_KNOWLEDGE_HIERARCHY_TABLE = """
CREATE TABLE IF NOT EXISTS url_knowledge_hierarchy (
    id TEXT PRIMARY KEY,
    application_name TEXT NOT NULL,
    business_process TEXT NOT NULL,
    variant_name TEXT NOT NULL,
    page_name TEXT NOT NULL,
    tab_name TEXT,
    section_name TEXT,
    element_name TEXT NOT NULL,
    element_type TEXT NOT NULL, -- button, input, select, tab, link
    locator_primary TEXT NOT NULL, -- e.g., get_by_role / #id
    locator_xpath TEXT,
    locator_css TEXT,
    placeholder_text TEXT,
    validation_rules TEXT, -- JSON array e.g. ["required", "min:3"]
    page_url TEXT NOT NULL,
    workflow_step_order INTEGER NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""

CREATE_WORKFLOW_DEPENDENCY_TABLE = """
CREATE TABLE IF NOT EXISTS workflow_dependencies (
    id TEXT PRIMARY KEY,
    business_process TEXT NOT NULL,
    start_url TEXT NOT NULL,
    end_url TEXT NOT NULL,
    step_sequence_json TEXT NOT NULL, -- JSON array of steps
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""