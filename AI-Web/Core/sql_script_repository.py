# Create: AI/Core/sql_script_repository.py

"""
QA AI Studio
SQL Script Repository

Version: 1.0

QA-AI-STUDIO-FINAL-ARCHITECTURE-UI-AUTOMATION-COMPLETION-V4 Rule 6:
"Upload SQL Script" — a physically-uploaded, pre-written, read-only
.sql FILE bound to a Test Cases (TC-*) folder. This mirrors
Core/api_collection_repository.py's Rule 5 TC-folder storage pattern
(upload -> file under "<TC folder>/SQL-TC-*/" -> DB row keyed by
test_case_version_id, see Database/schema.py's SQL_SCRIPTS_TABLE
comment) but is far simpler: a SQL script is a single plain-text file,
not a nested JSON structure to flatten into rows.

This is a NEW, ADDITIVE capability. It does not touch, duplicate, or
replace the existing per-test-case inline SQL mechanism already
implemented in Core/automation_web_repository.py's SqlAutomationWeb
class (AI-generates SQL for one test case's assertion, stores it as a
JSON envelope directly on test_cases.automation_script). The two sit
side by side, exactly the relationship Rule 5 already gives API
Collections vs. per-test-case AI generation:

    SqlAutomationWeb        -> "AI-generate/store SQL for THIS
                                 test case's own assertion"
    SqlScriptRepository      -> "upload a real, pre-written .sql
                                 script and bind it to a TC-* folder"

Per Rule 6's explicit instruction, this module does NOT build its own
SQL execution engine — Validate and Execute both call straight into
the already-working Core/sql_automation_runner.py
(validate_readonly_sql() / execute_query()), the same read-only engine
SqlAutomationWeb already uses. Uploading a script never executes it —
upload only runs the pure, connection-less validate_readonly_sql()
parse/regex check as a safety gate, exactly the rule that governs
Execute later.
"""

import hashlib
import time
from datetime import datetime
from pathlib import Path

from Database.db_manager import DatabaseManager
from Core.logger import Logger
from Core.sql_automation_runner import SqlAutomationRunner, validate_readonly_sql
from Core.sql_environment_config import SqlEnvironmentConfig


class SqlScriptRepository:

    def __init__(self):

        self.logger = Logger.get_logger()

        self.db = DatabaseManager()

        self.runner = SqlAutomationRunner()

        self.env = SqlEnvironmentConfig()

    # ================================================================
    # Upload
    # ================================================================

    def upload_script(self, file_path, test_case_version_id, display_name=None):
        """
        Validates, physically stores, and binds a .sql file to a real
        TC-* folder. Re-uploading under the SAME test_case_version_id
        updates the existing row/file in place (one script per TC-*
        folder — see _save_script()), matching how
        ApiCollectionRepository._save_collection() treats a re-import
        under the same TC folder as a refresh, not a duplicate.

        Returns:
            {
                "success": bool,
                "script_id": int | None,
                "test_case_version_id": int | None,
                "repository_path": str | None,
                "display_name": str | None,
                "script_already_existed": bool,
                "error": str | None,
            }
        """

        summary = {
            "success": False,
            "script_id": None,
            "test_case_version_id": None,
            "repository_path": None,
            "display_name": None,
            "script_already_existed": False,
            "error": None,
        }

        if not test_case_version_id:

            summary["error"] = "A Test Cases (TC-*) folder must be selected."

            return summary

        try:

            source_text = Path(file_path).read_text(encoding="utf-8")

        except Exception as ex:

            summary["error"] = f"Could not read file: {ex}"

            return summary

        try:

            # Upload-time safety gate: rejects anything that isn't a
            # single read-only SELECT/WITH statement up front. This
            # never opens a database connection — it is a pure
            # parse/regex check, so "upload" genuinely never executes
            # SQL, per Rule 6.
            validate_readonly_sql(source_text)

        except Exception as ex:

            summary["error"] = str(ex)

            return summary

        try:

            version_row, source_row = self._test_case_version_context(
                test_case_version_id
            )

            source_bytes = Path(file_path).read_bytes()

            source_name = Path(file_path).name

            managed = self._write_managed_script_file_in_tc_folder(
                version_row["folder_path"], source_bytes, source_name,
            )

            resolved_display_name = display_name or Path(source_name).stem

            script_id, already_existed = self._save_script(
                test_case_version_id=int(test_case_version_id),
                display_name=resolved_display_name,
                original_filename=source_name,
                repository_path=managed["repository_path"],
            )

            summary.update({
                "success": True,
                "script_id": script_id,
                "test_case_version_id": int(test_case_version_id),
                "repository_path": managed["repository_path"],
                "display_name": resolved_display_name,
                "script_already_existed": already_existed,
            })

        except Exception as ex:

            self.logger.exception("SQL script upload failed.")

            summary["error"] = str(ex)

        return summary

    # ================================================================
    # TC-* folder resolution + physical file placement
    # (mirrors ApiCollectionRepository's own private helpers exactly)
    # ================================================================

    def _test_case_version_context(self, test_case_version_id):
        """
        Returns (version_row, source_knowledge_item_row) for a real
        TC-* folder — version_row["folder_path"] is the actual
        physical TC folder to file the "SQL-TC-*" automation subfolder
        under. Raises ValueError if the id doesn't resolve, so a bad
        selection fails loudly instead of silently filing under a
        placeholder path.
        """

        conn = self.db.get_connection()

        conn.row_factory = self._dict_factory

        cursor = conn.cursor()

        cursor.execute(
            "SELECT * FROM test_case_versions WHERE id = ?",
            (int(test_case_version_id),),
        )

        version_row = cursor.fetchone()

        if not version_row:

            conn.close()

            raise ValueError(
                f"Test Cases folder (id={test_case_version_id}) was not found."
            )

        source_row = None

        if version_row.get("source_knowledge_id"):

            cursor.execute(
                "SELECT * FROM knowledge_items WHERE id = ?",
                (version_row["source_knowledge_id"],),
            )

            source_row = cursor.fetchone()

        conn.close()

        if not Path(version_row["folder_path"] or "").is_dir():

            raise ValueError(
                "The selected Test Cases folder is unavailable on disk."
            )

        return version_row, source_row

    def _write_managed_script_file_in_tc_folder(
        self, tc_folder_path, source_bytes, source_name,
    ):
        """
        Creates (if missing) the "SQL-TC-*" automation subfolder
        directly inside the given TC-* folder and writes source_bytes
        into it as source_name, UNCHANGED — same "do not unnecessarily
        rewrite the original" rule Rule 5 applies to API Collections.

        Collision-safe the same way: if a file of that name already
        exists there with DIFFERENT content, the new file is saved
        under a disambiguated name instead of silently overwriting or
        colliding; re-uploading the SAME bytes just refreshes the
        existing file in place.
        """

        tc_folder = Path(tc_folder_path)

        automation_folder = tc_folder / f"SQL-{tc_folder.name}"

        automation_folder.mkdir(parents=True, exist_ok=True)

        base_name = source_name or "sql-script.sql"

        stem = Path(base_name).stem

        suffix = Path(base_name).suffix or ".sql"

        destination = automation_folder / base_name

        counter = 2

        while destination.exists() and destination.read_bytes() != source_bytes:

            destination = automation_folder / f"{stem} ({counter}){suffix}"

            counter += 1

        destination.write_bytes(source_bytes)

        return {
            "repository_path": str(destination),
            "file_name": destination.name,
            "sha256": hashlib.sha256(source_bytes).hexdigest(),
            "file_size": len(source_bytes),
        }

    # ================================================================
    # Persistence
    # ================================================================

    def _save_script(
        self, test_case_version_id, display_name, original_filename,
        repository_path,
    ):
        """
        Get-or-create keyed by test_case_version_id (UNIQUE in
        SQL_SCRIPTS_TABLE) — a second upload under the same TC-*
        folder UPDATES the existing row (and, via
        _write_managed_script_file_in_tc_folder()'s own collision
        rule, refreshes the physical file) instead of creating a
        second, ambiguous binding for the same folder.
        """

        conn = self.db.get_connection()

        cursor = conn.cursor()

        now = datetime.now().isoformat()

        cursor.execute(
            "SELECT id FROM sql_scripts WHERE test_case_version_id = ?",
            (test_case_version_id,),
        )

        row = cursor.fetchone()

        if row:

            script_id = row[0]

            cursor.execute(
                """
                UPDATE sql_scripts
                SET display_name = ?, original_filename = ?,
                    repository_path = ?, status = 'Active',
                    modified_date = ?
                WHERE id = ?
                """,
                (display_name, original_filename, repository_path, now, script_id),
            )

            already_existed = True

        else:

            cursor.execute(
                """
                INSERT INTO sql_scripts
                (test_case_version_id, display_name, original_filename,
                 repository_path, status, created_date, modified_date)
                VALUES (?, ?, ?, ?, 'Active', ?, ?)
                """,
                (
                    test_case_version_id, display_name, original_filename,
                    repository_path, now, now,
                ),
            )

            script_id = cursor.lastrowid

            already_existed = False

        conn.commit()

        conn.close()

        return script_id, already_existed

    # ================================================================
    # Read / list
    # ================================================================

    def list_scripts(self):

        conn = self.db.get_connection()

        conn.row_factory = self._dict_factory

        cursor = conn.cursor()

        cursor.execute(
            """
            SELECT s.*, tcv.folder_path AS tc_folder_path,
                   tcv.version_number AS tc_version_number,
                   ki.domain AS domain, ki.module AS module,
                   ki.knowledge_name AS knowledge_name
            FROM sql_scripts s
            LEFT JOIN test_case_versions tcv ON tcv.id = s.test_case_version_id
            LEFT JOIN knowledge_items ki ON ki.id = tcv.source_knowledge_id
            ORDER BY s.modified_date DESC
            """
        )

        rows = cursor.fetchall()

        conn.close()

        return rows

    def get_script(self, script_id):

        conn = self.db.get_connection()

        conn.row_factory = self._dict_factory

        cursor = conn.cursor()

        cursor.execute(
            """
            SELECT s.*, tcv.folder_path AS tc_folder_path,
                   tcv.version_number AS tc_version_number,
                   ki.domain AS domain, ki.module AS module,
                   ki.knowledge_name AS knowledge_name
            FROM sql_scripts s
            LEFT JOIN test_case_versions tcv ON tcv.id = s.test_case_version_id
            LEFT JOIN knowledge_items ki ON ki.id = tcv.source_knowledge_id
            WHERE s.id = ?
            """,
            (int(script_id),),
        )

        row = cursor.fetchone()

        conn.close()

        if not row:

            return None

        row = dict(row)

        try:

            row["content"] = Path(row["repository_path"]).read_text(encoding="utf-8")

        except Exception:

            row["content"] = None

        return row

    def get_endpoints_for_test_case_version(self, test_case_version_id):
        """
        Kept named consistently with ApiCollectionRepository's own
        `get_endpoints_for_test_case_version()` lookup pattern — used
        the same way, by generation/grounding code that needs to know
        whether a real, uploaded SQL script already exists for a given
        TC-* folder before falling back to AI generation.
        """

        if not test_case_version_id:

            return None

        conn = self.db.get_connection()

        conn.row_factory = self._dict_factory

        cursor = conn.cursor()

        cursor.execute(
            "SELECT * FROM sql_scripts WHERE test_case_version_id = ? AND status = 'Active'",
            (int(test_case_version_id),),
        )

        row = cursor.fetchone()

        conn.close()

        return dict(row) if row else None

    # ================================================================
    # Unbind / delete
    # ================================================================

    def delete_script(self, script_id):
        """
        Soft-delete/unbind: flips status to 'Inactive' rather than
        removing the row or the physical file, matching
        api_collections' own status column and Rule 6's requirement
        that Delete/Unbind never silently destroys the uploaded asset.
        """

        conn = self.db.get_connection()

        cursor = conn.cursor()

        cursor.execute(
            "UPDATE sql_scripts SET status = 'Inactive', modified_date = ? WHERE id = ?",
            (datetime.now().isoformat(), int(script_id)),
        )

        updated = cursor.rowcount > 0

        conn.commit()

        conn.close()

        return updated

    # ================================================================
    # Validate / Execute — reuse SqlAutomationRunner, never duplicate it
    # ================================================================

    def _environment_label(self, profile):
        """
        A non-secret DISPLAY LABEL for SQL History — Rule 6: "Never
        persist credentials/secrets in execution history." Only the DB
        type plus a database name/file (never host, port, username, or
        password) ever gets built into this string, and it never
        touches AutomationExecutionRepository directly — record_sql_
        script_run() only ever receives this label, never the profile.
        """

        db_type = str((profile or {}).get("sql_db_type") or "").strip() or "unknown"

        if db_type == "sqlite":

            raw_path = str((profile or {}).get("sql_sqlite_path") or "").strip()

            label = Path(raw_path).name if raw_path else ""

        else:

            label = str((profile or {}).get("sql_database") or "").strip()

        return f"{db_type}:{label}" if label else db_type

    def _record_history(
        self, script, kind, result,
        executed_by_user_id=None, executed_by_username=None,
    ):
        """
        Rule 6 — SQL History. Best-effort: a history-write failure must
        never turn a successful/failed Validate or Execute into an
        HTTP error the operator didn't cause, so this only logs.
        """

        try:

            from Core.automation_execution_repository import AutomationExecutionRepository

            AutomationExecutionRepository().record_sql_script_run(
                sql_script_id=script.get("id"),
                test_case_version_id=script.get("test_case_version_id"),
                kind=kind,
                result=result,
                domain=script.get("domain"),
                module=script.get("module"),
                knowledge_name=script.get("knowledge_name"),
                environment_name=self._environment_label(self.env.load()),
                executed_by_user_id=executed_by_user_id,
                executed_by_username=executed_by_username,
            )

        except Exception:

            self.logger.exception(
                f"Failed to record SQL History for script_id={script.get('id')} ({kind})."
            )

    def validate_script(
        self, script_id, executed_by_user_id=None, executed_by_username=None,
    ):

        script = self.get_script(script_id)

        if not script:

            raise ValueError(f"SQL script {script_id} not found.")

        started = time.monotonic()

        try:

            cleaned = validate_readonly_sql(script.get("content") or "")

            result = {
                "success": True, "cleaned_sql": cleaned, "error": None,
                "duration_seconds": round(time.monotonic() - started, 4),
            }

        except Exception as ex:

            result = {
                "success": False, "cleaned_sql": None, "error": str(ex),
                "duration_seconds": round(time.monotonic() - started, 4),
            }

        self._record_history(
            script, "Validate", result,
            executed_by_user_id=executed_by_user_id,
            executed_by_username=executed_by_username,
        )

        return result

    def execute_script(
        self, script_id, executed_by_user_id=None, executed_by_username=None,
    ):

        script = self.get_script(script_id)

        if not script:

            raise ValueError(f"SQL script {script_id} not found.")

        profile = self.env.load()

        if not SqlEnvironmentConfig._is_ready(profile):

            result = {
                "success": False, "columns": [], "rows": [], "row_count": 0,
                "truncated": False, "duration_seconds": 0,
                "error": (
                    "SQL Test Environment is not configured yet. "
                    "Set it up under Test Environment Setting first."
                ),
            }

        else:

            result = self.runner.execute_query(profile, script.get("content") or "")

        self._record_history(
            script, "Execute", result,
            executed_by_user_id=executed_by_user_id,
            executed_by_username=executed_by_username,
        )

        return result

    def list_history(self, script_id, limit=50):
        """
        Rule 6 — the "History" surface for one uploaded SQL script:
        past Validate/Execute attempts, most recent first. Thin
        pass-through to the SAME automation_runs table/reader every
        other automation type's History already uses (see
        AutomationExecutionRepository.list_runs()) — no second store.
        """

        from Core.automation_execution_repository import AutomationExecutionRepository

        return AutomationExecutionRepository().list_runs(
            sql_script_id=script_id, limit=limit, offset=0,
        )

    @staticmethod
    def _dict_factory(cursor, row):

        return {
            column[0]: value
            for column, value in zip(cursor.description, row)
        }
