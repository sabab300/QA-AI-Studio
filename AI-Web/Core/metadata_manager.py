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
import sqlite3

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
        analysis=None,
        source_type="FILE",
    ):

        domain_id = self.get_or_create_domain(domain)

        module_id = self.get_or_create_module(domain, module)

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
                last_embedded,
                domain_id,
                module_id
            )
            VALUES
            (
                ?,?,?,?,?,?,?,?,?,?,
                ?,?,?,?,?,?,?,?,?,?,
                ?,?,?,?,?,?
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

                source_type or "FILE",

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

                None,

                domain_id,

                module_id
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

        conn.row_factory = sqlite3.Row

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

        conn.row_factory = sqlite3.Row

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

        conn.row_factory = sqlite3.Row

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

        try:
            cursor.execute(
                "UPDATE api_collections SET linked_knowledge_item_id=NULL WHERE linked_knowledge_item_id=?",
                (knowledge_id,),
            )
            api_links_cleared = cursor.rowcount

            cursor.execute(
                "UPDATE discovery_variants SET linked_knowledge_item_id=NULL WHERE linked_knowledge_item_id=?",
                (knowledge_id,),
            )
            discovery_links_cleared = cursor.rowcount

            cursor.execute(
                "DELETE FROM knowledge_tags WHERE knowledge_id=?",
                (knowledge_id,),
            )
            tags_deleted = cursor.rowcount

            cursor.execute(
                "DELETE FROM embedding_queue WHERE knowledge_id=?",
                (knowledge_id,)
            )
            queue_deleted = cursor.rowcount

            cursor.execute(
                "DELETE FROM knowledge_versions WHERE knowledge_item_id=?",
                (knowledge_id,)
            )
            versions_deleted = cursor.rowcount

            cursor.execute(
                "DELETE FROM knowledge_items WHERE id=?",
                (knowledge_id,)
            )
            knowledge_deleted = cursor.rowcount

            conn.commit()

        except Exception:
            conn.rollback()
            raise

        finally:
            conn.close()

        return {

            "knowledge": knowledge_deleted,

            "versions": versions_deleted,

            "queue": queue_deleted,

            "tags": tags_deleted,

            "api_links_cleared": api_links_cleared,

            "discovery_links_cleared": discovery_links_cleared

        }

   
    # ==================================================
    # Domain Management
    # ==================================================

    def create_domain(
        self,
        name,
        description=""
    ):

        conn = self.db.get_connection()
        cursor = conn.cursor()

        now = datetime.now().isoformat()

        cursor.execute(
            """
            INSERT INTO domains
            (
                name,
                description,
                status,
                created_date,
                modified_date
            )
            VALUES
            (?,?,?,?,?)
            """,
            (
                name,
                description,
                "Active",
                now,
                now
            )
        )

        domain_id = cursor.lastrowid

        conn.commit()
        conn.close()

        return domain_id


    # ==================================================

    def list_domains(self):

        conn = self.db.get_connection()

        cursor = conn.cursor()

        cursor.execute(
            """
            SELECT name
            FROM domains
            WHERE status='Active'
            ORDER BY name
            """
        )

        rows = [row[0] for row in cursor.fetchall()]

        conn.close()

        return rows

    # ==================================================

    def get_domain(
        self,
        domain_id
    ):

        conn = self.db.get_connection()

        cursor = conn.cursor()

        cursor.execute(
            """
            SELECT *
            FROM domains
            WHERE id=?
            """,
            (
                domain_id,
            )
        )

        row = cursor.fetchone()

        conn.close()

        return row


    # ==================================================

    def update_domain(
        self,
        domain_id,
        name,
        description
    ):

        conn = self.db.get_connection()

        cursor = conn.cursor()

        now = datetime.now().isoformat()

        cursor.execute(
            """
            UPDATE domains
            SET
                name=?,
                description=?,
                modified_date=?
            WHERE id=?
            """,
            (
                name,
                description,
                now,
                domain_id
            )
        )

        conn.commit()

        conn.close()

        return True


    # ==================================================

    def delete_domain(
        self,
        domain_id
    ):

        conn = self.db.get_connection()

        cursor = conn.cursor()

        cursor.execute(
            """
            DELETE FROM domains
            WHERE id=?
            """,
            (
                domain_id,
            )
        )

        conn.commit()

        conn.close()

        return True


    # ==================================================

    def update_domain_status(
        self,
        domain_id,
        status
    ):

        conn = self.db.get_connection()

        cursor = conn.cursor()

        now = datetime.now().isoformat()

        cursor.execute(
            """
            UPDATE domains
            SET
                status=?,
                modified_date=?
            WHERE id=?
            """,
            (
                status,
                now,
                domain_id
            )
        )

        conn.commit()

        conn.close()

        return True

    # ==================================================
    # Module Management
    # ==================================================

    def create_module(
        self,
        domain_name,
        module_name,
        description=""
    ):

        conn = self.db.get_connection()
        cursor = conn.cursor()

        cursor.execute(
            """
            SELECT id
            FROM domains
            WHERE name=?
            """,
            (domain_name,)
        )

        row = cursor.fetchone()

        if not row:
            conn.close()
            raise Exception(f"Domain '{domain_name}' not found.")

        domain_id = row[0]

        now = datetime.now().isoformat()

        cursor.execute(
            """
            INSERT INTO modules
            (
                domain_id,
                name,
                description,
                status,
                created_date,
                modified_date
            )
            VALUES
            (?, ?, ?, 'Active', ?, ?)
            """,
            (
                domain_id,
                module_name,
                description,
                now,
                now
            )
        )

        module_id = cursor.lastrowid

        conn.commit()
        conn.close()

        return module_id

    
    def get_or_create_domain(self, name):
 
        name = (name or "").strip()
 
        if not name:
 
            return None
 
        conn = self.db.get_connection()
 
        cursor = conn.cursor()
 
        cursor.execute(
            "SELECT id FROM domains WHERE name=?",
            (name,)
        )
 
        row = cursor.fetchone()
 
        conn.close()
 
        if row:
 
            return row[0]
 
        return self.create_domain(name)
 
 
    def get_or_create_module(self, domain_name, module_name):
 
        domain_name = (domain_name or "").strip()
 
        module_name = (module_name or "").strip()
 
        if not domain_name or not module_name:
 
            return None
 
        # Ensure the domain exists first — create_module() requires
        # it and raises otherwise.
        self.get_or_create_domain(domain_name)
 
        conn = self.db.get_connection()
 
        cursor = conn.cursor()
 
        cursor.execute(
            """
            SELECT modules.id
            FROM modules
            JOIN domains ON domains.id = modules.domain_id
            WHERE domains.name=? AND modules.name=?
            """,
            (domain_name, module_name)
        )
 
        row = cursor.fetchone()
 
        conn.close()
 
        if row:
 
            return row[0]
 
        return self.create_module(domain_name, module_name)


    # ==================================================

    def list_modules(self, domain_name):

        conn = self.db.get_connection()
        cursor = conn.cursor()

        cursor.execute(
            """
            SELECT m.name
            FROM modules m
            JOIN domains d
                ON d.id = m.domain_id
            WHERE d.name = ?
            ORDER BY m.name
            """,
            (domain_name,)
        )

        modules = [row[0] for row in cursor.fetchall()]

        conn.close()

        return modules

    # ==================================================

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

        names = [row[0] for row in cursor.fetchall()]

        conn.close()

        return names    

    # ==================================================

    def get_module(
        self,
        module_id
    ):

        conn = self.db.get_connection()
        cursor = conn.cursor()

        cursor.execute(
            """
            SELECT *
            FROM modules
            WHERE id=?
            """,
            (
                module_id,
            )
        )

        row = cursor.fetchone()

        conn.close()

        return row


    # ==================================================

    def update_module(
        self,
        module_id,
        module_name,
        description
    ):

        conn = self.db.get_connection()
        cursor = conn.cursor()

        now = datetime.now().isoformat()

        cursor.execute(
            """
            UPDATE modules
            SET
                name=?,
                description=?,
                modified_date=?
            WHERE id=?
            """,
            (
                module_name,
                description,
                now,
                module_id
            )
        )

        conn.commit()
        conn.close()

        return True


    # ==================================================

    def delete_module(
        self,
        module_id
    ):

        conn = self.db.get_connection()
        cursor = conn.cursor()

        cursor.execute(
            """
            DELETE
            FROM modules
            WHERE id=?
            """,
            (
                module_id,
            )
        )

        conn.commit()
        conn.close()

        return True


    # ==================================================

    def update_module_status(
        self,
        module_id,
        status
    ):

        conn = self.db.get_connection()
        cursor = conn.cursor()

        now = datetime.now().isoformat()

        cursor.execute(
            """
            UPDATE modules
            SET
                status=?,
                modified_date=?
            WHERE id=?
            """,
            (
                status,
                now,
                module_id
            )
        )

        conn.commit()
        conn.close()

        return True
    
    
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

    # --------------------------------------------------
    # Update Knowledge
    # --------------------------------------------------

    def update_knowledge(

        self,

        knowledge_id,

        domain,

        module,

        knowledge_name,

        version,

        document_type,

        upload_source

    ):

        conn = self.db.get_connection()

        cursor = conn.cursor()

        cursor.execute(
            """
            UPDATE knowledge_items
            SET
                domain=?,
                module=?,
                knowledge_name=?,
                version=?,
                document_type=?,
                repository_path=?,
                modified_date=datetime('now')
            WHERE id=?
            """,
            (
                domain,
                module,
                knowledge_name,
                version,
                document_type,
                upload_source,
                knowledge_id
            )
        )

        conn.commit()

        conn.close()


    # --------------------------------------------------
    # Move Knowledge
    # --------------------------------------------------

    def move_knowledge(

        self,

        knowledge_id,

        domain,

        module

    ):

        conn = self.db.get_connection()

        cursor = conn.cursor()

        cursor.execute(
            """
            UPDATE knowledge_items
            SET
                domain=?,
                module=?,
                modified_date=datetime('now')
            WHERE id=?
            """,
            (
                domain,
                module,
                knowledge_id
            )
        )

        conn.commit()

        conn.close()

    # --------------------------------------------------
    # Update Knowledge Item
    # --------------------------------------------------
 
    def _get_current_domain(self, knowledge_id):

        conn = self.db.get_connection()

        cursor = conn.cursor()

        cursor.execute(
            "SELECT domain FROM knowledge_items WHERE id=?",
            (knowledge_id,)
        )

        row = cursor.fetchone()

        conn.close()

        return row[0] if row else ""


    def _get_current_module(self, knowledge_id):

        conn = self.db.get_connection()

        cursor = conn.cursor()

        cursor.execute(
            "SELECT module FROM knowledge_items WHERE id=?",
            (knowledge_id,)
        )

        row = cursor.fetchone()

        conn.close()

        return row[0] if row else ""


    def update_knowledge_item(self, knowledge_id, **fields):
        """
        Updates any subset of editable columns on a knowledge_items
        row. Call it like:
 
            manager.update_knowledge_item(
                5,
                domain="PSW Core",
                module="SD Export",
                knowledge_name="CESS Waiver SRS",
                version="1.1",
                document_type="SRS",
                summary="Updated summary text",
                tags="cess,export,waiver",
            )
 
        Only the fields you pass in get changed; everything else on
        the row stays as-is.
        """
 
        allowed_fields = {
            "domain",
            "module",
            "knowledge_name",
            "version",
            "document_type",
            "summary",
            "tags",
            "platform",
            "category",
            "business_process",
            "status",
        }
 
        updates = {
            key: value
            for key, value in fields.items()
            if key in allowed_fields
        }

        if not updates:

            return False

        current = self.get(knowledge_id)

        if current is None:
            return False

        resolved_domain = updates.get("domain", current["domain"])
        resolved_module = updates.get("module", current["module"])
        resolved_name = updates.get("knowledge_name", current["knowledge_name"])
        resolved_version = updates.get("version", current["version"])

        # Physical repository_path is intentionally immutable. The logical
        # hierarchy is maintained separately and is safe to edit.
        updates["knowledge_path"] = "/".join(
            [resolved_domain, resolved_module, resolved_name, resolved_version]
        )

        if "domain" in updates or "module" in updates:
            updates["domain_id"] = self.get_or_create_domain(resolved_domain)
            updates["module_id"] = self.get_or_create_module(
                resolved_domain, resolved_module
            )

        conn = self.db.get_connection()
        cursor = conn.cursor()

        try:
            set_clause = ", ".join(f"{key}=?" for key in updates.keys())
            values = list(updates.values())
            values.extend([datetime.now().isoformat(), knowledge_id])

            cursor.execute(
                f"""
                UPDATE knowledge_items
                SET {set_clause}, modified_date=?
                WHERE id=?
                """,
                values,
            )

            if "version" in updates:
                cursor.execute(
                    "UPDATE knowledge_versions SET version=? WHERE knowledge_item_id=?",
                    (resolved_version, knowledge_id),
                )

            conn.commit()
            return cursor.rowcount >= 0

        except Exception:
            conn.rollback()
            raise

        finally:
            conn.close()
