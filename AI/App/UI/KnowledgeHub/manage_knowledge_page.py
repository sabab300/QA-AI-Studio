"""
==========================================================
QA AI Studio

Knowledge Hub

Manage Knowledge

Version : 2.0

Production Knowledge Management

Features:
    • MetadataManager integration
    • Search/filter
    • Knowledge details
    • Delete knowledge
    • Version history
==========================================================
"""

from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QLineEdit,
    QTableWidget,
    QTableWidgetItem,
    QHeaderView,
    QMessageBox,
    QTextEdit,
    QDialog,
    QDialogButtonBox
)

from Core.metadata_manager import MetadataManager


class KnowledgeDetailDialog(QDialog):

    def __init__(self, data, parent=None):

        super().__init__(parent)

        self.setWindowTitle(
            "Knowledge Details"
        )

        self.resize(
            600,
            450
        )

        layout = QVBoxLayout(
            self
        )


        text = QTextEdit()

        text.setReadOnly(
            True
        )


        for key, value in data.items():

            text.append(
                f"{key}: {value}"
            )


        layout.addWidget(
            text
        )


        buttons = QDialogButtonBox(
            QDialogButtonBox.Ok
        )


        buttons.accepted.connect(
            self.accept
        )


        layout.addWidget(
            buttons
        )



class ManageKnowledgePage(QWidget):

    def __init__(self):

        super().__init__()

        self.manager = MetadataManager()

        self.rows = []

        self.build_ui()

        self.load_data()



    # ======================================================
    # UI
    # ======================================================

    def build_ui(self):

        layout = QVBoxLayout(
            self
        )


        title = QLabel(
            "Manage Knowledge"
        )

        title.setObjectName(
            "SectionTitle"
        )


        layout.addWidget(
            title
        )


        toolbar = QHBoxLayout()


        self.search = QLineEdit()

        self.search.setPlaceholderText(
            "Search knowledge..."
        )


        self.refresh_btn = QPushButton(
            "Refresh"
        )


        self.view_btn = QPushButton(
            "View Details"
        )


        self.version_btn = QPushButton(
            "Versions"
        )


        self.delete_btn = QPushButton(
            "Delete"
        )


        toolbar.addWidget(
            self.search
        )


        toolbar.addWidget(
            self.refresh_btn
        )


        toolbar.addWidget(
            self.view_btn
        )


        toolbar.addWidget(
            self.version_btn
        )


        toolbar.addWidget(
            self.delete_btn
        )


        layout.addLayout(
            toolbar
        )



        self.table = QTableWidget()


        headers = [

            "ID",

            "Domain",

            "Module",

            "Knowledge",

            "Version",

            "Platform",

            "Category",

            "Document Type",

            "Confidence",

            "Status"

        ]


        self.table.setColumnCount(
            len(headers)
        )


        self.table.setHorizontalHeaderLabels(
            headers
        )


        self.table.horizontalHeader().setSectionResizeMode(
            QHeaderView.Stretch
        )


        self.table.setSelectionBehavior(
            QTableWidget.SelectRows
        )


        self.table.setEditTriggers(
            QTableWidget.NoEditTriggers
        )


        layout.addWidget(
            self.table
        )


        # Events

        self.refresh_btn.clicked.connect(
            self.load_data
        )


        self.search.textChanged.connect(
            self.search_data
        )


        self.view_btn.clicked.connect(
            self.view_details
        )


        self.version_btn.clicked.connect(
            self.show_versions
        )


        self.delete_btn.clicked.connect(
            self.delete_selected
        )



    # ======================================================
    # Load
    # ======================================================

    def load_data(self):

        self.rows = []

        keyword = self.search.text().strip()


        try:

            if keyword:

                self.rows = self.manager.search(
                    keyword
                )

            else:

                self.rows = self.manager.list_all()


        except Exception:

            self.rows = []


        self.populate_table()



    def search_data(self):

        self.load_data()



    # ======================================================
    # Table
    # ======================================================

    def populate_table(self):

        self.table.setRowCount(
            0
        )


        for row in self.rows:

            r = self.table.rowCount()


            self.table.insertRow(
                r
            )


            values = [

                row[0],       # id

                row[1],       # domain

                row[2],       # module

                row[3],       # knowledge

                row[4],       # version

                row[16],      # platform

                row[17],      # category

                row[19],      # document type

                row[15],      # confidence

                row[20]       # status

            ]


            for c, value in enumerate(values):

                self.table.setItem(

                    r,

                    c,

                    QTableWidgetItem(
                        str(value)
                    )

                )



    # ======================================================
    # Selected Row
    # ======================================================

    def selected_item(self):

        row = self.table.currentRow()


        if row < 0:

            return None


        return self.rows[row]



    # ======================================================
    # Details
    # ======================================================

    def view_details(self):

        row = self.selected_item()


        if not row:

            return


        data = {

            "ID": row[0],

            "Domain": row[1],

            "Module": row[2],

            "Knowledge": row[3],

            "Version": row[4],

            "Summary": row[13],

            "Tags": row[14],

            "Confidence": row[15],

            "Platform": row[16],

            "Category": row[17],

            "Business Process": row[18],

            "Document Type": row[19],

            "Status": row[20]

        }


        dialog = KnowledgeDetailDialog(
            data,
            self
        )


        dialog.exec()



    # ======================================================
    # Versions
    # ======================================================

    def show_versions(self):

        row = self.selected_item()


        if not row:

            return


        versions = self.manager.get_versions(
            row[0]
        )


        message = ""


        for item in versions:

            message += (

                f"Version: {item[0]}\n"

                f"SHA256: {item[1]}\n"

                f"Path: {item[2]}\n"

                f"Created: {item[3]}\n\n"

            )


        QMessageBox.information(

            self,

            "Version History",

            message or "No versions found."

        )



    # ======================================================
    # Delete
    # ======================================================

    def delete_selected(self):

        row = self.selected_item()


        if not row:

            return


        confirm = QMessageBox.question(

            self,

            "Delete Knowledge",

            f"Delete {row[3]} ?"

        )


        if confirm != QMessageBox.Yes:

            return



        try:

            self.manager.delete(
                row[0]
            )


            QMessageBox.information(

                self,

                "QA AI Studio",

                "Knowledge deleted."

            )


            self.load_data()



        except Exception as ex:


            QMessageBox.critical(

                self,

                "Delete Failed",

                str(ex)

            )

"""
QA AI Studio
Metadata Manager

Version: 3.0

Enhancement:
- Stores AI document classification metadata
- Stores platform, category, business process and document type
"""

from datetime import datetime
import json

from Database.db_manager import DatabaseManager


class MetadataManager:

    def __init__(self):

        self.db = DatabaseManager()


    # --------------------------------------------------
    # Save Knowledge Item
    # --------------------------------------------------

    def save_knowledge_item(
        self,
        domain,
        module,
        knowledge_name,
        version,
        file_info,
        analysis=None
    ):

        conn = self.db.get_connection()

        cursor = conn.cursor()

        now = datetime.now().isoformat()

        analysis = analysis or {}


        summary = analysis.get(
            "summary",
            ""
        )

        tags = analysis.get(
            "tags",
            []
        )

        confidence = analysis.get(
            "confidence",
            0
        )


        platform = analysis.get(
            "platform",
            ""
        )

        category = analysis.get(
            "category",
            ""
        )

        business_process = analysis.get(
            "business_process",
            ""
        )

        document_type = analysis.get(
            "document_type",
            ""
        )


        cursor.execute(
            """
            INSERT INTO knowledge_items
            (
                domain,
                module,
                knowledge_name,
                version,
                knowledge_path,
                file_name,
                original_path,
                repository_path,
                sha256,
                file_size,
                extension,
                knowledge_type,
                source_type,
                summary,
                tags,
                confidence,
                platform,
                category,
                business_process,
                document_type,
                status,
                created_date,
                modified_date,
                last_embedded
            )
            VALUES
            (
                ?,?,?,?,?,?,?,?,?,?,
                ?,?,?,?,?,?,?,?,?,?,
                ?,?,?,?
            )
            """,
            (
                domain,
                module,
                knowledge_name,
                version,
                f"{domain}/{module}/{knowledge_name}/{version}",

                file_info["file_name"],

                file_info["repository_path"],

                file_info["repository_path"],

                file_info["sha256"],

                file_info["file_size"],

                file_info["extension"],

                "DOCUMENT",

                "FILE",

                summary,

                json.dumps(tags),

                confidence,

                platform,

                category,

                business_process,

                document_type,

                "UPLOADED",

                now,

                now,

                None
            )
        )


        knowledge_id = cursor.lastrowid


        cursor.execute(
            """
            INSERT INTO knowledge_versions
            (
                knowledge_item_id,
                version,
                sha256,
                repository_path,
                created_date
            )
            VALUES
            (?, ?, ?, ?, ?)
            """,
            (
                knowledge_id,
                version,
                file_info["sha256"],
                file_info["repository_path"],
                now
            )
        )


        cursor.execute(
            """
            INSERT INTO embedding_queue
            (
                knowledge_id,
                status,
                priority,
                created_date
            )
            VALUES
            (?, ?, ?, ?)
            """,
            (
                knowledge_id,
                "PENDING",
                1,
                now
            )
        )


        conn.commit()

        conn.close()


        return knowledge_id



    # --------------------------------------------------
    # Embedding Status
    # --------------------------------------------------

    def update_embedding_status(
        self,
        knowledge_id,
        status
    ):

        conn = self.db.get_connection()

        cursor = conn.cursor()

        now = datetime.now().isoformat()


        cursor.execute(
            """
            UPDATE embedding_queue
            SET
                status=?,
                completed_date=?
            WHERE knowledge_id=?
            """,
            (
                status,
                now if status == "COMPLETED" else None,
                knowledge_id
            )
        )


        cursor.execute(
            """
            UPDATE knowledge_items
            SET
                status=?,
                last_embedded=?
            WHERE id=?
            """,
            (
                status,
                now if status == "COMPLETED" else None,
                knowledge_id
            )
        )


        conn.commit()

        conn.close()



    # --------------------------------------------------
    # Get
    # --------------------------------------------------

    def get(
        self,
        knowledge_id
    ):
        
        conn = self.db.get_connection()

        cursor = conn.cursor()


        cursor.execute(
            """
            SELECT *
            FROM knowledge_items
            WHERE id=?
            """,
            (
                knowledge_id,
            )
        )


        row = cursor.fetchone()

        conn.close()

        return row
    
    # --------------------------------------------------
    # Get Knowledge Item
    # --------------------------------------------------

    def get_knowledge_item(

        self,

        domain,

        module,

        knowledge_name

    ):

        conn = self.db.get_connection()

        cursor = conn.cursor()

        cursor.execute(
            """
            SELECT *
            FROM knowledge_items
            WHERE
                domain=?
                AND module=?
                AND knowledge_name=?
            ORDER BY id DESC
            LIMIT 1
            """,
            (
                domain,
                module,
                knowledge_name
            )
        )

        row = cursor.fetchone()

        conn.close()

        return row


    # --------------------------------------------------
    # Get Knowledge Item By ID
    # --------------------------------------------------

    def get_knowledge_item_by_id(

        self,

        knowledge_id

    ):

        return self.get(knowledge_id)
    
    # --------------------------------------------------
    # List
    # --------------------------------------------------

    def list_all(self):

        conn = self.db.get_connection()

        cursor = conn.cursor()


        cursor.execute(
            """
            SELECT *
            FROM knowledge_items
            ORDER BY id DESC
            """
        )


        rows = cursor.fetchall()

        conn.close()

        return rows
    
    # --------------------------------------------------
    # Search
    # --------------------------------------------------

    def search(

        self,

        keyword

    ):

        conn = self.db.get_connection()

        cursor = conn.cursor()

        keyword = f"%{keyword}%"

        cursor.execute(
            """
            SELECT *
            FROM knowledge_items
            WHERE
                domain LIKE ?
                OR module LIKE ?
                OR knowledge_name LIKE ?
                OR version LIKE ?
                OR summary LIKE ?
                OR tags LIKE ?
                OR platform LIKE ?
                OR category LIKE ?
                OR business_process LIKE ?
                OR document_type LIKE ?
            ORDER BY id DESC
            """,
            (
                keyword,
                keyword,
                keyword,
                keyword,
                keyword,
                keyword,
                keyword,
                keyword,
                keyword,
                keyword
            )
        )

        rows = cursor.fetchall()

        conn.close()

        return rows

    # --------------------------------------------------
    # Knowledge Tree
    # --------------------------------------------------

    def get_tree(self):

        rows = self.list_all()

        tree = {}

        for row in rows:

            domain = row[1]
            module = row[2]
            knowledge = row[3]
            version = row[4]

            tree.setdefault(domain, {})
            tree[domain].setdefault(module, {})
            tree[domain][module].setdefault(
                knowledge,
                []
            )

            if version not in tree[domain][module][knowledge]:

                tree[domain][module][knowledge].append(
                    version
                )

        return tree


    # --------------------------------------------------
    # Delete
    # --------------------------------------------------

    def delete(
        self,
        knowledge_id
    ):

        conn = self.db.get_connection()

        cursor = conn.cursor()


        cursor.execute(
            """
            DELETE FROM embedding_queue
            WHERE knowledge_id=?
            """,
            (
                knowledge_id,
            )
        )


        cursor.execute(
            """
            DELETE FROM knowledge_versions
            WHERE knowledge_item_id=?
            """,
            (
                knowledge_id,
            )
        )


        cursor.execute(
            """
            DELETE FROM knowledge_items
            WHERE id=?
            """,
            (
                knowledge_id,
            )
        )


        conn.commit()

        conn.close()

        return True
        # --------------------------------------------------
    # Get Domains
    # --------------------------------------------------

    def get_domains(self):

        conn = self.db.get_connection()
        cursor = conn.cursor()

        cursor.execute(
            """
            SELECT DISTINCT domain
            FROM knowledge_items
            ORDER BY domain
            """
        )

        rows = [row[0] for row in cursor.fetchall()]

        conn.close()

        return rows

    # --------------------------------------------------
    # Get Modules
    # --------------------------------------------------

    def get_modules(self, domain):

        conn = self.db.get_connection()
        cursor = conn.cursor()

        cursor.execute(
            """
            SELECT DISTINCT module
            FROM knowledge_items
            WHERE domain=?
            ORDER BY module
            """,
            (domain,)
        )

        rows = [row[0] for row in cursor.fetchall()]

        conn.close()

        return rows

    # --------------------------------------------------
    # Get Knowledge Names
    # --------------------------------------------------

    def get_knowledge_names(self, domain, module):

        conn = self.db.get_connection()
        cursor = conn.cursor()

        cursor.execute(
            """
            SELECT DISTINCT knowledge_name
            FROM knowledge_items
            WHERE domain=?
            AND module=?
            ORDER BY knowledge_name
            """,
            (
                domain,
                module
            )
        )

        rows = [row[0] for row in cursor.fetchall()]

        conn.close()

        return rows
    
    # --------------------------------------------------
    # Get Versions
    # --------------------------------------------------

    def get_versions(
        self,
        knowledge_id
    ):

        conn = self.db.get_connection()

        cursor = conn.cursor()

        cursor.execute(
            """
            SELECT
                version,
                sha256,
                repository_path,
                created_date
            FROM knowledge_versions
            WHERE knowledge_item_id=?
            ORDER BY created_date DESC
            """,
            (
                knowledge_id,
            )
        )

        rows = cursor.fetchall()

        conn.close()

        return rows

"""
QA AI Studio
Vector Store Manager

Version: 3.0
Production Ready
"""

import chromadb

from Core.config_manager import ConfigManager
from Core.logger import Logger


class VectorStore:

    _client = None
    _collection = None
    _logger = None

    COLLECTION_NAME = "psw_knowledge"

    def __init__(self):

        self.config = ConfigManager()

        if VectorStore._logger is None:
            VectorStore._logger = Logger.get_logger()

        self.logger = VectorStore._logger

        if VectorStore._client is None:

            self.logger.info("Initializing ChromaDB...")

            VectorStore._client = chromadb.PersistentClient(
                path=str(self.config.chroma_db)
            )

        self.client = VectorStore._client

        if VectorStore._collection is None:

            VectorStore._collection = (
                self.client.get_or_create_collection(
                    name=self.COLLECTION_NAME
                )
            )

        self.collection = VectorStore._collection

    # --------------------------------------------------
    # Metadata Normalizer
    # --------------------------------------------------

    def _prepare_metadata(self, metadata):

        defaults = {

            "domain": "",
            "module": "",
            "knowledge_name": "",
            "version": "",

            "document_id": "",
            "file_name": "",
            "file_type": "",

            "platform": "",
            "category": "",
            "business_process": "",
            "document_type": "",

            "summary": "",
            "tags": "",

            "confidence": 0.0,

            "chunk_number": 0,
            "total_chunks": 0

        }

        if not isinstance(metadata, dict):
            metadata = {}

        defaults.update(metadata)

        normalized = {}

        for key, value in defaults.items():

            if value is None:

                normalized[key] = ""

            elif isinstance(value, bool):

                normalized[key] = value

            elif isinstance(value, (int, float)):

                normalized[key] = value

            elif isinstance(value, list):

                normalized[key] = ", ".join(
                    str(item)
                    for item in value
                )

            elif isinstance(value, dict):

                normalized[key] = str(value)

            else:

                normalized[key] = str(value).strip()

        return normalized

    # --------------------------------------------------
    # Save Document
    # --------------------------------------------------

    def save_document(

        self,
        doc_id,
        text,
        embedding,
        metadata=None

    ):

        try:

            if not doc_id:
                raise ValueError("Document id is empty.")

            if embedding is None:
                raise ValueError("Embedding is None.")

            if hasattr(embedding, "tolist"):
                embedding = embedding.tolist()

            metadata = self._prepare_metadata(metadata)

            try:
                self.collection.delete(
                    ids=[str(doc_id)]
                )
            except Exception:
                pass

            self.collection.add(

                ids=[str(doc_id)],

                documents=[str(text)],

                embeddings=[embedding],

                metadatas=[metadata]

            )

            self.logger.info(
                f"Vector saved: {doc_id}"
            )

            return True

        except Exception:

            self.logger.exception(
                "Vector save failed."
            )

            return False

    # --------------------------------------------------
    # Search
    # --------------------------------------------------

    def search(

        self,
        embedding,
        limit=5,
        where=None

    ):

        try:

            if embedding is None:
                return None

            if hasattr(embedding, "tolist"):
                embedding = embedding.tolist()

            query = {

                "query_embeddings": [embedding],

                "n_results": limit

            }

            if where:

                query["where"] = where

            result = self.collection.query(
                **query
            )

            if not result:
                return None

            return result

        except Exception:

            self.logger.exception(
                "Vector search failed."
            )

            return None

    # --------------------------------------------------
    # Delete
    # --------------------------------------------------

    def delete(

        self,
        doc_id

    ):

        try:

            self.collection.delete(
                ids=[str(doc_id)]
            )

            return True

        except Exception:

            self.logger.exception(
                "Vector delete failed."
            )

            return False

    # --------------------------------------------------
    # Count
    # --------------------------------------------------

    def count(self):

        try:

            return self.collection.count()

        except Exception:

            self.logger.exception(
                "Collection count failed."
            )

            return 0

    # --------------------------------------------------
    # Clear
    # --------------------------------------------------

    def clear(self):

        try:

            data = self.collection.get()

            ids = data.get(
                "ids",
                []
            )

            if ids:

                self.collection.delete(
                    ids=ids
                )

            self.logger.info(
                "Vector collection cleared."
            )

            return True

        except Exception:

            self.logger.exception(
                "Collection clear failed."
            )

            return False

    # --------------------------------------------------
    # Information
    # --------------------------------------------------

    def info(self):

        try:

            return {

                "collection_name": self.collection.name,

                "documents": self.collection.count()

            }

        except Exception:

            self.logger.exception(
                "Collection info failed."
            )

            return {}

    # --------------------------------------------------
    # Get All
    # --------------------------------------------------

    def get_all(self):

        try:

            return self.collection.get()

        except Exception:

            self.logger.exception(
                "Get all documents failed."
            )

            return None

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


import sqlite3
from contextlib import contextmanager

from Core.config_manager import ConfigManager


class SQLiteManager:
    """
    Enterprise SQLite Manager

    Responsibilities
    ----------------
    • Connection Management
    • Transactions
    • Foreign Keys
    • Generic CRUD Helpers
    • Future Schema Migrations
    """

    def __init__(self):

        self.config = ConfigManager()

        self.db_path = self.config.metadata_db

    # --------------------------------------------------

    @contextmanager
    def connection(self):

        conn = sqlite3.connect(self.db_path)

        conn.execute("PRAGMA foreign_keys = ON")

        try:

            yield conn

            conn.commit()

        except Exception:

            conn.rollback()

            raise

        finally:

            conn.close()

    # --------------------------------------------------

    def execute_non_query(
        self,
        query,
        parameters=()
    ):

        with self.connection() as conn:

            cursor = conn.cursor()

            cursor.execute(query, parameters)

    # --------------------------------------------------

    def execute_insert(
        self,
        query,
        parameters=()
    ):

        with self.connection() as conn:

            cursor = conn.cursor()

            cursor.execute(query, parameters)

            return cursor.lastrowid

    # --------------------------------------------------

    def execute_many(
        self,
        query,
        values
    ):

        with self.connection() as conn:

            cursor = conn.cursor()

            cursor.executemany(query, values)

    # --------------------------------------------------

    def fetch_one(
        self,
        query,
        parameters=()
    ):

        with self.connection() as conn:

            cursor = conn.cursor()

            cursor.execute(query, parameters)

            return cursor.fetchone()

    # --------------------------------------------------

    def fetch_all(
        self,
        query,
        parameters=()
    ):

        with self.connection() as conn:

            cursor = conn.cursor()

            cursor.execute(query, parameters)

            return cursor.fetchall()

    # --------------------------------------------------

    def table_exists(
        self,
        table_name
    ):

        row = self.fetch_one(

            """
            SELECT name

            FROM sqlite_master

            WHERE type='table'

            AND name=?

            """,

            (table_name,)

        )

        return row is not None

    # --------------------------------------------------

    def column_exists(
        self,
        table_name,
        column_name
    ):

        with self.connection() as conn:

            cursor = conn.cursor()

            cursor.execute(

                f"PRAGMA table_info({table_name})"

            )

            columns = cursor.fetchall()

        for column in columns:

            if column[1] == column_name:

                return True

        return False