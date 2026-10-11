# Create: AI-Web/Core/automation_execution_repository.py

"""
QA AI Studio — Web
Automation Execution Repository

Version: 1.0

WEB PORT ADDITION — new capability, not a port of an existing Desktop
class. Confirmed by reading the Desktop reference in full (see
App/UI/QAAutomation/test_execution_page.py and
Core/test_case_repository.py.update_result()): QA AI Studio has NEVER
persisted a history of automation runs, on either Desktop or Web —
every Execute simply overwrites test_cases.last_result /
last_run_date in place, and the only "log" is an in-memory, never-
persisted QTextEdit that disappears when the desktop app closes.

For a background/job-based Web execution model (see
automation_web_repository.py's TestCasesWeb.start_execution(), which
can't block an HTTP request for up to ~2 minutes) this in-memory-only
model is not just a missing feature but a real defect: the ONLY
record of a run's job_id lived in a module-level Python dict, so it
was lost on every server restart (a run genuinely "Running" when the
process died would stay "Running" forever from the API's point of
view — an orphaned job with no way to ever resolve), and there was no
way to list past runs, re-run one, or see anything beyond the single
most recent result per test case.

This repository is the fix: one row per Execute attempt, persisted in
the same physical database (Database/metadata.db) every other Core
repository in this project already uses, following the exact same
self-initializing-table pattern as Core/test_case_repository.py
(ensure_schema() creates its own table on first use).

Table `automation_runs` intentionally does NOT touch test_cases at
all — a test case's own `status` / `automation_type` / `last_result`
columns keep meaning exactly what they always have (see
test_case_repository.py); this is purely an additive, parallel run
log keyed by test_case_id.
"""

import json
import sqlite3
import uuid
from datetime import datetime
from pathlib import Path

from Database.db_manager import DatabaseManager
from Core.logger import Logger


# Statuses a run can be in. "Queued"/"Running" are always transient —
# see reconcile_stale_running_on_startup(), which is the direct fix
# for "a run stuck at Running forever after a server restart/crash"
# (a fresh process can never have a real background thread still
# executing a run row that predates it).
TRANSIENT_STATUSES = ("Queued", "Running")

TERMINAL_STATUSES = ("Passed", "Failed", "Error", "Cancelled", "Stopped")


class AutomationExecutionRepository:

    def __init__(self):

        self.db = DatabaseManager()

        self.logger = Logger.get_logger()

        self.ensure_schema()

    # --------------------------------------------------
    # Schema
    # --------------------------------------------------

    # Rule 6 (QA-AI-STUDIO-FINAL-ARCHITECTURE-UI-AUTOMATION-COMPLETION-
    # V4) additive columns — a SQL Script Upload run (Validate/Execute
    # against Core/sql_script_repository.py's uploaded .sql file) is
    # scoped to a whole TC-* folder (sql_script_id + test_case_version_
    # id), not one single test_case row the way Playwright/API/inline-
    # SQL runs are. Every one of these stays NULL on every pre-existing
    # run row, and every pre-existing column keeps meaning exactly what
    # it always has — see _relax_test_case_id_not_null() below for why
    # test_case_id itself had to change from NOT NULL to nullable.
    _RULE6_COLUMNS = (
        ("sql_script_id", "INTEGER"),
        ("test_case_version_id", "INTEGER"),
        ("sql_run_kind", "TEXT"),
        ("environment_name", "TEXT"),
        ("rows_returned", "INTEGER"),
    )

    def ensure_schema(self):

        conn = self.db.get_connection()

        cursor = conn.cursor()

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS automation_runs
            (
                id INTEGER PRIMARY KEY AUTOINCREMENT,

                run_uuid TEXT NOT NULL UNIQUE,

                test_case_id INTEGER,
                tc_number TEXT,
                domain TEXT,
                module TEXT,
                knowledge_name TEXT,
                automation_type TEXT,

                status TEXT NOT NULL DEFAULT 'Queued',
                outcome TEXT,
                success INTEGER,
                return_code INTEGER,
                duration_seconds REAL,

                error_message TEXT,
                final_message TEXT,
                runtime_activity_json TEXT,
                stdout TEXT,
                stderr TEXT,

                script_path TEXT,
                screenshot_path TEXT,

                slow_mo_ms INTEGER,
                timeout_ms INTEGER,

                executed_by_user_id INTEGER,
                executed_by_username TEXT,

                re_run_of INTEGER,

                sql_script_id INTEGER,
                test_case_version_id INTEGER,
                sql_run_kind TEXT,
                environment_name TEXT,
                rows_returned INTEGER,

                started_at TEXT,
                finished_at TEXT,
                created_date TEXT NOT NULL
            )
            """
        )

        columns_info = cursor.execute("PRAGMA table_info(automation_runs)").fetchall()
        columns = {row[1] for row in columns_info}
        # row layout: (cid, name, type, notnull, dflt_value, pk)
        test_case_id_not_null = any(
            row[1] == "test_case_id" and row[3] for row in columns_info
        )

        if "final_message" not in columns:
            cursor.execute("ALTER TABLE automation_runs ADD COLUMN final_message TEXT")
        if "runtime_activity_json" not in columns:
            cursor.execute("ALTER TABLE automation_runs ADD COLUMN runtime_activity_json TEXT")
        for column_name, column_type in self._RULE6_COLUMNS:
            if column_name not in columns:
                cursor.execute(f"ALTER TABLE automation_runs ADD COLUMN {column_name} {column_type}")

        if test_case_id_not_null:
            self._relax_test_case_id_not_null(conn, cursor)

        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_automation_runs_tc "
            "ON automation_runs(test_case_id)"
        )

        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_automation_runs_scope "
            "ON automation_runs(domain, module, knowledge_name)"
        )

        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_automation_runs_status "
            "ON automation_runs(status)"
        )

        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_automation_runs_sql_script "
            "ON automation_runs(sql_script_id)"
        )

        from Shared.document_hierarchy import DocumentHierarchy
        DocumentHierarchy(conn).ensure_schema()
        conn.commit()

        conn.close()

    def _relax_test_case_id_not_null(self, conn, cursor):
        """
        One-time migration for a pre-Rule-6 database: SQLite has no
        ALTER COLUMN, so relaxing test_case_id from NOT NULL to
        nullable means the standard SQLite recipe — rename the old
        table aside, create the new one with the desired schema, copy
        every existing row across by explicit column list (preserving
        `id` values, so re_run_of and anything else that references a
        run by id stays valid), then drop the old table. Wrapped in the
        caller's own transaction (ensure_schema() commits once at the
        end); nothing here commits early, so this is all-or-nothing.
        """

        self.logger.warning(
            "Migrating automation_runs.test_case_id to nullable "
            "(Rule 6 SQL History: a script/TC-folder-level run has no "
            "single test_case_id)."
        )

        cursor.execute("PRAGMA table_info(automation_runs)")
        existing_cols = [row[1] for row in cursor.fetchall()]

        cursor.execute("ALTER TABLE automation_runs RENAME TO automation_runs_pre_rule6")

        cursor.execute(
            """
            CREATE TABLE automation_runs
            (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_uuid TEXT NOT NULL UNIQUE,
                test_case_id INTEGER,
                tc_number TEXT,
                domain TEXT,
                module TEXT,
                knowledge_name TEXT,
                automation_type TEXT,
                status TEXT NOT NULL DEFAULT 'Queued',
                outcome TEXT,
                success INTEGER,
                return_code INTEGER,
                duration_seconds REAL,
                error_message TEXT,
                final_message TEXT,
                runtime_activity_json TEXT,
                stdout TEXT,
                stderr TEXT,
                script_path TEXT,
                screenshot_path TEXT,
                slow_mo_ms INTEGER,
                timeout_ms INTEGER,
                executed_by_user_id INTEGER,
                executed_by_username TEXT,
                re_run_of INTEGER,
                sql_script_id INTEGER,
                test_case_version_id INTEGER,
                sql_run_kind TEXT,
                environment_name TEXT,
                rows_returned INTEGER,
                started_at TEXT,
                finished_at TEXT,
                created_date TEXT NOT NULL
            )
            """
        )

        # Only copy columns that actually existed on the old table —
        # the Rule 6 columns above are brand new on a pre-migration
        # database and simply default to NULL on every copied row.
        new_cols = {
            "id", "run_uuid", "test_case_id", "tc_number", "domain", "module",
            "knowledge_name", "automation_type", "status", "outcome", "success",
            "return_code", "duration_seconds", "error_message", "final_message",
            "runtime_activity_json", "stdout", "stderr", "script_path",
            "screenshot_path", "slow_mo_ms", "timeout_ms", "executed_by_user_id",
            "executed_by_username", "re_run_of", "sql_script_id",
            "test_case_version_id", "sql_run_kind", "environment_name",
            "rows_returned", "started_at", "finished_at", "created_date",
        }
        copy_cols = [c for c in existing_cols if c in new_cols]
        col_list = ",".join(copy_cols)

        cursor.execute(
            f"INSERT INTO automation_runs ({col_list}) "
            f"SELECT {col_list} FROM automation_runs_pre_rule6"
        )

        cursor.execute("DROP TABLE automation_runs_pre_rule6")

    # --------------------------------------------------
    # Startup reconciliation
    # --------------------------------------------------

    def reconcile_stale_running_on_startup(self):
        """
        Call ONCE, at application startup (see Web/main.py) — never
        per-request, since within a single live process a row can
        legitimately still be genuinely "Running" on a background
        thread. At startup, though, no background thread from a
        previous process can possibly still be alive, so any row
        still "Queued"/"Running" at this exact moment is provably
        orphaned (the process that was running it is gone).

        Returns the number of rows reconciled, for the startup log.
        """

        conn = self.db.get_connection()

        cursor = conn.cursor()

        now = datetime.now().isoformat()

        cursor.execute(
            """
            UPDATE automation_runs
            SET status='Error',
                outcome='Error',
                success=0,
                error_message=COALESCE(error_message, '') ||
                    CASE WHEN error_message IS NULL OR error_message='' THEN '' ELSE ' ' END ||
                    'Interrupted: the server restarted while this run was in progress.',
                finished_at=COALESCE(finished_at, ?)
            WHERE status IN ('Queued', 'Running')
            """,
            (now,),
        )

        reconciled = cursor.rowcount

        conn.commit()

        conn.close()

        if reconciled:

            self.logger.warning(
                f"Reconciled {reconciled} orphaned automation run(s) "
                f"left 'Queued'/'Running' from a previous server "
                f"process."
            )

        return reconciled

    # --------------------------------------------------
    # Create / update
    # --------------------------------------------------

    def create_run(
        self, test_case, executed_by_user_id=None,
        executed_by_username=None, re_run_of=None,
    ):

        run_uuid = uuid.uuid4().hex

        now = datetime.now().isoformat()

        conn = self.db.get_connection()

        cursor = conn.cursor()

        cursor.execute(
            """
            INSERT INTO automation_runs
            (
                run_uuid, test_case_id, tc_number, domain, module,
                knowledge_name, automation_type, status,
                executed_by_user_id, executed_by_username, re_run_of,
                created_date
            )
            VALUES (?,?,?,?,?, ?,?, 'Queued', ?,?,?, ?)
            """,
            (
                run_uuid,
                test_case.get("id"),
                test_case.get("tc_number"),
                test_case.get("domain"),
                test_case.get("module"),
                test_case.get("knowledge_name"),
                test_case.get("automation_type"),
                executed_by_user_id,
                executed_by_username,
                re_run_of,
                now,
            ),
        )

        conn.commit()

        conn.close()

        return self.get_run(run_uuid)

    def mark_running(self, run_uuid):

        conn = self.db.get_connection()

        cursor = conn.cursor()

        cursor.execute(
            "UPDATE automation_runs SET status='Running', started_at=? "
            "WHERE run_uuid=?",
            (datetime.now().isoformat(), run_uuid),
        )

        conn.commit()

        conn.close()

    # Cap stored stdout/stderr so a runaway/looping script (e.g. one
    # stuck printing inside a loop until the timeout kills it) can't
    # grow the database without bound.
    _MAX_LOG_CHARS = 200_000

    def _truncate_log(self, text):

        if text is None:

            return None

        if len(text) <= self._MAX_LOG_CHARS:

            return text

        return (
            text[: self._MAX_LOG_CHARS]
            + f"\n\n...[truncated, {len(text) - self._MAX_LOG_CHARS} "
            f"more characters omitted]..."
        )

    def mark_finished(self, run_uuid, result):
        """
        `result` is the dict returned by
        PlaywrightRunner.run_script()/TestExecutionManager.execute_playwright()
        — or a synthetic one built by the caller for a run that never
        actually started (e.g. Playwright not installed, no script
        available). Maps that shape onto this table's columns without
        guessing at meaning: "success" decides Passed/Failed only when
        no "error" key is present (an error means the run itself
        couldn't be judged pass/fail at all — see
        TestExecutionManager.execute_playwright()'s own comment on
        this same distinction).
        """

        cancelled = bool(result.get("cancelled"))

        has_hard_error = "error" in result and not result.get("stdout")

        if result.get("stopped"):

            status = "Stopped"

            outcome = "Stopped by User"

        elif cancelled:

            status = "Cancelled"

            outcome = "Cancelled"

        elif has_hard_error:

            status = "Error"

            outcome = "Error"

        elif result.get("success"):

            status = "Passed"

            outcome = "Pass"

        else:

            status = "Failed"

            outcome = "Fail"

        # BUGFIX (found in Part 2 runtime testing): result.get("error")
        # is ONLY set by PlaywrightRunner.run_script() for a "couldn't
        # even run" hard error (Playwright missing, timeout, subprocess
        # failed to start) — a genuine test FAILURE (script ran, an
        # assertion/step failed, non-zero exit code) has no "error" key
        # at all, so error_message was silently staying NULL for every
        # real Fail and the only diagnostic was the raw stdout/stderr
        # blob. Real failures always end with a Python traceback whose
        # LAST non-empty line IS the actual exception message Python
        # itself printed — extracting it is not fabricating a root
        # cause (AGENTS.md's "never auto-fabricate a root cause"),
        # it's surfacing text the runner already produced, just like
        # error_message does for the hard-error case.
        error_message = result.get("error")
        final_message = result.get("final_message")
        if not final_message:
            if status == "Passed":
                final_message = "Replay completed successfully."
            elif status == "Stopped":
                final_message = "Stopped by user."
            elif status == "Cancelled":
                final_message = "Cancelled during replay."
            elif status == "Error":
                final_message = error_message or "Replay ended with an error."
            else:
                final_message = error_message or "Replay failed."

        if error_message is None and status == "Failed":

            stderr_text = (result.get("stderr") or "").strip()

            if stderr_text:

                error_message = stderr_text.splitlines()[-1].strip()

        conn = self.db.get_connection()

        cursor = conn.cursor()

        cursor.execute(
            """
            UPDATE automation_runs
            SET status=?,
                outcome=?,
                success=?,
                return_code=?,
                duration_seconds=?,
                error_message=?,
                final_message=?,
                runtime_activity_json=?,
                stdout=?,
                stderr=?,
                script_path=?,
                screenshot_path=?,
                slow_mo_ms=?,
                timeout_ms=?,
                finished_at=?
            WHERE run_uuid=?
            """,
            (
                status,
                outcome,
                1 if result.get("success") else 0,
                result.get("return_code"),
                result.get("duration"),
                error_message,
                final_message,
                json.dumps(result.get("runtime_activity") or [], ensure_ascii=False),
                self._truncate_log(result.get("stdout")),
                self._truncate_log(result.get("stderr")),
                result.get("script_path"),
                result.get("screenshot_path"),
                result.get("slow_mo_ms"),
                result.get("timeout_ms"),
                datetime.now().isoformat(),
                run_uuid,
            ),
        )

        conn.commit()

        conn.close()

        return self.get_run(run_uuid)

    # --------------------------------------------------
    # Rule 6 — SQL History (Upload SQL Script Validate/Execute)
    # --------------------------------------------------

    def record_sql_script_run(
        self, sql_script_id, test_case_version_id, kind, result,
        domain=None, module=None, knowledge_name=None,
        environment_name=None,
        executed_by_user_id=None, executed_by_username=None,
    ):
        """
        Persists ONE row per Validate/Execute attempt against an
        uploaded SQL script (Core/sql_script_repository.py), reusing
        this SAME automation_runs table/History surface rather than a
        second execution-history store — Rule 6 explicitly forbids a
        second SQL execution engine, and this is the equivalent rule
        for its history.

        Unlike create_run()+mark_finished() (the background-job
        Playwright/API/inline-SQL lifecycle), SqlScriptRepository.
        validate_script()/execute_script() run synchronously and
        return immediately, so this writes ONE already-finished row in
        a single insert.

        `result` is exactly the dict validate_script()/execute_script()
        already returns. `environment_name` is a caller-supplied
        DISPLAY LABEL only (see SqlScriptsWeb — built from
        SqlEnvironmentConfig's db_type/database, never host/username/
        password) — this method has no access to and never stores the
        environment's connection profile, so a credential/secret can
        never end up in execution history even by accident.
        """

        run_uuid = uuid.uuid4().hex

        now = datetime.now().isoformat()

        success = bool(result.get("success"))

        # Validate never "fails" a test — an invalid script is simply
        # reported so the operator can fix it, which is an Error
        # (couldn't judge pass/fail), not a Fail. Execute follows the
        # same Passed/Failed vocabulary every other automation_runs row
        # already uses.
        if success:
            status = "Passed"
            outcome = "Pass"
        elif kind == "Validate":
            status = "Error"
            outcome = "Error"
        else:
            status = "Failed"
            outcome = "Fail"

        error_message = None if success else (result.get("error") or "Unknown error.")

        final_message = (
            f"{kind} completed successfully."
            if success else (error_message or f"{kind} failed.")
        )

        conn = self.db.get_connection()

        cursor = conn.cursor()

        cursor.execute(
            """
            INSERT INTO automation_runs
            (run_uuid, test_case_id, domain, module, knowledge_name,
             automation_type, sql_script_id, test_case_version_id,
             environment_name, sql_run_kind, status, outcome, success,
             duration_seconds, error_message, final_message,
             rows_returned, executed_by_user_id, executed_by_username,
             started_at, finished_at, created_date)
            VALUES (?, NULL, ?, ?, ?, 'SQL', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_uuid, domain, module, knowledge_name,
                sql_script_id, test_case_version_id,
                environment_name, kind,
                status, outcome, 1 if success else 0,
                result.get("duration_seconds"),
                error_message,
                final_message,
                result.get("row_count"),
                executed_by_user_id, executed_by_username,
                now, now, now,
            ),
        )

        conn.commit()

        conn.close()

        return self.get_run(run_uuid)

    # --------------------------------------------------
    # Read
    # --------------------------------------------------

    @staticmethod
    def _dict_factory(cursor, row):

        columns = [col[0] for col in cursor.description]

        return {columns[i]: row[i] for i in range(len(columns))}

    def get_run(self, run_uuid):

        conn = self.db.get_connection()

        conn.row_factory = self._dict_factory

        cursor = conn.cursor()

        cursor.execute(
            "SELECT * FROM automation_runs WHERE run_uuid=?", (run_uuid,)
        )

        row = cursor.fetchone()

        conn.close()

        if row:
            try:
                row["runtime_activity"] = json.loads(row.get("runtime_activity_json") or "[]")
            except (TypeError, ValueError):
                row["runtime_activity"] = []
        return row

    def delete_runs(self, run_uuids):
        """
        Phase 8 (API-Automation-Enhancement) — backs the History
        popup's "Clear" action (moved here from the old main-page
        "clear the displayed Execution Log table only" button, which
        never touched persisted history at all). Deletes ONLY the
        explicit run_uuids the caller passes — exactly the rows the
        operator saw, filtered, and confirmed in the History popup —
        never a server-recomputed scope/date re-filter, so a delete
        can never remove more than what was actually shown and
        confirmed. Best-effort removes each deleted run's own
        screenshot file (if any); a missing/unreadable file is not an
        error. Returns the number of rows actually deleted.
        """

        run_uuids = [str(u) for u in (run_uuids or []) if u]

        if not run_uuids:

            return 0

        conn = self.db.get_connection()

        conn.row_factory = self._dict_factory

        cursor = conn.cursor()

        placeholders = ",".join("?" for _ in run_uuids)

        cursor.execute(
            f"SELECT run_uuid, screenshot_path FROM automation_runs "
            f"WHERE run_uuid IN ({placeholders})",
            run_uuids,
        )

        rows = cursor.fetchall()

        cursor.execute(
            f"DELETE FROM automation_runs WHERE run_uuid IN ({placeholders})",
            run_uuids,
        )

        deleted = cursor.rowcount

        conn.commit()

        conn.close()

        for row in rows:

            path = row.get("screenshot_path")

            if not path:

                continue

            try:

                Path(path).unlink()

            except Exception:

                pass

        return deleted

    def list_runs(
        self, domain=None, module=None, knowledge_name=None,
        test_case_id=None, test_case_ids=None, status=None, automation_type=None, q=None,
        start_date=None, end_date=None,
        sql_script_id=None, test_case_version_id=None,
        limit=50, offset=0,
    ):
        """
        Returns {"runs": [...], "total": N} — total is the count
        BEFORE limit/offset, so the frontend can render real
        pagination instead of guessing from a possibly-short page.

        Reports & Analytics (Requirement 9) reuses this exact method
        (via TestCasesWeb.list_runs() -> the existing
        GET /api/automation/runs endpoint) for its date-range filter —
        `start_date`/`end_date` are the only two params added here;
        every other filter already existed and is unchanged. Dates are
        compared against `created_date` (an ISO `datetime.now().isoformat()`
        string — see create_run()), so a plain "YYYY-MM-DD" boundary
        still compares correctly against it lexicographically.
        """

        conditions = []

        params = []

        if domain:

            conditions.append("domain=?")

            params.append(domain)

        if module:

            conditions.append("module=?")

            params.append(module)

        if knowledge_name:

            conditions.append("knowledge_name=?")

            params.append(knowledge_name)

        if test_case_id:

            conditions.append("test_case_id=?")

            params.append(test_case_id)

        if test_case_ids is not None:
            ids = [int(value) for value in test_case_ids]
            if not ids:
                return {"runs": [], "total": 0}
            conditions.append("test_case_id IN ({})".format(",".join("?" for _ in ids)))
            params.extend(ids)

        # Rule 6 — SQL History: a script/TC-folder-level Validate/
        # Execute run (no single test_case_id) is looked up by these
        # two instead.
        if sql_script_id:
            conditions.append("sql_script_id=?")
            params.append(sql_script_id)

        if test_case_version_id:
            conditions.append("test_case_version_id=?")
            params.append(test_case_version_id)

        if status:

            conditions.append("status=?")

            params.append(status)

        if automation_type:

            conditions.append("automation_type=?")

            params.append(automation_type)

        if q:

            conditions.append("(tc_number LIKE ? OR knowledge_name LIKE ?)")

            like = f"%{q}%"

            params.extend([like, like])

        if start_date:

            conditions.append("created_date >= ?")

            params.append(start_date)

        if end_date:

            # Inclusive of the whole end day even though created_date
            # carries a time component — a bare "YYYY-MM-DD" end_date
            # would otherwise exclude every run from that day.
            conditions.append("created_date <= ?")

            params.append(end_date if len(str(end_date)) > 10 else f"{end_date}T23:59:59.999999")

        where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""

        conn = self.db.get_connection()

        conn.row_factory = self._dict_factory

        cursor = conn.cursor()

        cursor.execute(
            f"SELECT COUNT(*) as c FROM automation_runs {where_clause}",
            params,
        )

        total = cursor.fetchone()["c"]

        cursor.execute(
            f"""
            SELECT id, run_uuid, test_case_id, tc_number, domain, module,
                   knowledge_name, automation_type, status, outcome,
                   success, return_code, duration_seconds, script_path,
                   screenshot_path, executed_by_username, re_run_of,
                   error_message, final_message,
                   sql_script_id, test_case_version_id, sql_run_kind,
                   environment_name, rows_returned,
                   started_at, finished_at, created_date
            FROM automation_runs
            {where_clause}
            ORDER BY id DESC
            LIMIT ? OFFSET ?
            """,
            params + [limit, offset],
        )

        rows = cursor.fetchall()

        conn.close()

        return {"runs": rows, "total": total}

    def latest_runs_for_test_cases(self, test_case_ids):
        """
        One query, one row per test_case_id (its single most recent
        run) — used by the Automation Workspace listing so it never
        does an N+1 query per row. Returns {test_case_id: run_dict}.
        """

        test_case_ids = [tid for tid in test_case_ids if tid is not None]

        if not test_case_ids:

            return {}

        placeholders = ",".join("?" for _ in test_case_ids)

        conn = self.db.get_connection()

        conn.row_factory = self._dict_factory

        cursor = conn.cursor()

        cursor.execute(
            f"""
            SELECT ar.test_case_id, ar.run_uuid, ar.status, ar.outcome,
                   ar.duration_seconds, ar.started_at, ar.finished_at,
                   ar.created_date
            FROM automation_runs ar
            INNER JOIN (
                SELECT test_case_id, MAX(id) AS max_id
                FROM automation_runs
                WHERE test_case_id IN ({placeholders})
                GROUP BY test_case_id
            ) latest ON ar.id = latest.max_id
            """,
            test_case_ids,
        )

        rows = cursor.fetchall()

        conn.close()

        return {row["test_case_id"]: row for row in rows}

    # --------------------------------------------------
    # Requirement 9 — Dashboard "Test Execution" tab + Reports &
    # Analytics aggregates. All three read the SAME `automation_runs`
    # table every other method in this file already reads/writes — no
    # second, parallel execution-history store, and no fabricated
    # numbers: every value here is a real COUNT/AVG over real rows.
    # --------------------------------------------------

    def execution_trend(self, days=14, automation_type=None):
        """
        Real runs-per-day for the last `days` days (including today),
        optionally scoped to one automation_type. One row per day that
        had at least one run; a day with zero runs is simply absent —
        the caller (DashboardRepository) fills the gaps with 0 so the
        frontend can plot a continuous trend line without the backend
        ever inventing a data point that didn't happen.
        """

        conn = self.db.get_connection()

        conn.row_factory = self._dict_factory

        cursor = conn.cursor()

        # Rule 6 — a SQL Script Upload Validate/Execute row (test_case_id
        # IS NULL; see record_sql_script_run()) is a script/TC-folder-
        # level action, not a per-test-case execution — Dashboard/
        # Reports trends stay scoped to real test-case runs exactly as
        # before Rule 6, unaffected by SQL History's new activity.
        conditions = ["date(created_date) >= date('now', ?)", "test_case_id IS NOT NULL"]

        params = [f"-{max(int(days) - 1, 0)} days"]

        if automation_type:

            conditions.append("automation_type=?")

            params.append(automation_type)

        cursor.execute(
            f"""
            SELECT date(created_date) AS run_date,
                   COUNT(*) AS total,
                   SUM(CASE WHEN status='Passed' THEN 1 ELSE 0 END) AS passed,
                   SUM(CASE WHEN status='Failed' THEN 1 ELSE 0 END) AS failed
            FROM automation_runs
            WHERE {' AND '.join(conditions)}
            GROUP BY date(created_date)
            ORDER BY date(created_date)
            """,
            params,
        )

        rows = cursor.fetchall()

        conn.close()

        return rows

    def duration_stats(self, automation_type=None):
        """
        Real average/min/max run duration (seconds) from
        automation_runs.duration_seconds, grouped by automation_type
        (or for one type if given). Runs with no recorded duration
        (e.g. a hard connection error before anything could time
        itself) are excluded via the NOT NULL filter rather than
        counted as 0 seconds, which would silently understate the
        average.
        """

        conn = self.db.get_connection()

        conn.row_factory = self._dict_factory

        cursor = conn.cursor()

        # Rule 6 — same reasoning as execution_trend() above: exclude
        # script/TC-folder-level SQL History rows from this per-test-
        # case duration aggregate.
        conditions = ["duration_seconds IS NOT NULL", "test_case_id IS NOT NULL"]

        params = []

        if automation_type:

            conditions.append("automation_type=?")

            params.append(automation_type)

        cursor.execute(
            f"""
            SELECT automation_type,
                   COUNT(*) AS sample_count,
                   AVG(duration_seconds) AS avg_seconds,
                   MIN(duration_seconds) AS min_seconds,
                   MAX(duration_seconds) AS max_seconds
            FROM automation_runs
            WHERE {' AND '.join(conditions)}
            GROUP BY automation_type
            """,
            params,
        )

        rows = cursor.fetchall()

        conn.close()

        return rows
