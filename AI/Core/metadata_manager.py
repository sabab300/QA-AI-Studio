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