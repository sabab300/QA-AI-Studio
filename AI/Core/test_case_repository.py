# Create: AI/Core/test_case_repository.py

"""
QA AI Studio
Test Case Repository

Version: 1.0

Persists individual generated test cases (TC001, TC002...) so they
can be listed, statused, and executed from QA Automation — instead
of only ever existing inside an exported Excel/Word/PDF file.

Uses the same physical database (Database/metadata.db) and the same
flat domain/module TEXT columns already used by knowledge_items,
via Database.db_manager_v3.DatabaseManager (the connection object
already used successfully by metadata_manager.py).

Self-initializing: creates its own table on first use, following
the same pattern as other Core modules in this project (e.g.
memory_manager.py's ALTER TABLE ADD COLUMN pattern) rather than
touching either of the two existing (and currently inconsistent)
schema files.
"""

from datetime import datetime

from Database.db_manager import DatabaseManager
from Core.logger import Logger


class TestCaseRepository:

    def __init__(self):

        self.db = DatabaseManager()

        self.logger = Logger.get_logger()

        self.ensure_schema()

    # --------------------------------------------------
    # Domain/Module ID resolution
    # --------------------------------------------------

    def _resolve_ids(self, domain, module):
        """
        Lazy import to avoid any import-order issues between this
        module and metadata_manager.py — both are Core modules and
        neither currently imports the other, but this keeps it safe
        regardless of future changes.
        """

        from Core.metadata_manager import MetadataManager

        metadata = MetadataManager()

        domain_id = metadata.get_or_create_domain(domain)

        module_id = metadata.get_or_create_module(domain, module)

        return domain_id, module_id

    # --------------------------------------------------
    # Schema
    # --------------------------------------------------

    def ensure_schema(self):

        conn = self.db.get_connection()

        cursor = conn.cursor()

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS test_cases
            (
                id INTEGER PRIMARY KEY AUTOINCREMENT,

                tc_number TEXT NOT NULL,

                domain TEXT NOT NULL,
                module TEXT NOT NULL,
                knowledge_name TEXT NOT NULL,
                version TEXT,

                domain_id INTEGER,
                module_id INTEGER,

                scenario TEXT,
                importance TEXT,
                test_type TEXT,
                test_case TEXT,
                pre_conditions TEXT,
                steps TEXT,
                expected_result TEXT,

                status TEXT DEFAULT 'Manual',

                automation_type TEXT DEFAULT 'None',
                automation_script TEXT,

                last_result TEXT DEFAULT 'Not Run',
                last_run_date TEXT,

                created_date TEXT,
                modified_date TEXT,

                UNIQUE(domain, module, knowledge_name, tc_number)
            )
            """
        )

        cursor.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_test_cases_scope
            ON test_cases(domain, module, knowledge_name)
            """
        )

        # For a test_cases table that already existed before these
        # columns were added — CREATE TABLE IF NOT EXISTS above is
        # a no-op on an existing table, so this catches it.
        for migration in (
            "ALTER TABLE test_cases ADD COLUMN domain_id INTEGER",
            "ALTER TABLE test_cases ADD COLUMN module_id INTEGER",
        ):

            try:

                cursor.execute(migration)

            except Exception:

                pass

        conn.commit()

        conn.close()

    # --------------------------------------------------
    # Save generated cases (bulk, from TestCaseGenerator)
    # --------------------------------------------------

    def save_generated_cases(

        self,

        domain,

        module,

        knowledge_name,

        version,

        rows

    ):

        if not rows:

            return []

        domain_id, module_id = self._resolve_ids(domain, module)

        conn = self.db.get_connection()

        cursor = conn.cursor()

        now = datetime.now().isoformat()

        next_number = self._next_tc_number(
            cursor,
            domain,
            module,
            knowledge_name
        )

        saved_ids = []

        for row in rows:

            tc_number = f"TC{next_number:03d}"

            try:

                cursor.execute(
                    """
                    INSERT INTO test_cases
                    (
                        tc_number, domain, module, knowledge_name, version,
                        domain_id, module_id,
                        scenario, importance, test_type, test_case,
                        pre_conditions, steps, expected_result,
                        status, automation_type, last_result,
                        created_date, modified_date
                    )
                    VALUES
                    (?,?,?,?,?, ?,?, ?,?,?,?, ?,?,?, 'Manual','None','Not Run', ?,?)
                    """,
                    (
                        tc_number,
                        domain,
                        module,
                        knowledge_name,
                        version or "",
                        domain_id,
                        module_id,
                        row.get("scenario", ""),
                        row.get("importance", ""),
                        row.get("test_type", ""),
                        row.get("test_case", ""),
                        row.get("pre_conditions", ""),
                        row.get("steps", ""),
                        row.get("expected_result", ""),
                        now,
                        now,
                    )
                )

                saved_ids.append(cursor.lastrowid)

                next_number += 1

            except Exception:

                # Extremely unlikely UNIQUE collision (concurrent
                # generation) — skip this row rather than fail
                # the whole batch.
                self.logger.exception(
                    f"Could not save test case {tc_number}, skipping."
                )

        conn.commit()

        conn.close()

        self.logger.info(
            f"Saved {len(saved_ids)} test case(s) to the database "
            f"for {domain} / {module} / {knowledge_name}."
        )

        return saved_ids


    def _next_tc_number(self, cursor, domain, module, knowledge_name):

        cursor.execute(
            """
            SELECT tc_number
            FROM test_cases
            WHERE domain=? AND module=? AND knowledge_name=?
            ORDER BY id DESC
            LIMIT 1
            """,
            (domain, module, knowledge_name)
        )

        row = cursor.fetchone()

        if not row:

            return 1

        try:

            return int(row[0].replace("TC", "")) + 1

        except (ValueError, AttributeError):

            return 1


    # --------------------------------------------------
    # List
    # --------------------------------------------------

    def list_test_cases(self, domain, module, knowledge_name):

        conn = self.db.get_connection()

        conn.row_factory = self._dict_factory

        cursor = conn.cursor()

        cursor.execute(
            """
            SELECT *
            FROM test_cases
            WHERE domain=? AND module=? AND knowledge_name=?
            ORDER BY
                CAST(REPLACE(tc_number,'TC','') AS INTEGER)
            """,
            (domain, module, knowledge_name)
        )

        rows = cursor.fetchall()

        conn.close()

        return rows


    @staticmethod
    def _dict_factory(cursor, row):

        columns = [col[0] for col in cursor.description]

        return {
            columns[i]: row[i]
            for i in range(len(columns))
        }


    # --------------------------------------------------
    # Updates
    # --------------------------------------------------

    def update_status(self, test_case_id, status):

        self._update_field(
            test_case_id,
            "status",
            status
        )


    def update_automation(

        self,

        test_case_id,

        automation_type,

        automation_script=None

    ):

        conn = self.db.get_connection()

        cursor = conn.cursor()

        now = datetime.now().isoformat()

        cursor.execute(
            """
            UPDATE test_cases
            SET automation_type=?,
                automation_script=COALESCE(?, automation_script),
                status='Automated',
                modified_date=?
            WHERE id=?
            """,
            (
                automation_type,
                automation_script,
                now,
                test_case_id,
            )
        )

        conn.commit()

        conn.close()


    def update_result(self, test_case_id, result):

        conn = self.db.get_connection()

        cursor = conn.cursor()

        now = datetime.now().isoformat()

        cursor.execute(
            """
            UPDATE test_cases
            SET last_result=?,
                last_run_date=?,
                modified_date=?
            WHERE id=?
            """,
            (
                result,
                now,
                now,
                test_case_id,
            )
        )

        conn.commit()

        conn.close()


    def get_test_case(self, test_case_id):

        conn = self.db.get_connection()

        conn.row_factory = self._dict_factory

        cursor = conn.cursor()

        cursor.execute(
            "SELECT * FROM test_cases WHERE id=?",
            (test_case_id,)
        )

        row = cursor.fetchone()

        conn.close()

        return row


    def _update_field(self, test_case_id, field, value):

        conn = self.db.get_connection()

        cursor = conn.cursor()

        now = datetime.now().isoformat()

        cursor.execute(
            f"UPDATE test_cases SET {field}=?, modified_date=? WHERE id=?",
            (value, now, test_case_id)
        )

        conn.commit()

        conn.close()