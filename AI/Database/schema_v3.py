"""
QA AI Studio
Database Schema

Version: 3.0
"""

SCHEMA_VERSION = 3

KNOWLEDGE_ITEMS_TABLE = """CREATE TABLE IF NOT EXISTS knowledge_items(
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
);"""

KNOWLEDGE_VERSIONS_TABLE="""CREATE TABLE IF NOT EXISTS knowledge_versions(
id INTEGER PRIMARY KEY AUTOINCREMENT,
knowledge_item_id INTEGER NOT NULL,
version TEXT NOT NULL,
sha256 TEXT,
repository_path TEXT,
created_date TEXT,
FOREIGN KEY(knowledge_item_id) REFERENCES knowledge_items(id));"""

KNOWLEDGE_TAGS_TABLE="""CREATE TABLE IF NOT EXISTS knowledge_tags(
id INTEGER PRIMARY KEY AUTOINCREMENT,
knowledge_id INTEGER NOT NULL,
tag TEXT NOT NULL,
FOREIGN KEY(knowledge_id) REFERENCES knowledge_items(id));"""

EMBEDDING_QUEUE_TABLE="""CREATE TABLE IF NOT EXISTS embedding_queue(
id INTEGER PRIMARY KEY AUTOINCREMENT,
knowledge_id INTEGER NOT NULL,
status TEXT,
priority INTEGER DEFAULT 1,
created_date TEXT,
completed_date TEXT,
FOREIGN KEY(knowledge_id) REFERENCES knowledge_items(id));"""

KNOWLEDGE_ITEMS_MIGRATION=[
"ALTER TABLE knowledge_items ADD COLUMN platform TEXT",
"ALTER TABLE knowledge_items ADD COLUMN category TEXT",
"ALTER TABLE knowledge_items ADD COLUMN business_process TEXT",
"ALTER TABLE knowledge_items ADD COLUMN document_type TEXT",
"ALTER TABLE knowledge_items ADD COLUMN owner TEXT",
"ALTER TABLE knowledge_items ADD COLUMN created_by INTEGER",
"ALTER TABLE knowledge_items ADD COLUMN updated_by INTEGER"
]

ROLES_TABLE="""CREATE TABLE IF NOT EXISTS roles(
id INTEGER PRIMARY KEY AUTOINCREMENT,
role_name TEXT UNIQUE,
description TEXT,
is_system INTEGER DEFAULT 1,
status TEXT DEFAULT 'Active',
created_date TEXT,
modified_date TEXT);"""

USERS_TABLE="""CREATE TABLE IF NOT EXISTS users(
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
FOREIGN KEY(role_id) REFERENCES roles(id));"""

PERMISSIONS_TABLE="""CREATE TABLE IF NOT EXISTS permissions(
id INTEGER PRIMARY KEY AUTOINCREMENT,
module TEXT,
permission TEXT,
description TEXT);"""

ROLE_PERMISSIONS_TABLE="""CREATE TABLE IF NOT EXISTS role_permissions(
id INTEGER PRIMARY KEY AUTOINCREMENT,
role_id INTEGER,
permission_id INTEGER,
UNIQUE(role_id,permission_id),
FOREIGN KEY(role_id) REFERENCES roles(id),
FOREIGN KEY(permission_id) REFERENCES permissions(id));"""

AUDIT_LOGS_TABLE="""CREATE TABLE IF NOT EXISTS audit_logs(
id INTEGER PRIMARY KEY AUTOINCREMENT,
user_id INTEGER,
action TEXT,
module TEXT,
details TEXT,
status TEXT,
created_date TEXT,
FOREIGN KEY(user_id) REFERENCES users(id));"""

USER_SESSIONS_TABLE="""CREATE TABLE IF NOT EXISTS user_sessions(
id INTEGER PRIMARY KEY AUTOINCREMENT,
user_id INTEGER,
session_token TEXT,
login_time TEXT,
logout_time TEXT,
is_active INTEGER DEFAULT 1,
FOREIGN KEY(user_id) REFERENCES users(id));"""

ALL_TABLES=[ROLES_TABLE,USERS_TABLE,PERMISSIONS_TABLE,ROLE_PERMISSIONS_TABLE,AUDIT_LOGS_TABLE,USER_SESSIONS_TABLE]

INDEXES=[
"CREATE INDEX IF NOT EXISTS idx_knowledge_domain ON knowledge_items(domain)",
"CREATE INDEX IF NOT EXISTS idx_knowledge_module ON knowledge_items(module)",
"CREATE INDEX IF NOT EXISTS idx_knowledge_name ON knowledge_items(knowledge_name)",
"CREATE INDEX IF NOT EXISTS idx_users_username ON users(username)",
"CREATE INDEX IF NOT EXISTS idx_users_email ON users(email)",
"CREATE INDEX IF NOT EXISTS idx_audit_user ON audit_logs(user_id)"
]

SEED_DATA=[
("INSERT OR IGNORE INTO roles(role_name,description) VALUES(?,?)",("Admin","System Administrator")),
("INSERT OR IGNORE INTO roles(role_name,description) VALUES(?,?)",("QA Lead","QA Lead")),
("INSERT OR IGNORE INTO roles(role_name,description) VALUES(?,?)",("QA Engineer","QA Engineer")),
("INSERT OR IGNORE INTO roles(role_name,description) VALUES(?,?)",("Viewer","Read Only"))
]
