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

TERMINAL_STATUSES = ("Passed", "Failed", "Error", "Cancelled")


class AutomationExecutionRepository:

    def __init__(self):

        self.db = DatabaseManager()

        self.logger = Logger.get_logger()

        self.ensure_schema()

    # --------------------------------------------------
    # Schema
    # --------------------------------------------------

    def ensure_schema(self):

        conn = self.db.get_connection()

        cursor = conn.cursor()

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS automation_runs
            (
                id INTEGER PRIMARY KEY AUTOINCREMENT,

                run_uuid TEXT NOT NULL UNIQUE,

                test_case_id INTEGER NOT NULL,
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
                stdout TEXT,
                stderr TEXT,

                script_path TEXT,
                screenshot_path TEXT,

                slow_mo_ms INTEGER,
                timeout_ms INTEGER,

                executed_by_user_id INTEGER,
                executed_by_username TEXT,

                re_run_of INTEGER,

                started_at TEXT,
                finished_at TEXT,
                created_date TEXT NOT NULL
            )
            """
        )

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

        conn.commit()

        conn.close()

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

        if cancelled:

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

        return row

    def list_runs(
        self, domain=None, module=None, knowledge_name=None,
        test_case_id=None, test_case_ids=None, status=None, automation_type=None, q=None,
        limit=50, offset=0,
    ):
        """
        Returns {"runs": [...], "total": N} — total is the count
        BEFORE limit/offset, so the frontend can render real
        pagination instead of guessing from a possibly-short page.
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
                   error_message,
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
