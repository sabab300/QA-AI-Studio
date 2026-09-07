# Create: AI/Core/sql_automation_manager.py

"""
QA AI Studio
SQL Automation — Desktop manager

Version: 1.0

New for QA-AUTOMATION-FINAL-ARCHITECTURE-04. "SQL" already existed as
a per-row Automation Type choice on Desktop (see
App/UI/QAAutomation/test_execution_page.py's AUTOMATION_TYPES) but
had no runner — selecting it always fell into "Manual Review
Required — no automatic runner yet". This class is the missing
runner, and is the Desktop-side mirror of the Web port's
Core/automation_web_repository.py -> SqlAutomationWeb (same logic,
ported rather than duplicated-and-diverged — kept intentionally
close to that class so both stay easy to compare/keep in sync, the
same relationship every other AI/Core <-> AI-Web/Core file pair in
this project already has).

Reuses, rather than duplicates:
    - TestCaseRepository / test_cases (automation_type='SQL',
      automation_script holds this class's own small JSON envelope —
      see _pack_script()/_unpack_script() — not raw Python/SQL text
      alone, so an assertion rule always has somewhere real to live).
    - Core/sql_generator.py's SQLGenerator (already built and already
      registered in AIOrchestrator for the AI Assistant — reused
      as-is here for its own built-in "never invent SQL when no
      schema is available" safety behavior, not re-implemented).
    - Core/sql_environment_config.py / Core/sql_automation_runner.py
      (already built for this same task — see their own module
      docstrings).

Does NOT reuse (because it does not exist on Desktop at all, for ANY
automation type, not just SQL): a persisted execution-run history
table. The Web port's AutomationExecutionRepository/automation_runs
table has no Desktop equivalent — Desktop's existing Playwright/API
execution (see TestExecutionManager.execute_playwright() /
App/UI/QAAutomation/test_execution_page.py's run_api_automation_rows())
only ever updates the test case's own last_result and prints to the
page's own in-session Log widget; there is no Run ID/timestamp/
duration/stdout history persisted anywhere for those either. Building
a brand-new persisted run-history system is a real, valuable
architecture change, but a materially bigger one than "add a SQL
tab" and would need its own design/migration across ALL THREE
automation types to be done honestly rather than bolted on quietly
for SQL alone — so execute_sql() below returns a plain result dict
for the CURRENT run only (same shape of information the Playwright/
API tabs already surface live), and the SQL Automation page logs it
into its own Log widget exactly like the other two tabs already do.
This is a known, intentional Desktop/Web architecture difference —
see this task's final report, "Logs/Details" section.
"""

import json

from Core.test_case_repository import TestCaseRepository
from Core.sql_environment_config import SqlEnvironmentConfig
from Core.sql_automation_runner import SqlAutomationRunner, SqlValidationError
from Core.sql_generator import SQLGenerator


class SqlAutomationManager:

    def __init__(self):

        self.repository = TestCaseRepository()

        self.env = SqlEnvironmentConfig()

        self.runner = SqlAutomationRunner()

        self.env = SqlEnvironmentConfig()
        
        self.runner = SqlAutomationRunner()

    # --------------------------------------------------
    # Script envelope — identical shape to the Web port's
    # SqlAutomationWeb._pack_script()/_unpack_script().
    # --------------------------------------------------

    @staticmethod
    def _pack_script(sql, assertion_type, assertion_value, assertion_column):

        return json.dumps({
            "sql": sql or "",
            "assertion_type": assertion_type or "row_exists",
            "assertion_value": assertion_value,
            "assertion_column": assertion_column,
        })

    @staticmethod
    def _unpack_script(automation_script):

        if not automation_script:

            return {
                "sql": "", "assertion_type": "row_exists",
                "assertion_value": None, "assertion_column": None,
            }

        try:

            data = json.loads(automation_script)

            if isinstance(data, dict) and "sql" in data:

                return data

        except (ValueError, TypeError):

            pass

        # Anything else (plain text — e.g. an older/hand-typed value,
        # or a generated-but-not-yet-saved draft) is treated as the
        # SQL body itself with no assertion configured yet, rather
        # than raised as a corrupt-data error — Save Script always
        # writes the JSON envelope going forward.
        return {
            "sql": str(automation_script), "assertion_type": "row_exists",
            "assertion_value": None, "assertion_column": None,
        }

    # --------------------------------------------------
    # Environment (connection profile)
    # --------------------------------------------------

    def get_environment(self):

        return self.env.get_masked()

    def update_environment(self, fields):

        self.env.update_masked(fields)

        return self.env.get_masked()

    def test_connection(self):

        profile = self.env.load()

        return self.runner.test_connection(profile)

    # --------------------------------------------------
    # AI generation
    # --------------------------------------------------

    def generate_sql(self, test_case_id, database_schema=None):

        test_case = self.repository.get_test_case(test_case_id)

        if not test_case:

            raise ValueError(f"Test case {test_case_id} not found.")

        requirement = (
            f"Test Case: {test_case.get('test_case') or test_case.get('scenario') or ''}\n"
            f"Steps: {test_case.get('steps') or ''}\n"
            f"Expected Result: {test_case.get('expected_result') or ''}"
        )

        generator = SQLGenerator()

        result = generator.generate(
            requirement=requirement,
            context=None,
            database_schema=database_schema,
        )

        answer = result.get("answer") or ""

        # Pull just the "SQL Query:" section out of SQLGenerator's
        # structured text block — the rest (Purpose/Expected Result/
        # Validation Notes/Assumptions/Missing Information) is real,
        # useful context for the operator reviewing the draft, kept
        # in full in `raw_answer`, just not treated as executable SQL.
        sql_text = ""

        marker = "SQL Query:"

        if marker in answer:

            after = answer.split(marker, 1)[1]

            for stop in ("Expected Result:", "Validation Notes:",
                         "Assumptions:", "Missing Information:"):

                if stop in after:

                    after = after.split(stop, 1)[0]

            sql_text = after.strip().strip("`").strip()

        # Whether this looks safe/executable is decided by the SAME
        # gate a manual save/execute goes through — never a relaxed/
        # AI-only check. If the schema was unavailable, SQLGenerator
        # already deliberately did not produce executable SQL, so
        # this will legitimately fail validation and the draft is
        # saved as needs-review rather than becoming Active.
        needs_review = True

        validation_error = None

        try:

            from Core.sql_automation_runner import validate_readonly_sql

            validate_readonly_sql(sql_text)

            needs_review = False

        except SqlValidationError as ex:

            validation_error = str(ex)

        script_json = self._pack_script(sql_text, "row_exists", None, None)

        self.repository.update_automation(test_case_id, "SQL", script_json)

        # A Draft (needs review) is deliberately NOT marked Automated
        # by this same call — set_active()/validate_sql() is the only
        # path that flips status, exactly like the Playwright/API
        # AI-generation flow's own "generated text exists" vs "status
        # says Automated" distinction.
        return {
            "test_case": self.repository.get_test_case(test_case_id),
            "sql": sql_text,
            "raw_answer": answer,
            "needs_review": needs_review,
            "validation_error": validation_error,
        }

    # --------------------------------------------------
    # Save / validate
    # --------------------------------------------------

    def save_script(self, test_case_id, sql, assertion_type,
                     assertion_value, assertion_column):

        test_case = self.repository.get_test_case(test_case_id)

        if not test_case:

            raise ValueError(f"Test case {test_case_id} not found.")

        script_json = self._pack_script(
            sql, assertion_type, assertion_value, assertion_column
        )

        self.repository.update_automation(test_case_id, "SQL", script_json)

        return self.repository.get_test_case(test_case_id)

    def validate_sql(self, test_case_id):
        """
        Read-only safety + syntax gate — the ONLY thing that decides
        whether this test case's saved SQL is allowed to become
        Active for execution (see set_active()). A query that fails
        here can stay saved as a Draft, per this task's "Invalid
        scripts may be retained as Draft/Validation Failed but must
        NOT be Active for Execution" rule.
        """

        test_case = self.repository.get_test_case(test_case_id)

        if not test_case:

            raise ValueError(f"Test case {test_case_id} not found.")

        script = self._unpack_script(test_case.get("automation_script"))

        try:

            from Core.sql_automation_runner import validate_readonly_sql

            cleaned = validate_readonly_sql(script.get("sql"))

            return {"valid": True, "cleaned_sql": cleaned, "error": None}

        except SqlValidationError as ex:

            return {"valid": False, "cleaned_sql": None, "error": str(ex)}

    def set_active(self, test_case_id, active):
        """
        `active` is a bool. Setting True re-runs validate_sql() first
        and refuses (raises ValueError) if it fails — there is no way
        to force an unsafe/invalid query to Active from this method.
        """

        test_case = self.repository.get_test_case(test_case_id)

        if not test_case:

            raise ValueError(f"Test case {test_case_id} not found.")

        if active:

            validation = self.validate_sql(test_case_id)

            if not validation["valid"]:

                raise ValueError(
                    f"Cannot activate — this query is not safe/valid "
                    f"to execute: {validation['error']}"
                )

            self.repository.update_status(test_case_id, "Automated")

        return self.repository.get_test_case(test_case_id)

    # --------------------------------------------------
    # Execute — re-validates immediately before running (never trusts
    # a stale status flag alone), then evaluates the configured
    # assertion against the real query result. No run-history row is
    # persisted (see this module's docstring) — the caller (
    # sql_automation_page.py) is responsible for showing this result
    # to the operator (Log widget) and refreshing the grid's Result
    # column from the updated test case.
    # --------------------------------------------------

    def execute_sql(self, test_case_id):

        test_case = self.repository.get_test_case(test_case_id)

        if not test_case:

            raise ValueError(f"Test case {test_case_id} not found.")

        if (test_case.get("automation_type") or "") != "SQL":

            raise ValueError("This test case's Automation Type is not 'SQL'.")

        validation = self.validate_sql(test_case_id)

        if not validation["valid"]:

            raise ValueError(
                f"This query is not safe/valid to execute: "
                f"{validation['error']}"
            )

        script = self._unpack_script(test_case.get("automation_script"))

        profile = self.env.load()

        query_result = self.runner.execute_query(
            profile, validation["cleaned_sql"]
        )

        if not query_result.get("success"):

            self.repository.update_result(test_case_id, "Fail")

            return {
                "success": False,
                "outcome": "Fail",
                "sql": validation["cleaned_sql"],
                "error": query_result.get("error"),
                "duration_seconds": query_result.get("duration_seconds"),
                "columns": [],
                "rows": [],
                "row_count": 0,
                "assertion_message": query_result.get("error"),
            }

        assertion = self.runner.evaluate_assertion(
            script.get("assertion_type"), script.get("assertion_value"),
            script.get("assertion_column"), query_result,
        )

        self.repository.update_result(test_case_id, assertion["outcome"])

        return {
            "success": assertion["outcome"] == "Pass",
            "outcome": assertion["outcome"],
            "sql": validation["cleaned_sql"],
            "error": None,
            "duration_seconds": query_result.get("duration_seconds"),
            "columns": query_result.get("columns") or [],
            "rows": (query_result.get("rows") or [])[:20],
            "row_count": query_result.get("row_count"),
            "truncated": query_result.get("truncated"),
            "assertion_message": assertion["message"],
        }
