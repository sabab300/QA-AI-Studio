# Create: AI-Web/Core/automation_web_repository.py

"""
QA AI Studio — Web
QA Automation Web Repository

Version: 2.0

Thin web-facing wrappers around the SAME Core classes the desktop
app's QA Automation hub already uses (ApiCollectionRepository,
GitService, TestCaseRepository, TestExecutionManager, PlaywrightRunner,
ApiAutomationRunner, TestEnvironmentConfig) — none of those files
change in behavior to support this beyond the specific, individually
documented additions in each file's own comments (force_headless,
real cancellation, failure-screenshot evidence, CWD-relative path
fixes, and the new cross-scope TestCaseRepository queries).

v2.0 adds, on top of the v1.0 API Collections / Git Automation /
basic Test Case + Execute wrapper:
    - AutomationExecutionRepository-backed run history (Execute was
      previously an in-memory-only job dict — lost on every server
      restart, no history, no re-run). See
      Core/automation_execution_repository.py's module docstring for
      the full reasoning.
    - Real Cancel for a running Playwright job (PlaywrightRunner.run_script()
      is now Popen-based specifically to make this possible).
    - Script validation before execution (readiness checks, not just
      "the file exists").
    - Script management (manual edit/save for both the AI-generated
      and Manually Recorded slots, active-script toggle, AI type
      suggestion).
    - A cross-scope "Automation Workspace" listing with each test
      case's latest run attached.
    - Real "Execute Against Real Server" for API-type test cases
      (Core/api_automation_runner.py), previously not wired into the
      web port at all.
    - Test Environment Settings (Core/test_environment_config.py),
      previously not exposed to the web port at all — secrets are
      masked on read and only overwritten when a caller explicitly
      sends a new, non-empty value (see EnvironmentConfigWeb).

Deliberately still NOT covered here (see Core/test_execution_manager.py
and Core/playwright_runner.py's own module docstrings, and the
delivery notes for this task): the Playwright codegen RECORDER
(record_manual_script()) and INTERACTIVE locator-repair
(execute_playwright_interactive() / suggest_locator_fix()) both need
a live, human-drivable browser window / a bidirectional channel — a
WebSocket-based remote-control bridge, a separate piece of work, not
a REST wrapper like this one. A test case recorded on Desktop can
still be viewed/edited/executed (non-interactively) from Web.
"""

import json
import re
import shutil
import tempfile
import threading
from pathlib import Path

from Core.api_collection_repository import ApiCollectionRepository
from Core.automation_execution_repository import AutomationExecutionRepository
from Core.git_service import GitService
from Core.test_case_repository import TestCaseRepository
from Core.test_execution_manager import TestExecutionManager
from Core.test_environment_config import TestEnvironmentConfig
from Core.qa_engineering_web_repository import QaEngineeringWeb
from Core.api_automation_runner import ApiAutomationRunner
from Core.sql_environment_config import SqlEnvironmentConfig
from Core.sql_automation_runner import SqlAutomationRunner, SqlValidationError
from Core.sql_generator import SQLGenerator

GIT_WORKSPACE_ROOT = Path(__file__).resolve().parent.parent / "Output" / "GitWorkspaces"

# Section F/I: the same 7 assertion types
# Core/api_automation_runner.py's evaluate_assertions() actually
# understands — kept here too (not imported from there) purely so
# validate_for_execution()'s readiness check can flag an assertion
# with an unrecognized/misspelled "type" BEFORE Execute ever runs it,
# without adding an import-time dependency between the two modules
# for what is fundamentally a duplicated constant, not shared logic.
_API_ASSERTION_TYPES = {
    "status_equals", "json_field_exists", "json_field_equals",
    "json_field_contains", "response_time_lte", "header_exists",
    "header_equals",
}

# API-SQL-AUTOMATION-END-TO-END: a coarse "this isn't SQL at all" gate
# for SqlAutomationWeb.save_script(), deliberately SEPARATE from and
# looser than sql_automation_runner.validate_readonly_sql() (which
# still fully applies at Validate/Set Active time). Save Script must
# still allow an imperfect/in-progress SQL draft to be iterated on —
# this only rejects content that plainly isn't SQL to begin with
# (Python/Selenium/Playwright script text pasted into the wrong slot),
# per "Reject INSERT/UPDATE/... Python/Selenium/Playwright/HTTP code."
_NON_SQL_CONTENT_PATTERN = re.compile(
    r"^\s*(import\s+\w|from\s+\w[\w.]*\s+import\b|class\s+\w+.*:|"
    r"def\s+\w+\s*\(|@\w+|<html|<!doctype|\{\{|"
    r"(self\.)?driver\.|webdriver\.|page\.(goto|click|fill)\(|"
    r"async\s+def\b)",
    re.IGNORECASE | re.MULTILINE,
)


def _workspace_path_for(remote_url):
    import hashlib

    key = hashlib.sha256(remote_url.encode("utf-8")).hexdigest()[:16]

    return GIT_WORKSPACE_ROOT / key


# ============================================================
# API Collections
# ============================================================


class ApiCollectionsWeb:

    def __init__(self):

        self.repo = ApiCollectionRepository()

    def import_collection(
        self, file_bytes, original_filename, domain=None, module=None,
        knowledge_name=None, version=None, document_type=None,
    ):

        if not file_bytes:

            raise ValueError("A Postman collection .json file is required.")

        safe_name = Path(original_filename or "collection.json").name or "collection.json"

        temp_dir = tempfile.mkdtemp(prefix="qaais_api_")
        temp_path = Path(temp_dir) / safe_name

        temp_path.write_bytes(file_bytes)

        try:

            result = self.repo.import_postman_collection(
                file_path=str(temp_path),
                domain=domain,
                module=module,
                knowledge_name=knowledge_name,
                version=version,
                document_type=document_type,
            )

        finally:

            shutil.rmtree(temp_dir, ignore_errors=True)

        if not result.get("success"):

            raise ValueError(result.get("error") or "Import failed.")

        return result

    def list_collections(self):

        return self.repo.list_collections()

    def get_collection(self, collection_id):

        collection = self.repo.get_collection(collection_id)

        if collection is None:

            return None

        collection = dict(collection)

        collection["endpoints"] = self.repo.list_endpoints(collection_id)

        return collection

    def get_endpoint(self, endpoint_id):

        return self.repo.get_endpoint(endpoint_id)

    def update_endpoint(self, endpoint_id, **fields):

        existing = self.repo.get_endpoint(endpoint_id)

        if existing is None:

            return None

        if "headers" in fields and fields["headers"] is not None:

            fields["headers_json"] = json.dumps(fields.pop("headers"))

        else:

            fields.pop("headers", None)

        ok = self.repo.update_endpoint(endpoint_id, **fields)

        if not ok:

            raise ValueError("Nothing to update.")

        return self.repo.get_endpoint(endpoint_id)


# ============================================================
# Git Automation
# ============================================================


class GitAutomationWeb:
    """
    One server-side workspace directory per distinct remote_url
    (keyed by a short hash), so different repos don't collide. This
    is a first-cut, single-tenant-per-repo model — it does not yet
    isolate workspaces per PSW user, which the desktop app didn't
    need to either (repo_path was just wherever the operator's own
    checkout lived). Flagged in delivery notes as a follow-up once
    this is used by more than one concurrent user against the same
    repo.
    """

    def _service(self, remote_url, branch="main", username="", token=""):

        if not remote_url:

            raise ValueError("remote_url is required.")

        workspace = _workspace_path_for(remote_url)

        workspace.mkdir(parents=True, exist_ok=True)

        return GitService(
            repo_path=str(workspace),
            remote_url=remote_url,
            branch=branch,
            username=username,
            token=token,
        )

    def open_or_clone(self, remote_url, branch="main", username="", token=""):

        service = self._service(remote_url, branch, username, token)

        # BUGFIX (web port): GitService.open_or_clone() returns a raw
        # GitPython Repo object — fine for the desktop app, which
        # keeps using it in-process, but not JSON-serializable across
        # an HTTP boundary (FastAPI's encoder fails trying to encode
        # it). Return a plain summary dict instead; status() already
        # gives the fuller live picture for anything past this.
        repo = service.open_or_clone()

        try:
            active_branch = repo.active_branch.name
        except Exception:
            active_branch = None

        return {
            "opened": True,
            "workspace_path": service.repo_path,
            "active_branch": active_branch,
            "remotes": [r.name for r in repo.remotes],
        }

    def status(self, remote_url, branch="main", username="", token=""):

        service = self._service(remote_url, branch, username, token)

        return service.status()

    def add_and_commit(self, remote_url, files, message, branch="main", username="", token=""):

        service = self._service(remote_url, branch, username, token)

        return service.add_and_commit(files, message)

    def pull(self, remote_url, branch=None, username="", token=""):

        service = self._service(remote_url, branch or "main", username, token)

        return service.pull(branch)

    def push(self, remote_url, branch=None, username="", token=""):

        service = self._service(remote_url, branch or "main", username, token)

        return service.push(branch)

    def merge(self, remote_url, source_branch, branch="main", username="", token=""):

        service = self._service(remote_url, branch, username, token)

        return service.merge(source_branch)

    def compare(self, remote_url, ref_a, ref_b, branch="main", username="", token=""):

        service = self._service(remote_url, branch, username, token)

        return service.compare(ref_a, ref_b)

    def list_conflicts(self, remote_url, branch="main", username="", token=""):

        service = self._service(remote_url, branch, username, token)

        return service.list_conflicts()

    def resolve_conflict(self, remote_url, file_path, strategy, branch="main", username="", token=""):

        service = self._service(remote_url, branch, username, token)

        return service.resolve_conflict(file_path, strategy)

    def generate_commit_message(self, remote_url, branch="main", username="", token=""):

        service = self._service(remote_url, branch, username, token)

        return service.generate_commit_message()


# ============================================================
# Test Environment Settings (execution configuration)
# ============================================================

# Never echoed back to the client in plaintext once saved — GET
# returns "<field>_is_set": bool instead. Matches the redaction
# already applied to imported API-collection headers in
# api_collection_repository.py (see its own diff/comment) — the same
# "don't put a secret in a JSON response just because it's technically
# already stored" principle, now applied here too.
_SECRET_FIELDS = (
    "password", "api_auth_token", "api_auth_header_value", "api_password",
)


class EnvironmentConfigWeb:
    """
    Wraps Core/test_environment_config.py (Base URL, Playback Speed /
    Default Timeout, API auth/headers/timeout/SSL/base-URL-override,
    remembered {{variable}} values) — the SAME config file/fields the
    desktop app's Test Environment Settings dialog reads and writes.
    Not exposed anywhere in the v1.0 web port at all; every generated/
    executed script silently ran with the same blank defaults
    regardless of what an operator entered, since there was no way to
    enter anything. This closes that gap.
    """

    def __init__(self):

        self.config = TestEnvironmentConfig()

    def get(self):

        data = self.config.load()

        masked = dict(data)

        for field in _SECRET_FIELDS:

            masked[f"{field}_is_set"] = bool(data.get(field))

            masked[field] = ""

        return masked

    def update(self, fields):
        """
        `fields`: a dict of any subset of TestEnvironmentConfig's
        keys. A key that is ABSENT or null keeps its existing stored
        value. For a secret field specifically, an EMPTY string is
        also treated as "keep existing" — there's no legitimate UI
        reason to blank out a token/password via this masked-on-read
        endpoint (the client never sees the real value to confirm
        it's intentionally clearing it), so only a real, non-empty
        replacement value actually changes a secret. Non-secret
        fields DO support an explicit empty-string clear.
        """

        existing = self.config.load()

        def resolve(name, is_secret=False):

            if name not in fields or fields[name] is None:

                return existing.get(name)

            if is_secret and fields[name] == "":

                return existing.get(name)

            return fields[name]

        self.config.save(
            base_url=resolve("base_url") or "",
            username=resolve("username") or "",
            password=resolve("password", is_secret=True) or "",
            notes=resolve("notes") or "",
            slow_mo_ms=resolve("slow_mo_ms") or "",
            default_timeout_ms=resolve("default_timeout_ms") or "",
            api_auth_type=resolve("api_auth_type"),
            api_auth_token=resolve("api_auth_token", is_secret=True),
            api_auth_header_name=resolve("api_auth_header_name"),
            api_auth_header_value=resolve("api_auth_header_value", is_secret=True),
            api_username=resolve("api_username"),
            api_password=resolve("api_password", is_secret=True),
            api_extra_headers=fields.get("api_extra_headers"),
            api_timeout_seconds=resolve("api_timeout_seconds"),
            api_verify_ssl=fields.get("api_verify_ssl"),
            api_base_url_override=resolve("api_base_url_override"),
            api_variables=None,
        )

        return self.get()

    def remember_variables(self, variables):

        for name, value in (variables or {}).items():

            self.config.remember_api_variable(name, value)

        return self.get()


# ============================================================
# Test Cases + Scripts + Execute (background job, persisted) +
# Execution History + API "Execute Against Real Server"
# ============================================================

# In-process registry of the TestExecutionManager currently running a
# given run_uuid's Playwright job on its background thread — the
# handle a genuine Cancel needs (see cancel_execution()). Deliberately
# separate from AutomationExecutionRepository (which is the durable,
# cross-restart record): this dict can ONLY ever be valid for jobs
# started by THIS server process, and is never read as a source of
# truth for status — that's always the persisted row. Fine for a
# single-server deployment (matches "start on my PC / LAN" from the
# requirement); a multi-instance deployment would need a different
# cancellation transport (e.g. a message per worker), out of scope
# here — flagged in delivery notes as a follow-up.
_RUNNING_MANAGERS = {}
_RUNNING_MANAGERS_LOCK = threading.Lock()


class TestCasesWeb:

    def __init__(self):

        self.repository = TestCaseRepository()

        self.runs = AutomationExecutionRepository()

    # --------------------------------------------------
    # Listing
    # --------------------------------------------------

    def list_test_cases(self, domain, module, knowledge_name, automation_type=None):

        rows = self.repository.list_test_cases(domain, module, knowledge_name)
        if not automation_type:
            return rows
        tool = self.repository.execution_tool_for_automation_type(automation_type)
        return [row for row in rows if row.get("execution_type") == "Automatable" and row.get("execution_tool") == tool]

    def list_workspace(
        self, domain=None, module=None, knowledge_name=None, version=None,
        document_type=None, source_knowledge_ids=None, test_case_document=None,
        status=None, automation_type=None, q=None, limit=50, offset=0,
    ):
        """
        Backs the Automation Workspace landing page: every automation
        asset across every scope, each with its latest execution
        result/time attached (a single extra query, not one per row —
        see AutomationExecutionRepository.latest_runs_for_test_cases()).
        """

        execution_tool = (
            self.repository.execution_tool_for_automation_type(automation_type)
            if automation_type else None
        )

        result = self.repository.list_all_test_cases(
            domain=domain, module=module, knowledge_name=knowledge_name,
            version=version, document_type=document_type,
            status=status,
            execution_type="Automatable", execution_tool=execution_tool, q=q,
            limit=10000, offset=0,
        )

        selected_sources = {int(value) for value in (source_knowledge_ids or [])}
        rows = result["test_cases"]
        if selected_sources:
            rows = [row for row in rows if set(row.get("source_knowledge_ids") or []) == selected_sources]
        if test_case_document:
            rows = [row for row in rows if self._document_key(row) == test_case_document]
        total = len(rows)
        rows = rows[offset:offset + limit]
        result = {"test_cases": rows, "total": total}

        test_case_ids = [tc["id"] for tc in result["test_cases"]]

        latest_runs = self.runs.latest_runs_for_test_cases(test_case_ids)

        for test_case in result["test_cases"]:

            test_case["latest_run"] = latest_runs.get(test_case["id"])

        # Section D: the grid's "TEST CASE / API TITLE" column must
        # show the REAL bound endpoint's METHOD + name for an API
        # test case that has one — never fake/placeholder data. One
        # batch lookup for every bound endpoint id on this page,
        # rather than one query per row.
        api_endpoint_ids = {
            test_case.get("bound_api_endpoint_id")
            for test_case in result["test_cases"]
            if test_case.get("automation_type") == "API"
            and test_case.get("bound_api_endpoint_id")
        }

        if api_endpoint_ids:

            endpoints_by_id = ApiCollectionRepository().get_endpoints_by_ids(
                api_endpoint_ids
            )

            for test_case in result["test_cases"]:

                if test_case.get("automation_type") != "API":

                    continue

                endpoint = endpoints_by_id.get(test_case.get("bound_api_endpoint_id"))

                if not endpoint:

                    continue

                method = (endpoint.get("method") or "").upper()

                name = endpoint.get("name") or ""

                test_case["bound_endpoint_name"] = (
                    f"{method} {name}".strip() if method else name
                )

        return result

    def list_scopes(self):
        sources = []
        for item in QaEngineeringWeb().list_scope():
            if not item.get("display_name"):
                continue
            sources.append({
                "id": item.get("id"), "domain": item.get("domain") or "",
                "module": item.get("module") or "", "knowledge_name": item.get("knowledge_name") or "",
                "version": item.get("version") or "", "document_type": item.get("document_type") or "",
                "display_name": item.get("display_name"), "source_type": item.get("source_type") or "",
            })
        rows = self.repository.list_all_test_cases(limit=10000, offset=0)["test_cases"]
        documents = {}
        for row in rows:
            key = self._document_key(row)
            if not key or row.get("review_state") != "Reviewed/Saved":
                continue
            group_key = (
                row.get("domain"), row.get("module"), row.get("knowledge_name"), row.get("version"),
                row.get("document_type"), tuple(sorted(row.get("source_knowledge_ids") or [])), key,
            )
            group = documents.setdefault(group_key, {
                "key": key, "name": self._document_name(row), "domain": row.get("domain") or "",
                "module": row.get("module") or "", "knowledge_name": row.get("knowledge_name") or "",
                "version": row.get("version") or "", "document_type": row.get("document_type") or "",
                "source_knowledge_ids": sorted(row.get("source_knowledge_ids") or []),
                "counts": {"Playwright": 0, "API": 0, "SQL": 0}, "total": 0,
            })
            group["total"] += 1
            tool = {"Playwright": "Playwright", "API Automation": "API", "SQL Automation": "SQL"}.get(row.get("execution_tool"))
            if row.get("execution_type") == "Automatable" and tool:
                group["counts"][tool] += 1
        return {"sources": sources, "documents": list(documents.values())}

    @staticmethod
    def _document_key(row):
        return str(row.get("reviewed_workbook_path") or row.get("test_case_document_name") or "").strip()

    @staticmethod
    def _document_name(row):
        value = str(row.get("test_case_document_name") or "").strip()
        return value or Path(str(row.get("reviewed_workbook_path") or "")).name

    def get_test_case(self, test_case_id):

        return self.repository.get_test_case(test_case_id)

    def delete_test_case(self, test_case_id):

        return self.repository.delete_test_case(test_case_id)

    # --------------------------------------------------
    # Import
    # --------------------------------------------------

    def import_from_excel(self, file_bytes, original_filename, domain, module, knowledge_name,
                          version=None, document_type=None, source_knowledge_ids=None,
                          automation_type="Playwright"):

        if not file_bytes:

            raise ValueError("An Excel (.xlsx) file is required.")

        safe_name = Path(original_filename or "test_cases.xlsx").name or "test_cases.xlsx"

        temp_dir = tempfile.mkdtemp(prefix="qaais_tc_")
        temp_path = Path(temp_dir) / safe_name

        temp_path.write_bytes(file_bytes)

        try:

            manager = TestExecutionManager(initialize_automation=False)

            result = manager.import_test_cases_from_excel(
                file_path=str(temp_path), domain=domain, module=module,
                knowledge_name=knowledge_name, version=version,
                document_type=document_type, source_knowledge_ids=source_knowledge_ids,
                test_case_document_name=safe_name,
                default_automation_type=automation_type,
            )

        finally:

            shutil.rmtree(temp_dir, ignore_errors=True)

        if not result.get("success") and result.get("error"):

            raise ValueError(result["error"])

        result["document"] = safe_name
        result["automation_target"] = self.repository.execution_tool_for_automation_type(
            automation_type
        )
        return result

    # --------------------------------------------------
    # Status / Result
    # --------------------------------------------------

    def set_status(self, test_case_id, status):

        self.repository.update_status(test_case_id, status)

        return self.get_test_case(test_case_id)

    def record_result(self, test_case_id, result):
        """
        Records a result WITHOUT running anything — the web
        equivalent of Desktop's RecordResultDialog for Manual rows,
        and also used to confirm/override the outcome of an
        ambiguous "Execute Against Real Server" API call (see
        execute_api() below) or a Selenium/SQL script reviewed by
        hand outside the app.
        """

        manager = TestExecutionManager()

        manager.record_manual_result(test_case_id, result)

        return self.get_test_case(test_case_id)

    # --------------------------------------------------
    # AI Generation
    # --------------------------------------------------

    def generate_automation(self, test_case_id, automation_type, domain, module, knowledge_name, version=None):

        manager = TestExecutionManager()

        script = manager.generate_automation(
            test_case_id=test_case_id, automation_type=automation_type,
            domain=domain, module=module, knowledge_name=knowledge_name,
            version=version,
        )

        return {"test_case": self.get_test_case(test_case_id), "script": script}

    def generate_from_collection(
        self, domain, module, knowledge_name, version=None,
        automation_type="API", endpoint_ids=None,
    ):

        manager = TestExecutionManager()

        return manager.generate_automation_from_collection(
            domain=domain, module=module, knowledge_name=knowledge_name,
            version=version, automation_type=automation_type,
            endpoint_ids=endpoint_ids,
        )

    def suggest_type(self, test_case_id):

        manager = TestExecutionManager()

        return manager.suggest_automation_type(test_case_id)

    # --------------------------------------------------
    # Script management
    # --------------------------------------------------

    def update_script(self, test_case_id, script_text):

        manager = TestExecutionManager()

        test_case = manager.repository.get_test_case(test_case_id)

        if not test_case:

            raise ValueError(f"Test case {test_case_id} not found.")

        # BUGFIX (caught in testing): test_cases.automation_type
        # defaults to the literal STRING "None" (see
        # TestCaseRepository.ensure_schema()'s DEFAULT 'None'), not
        # SQL NULL / Python None — `or "Playwright"` alone never
        # catches that case, since a non-empty string is truthy. A
        # freshly imported/generated test case with no automation
        # type chosen yet would otherwise keep saving its manually
        # entered Playwright script under automation_type "None",
        # silently breaking can_auto_execute()'s
        # `automation_type == "Playwright"` check.
        automation_type = test_case.get("automation_type") or "Playwright"

        if automation_type == "None":

            automation_type = "Playwright"

        syntax_warning = (
            manager.check_script_syntax(script_text)
            if automation_type == "Playwright" else None
        )

        manager.update_script(test_case_id, automation_type, script_text)

        return {
            "test_case": self.get_test_case(test_case_id),
            "syntax_warning": syntax_warning,
        }

    def update_recorded_script(self, test_case_id, script_text):

        manager = TestExecutionManager()

        syntax_warning = manager.check_script_syntax(script_text)

        manager.update_recorded_script(test_case_id, script_text)

        return {
            "test_case": self.get_test_case(test_case_id),
            "syntax_warning": syntax_warning,
        }

    def set_active_script(self, test_case_id, source):

        manager = TestExecutionManager()

        # QA-AUTOMATION-FINAL-ARCHITECTURE-04 hidden-bug fix (ported
        # from the Desktop port — see test_execution_page.py's
        # ViewScriptDialog._set_active()/on_active_script_changed()
        # matching comments): this endpoint is the actual gate that
        # flips a script to Active for Execution, and never validated
        # it — a script could be saved as Draft with a real syntax
        # error (update_script()/update_recorded_script() only return
        # a non-blocking syntax_warning) and then still be set Active
        # here. Confirmed real by finding genuinely broken persisted
        # scripts on disk (AI/App/Output/AutomationRuns/TC003_*.py —
        # "unterminated string literal"). Per this task's explicit
        # rule ("invalid scripts may stay Draft but must NEVER become
        # Active"), block it here instead.
        test_case = self.repository.get_test_case(test_case_id)

        if not test_case:

            raise ValueError(f"Test case {test_case_id} not found.")

        # REMOVE-SCRIPT-LIFECYCLE item 7: Set Active is a Playwright/
        # SQL script-lifecycle concept (Draft script -> Validate ->
        # Set Active -> Execute). API Automation has no script to
        # activate — its executable asset is the persisted Request
        # Configuration, and a successful Validate marks it Ready to
        # Execute directly (see validate_for_execution()'s
        # mark_script_validated(..., keep_active=True) for API). This
        # endpoint must never be reachable for an API test case —
        # every branch below that used to special-case is_api has been
        # removed along with it; only Playwright/SQL reach past here.
        if (test_case.get("automation_type") or "").upper() == "API":

            raise ValueError(
                "Set Active is not used for API Automation — a "
                "successful Validate marks the saved Request "
                "Configuration Ready to Execute directly."
            )

        script_text = (
            test_case.get("recorded_script")
            if source == "MANUAL"
            else test_case.get("automation_script")
        ) or ""

        if not script_text.strip():

            raise ValueError(
                "The selected script source is empty and cannot be activated."
            )

        expected_tool = self.repository.execution_tool_for_automation_type(
            test_case.get("automation_type")
        )

        if (
            test_case.get("execution_type") != "Automatable"
            or not expected_tool
            or test_case.get("execution_tool") != expected_tool
        ):

            raise ValueError(
                "This Test Case is not eligible for the selected automation tool."
            )

        if script_text.strip():

            syntax_error = manager.check_script_syntax(script_text)

            if syntax_error:

                raise ValueError(
                    f"This script has a Python syntax problem and "
                    f"cannot be made Active for Execution: "
                    f"{syntax_error}"
                )

        if not self.repository.is_script_source_validated(test_case, source):
            raise ValueError(
                "This saved script source has not been validated, or it "
                "changed since validation. Validate it before Set Active."
            )

        manager.set_active_script(test_case_id, source)

        self.repository.update_status(test_case_id, "Automated")

        return self.get_test_case(test_case_id)

    # --------------------------------------------------
    # Validation before execution (spec: "Do not claim a script is
    # valid merely because the file exists.")
    # --------------------------------------------------

    def validate_for_execution(self, test_case_id, source=None):
        """
        Two modes, selected by `source` (item 5/6 lifecycle: "Validate
        validates selected source only, must NOT execute/activate"):

        - source=None (default) — EXECUTE READINESS. "Is this test
          case ready to run right now, as currently configured?" Used
          by the Execute tab's readiness panel and by Execute
          Selected's per-test-case preflight (item 8). Always checks
          the currently ACTIVE script (whichever
          active_script_source already points at), and includes the
          execution_tool_configured / active_script_approved checks
          that only make sense for "about to execute right now".

        - source="AUTO"|"MANUAL" — VALIDATE (SELECTED) SCRIPT. "Is
          THIS saved draft — which may or may not be the currently
          Active one — syntactically/structurally sound?" Used by the
          Validate button on the Automation tab (item 6), which must
          validate whichever source tab the operator is currently
          viewing, independent of what's Active. Skips the
          execution_tool_configured / active_script_approved checks
          entirely — those describe "ready to execute", not "this
          draft is valid", and Set Active (set_active_script(), above)
          independently re-checks syntax/eligibility right before
          activation regardless of what Validate already reported.
        """

        manager = TestExecutionManager()

        test_case = manager.repository.get_test_case(test_case_id)

        if not test_case:

            raise ValueError(f"Test case {test_case_id} not found.")

        automation_type = test_case.get("automation_type") or "None"

        # POSTMAN-STYLE-END-TO-END-FINAL-COMPLETION section 11: a
        # masked Request Preview (Method/Resolved URL/Headers/Body) —
        # populated only for API test cases with a successfully-built
        # request, below. Never includes a real secret (reuses the
        # same masking helpers execute_api()'s result already goes
        # through) and Validate never sends this request.
        request_preview = None

        checks = []

        def add(name, ok, detail=""):

            checks.append({"check": name, "ok": bool(ok), "detail": detail})

        validating_selected_source = source in ("AUTO", "MANUAL")

        if not validating_selected_source:

            expected_tool = self.repository.execution_tool_for_automation_type(
                automation_type
            )

            add(
                "execution_tool_configured",
                test_case.get("execution_type") == "Automatable"
                and bool(expected_tool)
                and test_case.get("execution_tool") == expected_tool,
                "" if test_case.get("execution_type") == "Automatable" else
                "The central Test Case is not classified as Automatable.",
            )

            # REMOVE-SCRIPT-LIFECYCLE item 4/7: API Automation has no
            # Set Active step and no "active_script_source" concept —
            # a successful Validate is what marks the saved Request
            # Configuration Ready to Execute (see the
            # mark_script_validated(..., keep_active=True) call at the
            # bottom of this method). Readiness for API is therefore
            # just "is the CURRENT persisted request config the one
            # that was last successfully validated" — content-
            # fingerprint based (is_script_source_validated() against
            # the fixed "AUTO" slot API always uses), never gated on
            # active_script_source or a separate activation click.
            # Playwright/SQL keep their existing Set-Active-based
            # active_script_approved check untouched.
            if automation_type == "API":

                request_validated = self.repository.is_script_source_validated(
                    test_case, "AUTO"
                )
                add(
                    "request_validated",
                    test_case.get("status") == "Automated" and request_validated,
                    "" if test_case.get("status") == "Automated" and request_validated
                    else "The saved Request Configuration has not been "
                    "validated yet, or it changed since the last successful "
                    "Validate — run Validate again.",
                )

            else:

                active_source = (
                    test_case.get("active_script_source") or "AUTO"
                ).upper()
                source_validated = self.repository.is_script_source_validated(
                    test_case, active_source
                )
                add(
                    "active_script_approved",
                    test_case.get("status") == "Automated" and source_validated,
                    "" if test_case.get("status") == "Automated" and source_validated
                    else "The active script changed since validation, or has not "
                    "been validated and activated.",
                )

        # REMOVE-SCRIPT-LIFECYCLE item 4: "automation_type_set" is a
        # script-lifecycle-era generic check ("has ANY automation type
        # been chosen at all") that is trivially/meaninglessly true for
        # every API test case reaching this point (automation_type is
        # already "API") — API readiness/validation is reported purely
        # via the substantive checks below (endpoint/binding, method,
        # URL, params, headers, auth, body, variables/secrets,
        # assertions, extraction). Playwright/SQL/unset keep this
        # check unchanged.
        if automation_type != "API":

            add(
                "automation_type_set", automation_type != "None",
                "" if automation_type != "None" else (
                    "No Automation Type is set on this test case yet."
                ),
            )

        if validating_selected_source:
            script = (
                test_case.get("recorded_script") if source == "MANUAL"
                else test_case.get("automation_script")
            ) or ""
        else:
            script = TestExecutionManager.get_active_script(test_case)

        # API-SQL-AUTOMATION-END-TO-END: "Selenium/browser script text
        # must NOT control API execution readiness" — automation_script
        # is documentation/reference only for API test cases (the real
        # runner sends the bound endpoint's own request directly, see
        # execute_api()), so script presence/syntax is not part of API
        # readiness at all. Skip this check for API; it still applies
        # to Playwright (and to the generic "no script yet" case for
        # any other/unset type).
        if automation_type != "API":

            add(
                "script_present", bool(script and script.strip()),
                "" if script else (
                    "No automation script has been generated or recorded "
                    "yet."
                ),
            )

        if automation_type == "Playwright" and script:

            syntax_error = manager.check_script_syntax(script)

            add("syntax_valid", syntax_error is None, syntax_error or "")

        if automation_type == "Playwright":

            environment = manager.environment_config.load()

            has_base_url = bool((environment.get("base_url") or "").strip())

            add(
                "base_url_configured", has_base_url,
                "" if has_base_url else (
                    "No Base URL is set in Test Environment Settings "
                    "— a generated/recorded script may navigate to a "
                    "placeholder or unintended URL."
                ),
            )

            installed = manager.playwright_runner.is_playwright_installed()

            add(
                "runner_available", installed,
                "" if installed else (
                    "Playwright isn't installed on the server "
                    "(pip install playwright && playwright install "
                    "chromium)."
                ),
            )

        elif automation_type == "API":

            # API-SQL-AUTOMATION-END-TO-END: readiness = automation
            # type + a real BOUND endpoint + valid request config +
            # required variables supplied + an assertion configured
            # + validated/active state — NEVER script text. This is a
            # read-only check (it must never silently persist a
            # binding just from being asked "are you ready") —
            # compare against test_case's OWN persisted
            # bound_api_endpoint_id, do not auto-resolve/auto-bind
            # here even when exactly one candidate exists. Only
            # Execute (execute_api(), via _resolve_bound_endpoint())
            # may actually persist an auto-bind.

            # Section F/I: the per-Test-Case Params/Headers/Auth/Body/
            # Assertions/Extraction definition (never the masked
            # get_api_request_config() — readiness checks below build
            # the SAME request_overrides execute_api() will actually
            # send, so a raw, unmasked secret is fine here; nothing in
            # this dict is ever returned to the caller).
            request_config = self._parsed_api_request_config(test_case)

            structured_assertions = request_config.get("assertions") or []

            invalid_assertion_types = [
                str((assertion or {}).get("type") or "<blank>")
                for assertion in structured_assertions
                if str((assertion or {}).get("type") or "")
                not in _API_ASSERTION_TYPES
            ]

            try:

                candidates = manager.list_api_endpoints_for_test_case(
                    test_case
                )

            except Exception:

                candidates = []

            bound_endpoint_id = test_case.get("bound_api_endpoint_id")

            bound_endpoint = None

            if bound_endpoint_id:

                bound_endpoint = next(
                    (
                        endpoint for endpoint in candidates
                        if endpoint.get("id") == bound_endpoint_id
                    ),
                    None,
                )

            if bound_endpoint:

                endpoint_bound_detail = ""

            elif not candidates:

                endpoint_bound_detail = (
                    "No imported API Collection endpoint matches this "
                    "test case's Domain / Module / Knowledge Name — "
                    "import a Postman Collection for this scope first."
                )

            elif len(candidates) == 1:

                endpoint_bound_detail = (
                    "One matching endpoint was found but is not yet "
                    "bound — bind it (or use Execute, which will "
                    "auto-bind it)."
                )

            else:

                endpoint_bound_detail = (
                    f"{len(candidates)} matching endpoints were found "
                    f"— select and bind exactly one before this test "
                    f"case can be considered ready."
                )

            add("endpoint_bound", bool(bound_endpoint), endpoint_bound_detail)

            if bound_endpoint:

                runner = ApiAutomationRunner()

                config = manager.environment_config.load()

                request_spec = None

                try:

                    request_spec = runner.build_request(
                        bound_endpoint, config, request_config
                    )

                    request_config_valid = True

                    request_config_detail = ""

                except Exception as error:

                    request_config_valid = False

                    request_config_detail = (
                        f"The bound endpoint's request (including its "
                        f"Params/Headers/Auth/Body overrides) could "
                        f"not be built: {error}"
                    )

                add(
                    "request_config_valid", request_config_valid,
                    request_config_detail,
                )

                # Section I: "URL/path resolvable" — the endpoint's own
                # URL is either already absolute, or becomes absolute
                # once Test Environment Settings' base URL override is
                # applied (see build_request()'s
                # _apply_base_url_override()). Only meaningful once
                # request_spec itself built successfully above.
                resolved_url = str((request_spec or {}).get("url") or "")

                environment_valid = bool(request_spec) and resolved_url.lower().startswith(
                    ("http://", "https://")
                )

                add(
                    "environment_valid", environment_valid,
                    "" if environment_valid else (
                        "The resolved request URL is not a valid "
                        "absolute http(s) URL — set a Base URL "
                        "override in Test Environment Settings, or fix "
                        "the endpoint's own imported URL."
                    ),
                )

                # Section 10: "method valid" — the resolved method
                # (per-Test-Case override, else the bound endpoint's
                # own imported method) must be one this runner can
                # actually issue.
                resolved_method = str((request_spec or {}).get("method") or "").upper()

                method_valid = resolved_method in self._API_ALLOWED_METHODS

                add(
                    "method_valid", method_valid,
                    "" if method_valid else (
                        f"HTTP method '{resolved_method or '(none)'}' is "
                        f"not supported — use one of: "
                        + ", ".join(self._API_ALLOWED_METHODS)
                    ),
                )

                # Section 10: "JSON body valid when applicable" — only
                # meaningful once the request actually resolved to a
                # JSON body (Raw JSON body mode, or an imported/legacy
                # body whose Content-Type was guessed as JSON).
                body_valid = True

                body_valid_detail = ""

                if request_spec is not None:

                    resolved_content_type = str(
                        (request_spec.get("headers") or {}).get(
                            "Content-Type", ""
                        )
                    ).lower()

                    resolved_body_bytes = request_spec.get("data")

                    if "json" in resolved_content_type and resolved_body_bytes:

                        try:

                            json.loads(resolved_body_bytes.decode("utf-8"))

                        except Exception as body_error:

                            body_valid = False

                            body_valid_detail = (
                                f"Request body is not valid JSON: "
                                f"{body_error}"
                            )

                add("body_valid", body_valid, body_valid_detail)

                if request_spec is not None:

                    body_text_for_preview = ""

                    if request_spec.get("data"):

                        try:

                            body_text_for_preview = request_spec["data"].decode(
                                "utf-8"
                            )

                        except Exception:

                            body_text_for_preview = "<binary body — not shown>"

                    request_preview = {
                        "method": request_spec.get("method"),
                        "url": request_spec.get("url"),
                        "headers": ApiAutomationRunner._mask_headers(
                            request_spec.get("headers")
                        ),
                        "body": ApiAutomationRunner._mask_body_text(
                            body_text_for_preview
                        ),
                    }

                missing_variables = runner.find_missing_variables(
                    bound_endpoint, config, request_config
                )

                add(
                    "variables_available", not missing_variables,
                    "" if not missing_variables else (
                        "Missing values for required variable(s): "
                        + ", ".join(missing_variables)
                        + " — set them in Test Environment Settings."
                    ),
                )

                # Section I: "authentication configuration valid" —
                # narrower than variables_available above: scoped ONLY
                # to the auth-credential override fields (Bearer token
                # / API Key header value / Basic username+password),
                # so an operator sees immediately whether the PROBLEM
                # is specifically their auth setup vs. some unrelated
                # header/body variable.
                missing_secret_variables = runner.find_missing_secret_variables(
                    bound_endpoint, config, request_config
                )

                add(
                    "secret_references_valid", not missing_secret_variables,
                    "" if not missing_secret_variables else (
                        "Missing value(s) for auth credential "
                        "variable(s): "
                        + ", ".join(missing_secret_variables)
                        + " — set them in Test Environment Settings."
                    ),
                )

                # Section F/I: an assertion is configured either the
                # legacy way (an explicit expected status override, or
                # the bound endpoint's own recorded example status) OR
                # via the structured Assertions list (section F) —
                # either is sufficient; Execute (execute_api()) prefers
                # the structured list when present, falling back to
                # the legacy expected-status comparison otherwise (see
                # ApiAutomationRunner.send()'s `assertions` parameter).
                has_assertion = bool(
                    test_case.get("api_expected_status_code")
                    or bound_endpoint.get("example_response_status")
                    or structured_assertions
                )

                if invalid_assertion_types:

                    assertion_detail = (
                        "Unrecognized assertion type(s): "
                        + ", ".join(sorted(set(invalid_assertion_types)))
                        + " — fix or remove these assertions."
                    )

                elif not has_assertion:

                    assertion_detail = (
                        "No expected HTTP status or assertion is "
                        "configured — set an explicit expected status "
                        "(required for negative tests), add an "
                        "Assertion, or import an endpoint whose "
                        "example response has a status."
                    )

                else:

                    assertion_detail = ""

                add(
                    "assertion_configured",
                    has_assertion and not invalid_assertion_types,
                    assertion_detail,
                )

                # REMOVE-SCRIPT-LIFECYCLE item 5: "extraction
                # configuration" is one of the things Validate must
                # actually check (endpoint/binding, method, URL,
                # params, headers, auth, body, variables/secrets,
                # assertions, AND extraction). Extraction itself is
                # optional — an empty list is valid — but any rule
                # that IS configured must have both a Path/Header
                # locator and a Save-as Variable name, or it can never
                # actually extract anything at Execute time.
                extraction_rules = request_config.get("extraction_rules") or []

                incomplete_extraction_rules = []

                for rule in extraction_rules:

                    rule = rule or {}

                    variable_name = str(rule.get("variable") or "").strip()

                    rule_type = str(rule.get("type") or "json_path").strip()

                    locator = str(
                        (
                            rule.get("header") if rule_type == "header"
                            else rule.get("path")
                        ) or ""
                    ).strip()

                    if not variable_name or not locator:

                        incomplete_extraction_rules.append(
                            variable_name or locator or "<blank rule>"
                        )

                add(
                    "extraction_valid",
                    not incomplete_extraction_rules,
                    "" if not incomplete_extraction_rules else (
                        "Incomplete extraction rule(s) — every rule needs "
                        "both a Path/Header and a Save-as Variable name: "
                        + ", ".join(incomplete_extraction_rules)
                    ),
                )

                add("runner_available", True, "")

            else:

                add(
                    "request_config_valid", False,
                    "Bind a real endpoint before the request config "
                    "can be checked.",
                )

                add(
                    "variables_available", False,
                    "Bind a real endpoint before required variables "
                    "can be checked.",
                )

                add(
                    "assertion_configured", False,
                    "Bind a real endpoint before an assertion can be "
                    "checked.",
                )

        elif automation_type == "SQL":

            add(
                "runner_available", True,
                f"{automation_type} automation has no execution runner "
                f"in QA AI Studio yet — only Playwright (browser) and "
                f"API (real HTTP request) can actually run here. Use "
                f"View Script for manual review/execution outside the "
                f"app, then record the result with Record Result.",
            )

        ready = all(check["ok"] for check in checks)

        if validating_selected_source and ready:
            # REMOVE-SCRIPT-LIFECYCLE item 7: API Automation has no Set
            # Active step — a successful Validate must, by itself,
            # mark the saved Request Configuration Ready to Execute
            # (keep_active=True promotes status straight to
            # "Automated", same as an already-Active Playwright/SQL
            # script surviving a re-validate — see
            # TestCaseRepository.mark_script_validated()). Playwright/
            # SQL are unaffected: keep_active stays False there, so
            # Validate alone still never activates a script that
            # wasn't already Active — Set Active remains required.
            self.repository.mark_script_validated(
                test_case_id, source, keep_active=(automation_type == "API")
            )

        return {"ready": ready, "checks": checks, "request_preview": request_preview}

    # --------------------------------------------------
    # Execute — Playwright, background job (persisted)
    # --------------------------------------------------

    def can_auto_execute(self, test_case):

        return (
            test_case.get("automation_type") == "Playwright"
            and test_case.get("execution_type") == "Automatable"
            and test_case.get("execution_tool") == "Playwright"
            and test_case.get("status") == "Automated"
            and self.repository.is_script_source_validated(
                test_case, test_case.get("active_script_source") or "AUTO"
            )
            and bool(TestExecutionManager.get_active_script(test_case))
        )

    def start_execution(
        self, test_case_id, executed_by_user_id=None,
        executed_by_username=None, re_run_of=None,
    ):

        test_case = self.repository.get_test_case(test_case_id)

        if not test_case:

            raise ValueError(f"Test case {test_case_id} not found.")

        if not self.can_auto_execute(test_case):

            raise ValueError(
                "This test case has no Playwright automation script "
                "available yet (or its Automation Type isn't "
                "Playwright) — use Add Automation first, or use "
                "Execute Against Real Server for an API test case."
            )

        run = self.runs.create_run(
            test_case, executed_by_user_id, executed_by_username, re_run_of,
        )

        thread = threading.Thread(
            target=self._run_job, args=(run["run_uuid"], test_case_id),
            daemon=True,
        )

        thread.start()

        return {"job_id": run["run_uuid"], "run_uuid": run["run_uuid"], "status": "Queued"}

    def rerun(self, run_uuid, executed_by_user_id=None, executed_by_username=None):

        old_run = self.runs.get_run(run_uuid)

        if not old_run:

            raise ValueError(f"Run {run_uuid} not found.")

        return self.start_execution(
            old_run["test_case_id"], executed_by_user_id,
            executed_by_username, re_run_of=old_run["id"],
        )

    def _run_job(self, run_uuid, test_case_id):

        self.runs.mark_running(run_uuid)

        # force_headless=True: this runs server-side, no display —
        # see PlaywrightRunner's docstring for why this is safe and
        # necessary (the generated/recorded script's own
        # headless=False is overridden regardless of what it says).
        manager = TestExecutionManager(force_headless=True)

        with _RUNNING_MANAGERS_LOCK:

            _RUNNING_MANAGERS[run_uuid] = manager

        try:

            result = manager.execute_playwright(test_case_id)

            self.runs.mark_finished(run_uuid, result)

        except Exception as ex:

            self.runs.mark_finished(run_uuid, {"error": str(ex)})

        finally:

            with _RUNNING_MANAGERS_LOCK:

                _RUNNING_MANAGERS.pop(run_uuid, None)

    def cancel_execution(self, run_uuid):
        """
        Real cancellation — not a fake button. Only works while the
        job is actually running IN THIS SERVER PROCESS (see
        _RUNNING_MANAGERS's docstring); returns False (not an error)
        for a run that already finished, or one this process doesn't
        recognise (e.g. after a restart) — the caller/frontend should
        treat that as "nothing to cancel" rather than a failure.
        """

        with _RUNNING_MANAGERS_LOCK:

            manager = _RUNNING_MANAGERS.get(run_uuid)

        if not manager:

            return False

        manager.cancel_interactive_run()

        return True

    def get_job(self, run_uuid):

        return self.runs.get_run(run_uuid)

    def list_runs(
        self, domain=None, module=None, knowledge_name=None,
        version=None, document_type=None, source_knowledge_ids=None, test_case_document=None,
        test_case_id=None, status=None, automation_type=None, q=None,
        limit=50, offset=0,
    ):

        scoped_ids = None
        if version or document_type or source_knowledge_ids or test_case_document:
            workspace = self.list_workspace(
                domain=domain, module=module, knowledge_name=knowledge_name, version=version,
                document_type=document_type, source_knowledge_ids=source_knowledge_ids,
                test_case_document=test_case_document, automation_type=automation_type,
                limit=10000, offset=0,
            )
            scoped_ids = [row["id"] for row in workspace["test_cases"]]
        return self.runs.list_runs(
            domain=domain, module=module, knowledge_name=knowledge_name,
            test_case_id=test_case_id, status=status,
            test_case_ids=scoped_ids,
            automation_type=automation_type, q=q,
            limit=limit, offset=offset,
        )

    def get_evidence_path(self, run_uuid):
        """
        Returns a validated, resolved Path to this run's failure
        screenshot, or None if it has none. Re-validates the stored
        path is actually inside EVIDENCE_FOLDER before returning it —
        defence in depth against ever serving an arbitrary file even
        if a row's screenshot_path were ever corrupted/tampered with,
        since the router hands this straight to FileResponse.
        """

        from Core.playwright_runner import EVIDENCE_FOLDER

        run = self.runs.get_run(run_uuid)

        if not run or not run.get("screenshot_path"):

            return None

        resolved = Path(run["screenshot_path"]).resolve()

        evidence_root = EVIDENCE_FOLDER.resolve()

        if evidence_root not in resolved.parents and resolved != evidence_root:

            return None

        if not resolved.is_file():

            return None

        return resolved

    # --------------------------------------------------
    # API endpoint binding — explicit, "never silently guess"
    # resolution shared between Execute (which may auto-bind the
    # single-candidate case) and the Bind API Endpoint UI (which
    # always persists exactly what the operator chose).
    # --------------------------------------------------

    def list_api_endpoint_candidates(self, test_case_id):
        """
        Every real, imported endpoint matching this test case's
        scope (Domain/Module/Knowledge Name), plus which one (if
        any) is currently bound — powers the Bind API Endpoint
        picker. Never narrows or ranks; the operator must see every
        real option.

        initialize_automation=False (section D/E fix): this is a
        read-only lookup — it never needs AutomationGenerator/
        LLMEngine/PlaywrightRunner/TestEnvironmentConfig, all of
        which the default TestExecutionManager() constructor builds
        eagerly. Building them here was the actual cause of "Could
        not load endpoints." (a slow/failing eager init unrelated to
        endpoint listing) surfacing on this read-only picker.
        """

        manager = TestExecutionManager(initialize_automation=False)

        test_case = manager.repository.get_test_case(test_case_id)

        if not test_case:

            raise ValueError(f"Test case {test_case_id} not found.")

        candidates = manager.list_api_endpoints_for_test_case(test_case)

        return {
            "bound_endpoint_id": test_case.get("bound_api_endpoint_id"),
            "endpoints": [
                {
                    "id": item.get("id"),
                    "name": item.get("name"),
                    "method": item.get("method"),
                    "url": item.get("url_resolved") or item.get("url_raw"),
                }
                for item in candidates
            ],
        }

    def bind_api_endpoint(self, test_case_id, endpoint_id):
        """
        Explicitly persists (or, with endpoint_id=None, clears)
        which real, imported endpoint this API test case's
        execution should use. Rejects an endpoint_id that doesn't
        belong to this test case's own scope, so the UI can never
        bind an unrelated endpoint.

        initialize_automation=False (section D/E fix, same rationale
        as list_api_endpoint_candidates() above): binding is a
        read-only-scope-check-then-persist operation, never needs the
        eagerly-built AutomationGenerator/LLMEngine/PlaywrightRunner/
        TestEnvironmentConfig.
        """

        manager = TestExecutionManager(initialize_automation=False)

        test_case = manager.repository.get_test_case(test_case_id)

        if not test_case:

            raise ValueError(f"Test case {test_case_id} not found.")

        if endpoint_id is not None:

            candidates = manager.list_api_endpoints_for_test_case(test_case)

            if not any(item.get("id") == endpoint_id for item in candidates):

                raise ValueError(
                    "The selected endpoint does not belong to this "
                    "test case's scope."
                )

        self.repository.set_bound_api_endpoint(test_case_id, endpoint_id)

        return self.repository.get_test_case(test_case_id)

    def set_api_expected_status(self, test_case_id, expected_status_code):
        """
        Persists the explicit assertion override that makes negative
        testing possible (see ApiAutomationRunner.send()'s
        docstring): an expected HTTP status that overrides the bound
        endpoint's own recorded example_response_status.
        expected_status_code=None clears the override.
        """

        test_case = self.repository.get_test_case(test_case_id)

        if not test_case:

            raise ValueError(f"Test case {test_case_id} not found.")

        if expected_status_code is not None:

            try:

                expected_status_code = int(expected_status_code)

            except (TypeError, ValueError):

                raise ValueError(
                    "Expected HTTP status must be a whole number."
                )

            if not (100 <= expected_status_code <= 599):

                raise ValueError(
                    "Expected HTTP status must be between 100 and 599."
                )

        self.repository.set_api_expected_status_code(
            test_case_id, expected_status_code
        )

        return self.repository.get_test_case(test_case_id)

    # --------------------------------------------------
    # Per-Test-Case API Request Configuration (section F) — Params/
    # Headers/Auth/Body/Assertions/Extraction layered on TOP of the
    # bound endpoint's own imported values and Test Environment
    # Settings (see Core/api_automation_runner.py's build_request()
    # `request_overrides` parameter — this is exactly the JSON it
    # consumes). Stored on the Test Case itself
    # (TestCaseRepository.api_request_config_json), never on the
    # shared ApiCollections endpoint record, since two different Test
    # Cases bound to the SAME endpoint may legitimately want
    # different params/body/assertions for that endpoint.
    # --------------------------------------------------

    _API_REQUEST_CONFIG_KEYS = (
        "method", "url", "query_params", "path_params", "headers",
        "auth", "body", "body_mode", "body_params", "assertions",
        "extraction_rules",
    )

    # POSTMAN-STYLE-END-TO-END-FINAL-COMPLETION section 1: the
    # editable request bar's Method dropdown — the same set
    # ApiAutomationRunner.build_request()/send() actually issue via
    # `requests`.
    _API_ALLOWED_METHODS = (
        "GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS",
    )

    # Section G/P: these auth-override sub-fields are secrets — never
    # returned in plain text by get_api_request_config() (mirrors the
    # already-established EnvironmentConfigWeb masked-on-read /
    # blank-preserves-on-write pattern below).
    _API_REQUEST_AUTH_SECRET_FIELDS = ("token", "password", "header_value")

    def _parsed_api_request_config(self, test_case):
        """
        The RAW, unmasked stored config — every secret field intact.
        Only ever used internally (by set_api_request_config()'s
        merge-on-write below, and by execute_api() to build the REAL
        outgoing request) — never returned directly to a caller
        outside this class. get_api_request_config() is the
        masked-for-display counterpart callers outside this class
        should use instead.
        """

        try:

            return json.loads(
                test_case.get("api_request_config_json") or "{}"
            ) or {}

        except (TypeError, ValueError):

            return {}

    def get_api_request_config(self, test_case_id):
        """
        The masked-for-display config — powers the Request
        Configuration UI (section F). Every secret auth field
        (token/password/header_value) comes back blank, with a
        companion "<field>_is_set" boolean so the UI can show
        "Bearer ********" / a "configured" indicator without ever
        receiving the real value — mirrors
        EnvironmentConfigWeb.get()'s established masked-on-read
        pattern exactly.
        """

        test_case = self.repository.get_test_case(test_case_id)

        if not test_case:

            raise ValueError(f"Test case {test_case_id} not found.")

        config = self._parsed_api_request_config(test_case)

        auth = dict(config.get("auth") or {})

        for field in self._API_REQUEST_AUTH_SECRET_FIELDS:

            auth[f"{field}_is_set"] = bool(auth.get(field))

            auth[field] = ""

        config["auth"] = auth

        return config

    def set_api_request_config(self, test_case_id, request_config):
        """
        Persists ONLY the parts of the request config the caller
        actually sent — a key absent from `request_config` leaves
        that part of the stored config untouched (so, e.g., saving
        just an edited Header list never wipes out an already-saved
        Body). Mirrors EnvironmentConfigWeb.update()'s established
        "blank/absent secret preserves the existing value" rule for
        the nested auth sub-fields specifically: a blank
        token/password/header_value in the incoming payload means
        "the operator didn't change this secret" (see
        get_api_request_config() above — the UI never gets the real
        value back to re-submit), NOT "clear it".

        Persisting any part of this config demotes an AUTO-active
        Test Case back to Draft and clears its validated-config
        fingerprint (see TestCaseRepository._update_api_config_field(),
        the same shared helper set_bound_api_endpoint()/
        set_api_expected_status_code() already use) — a changed
        request definition invalidates whatever was previously
        Validated/Set Active, exactly like changing the bound
        endpoint or the expected status already does.
        """

        test_case = self.repository.get_test_case(test_case_id)

        if not test_case:

            raise ValueError(f"Test case {test_case_id} not found.")

        existing = self._parsed_api_request_config(test_case)

        request_config = request_config or {}

        merged = dict(existing)

        for key in self._API_REQUEST_CONFIG_KEYS:

            if key not in request_config:

                continue

            if key == "auth":

                new_auth = dict(request_config.get("auth") or {})

                existing_auth = dict(existing.get("auth") or {})

                for field in self._API_REQUEST_AUTH_SECRET_FIELDS:

                    if not new_auth.get(field):

                        new_auth[field] = existing_auth.get(field, "")

                merged["auth"] = new_auth

            elif key == "method":

                # POSTMAN-STYLE-END-TO-END-FINAL-COMPLETION section 1:
                # an explicit, blank method clears the override (falls
                # back to the bound endpoint's own method); a non-blank
                # value must be one of the methods the runner actually
                # supports — validated here rather than left to fail
                # confusingly deep inside build_request()/requests.
                raw_method = str(request_config.get("method") or "").strip().upper()

                if raw_method and raw_method not in self._API_ALLOWED_METHODS:

                    raise ValueError(
                        f"'{raw_method}' is not a supported HTTP method "
                        f"— use one of: {', '.join(self._API_ALLOWED_METHODS)}."
                    )

                merged["method"] = raw_method

            else:

                merged[key] = request_config[key]

        self.repository.set_api_request_config(
            test_case_id, json.dumps(merged)
        )

        return self.get_api_request_config(test_case_id)

    def _resolve_bound_endpoint(
        self, test_case, manager, explicit_endpoint_id=None
    ):
        """
        The single "never silently guess" endpoint-resolution rule.

        - explicit_endpoint_id given: must belong to this test
          case's scope; persisted as the new binding and returned.
        - else, an already-bound endpoint that's still a valid
          candidate for this scope: reused as-is, no re-prompt.
        - else, exactly ONE real candidate exists for this scope:
          auto-bound (persisted) and returned — the one case where
          the app may choose without asking, since there is
          genuinely only one possible answer.
        - else (zero candidates, or 2+ with none currently bound):
          returns (None, candidates) — caller must have the operator
          choose explicitly.

        Returns (endpoint_or_None, candidates_list).
        """

        candidates = manager.list_api_endpoints_for_test_case(test_case)

        if explicit_endpoint_id is not None:

            endpoint = next(
                (
                    item for item in candidates
                    if item.get("id") == explicit_endpoint_id
                ),
                None,
            )

            if endpoint is None:

                raise ValueError(
                    "The selected endpoint does not belong to this "
                    "test case's scope."
                )

            self.repository.set_bound_api_endpoint(
                test_case["id"], endpoint["id"]
            )

            return endpoint, candidates

        bound_id = test_case.get("bound_api_endpoint_id")

        if bound_id:

            endpoint = next(
                (item for item in candidates if item.get("id") == bound_id),
                None,
            )

            if endpoint is not None:

                return endpoint, candidates

        if len(candidates) == 1:

            endpoint = candidates[0]

            self.repository.set_bound_api_endpoint(
                test_case["id"], endpoint["id"]
            )

            return endpoint, candidates

        return None, candidates

    # --------------------------------------------------
    # Execute — API, real HTTP request against the real,
    # imported endpoint (never the AI-generated script text — see
    # Core/api_automation_runner.py's module docstring for why).
    # --------------------------------------------------

    def execute_api(
        self, test_case_id, endpoint_id=None, executed_by_user_id=None,
        executed_by_username=None,
    ):

        manager = TestExecutionManager()

        test_case = manager.repository.get_test_case(test_case_id)

        if not test_case:

            raise ValueError(f"Test case {test_case_id} not found.")

        if (test_case.get("automation_type") or "") != "API":

            raise ValueError(
                "This test case's Automation Type is not 'API'."
            )

        if (
            test_case.get("execution_type") != "Automatable"
            or test_case.get("execution_tool") != "API Automation"
        ):

            raise ValueError(
                "This Test Case is not eligible for API Automation."
            )

        if test_case.get("status") != "Automated":

            raise ValueError(
                "The API automation asset is still Draft. Validate and "
                "activate it before execution."
            )

        endpoint, endpoints = self._resolve_bound_endpoint(
            test_case, manager, explicit_endpoint_id=endpoint_id
        )

        if endpoint is None:

            if not endpoints:

                raise ValueError(
                    "No imported API Collection endpoint matches this "
                    "test case's Domain / Module / Knowledge Name — "
                    "import a Postman Collection for this scope first "
                    "(Knowledge Hub -> Upload -> Source Type: API "
                    "Collection)."
                )

            return {
                "executed": False,
                "selection_required": True,
                "missing_variables": [],
                "endpoints": [
                    {
                        "id": item.get("id"), "name": item.get("name"),
                        "method": item.get("method"),
                        "url": item.get("url_resolved") or item.get("url_raw"),
                    }
                    for item in endpoints
                ],
            }

        config = TestEnvironmentConfig().load()

        runner = ApiAutomationRunner()

        # Section F: the per-Test-Case Params/Headers/Auth/Body/
        # Assertions/Extraction definition — the RAW, unmasked config
        # (never get_api_request_config()'s masked-for-display
        # version, which blanks the secret auth fields) since this is
        # what actually builds and sends the real outgoing request.
        request_config = self._parsed_api_request_config(test_case)

        missing = runner.find_missing_variables(
            endpoint, config, request_config
        )

        endpoint_summary = {
            "id": endpoint.get("id"),
            "name": endpoint.get("name"),
            "method": endpoint.get("method"),
            "url": endpoint.get("url_resolved") or endpoint.get("url_raw"),
        }

        if missing:

            return {
                "executed": False,
                "missing_variables": missing,
                "endpoint": endpoint_summary,
            }

        run = self.runs.create_run(
            test_case, executed_by_user_id, executed_by_username,
        )

        self.runs.mark_running(run["run_uuid"])

        # Section I/J: the structured Assertions list (section F), when
        # configured, is what actually decides Pass/Fail (an expected
        # JSON field mismatch FAILS even on HTTP 200) — send() falls
        # back to the legacy single expected-status comparison when no
        # structured assertions exist for this test case.
        structured_assertions = request_config.get("assertions") or []

        result = runner.send(
            endpoint, config,
            expected_status_override=test_case.get(
                "api_expected_status_code"
            ),
            request_overrides=request_config,
            assertions=structured_assertions or None,
        )

        # Section I/J: exactly ONE request has already been sent, above
        # — everything below only interprets/persists that single
        # result; extraction/history never triggers a second request.

        extracted_variables = {}

        if not result.get("error"):

            extraction_rules = request_config.get("extraction_rules") or []

            if extraction_rules:

                extracted_variables = runner.extract_variables(
                    extraction_rules, result
                )

                # Section J: "extracted variable reuse in subsequent
                # request" — remembered into Test Environment Settings'
                # api_variables via the SAME dialog-facing method an
                # operator's own variable-prompt answer already uses,
                # so a later run's {{variable}} resolves to this run's
                # extracted value automatically. A remember failure
                # must never invalidate an otherwise-completed
                # execution — the run's own result still carries the
                # extracted value either way.
                for variable_name, variable_value in extracted_variables.items():

                    try:

                        TestEnvironmentConfig().remember_api_variable(
                            variable_name, variable_value
                        )

                    except Exception:

                        pass

            # REMOVE-SCRIPT-LIFECYCLE item 13: `extracted_variables`
            # above holds the REAL extracted value (e.g. a real
            # access_token) — it was only ever needed to remember it
            # into Environment Settings, just above. From this point
            # on this function must never let a real extracted value
            # escape again: not in the HTTP response returned to the
            # browser, and not in the run history `stdout` blob
            # persisted below (both used to embed the raw dict
            # directly). Keep the real NAMES (still useful — see
            # extracted_variable_names use in Assertions/Extraction UI
            # and the "chained variable reuse" story) but replace every
            # value with a fixed mask; the real value now lives only in
            # Environment Settings (Section G's existing masked-on-read
            # secret handling already covers that).
            masked_extracted_variables = {
                name: "******" for name in sorted(extracted_variables.keys())
            }

            result["extracted_variables"] = masked_extracted_variables

        else:

            masked_extracted_variables = {}

        # Section P: the RAW response (headers + body) that send()
        # returns is intentionally unmasked up to this point — both
        # assertion evaluation (already done, inside send()) and
        # variable extraction (just above) need the real values (e.g.
        # to actually read out a real access_token). But a login-style
        # response body routinely echoes back the very secret this
        # test case's config never let onto the wire unmasked (its
        # own request side is already masked at result["request"] by
        # send() itself) — so response_headers/response_body_text
        # must be masked here, AFTER extraction, before this result
        # is persisted to history or returned to Run Details/the UI.
        result["response_headers"] = ApiAutomationRunner._mask_headers(
            result.get("response_headers")
        )

        result["response_body_text"] = ApiAutomationRunner._mask_body_text(
            result.get("response_body_text")
        )

        if result.get("error"):

            finished = self.runs.mark_finished(run["run_uuid"], {
                "error": result["error"],
                "duration": (result.get("elapsed_ms") or 0) / 1000,
                "stderr": result["error"],
            })

        elif result.get("auto_verdict") == "Pass":

            finished = self.runs.mark_finished(run["run_uuid"], {
                "success": True,
                "duration": (result.get("elapsed_ms") or 0) / 1000,
                "stdout": json.dumps({
                    "endpoint": endpoint_summary,
                    "status_code": result.get("status_code"),
                    "expected_status": result.get("expected_status"),
                    "assertions": result.get("assertions") or [],
                    "extracted_variables": masked_extracted_variables,
                }),
            })

            self.repository.update_result(test_case_id, "Pass")

        elif result.get("auto_verdict") == "Fail":

            # Section J's explicit examples: expected 400 + actual
            # 200 -> FAIL; an expected JSON field value mismatch ->
            # FAIL even on HTTP 200 — both now arrive here as an
            # explicit "Fail" auto_verdict from send()/
            # evaluate_assertions(), rather than being inferred from
            # "some expected_status happened to be set".
            failed_assertions = [
                item for item in (result.get("assertions") or [])
                if not item.get("passed")
            ]

            if failed_assertions:

                failure_detail = "; ".join(
                    f"{item.get('label') or item.get('type')}: "
                    f"{item.get('message')}"
                    for item in failed_assertions
                )

            else:

                failure_detail = (
                    f"Expected HTTP {result.get('expected_status')}; "
                    f"received {result.get('status_code')}."
                )

            finished = self.runs.mark_finished(run["run_uuid"], {
                "success": False,
                "duration": (result.get("elapsed_ms") or 0) / 1000,
                "stderr": failure_detail,
                "stdout": json.dumps({
                    "endpoint": endpoint_summary,
                    "status_code": result.get("status_code"),
                    "expected_status": result.get("expected_status"),
                    "assertions": result.get("assertions") or [],
                    "extracted_variables": masked_extracted_variables,
                }),
            })

            self.repository.update_result(test_case_id, "Fail")

        else:

            finished = self.runs.mark_finished(run["run_uuid"], {
                "error": "Blocked: no expected HTTP status or assertion "
                "is configured; the result cannot be judged Pass or "
                "Fail.",
                "duration": (result.get("elapsed_ms") or 0) / 1000,
            })

        return {
            "executed": True,
            "missing_variables": [],
            "endpoint": endpoint_summary,
            "result": result,
            "run": finished,
        }


# ============================================================
# SQL Automation (new — QA-AUTOMATION-FINAL-ARCHITECTURE-04)
# ============================================================


class SqlAutomationWeb:
    """
    "SQL" already existed as a per-row Automation Type choice on
    Desktop (see test_execution_page.py's AUTOMATION_TYPES) but had
    no runner — selecting it always fell into "Manual Review
    Required — no automatic runner yet". This class is the missing
    runner's web-facing wrapper.

    Reuses, rather than duplicates:
        - TestCaseRepository / test_cases (automation_type='SQL',
          automation_script holds this class's own small JSON
          envelope — see _pack_script()/_unpack_script() — not raw
          Python, so it's never confused with a Playwright script).
        - AutomationExecutionRepository / automation_runs (same
          table Playwright and API runs already use — see
          execute() below for how a SQL result is mapped onto its
          generic Pass/Fail/Error columns).
        - Core/sql_generator.py's SQLGenerator (already built, and
          already registered in AIOrchestrator for the AI Assistant
          — reused here as-is for its own built-in "never invent
          SQL when no schema is available" safety behavior, not
          re-implemented).
    New, because nothing like it existed before:
        - Core/sql_environment_config.py (target-database connection
          profile).
        - Core/sql_automation_runner.py (the read-only safety gate +
          actual query execution + assertion evaluation).
    """

    def __init__(self):

        self.repository = TestCaseRepository()

        self.runs = AutomationExecutionRepository()

        self.env = SqlEnvironmentConfig()

        self.runner = SqlAutomationRunner()

    # --------------------------------------------------
    # Script envelope: automation_script for a SQL-type test case is
    # always this JSON shape, never raw SQL text alone — an
    # assertion rule with no clear way to store it next to the query
    # would otherwise have nowhere real to live.
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

        # Anything else (plain text — e.g. an older/hand-typed
        # value, or a generated-but-not-yet-saved draft) is treated
        # as the SQL body itself with no assertion configured yet,
        # rather than raised as a corrupt-data error — Save Script
        # always writes the JSON envelope going forward.
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

        # API-SQL-AUTOMATION-END-TO-END: the frontend always POSTs
        # database_schema as {} (never a real schema string), so
        # without this, AI Generate SQL had no real schema to ground
        # itself in and would either invent table/column names or
        # (correctly, but uselessly) always refuse. Auto-introspect
        # the operator's configured SQL Environment connection
        # instead — real, read-only (SqlAutomationRunner.describe_schema()
        # only ever runs sqlite_master/information_schema/
        # INFORMATION_SCHEMA introspection queries, never touches
        # data) — so generation is grounded in the REAL target schema
        # whenever a connection is configured, and still safely
        # refuses (via SQLGenerator's own "no schema" handling) when
        # it isn't.
        if not (database_schema or "").strip():

            profile = self.env.load()

            if SqlEnvironmentConfig._is_ready(profile):

                try:

                    database_schema = self.runner.describe_schema(profile)

                except Exception as error:

                    return {
                        "test_case": test_case,
                        "sql": "",
                        "raw_answer": "",
                        "needs_review": True,
                        "validation_error": (
                            f"Could not read the configured database's "
                            f"schema: {error}"
                        ),
                    }

                if not database_schema:

                    return {
                        "test_case": test_case,
                        "sql": "",
                        "raw_answer": "",
                        "needs_review": True,
                        "validation_error": (
                            "The configured database has no readable "
                            "tables — nothing to generate SQL against."
                        ),
                    }

        generator = SQLGenerator()

        result = generator.generate(
            requirement=requirement,
            context=None,
            database_schema=database_schema,
        )

        answer = result.get("answer") or ""

        # Pull just the "SQL Query:" section out of SQLGenerator's
        # structured text block (see its own module docstring/
        # _build_prompt()) — the rest (Purpose/Expected Result/
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
        # gate a manual save/execute goes through — never a
        # relaxed/AI-only check. AI Generate SQL must validate as
        # read-only BEFORE placing anything into the editor — an
        # invalid/non-SQL AI result must show a clear error and must
        # NOT populate the editor (i.e. must NOT be persisted into
        # automation_script at all), per the explicit spec. If the
        # schema was unavailable, SQLGenerator already deliberately
        # did not produce executable SQL (see its
        # _build_prompt()/_validate_output()), so this will
        # legitimately fail validation here too.
        try:

            from Core.sql_automation_runner import validate_readonly_sql

            validate_readonly_sql(sql_text)

        except SqlValidationError:

            return {
                "test_case": test_case,
                "sql": "",
                "raw_answer": answer,
                "needs_review": True,
                "validation_error": (
                    "AI did not return a valid read-only SQL query."
                ),
            }

        script_json = self._pack_script(
            sql_text, "row_exists", None, None
        )

        self.repository.update_automation(test_case_id, "SQL", script_json)

        # A generated-and-valid draft is deliberately NOT marked
        # Automated by this same call — set_active()/validate_sql()
        # is the only path that flips status, exactly like the
        # Playwright/API AI-generation flow's own "generated text
        # exists" vs "status says Automated" distinction.
        return {
            "test_case": self.repository.get_test_case(test_case_id),
            "sql": sql_text,
            "raw_answer": answer,
            "needs_review": False,
            "validation_error": None,
        }

    # --------------------------------------------------
    # Save / validate
    # --------------------------------------------------

    # API-SQL-AUTOMATION-END-TO-END: which assertion types require a
    # Column and/or an Expected Value, enforced by
    # _validate_assertion_config() before Set Active — matches the
    # explicit spec: row_exists/no_rows need neither; row_count_equals
    # /first_value_equals (scalar_equals) need only Expected Value;
    # column_value_equals (value_equals) needs both.
    _ASSERTION_REQUIREMENTS = {
        "row_exists": {"column": False, "value": False},
        "no_rows": {"column": False, "value": False},
        "row_count_equals": {"column": False, "value": True},
        "scalar_equals": {"column": False, "value": True},
        "value_equals": {"column": True, "value": True},
    }

    @classmethod
    def _validate_assertion_config(
        cls, assertion_type, assertion_value, assertion_column
    ):

        assertion_type = assertion_type or "row_exists"

        requirements = cls._ASSERTION_REQUIREMENTS.get(assertion_type)

        if requirements is None:

            raise SqlValidationError(
                f"Unknown assertion type: {assertion_type}"
            )

        has_value = assertion_value is not None and str(assertion_value).strip() != ""

        has_column = assertion_column is not None and str(assertion_column).strip() != ""

        if requirements["value"] and not has_value:

            raise SqlValidationError(
                f"Assertion type '{assertion_type}' requires an "
                f"Expected Value."
            )

        if requirements["column"] and not has_column:

            raise SqlValidationError(
                f"Assertion type '{assertion_type}' requires a Column."
            )

        return True

    def save_script(self, test_case_id, sql, assertion_type,
                     assertion_value, assertion_column):

        test_case = self.repository.get_test_case(test_case_id)

        if not test_case:

            raise ValueError(f"Test case {test_case_id} not found.")

        # API-SQL-AUTOMATION-END-TO-END: reject obviously non-SQL
        # content (Python/Selenium/Playwright/HTTP code pasted into
        # the wrong slot) at Save time already — deliberately looser
        # than validate_readonly_sql() (still the full gate at
        # Validate/Set Active), so an imperfect/in-progress SQL draft
        # can still be saved and iterated on.
        if sql and _NON_SQL_CONTENT_PATTERN.search(sql):

            raise SqlValidationError(
                "This does not look like SQL — Python, Selenium, "
                "Playwright, or HTTP client code cannot be saved as "
                "a SQL Automation script."
            )

        script_json = self._pack_script(
            sql, assertion_type, assertion_value, assertion_column
        )

        self.repository.update_automation(test_case_id, "SQL", script_json)

        return self.repository.get_test_case(test_case_id)

    def validate_sql(self, test_case_id):
        """
        Read-only safety + syntax gate, AND assertion-config gate —
        together the ONLY thing that decides whether this test
        case's saved SQL is allowed to become Active for execution
        (see set_active()). A query (or assertion config) that fails
        here can stay saved as a Draft, exactly per this task's
        "Invalid scripts may be retained as Draft/Validation Failed
        but must NOT be Active for Execution" rule.
        """

        test_case = self.repository.get_test_case(test_case_id)

        if not test_case:

            raise ValueError(f"Test case {test_case_id} not found.")

        script = self._unpack_script(test_case.get("automation_script"))

        try:

            from Core.sql_automation_runner import validate_readonly_sql

            cleaned = validate_readonly_sql(script.get("sql"))

            self._validate_assertion_config(
                script.get("assertion_type"),
                script.get("assertion_value"),
                script.get("assertion_column"),
            )

            return {"valid": True, "cleaned_sql": cleaned, "error": None}

        except SqlValidationError as ex:

            return {"valid": False, "cleaned_sql": None, "error": str(ex)}

    def set_active(self, test_case_id, active):
        """
        `active` is a bool. Setting True re-runs validate_sql() first
        and refuses (raises ValueError) if it fails — there is no
        way to force an unsafe/invalid query to Active from this
        method. Mirrors active_script_source for Playwright, but as
        a plain boolean rather than a source picker (SQL has exactly
        one script slot, no separate AI/Manual variant).
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

        else:

            self.repository.update_status(test_case_id, "Draft")

        return self.repository.get_test_case(test_case_id)

    # --------------------------------------------------
    # Execute
    # --------------------------------------------------

    def execute(self, test_case_id, executed_by_user_id=None,
                executed_by_username=None):

        test_case = self.repository.get_test_case(test_case_id)

        if not test_case:

            raise ValueError(f"Test case {test_case_id} not found.")

        if (test_case.get("automation_type") or "") != "SQL":

            raise ValueError("This test case's Automation Type is not 'SQL'.")

        if (
            test_case.get("execution_type") != "Automatable"
            or test_case.get("execution_tool") != "SQL Automation"
        ):

            raise ValueError(
                "This Test Case is not eligible for SQL Automation."
            )

        if test_case.get("status") != "Automated":

            raise ValueError(
                "The SQL script is still Draft. Validate and activate it first."
            )

        validation = self.validate_sql(test_case_id)

        if not validation["valid"]:

            raise ValueError(
                f"This query is not safe/valid to execute: "
                f"{validation['error']}"
            )

        script = self._unpack_script(test_case.get("automation_script"))

        run = self.runs.create_run(
            test_case, executed_by_user_id=executed_by_user_id,
            executed_by_username=executed_by_username,
        )

        self.runs.mark_running(run["run_uuid"])

        profile = self.env.load()

        query_result = self.runner.execute_query(
            profile, validation["cleaned_sql"]
        )

        if not query_result.get("success"):

            finished = self.runs.mark_finished(run["run_uuid"], {
                "error": query_result.get("error"),
                "duration": query_result.get("duration_seconds"),
            })

            self.repository.update_result(test_case_id, "Error")

            return finished

        assertion = self.runner.evaluate_assertion(
            script.get("assertion_type"), script.get("assertion_value"),
            script.get("assertion_column"), query_result,
        )

        summary_lines = [
            f"SQL: {validation['cleaned_sql']}",
            f"Columns: {', '.join(query_result.get('columns') or [])}",
            f"Row count: {query_result.get('row_count')}"
            + (" (truncated)" if query_result.get("truncated") else ""),
        ]

        for row in (query_result.get("rows") or [])[:20]:

            summary_lines.append(str(row))

        summary_lines.append(f"Assertion: {assertion['message']}")

        finished = self.runs.mark_finished(run["run_uuid"], {
            "success": assertion["outcome"] == "Pass",
            "duration": query_result.get("duration_seconds"),
            "stdout": "\n".join(summary_lines),
            "stderr": assertion["message"],
        })

        self.repository.update_result(
            test_case_id, assertion["outcome"]
        )

        return finished
