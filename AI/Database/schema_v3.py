"""
QA AI Studio
Database Schema

Version : 3.0
"""

SCHEMA_VERSION = 3

# ==========================================================
# MASTER TABLES
# ==========================================================

DOMAINS_TABLE = """
CREATE TABLE IF NOT EXISTS domains
(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT UNIQUE NOT NULL,
    description TEXT,
    status TEXT DEFAULT 'Active',
    created_date TEXT,
    modified_date TEXT
);
"""


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

    UNIQUE(domain_id,name),

    FOREIGN KEY(domain_id)
        REFERENCES domains(id)
        ON DELETE CASCADE
);
"""


# ==========================================================
# KNOWLEDGE
# ==========================================================

KNOWLEDGE_ITEMS_TABLE = """
CREATE TABLE IF NOT EXISTS knowledge_items
(
    id INTEGER PRIMARY KEY AUTOINCREMENT,

    domain_id INTEGER NOT NULL,
    module_id INTEGER NOT NULL,

    knowledge_name TEXT NOT NULL,
    version TEXT NOT NULL,

    knowledge_path TEXT,
    repository_path TEXT,

    file_name TEXT,
    original_path TEXT,

    sha256 TEXT,

    file_size INTEGER,
    extension TEXT,

    document_type TEXT,
    knowledge_type TEXT,
    source_type TEXT,

    business_process TEXT,
    summary TEXT,
    tags TEXT,

    confidence REAL,

    owner TEXT,

    status TEXT DEFAULT 'Active',

    created_date TEXT,
    modified_date TEXT,
    last_embedded TEXT,

    FOREIGN KEY(domain_id)
        REFERENCES domains(id),

    FOREIGN KEY(module_id)
        REFERENCES modules(id)
);
"""


KNOWLEDGE_VERSIONS_TABLE = """
CREATE TABLE IF NOT EXISTS knowledge_versions
(
    id INTEGER PRIMARY KEY AUTOINCREMENT,

    knowledge_item_id INTEGER NOT NULL,

    version TEXT,

    repository_path TEXT,

    sha256 TEXT,

    created_date TEXT,

    FOREIGN KEY(knowledge_item_id)
        REFERENCES knowledge_items(id)
        ON DELETE CASCADE
);
"""


KNOWLEDGE_TAGS_TABLE = """
CREATE TABLE IF NOT EXISTS knowledge_tags
(
    id INTEGER PRIMARY KEY AUTOINCREMENT,

    knowledge_id INTEGER NOT NULL,

    tag TEXT,

    FOREIGN KEY(knowledge_id)
        REFERENCES knowledge_items(id)
        ON DELETE CASCADE
);
"""


EMBEDDING_QUEUE_TABLE = """
CREATE TABLE IF NOT EXISTS embedding_queue
(
    id INTEGER PRIMARY KEY AUTOINCREMENT,

    knowledge_id INTEGER NOT NULL,

    status TEXT,

    priority INTEGER DEFAULT 1,

    created_date TEXT,

    completed_date TEXT,

    FOREIGN KEY(knowledge_id)
        REFERENCES knowledge_items(id)
        ON DELETE CASCADE
);
"""


KNOWLEDGE_RELATIONSHIPS_TABLE = """
CREATE TABLE IF NOT EXISTS knowledge_relationships
(
    id INTEGER PRIMARY KEY AUTOINCREMENT,

    parent_id INTEGER,

    child_id INTEGER,

    relationship_type TEXT,

    created_date TEXT
);
"""


# ==========================================================
# SECURITY
# ==========================================================

ROLES_TABLE = """
CREATE TABLE IF NOT EXISTS roles
(
    id INTEGER PRIMARY KEY AUTOINCREMENT,

    role_name TEXT UNIQUE,

    description TEXT,

    status TEXT DEFAULT 'Active',

    created_date TEXT,

    modified_date TEXT
);
"""


USERS_TABLE = """
CREATE TABLE IF NOT EXISTS users
(
    id INTEGER PRIMARY KEY AUTOINCREMENT,

    username TEXT UNIQUE,

    full_name TEXT,

    email TEXT UNIQUE,

    password_hash TEXT,

    role_id INTEGER,

    status TEXT DEFAULT 'Active',

    last_login TEXT,

    created_date TEXT,

    modified_date TEXT,

    FOREIGN KEY(role_id)
        REFERENCES roles(id)
);
"""


PERMISSIONS_TABLE = """
CREATE TABLE IF NOT EXISTS permissions
(
    id INTEGER PRIMARY KEY AUTOINCREMENT,

    module TEXT,

    permission TEXT,

    description TEXT
);
"""


ROLE_PERMISSIONS_TABLE = """
CREATE TABLE IF NOT EXISTS role_permissions
(
    id INTEGER PRIMARY KEY AUTOINCREMENT,

    role_id INTEGER,

    permission_id INTEGER,

    UNIQUE(role_id,permission_id),

    FOREIGN KEY(role_id)
        REFERENCES roles(id),

    FOREIGN KEY(permission_id)
        REFERENCES permissions(id)
);
"""


AUDIT_LOGS_TABLE = """
CREATE TABLE IF NOT EXISTS audit_logs
(
    id INTEGER PRIMARY KEY AUTOINCREMENT,

    user_id INTEGER,

    action TEXT,

    module TEXT,

    details TEXT,

    status TEXT,

    created_date TEXT,

    FOREIGN KEY(user_id)
        REFERENCES users(id)
);
"""


USER_SESSIONS_TABLE = """
CREATE TABLE IF NOT EXISTS user_sessions
(
    id INTEGER PRIMARY KEY AUTOINCREMENT,

    user_id INTEGER,

    session_token TEXT,

    login_time TEXT,

    logout_time TEXT,

    is_active INTEGER DEFAULT 1,

    FOREIGN KEY(user_id)
        REFERENCES users(id)
);
"""


# ==========================================================
# TABLES
# ==========================================================

ALL_TABLES = [
    KNOWLEDGE_ITEMS_TABLE,
    KNOWLEDGE_VERSIONS_TABLE,
    KNOWLEDGE_TAGS_TABLE,
    EMBEDDING_QUEUE_TABLE,
    DOMAINS_TABLE,
    MODULES_TABLE,
    KNOWLEDGE_RELATIONSHIPS_TABLE,
    ROLES_TABLE,
    USERS_TABLE,
    PERMISSIONS_TABLE,
    ROLE_PERMISSIONS_TABLE,
    AUDIT_LOGS_TABLE,
    USER_SESSIONS_TABLE
]


# ==========================================================
# INDEXES
# ==========================================================

INDEXES = [

    "CREATE INDEX IF NOT EXISTS idx_domain_name ON domains(name)",

    "CREATE INDEX IF NOT EXISTS idx_module_domain ON modules(domain_id)",

    "CREATE INDEX IF NOT EXISTS idx_module_name ON modules(name)",

    "CREATE INDEX IF NOT EXISTS idx_knowledge_domain ON knowledge_items(domain_id)",

    "CREATE INDEX IF NOT EXISTS idx_knowledge_module ON knowledge_items(module_id)",

    "CREATE INDEX IF NOT EXISTS idx_knowledge_name ON knowledge_items(knowledge_name)",

    "CREATE INDEX IF NOT EXISTS idx_user_username ON users(username)",

    "CREATE INDEX IF NOT EXISTS idx_user_email ON users(email)"
]


# ==========================================================
# SEED DATA
# ==========================================================

SEED_DATA = [

(
"INSERT OR IGNORE INTO roles(role_name,description) VALUES(?,?)",
("Admin","System Administrator")
),

(
"INSERT OR IGNORE INTO roles(role_name,description) VALUES(?,?)",
("QA Lead","QA Lead")
),

(
"INSERT OR IGNORE INTO roles(role_name,description) VALUES(?,?)",
("QA Engineer","QA Engineer")
),

(
"INSERT OR IGNORE INTO roles(role_name,description) VALUES(?,?)",
("Viewer","Read Only")
)
]

