"""
QA AI Studio
Database Schema

Version: 2.0
"""

# ==========================================================
# Knowledge Items
# ==========================================================

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
    """
]