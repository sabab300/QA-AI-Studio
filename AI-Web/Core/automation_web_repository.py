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
from Core.api_automation_runner import ApiAutomationRunner

GIT_WORKSPACE_ROOT = Path(__file__).resolve().parent.parent / "Output" / "GitWorkspaces"


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
        knowledge_name=None, version=None,
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

    def list_test_cases(self, domain, module, knowledge_name):

        return self.repository.list_test_cases(domain, module, knowledge_name)

    def list_workspace(
        self, domain=None, module=None, knowledge_name=None,
        status=None, automation_type=None, q=None, limit=50, offset=0,
    ):
        """
        Backs the Automation Workspace landing page: every automation
        asset across every scope, each with its latest execution
        result/time attached (a single extra query, not one per row —
        see AutomationExecutionRepository.latest_runs_for_test_cases()).
        """

        result = self.repository.list_all_test_cases(
            domain=domain, module=module, knowledge_name=knowledge_name,
            status=status, automation_type=automation_type, q=q,
            limit=limit, offset=offset,
        )

        test_case_ids = [tc["id"] for tc in result["test_cases"]]

        latest_runs = self.runs.latest_runs_for_test_cases(test_case_ids)

        for test_case in result["test_cases"]:

            test_case["latest_run"] = latest_runs.get(test_case["id"])

        return result

    def list_scopes(self):

        return self.repository.list_distinct_scopes()

    def get_test_case(self, test_case_id):

        return self.repository.get_test_case(test_case_id)

    def delete_test_case(self, test_case_id):

        return self.repository.delete_test_case(test_case_id)

    # --------------------------------------------------
    # Import
    # --------------------------------------------------

    def import_from_excel(self, file_bytes, original_filename, domain, module, knowledge_name, version=None):

        if not file_bytes:

            raise ValueError("An Excel (.xlsx) file is required.")

        safe_name = Path(original_filename or "test_cases.xlsx").name or "test_cases.xlsx"

        temp_dir = tempfile.mkdtemp(prefix="qaais_tc_")
        temp_path = Path(temp_dir) / safe_name

        temp_path.write_bytes(file_bytes)

        try:

            manager = TestExecutionManager()

            result = manager.import_test_cases_from_excel(
                file_path=str(temp_path), domain=domain, module=module,
                knowledge_name=knowledge_name, version=version,
            )

        finally:

            shutil.rmtree(temp_dir, ignore_errors=True)

        if not result.get("success") and result.get("error"):

            raise ValueError(result["error"])

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

        manager.set_active_script(test_case_id, source)

        return self.get_test_case(test_case_id)

    # --------------------------------------------------
    # Validation before execution (spec: "Do not claim a script is
    # valid merely because the file exists.")
    # --------------------------------------------------

    def validate_for_execution(self, test_case_id):

        manager = TestExecutionManager()

        test_case = manager.repository.get_test_case(test_case_id)

        if not test_case:

            raise ValueError(f"Test case {test_case_id} not found.")

        automation_type = test_case.get("automation_type") or "None"

        checks = []

        def add(name, ok, detail=""):

            checks.append({"check": name, "ok": bool(ok), "detail": detail})

        add(
            "automation_type_set", automation_type != "None",
            "" if automation_type != "None" else (
                "No Automation Type is set on this test case yet."
            ),
        )

        script = TestExecutionManager.get_active_script(test_case)

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

            try:

                endpoints = manager.get_relevant_endpoints_for_test_case(test_case)

            except Exception:

                endpoints = []

            add(
                "endpoint_available", bool(endpoints),
                "" if endpoints else (
                    "No imported API Collection endpoint matches this "
                    "test case's Domain / Module / Knowledge Name — "
                    "import a Postman Collection for this scope first."
                ),
            )

        elif automation_type in ("Selenium", "SQL"):

            add(
                "runner_available", False,
                f"{automation_type} automation has no execution runner "
                f"in QA AI Studio yet — only Playwright (browser) and "
                f"API (real HTTP request) can actually run here. Use "
                f"View Script for manual review/execution outside the "
                f"app, then record the result with Record Result.",
            )

        ready = all(check["ok"] for check in checks)

        return {"ready": ready, "checks": checks}

    # --------------------------------------------------
    # Execute — Playwright, background job (persisted)
    # --------------------------------------------------

    def can_auto_execute(self, test_case):

        return (
            test_case.get("automation_type") == "Playwright"
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
        test_case_id=None, status=None, q=None, limit=50, offset=0,
    ):

        return self.runs.list_runs(
            domain=domain, module=module, knowledge_name=knowledge_name,
            test_case_id=test_case_id, status=status, q=q,
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
    # Execute — API, real HTTP request against the real,
    # imported endpoint (never the AI-generated script text — see
    # Core/api_automation_runner.py's module docstring for why).
    # --------------------------------------------------

    def execute_api(self, test_case_id):

        manager = TestExecutionManager()

        test_case = manager.repository.get_test_case(test_case_id)

        if not test_case:

            raise ValueError(f"Test case {test_case_id} not found.")

        if (test_case.get("automation_type") or "") != "API":

            raise ValueError(
                "This test case's Automation Type is not 'API'."
            )

        endpoints = manager.get_relevant_endpoints_for_test_case(test_case)

        if not endpoints:

            raise ValueError(
                "No imported API Collection endpoint matches this "
                "test case's Domain / Module / Knowledge Name — "
                "import a Postman Collection for this scope first "
                "(Knowledge Hub -> Upload -> Source Type: API "
                "Collection)."
            )

        endpoint = endpoints[0]

        config = TestEnvironmentConfig().load()

        runner = ApiAutomationRunner()

        missing = runner.find_missing_variables(endpoint, config)

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

        result = runner.send(endpoint, config)

        return {
            "executed": True,
            "missing_variables": [],
            "endpoint": endpoint_summary,
            "result": result,
        }
