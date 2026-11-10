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

import hashlib
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
from Core.token_configuration_repository import TokenConfigurationRepository
from Core.qa_engineering_web_repository import QaEngineeringWeb
from Core.api_automation_runner import ApiAutomationRunner
from Core.secret_masking import mask_headers, is_sensitive_key, MASK
from Core.sql_environment_config import SqlEnvironmentConfig
from Core.sql_automation_runner import SqlAutomationRunner, SqlValidationError
from Core.sql_generator import SQLGenerator
from Core.sql_script_repository import SqlScriptRepository
from Shared.central_repository import CentralRepository

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
        test_case_version_id=None, test_case_file_id=None,
    ):
        # QA-AI-STUDIO-FINAL-ARCHITECTURE-UI-AUTOMATION-COMPLETION-V4
        # Rule 5: test_case_version_id is new and OPTIONAL — added
        # additively so the pre-existing call shape keeps working
        # unchanged for anything that doesn't pass it. When given, it
        # binds the upload to a real TC-* folder (see
        # ApiCollectionRepository.import_postman_collection()'s own
        # docstring for the storage model this enables); the frontend's
        # rebuilt "API Collection Upload" modal (Frontend/index.html)
        # always sends it now, since Source Files there only offers
        # TC-* folders — but this method itself stays usable by any
        # other caller that doesn't have one yet.

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
                test_case_version_id=test_case_version_id,
                test_case_file_id=test_case_file_id,
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

        # FINAL-END-TO-END-WORKSPACE-V2 section 10/19: this used to
        # hand back the raw `api_variables` dict as-is — a genuine
        # secret (e.g. a bearer token remembered from a real run)
        # came back in PLAINTEXT in this response (confirmed live —
        # the "Remembered {{variable}} Values" table showing an
        # unmasked JWT). Now every variable is exposed ONLY through
        # this masked `variables` list, and the raw
        # api_variables/api_variable_secrets dicts are stripped from
        # the response entirely. Nothing else in the web layer reads
        # them off get()'s return value — ApiAutomationRunner and the
        # generation prompt builder both read the config file
        # directly via TestEnvironmentConfig, never through this
        # masked view, so this cannot break variable resolution.
        raw_variables = data.get("api_variables") or {}

        secret_flags = data.get("api_variable_secrets") or {}

        variables = []

        for name in sorted(raw_variables.keys()):

            value = raw_variables.get(name) or ""

            is_secret = secret_flags.get(name)

            if is_secret is None:

                is_secret = is_sensitive_key(name)

            variables.append({
                "name": name,
                "value": MASK if (is_secret and value) else value,
                "secret": bool(is_secret),
                "value_is_set": bool(value),
            })

        masked["variables"] = variables

        masked.pop("api_variables", None)

        masked.pop("api_variable_secrets", None)

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
        """
        Bulk upsert, `{name: value}` — kept for callers that don't
        need to set a secret flag explicitly (each name falls back to
        Core.secret_masking.is_sensitive_key() the same as everywhere
        else). Used e.g. when a real API run pauses to ask the
        operator for one or more missing {{variable}} values and
        wants to remember every answer in one call.
        """

        for name, value in (variables or {}).items():

            self.config.remember_api_variable(name, value)

        return self.get()

    def upsert_variable(self, name, value, secret=None):
        """
        Add-or-edit ONE remembered {{variable}} (section 10's CRUD
        grid Add/Save row) — `secret` explicitly sets/clears the
        masked flag; None preserves the existing flag for an edit, or
        infers it from the name for a brand-new variable. Rejects a
        blank name so the CRUD grid can't create an unusable row.

        Same "blank value means keep what's already stored" rule
        applied to every other secret field in this file's update() —
        a secret's real value is never sent back to the client (get()
        masks it to ******), so the CRUD grid's edit form can only
        ever submit a blank value for an untouched secret, and reading
        that blank as "clear it" would silently wipe the secret every
        time the operator edits just the Secret checkbox or another
        field. A non-secret value keeps supporting an explicit empty
        clear, since its real value IS visible to edit in the first
        place.
        """

        name = (name or "").strip()

        if not name:

            raise ValueError("Variable name is required.")

        existing_data = self.config.load()

        existing_variables = existing_data.get("api_variables") or {}

        existing_secret_flags = existing_data.get("api_variable_secrets") or {}

        is_update = name in existing_variables

        is_secret = secret

        if is_secret is None:

            is_secret = existing_secret_flags.get(name)

        if is_secret is None:

            is_secret = is_sensitive_key(name)

        if is_update and is_secret and not value:

            value = existing_variables.get(name, "")

        self.config.remember_api_variable(name, value or "", secret=secret)

        return self.get()

    def delete_variable(self, name):
        """Removes one remembered {{variable}} (CRUD grid Delete)."""

        name = (name or "").strip()

        if name:

            self.config.forget_api_variable(name)

        return self.get()

    # These are the ONLY request_config keys forwarded to
    # ApiAutomationRunner.build_request()/send() — deliberately the
    # same override vocabulary the per-Test-Case Request Configuration
    # tabs already use (TestCasesWeb._API_REQUEST_CONFIG_KEYS), plus
    # the two Settings-tab-only overrides (timeout_seconds/verify_ssl)
    # build_request() now also understands. Anything else in the
    # payload (e.g. an unrelated field) is silently dropped rather
    # than forwarded, so this can never become a second, looser
    # request-building path.
    _TOKEN_REQUEST_CONFIG_KEYS = (
        "method", "url", "query_params", "path_params", "headers",
        "auth", "body", "body_mode", "body_params",
        "timeout_seconds", "verify_ssl",
    )

    def generate_token(
        self, request_config=None,
        extract_type="json_path", extract_path="", save_to_variable="",
    ):
        """
        API-Automation Requirement 1 — "Add Token" full Postman-style
        request builder (Params/Authorization/Headers/Body/Settings).
        Actually SENDS the configured request via the SAME
        ApiAutomationRunner.send()/build_request() every other real
        API execution in this app uses (never a second, parallel
        HTTP-calling mechanism) — `request_config` is the exact same
        method/url/query_params/path_params/headers/auth/body/
        body_mode/body_params shape the per-Test-Case Request
        Configuration tabs already produce, so this reuses that same
        builder end to end rather than a bespoke token-only one.

        Unlike the original (Phase 3) design, the full real response
        (status, headers, body text, duration, masked request) is now
        ALWAYS returned to the caller — exactly like a normal Postman
        Send — since the operator needs to actually see the response
        to configure/debug the extraction path in the first place, and
        the response body is what they're inspecting anyway. The one
        thing still never returned is the individually-extracted
        token VALUE itself post-extraction as a separate field — it is
        only ever visible as part of the response body the operator
        already asked to see, and is persisted straight into the
        destination variable server-side.

        On any failure (network error, timeout, non-2xx response,
        invalid JSON where a JSON-path extraction was configured, or a
        successful-and-valid response the extraction rule still
        couldn't find a value in) the destination variable is left
        completely untouched — never cleared, never overwritten with
        a blank — and `response["response_error"]` carries a specific,
        distinguishing message for each of those cases.
        """

        save_to_variable = (save_to_variable or "").strip()

        if not save_to_variable:

            raise ValueError("'Save to variable' is required.")

        request_config = request_config or {}

        overrides = {
            key: request_config[key]
            for key in self._TOKEN_REQUEST_CONFIG_KEYS
            if key in request_config and request_config[key] is not None
        }

        if not (overrides.get("url") or "").strip():

            raise ValueError("Request URL is required.")

        config = self.config.load()

        runner = ApiAutomationRunner()

        endpoint = {}

        result = runner.send(endpoint, config, request_overrides=overrides)

        response = {
            "status_code": result.get("status_code"),
            "elapsed_ms": result.get("elapsed_ms"),
            "response_headers": result.get("response_headers") or {},
            "response_body_text": result.get("response_body_text") or "",
            "request": result.get("request") or {},
        }

        if result.get("error"):

            response["success"] = False

            response["error"] = result["error"]

            response["response_error"] = "connection_error"

            return response

        status_code = result.get("status_code")

        if status_code is not None and status_code >= 400:

            response["success"] = False

            response["error"] = (
                "Request completed with HTTP " + str(status_code) +
                " — a non-2xx/3xx response is treated as a failed "
                "token request; the destination variable was left "
                "unchanged."
            )

            response["response_error"] = "non_2xx"

            return response

        extract_type = extract_type or "json_path"

        extract_path = (extract_path or "").strip()

        if extract_type == "json_path":

            # API-Automation Requirement 5 — reviewed the extraction
            # controls against the real token flow: for a normal OAuth/
            # token response (`{"access_token": "..."}` at the top
            # level) requiring the operator to type "access_token" by
            # hand added a step with only one realistic value. Default
            # it here, server-side, so the behavior holds however the
            # request reaches this method (not just when the frontend
            # happens to prefill it). An operator with a non-standard
            # response shape can still type a different path — that
            # advanced path is preserved, just no longer mandatory.
            if not extract_path:

                extract_path = "access_token"

        elif extract_type == "header" and not extract_path:

            raise ValueError(
                "Header name is required when extracting from a "
                "response header — there is no default header name."
            )

        if extract_type == "json_path":

            try:

                json.loads(result.get("response_body_text") or "")

            except (TypeError, ValueError):

                response["success"] = False

                response["error"] = (
                    "Response is not valid JSON — cannot resolve a "
                    "JSON path from it. Switch Extract From to "
                    "'Response header', or fix the request so it "
                    "returns JSON."
                )

                response["response_error"] = "invalid_json"

                return response

        extracted = runner.extract_variables(
            [{
                "type": extract_type,
                "path": extract_path,
                "header": extract_path,
                "variable": save_to_variable,
            }],
            result,
        )

        value = extracted.get(save_to_variable)

        if value is None or value == "":

            response["success"] = False

            response["error"] = (
                "Request completed (HTTP " + str(status_code) +
                ") but no value could be extracted at "
                + ("JSON path" if extract_type == "json_path" else "header")
                + " '" + str(extract_path) + "' — the destination variable was left unchanged."
            )

            response["response_error"] = "extraction_missing"

            return response

        # Save ONLY the raw extracted value — e.g. "eyJhb..." — never
        # prefixed with "Bearer " or any other scheme text; the
        # variable is a plain credential value that Authorization
        # configuration elsewhere (Auth type = Bearer Token) adds the
        # scheme prefix to at request-build time, not storage time.
        # Extraction updates the value without changing the operator's
        # Environment Settings choice. New destinations are normal
        # variables until the operator explicitly marks them Secret.
        existing_environment = self.config.load()
        secret_flags = existing_environment.get("api_variable_secrets") or {}
        existing_secret_choice = bool(secret_flags.get(save_to_variable, False))
        self.upsert_variable(
            save_to_variable,
            str(value),
            secret=existing_secret_choice,
        )

        response["success"] = True

        response["saved_to_variable"] = save_to_variable

        return response


class TokenConfigurationsWeb:
    """CRUD for reusable Add Token request templates (never runtime values)."""

    def __init__(self):
        self.repository = TokenConfigurationRepository()

    def list_all(self):
        return self.repository.list_all()

    def create(self, payload):
        return self.repository.create(
            payload.get("token_name"), payload.get("request_config"),
            payload.get("extract_type"), payload.get("extract_path"),
            payload.get("save_to_variable"),
        )

    def update(self, config_id, payload):
        return self.repository.update(
            config_id, payload.get("token_name"), payload.get("request_config"),
            payload.get("extract_type"), payload.get("extract_path"),
            payload.get("save_to_variable"),
        )

    def delete(self, config_id):
        if not self.repository.delete(config_id):
            raise ValueError("Token configuration was not found.")
        return {"deleted": True, "id": int(config_id)}


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

    def __init__(self, repository=None):

        self.repository = repository or TestCaseRepository()

        self.runs = AutomationExecutionRepository()

    # --------------------------------------------------
    # Listing
    # --------------------------------------------------

    def list_test_cases(self, domain, module, knowledge_name, automation_type=None):

        rows = self.repository.list_test_cases(domain, module, knowledge_name)
        if not automation_type:
            return rows
        execution = self.repository.execution_for_automation_type(automation_type)
        return [row for row in rows if row.get("execution") == execution]

    def list_workspace(
        self, domain=None, module=None, knowledge_name=None, version=None,
        document_type=None, source_knowledge_ids=None, test_case_document=None,
        test_case_version_id=None, test_case_file_id=None,
        status=None, automation_type=None, q=None, limit=50, offset=0,
    ):
        """
        Backs the Automation Workspace landing page: every automation
        asset across every scope, each with its latest execution
        result/time attached (a single extra query, not one per row —
        see AutomationExecutionRepository.latest_runs_for_test_cases()).

        test_case_version_id — Rule 4's exact hierarchy ("Source Files"
        = a TC-* folder). When given, it is authoritative: the SQL-level
        filter in list_all_test_cases() already scopes the rows to that
        folder, so the legacy source_knowledge_ids/test_case_document
        narrowing below (the abbreviated selector's grouping) is skipped
        rather than applied on top of it.
        """

        execution = (
            self.repository.execution_for_automation_type(automation_type)
            if automation_type else None
        )

        result = self.repository.list_all_test_cases(
            domain=domain, module=module, knowledge_name=knowledge_name,
            version=version, document_type=document_type,
            test_case_version_id=test_case_version_id,
            reviewed_workbook_path=self._workbook_path(test_case_file_id, test_case_version_id),
            status=status,
            execution=execution, q=q,
            limit=10000, offset=0,
        )

        rows = result["test_cases"]
        if not test_case_version_id:
            selected_sources = {int(value) for value in (source_knowledge_ids or [])}
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

                # Phase 8-grid (API-Automation-Enhancement) — API TITLE
                # is the endpoint NAME ONLY (never method + full URL,
                # which made the column unreadable and duplicated the
                # request's own Method/URL already shown in Request
                # Configuration). bound_endpoint_method is exposed
                # separately so the grid's TEST CASE column can build a
                # deterministic (non-AI) title from method + name — see
                # the frontend's apiDeterministicTestCaseTitle().
                test_case["bound_endpoint_name"] = name

                test_case["bound_endpoint_method"] = method

                test_case["bound_endpoint_url"] = (
                    endpoint.get("url_resolved") or endpoint.get("url_raw") or ""
                )

        return result

    def _workbook_path(self, test_case_file_id, test_case_version_id):
        if not test_case_file_id:
            return None
        conn = self.repository.db.get_connection()
        try:
            row = conn.execute(
                "SELECT workbook_path FROM test_case_files WHERE id=? AND test_case_version_id=?",
                (int(test_case_file_id), int(test_case_version_id or 0)),
            ).fetchone()
        finally:
            conn.close()
        return str(row[0]) if row else "__invalid_workbook__"

    def list_scopes(self):
        sources = []
        seen_containers = set()
        for item in QaEngineeringWeb().list_scope():
            if not item.get("display_name"):
                continue
            # V5-01: QA Automation's "Source Document" level must
            # represent the SD-* managed CONTAINER folder, never an
            # individual physical DOCX/PDF inside it. Several
            # knowledge_items rows can legitimately share one SD folder
            # (CentralRepository.resolve_unique_segment() reuses the
            # same folder for repeat uploads of the same document
            # identity), so collapse every sibling to the ONE dropdown
            # entry representing their shared container, and show the
            # container's own clean name (its "SD-" prefix stripped)
            # instead of a physical file name. The QA Engineering
            # screen's own per-file picker (QaEngineeringWeb.list_scope
            # consumed directly by qa_engineering_router.py) is
            # DELIBERATELY left untouched — grouping only applies here,
            # to the automation hierarchy's Source Document level.
            container_key, display_name = self._source_document_container(item)
            if container_key in seen_containers:
                continue
            seen_containers.add(container_key)
            sources.append({
                "id": item.get("id"), "domain": item.get("domain") or "",
                "module": item.get("module") or "", "knowledge_name": item.get("knowledge_name") or "",
                "version": item.get("version") or "", "document_type": item.get("document_type") or "",
                "display_name": display_name, "source_type": item.get("source_type") or "",
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
            tool = row.get("execution") if row.get("execution") in {"Playwright", "API", "SQL"} else None
            if tool:
                group["counts"][tool] += 1
        return {"sources": sources, "documents": list(documents.values())}

    @staticmethod
    def _source_document_container(item):
        """Return (container_key, clean_display_name) for a QA
        Engineering scope item, per V5-01.

        container_key groups sibling knowledge_items rows that
        physically live inside the SAME managed SD-* folder into one
        Source Document entry — derived from the item's own physical
        parent folder, which is the actual container on disk,
        regardless of how many files it holds.

        clean_display_name is that SD folder's own name with a leading
        "SD-" stripped for display (e.g. "SD-V0.3 CRF-DPP" ->
        "V0.3 CRF-DPP"). Items with no real managed path on disk (API
        Collection placeholders, or a legacy row that never migrated
        into a Central Repository folder) fall back to remaining their
        own standalone container, keyed by id, exactly as they behaved
        before this fix — nothing selectable disappears.
        """
        for path_key in ("repository_path", "original_path"):
            raw_path = str(item.get(path_key) or "").strip()
            if not raw_path:
                continue
            folder = Path(raw_path).parent
            folder_name = folder.name
            display = folder_name[3:] if folder_name[:3].upper() == "SD-" else folder_name
            return str(folder), (display.strip() or item.get("display_name"))
        return f"id:{item.get('id')}", item.get("display_name")

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
        result["automation_target"] = self.repository.execution_for_automation_type(
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
    # V5-04 — Playwright "Upload Playwright Scripts" mapping import.
    # Reuses the SAME script/test-case binding architecture as the
    # existing "View Script" -> Edit -> Save flow (update_script()
    # below, also called by PUT /test-cases/{id}/script) — no second
    # Playwright execution engine, no separate storage model.
    # --------------------------------------------------

    def import_playwright_mapping(self, file_bytes, filename, test_case_version_id, test_case_file_id):
        from Core.automation_import_service import (
            AutomationImportService, PLAYWRIGHT_CONTENT_ALIASES, PLAYWRIGHT_CONTENT_LABEL,
        )
        service = AutomationImportService(self.repository)
        result = service.validate(
            file_bytes, filename, test_case_version_id, test_case_file_id,
            PLAYWRIGHT_CONTENT_ALIASES, PLAYWRIGHT_CONTENT_LABEL,
        )
        response = result.to_response()
        if not result.ok:
            # Atomic per V5: a validation failure (unknown/duplicate
            # Test Case ID, malformed row, or nothing usable at all)
            # saves NOTHING — the caller shows this breakdown instead
            # of a bare 400.
            return response

        saved_ids = []
        mapped_without_script_ids = []
        stable_updates = []
        for row in result.valid_rows:
            test_case_id = row["matched_test_case"]["id"]
            if row["content"]:
                stable_updates.append((test_case_id, row["content"]))
                saved_ids.append(row["test_case_id"])
            else:
                # Script is optional (V5-04): a Test Case ID with no
                # script text still counts as a valid, accepted mapping
                # row (it passed validation above), but nothing is
                # written to automation_script — there is no existing
                # "bound with no script" concept in the current schema
                # to safely repurpose here, and writing an empty script
                # would make Execute find nothing to run while looking
                # like it succeeded. Reported separately so the import
                # summary is honest about what was and wasn't saved.
                mapped_without_script_ids.append(row["test_case_id"])
        # Validation above remains workbook-specific; persistence below is
        # stable-Test-Case-specific and one DB/filesystem transaction for the
        # whole accepted mapping. A failed physical write restores every file
        # already replaced and rolls back every database row.
        if stable_updates:
            self.repository.update_playwright_scripts_atomically(stable_updates)
        response["saved_test_case_ids"] = saved_ids
        response["mapped_without_script_ids"] = mapped_without_script_ids
        return response

    def update_script(self, test_case_id, script_text):

        manager = TestExecutionManager(initialize_automation=False)
        manager.repository = self.repository

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

    def update_uploaded_script(self, test_case_id, script_text):
        manager = TestExecutionManager(initialize_automation=False)
        syntax_warning = manager.check_script_syntax(script_text)
        changed = self.repository.update_uploaded_script(test_case_id, script_text)
        return {
            "test_case": self.get_test_case(test_case_id),
            "syntax_warning": syntax_warning,
            "changed": changed,
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

        script_text = {
            "AUTO": test_case.get("automation_script"),
            "UPLOADED": test_case.get("uploaded_script"),
            "MANUAL": test_case.get("recorded_script"),
        }.get(source) or ""

        if not script_text.strip():

            raise ValueError(
                "The selected script source is empty and cannot be activated."
            )

        # Task-1 reliability fix: a recording that is missing user actions must not become Active.
        from Core.recording_completeness import RecordingCompleteness

        incomplete_reason = RecordingCompleteness(self.repository).block_reason(test_case, source)

        if incomplete_reason:

            raise ValueError(incomplete_reason)

        expected_tool = self.repository.execution_for_automation_type(
            test_case.get("automation_type")
        )

        if (
            not expected_tool
            or test_case.get("execution") != expected_tool
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

        - source="AUTO"|"UPLOADED"|"MANUAL" — VALIDATE (SELECTED) SCRIPT. "Is
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

        # REQUEST-CENTRIC-LIFECYCLE-FINAL: self-heal the legacy
        # automation_type label here too, not only at the Request-
        # workspace save choke point (TestCaseRepository.
        # _update_api_config_field()) — a Test Case can reach Validate
        # without having saved anything in THIS session (e.g. the
        # Central Test Case classification was changed to Execution
        # Type=Automatable / Execution Tool=API Automation AFTER the
        # Request Configuration was already bound/saved). Without
        # this, Validate would keep falling into the legacy generic
        # branch below ("No Automation Type is set" / "No automation
        # script...") even though the operator has fully configured
        # the Request workspace — exactly the failure mode reported
        # live. This never sets Execution Tool itself, only mirrors a
        # classification already made, and never touches Playwright/
        # SQL test cases.
        if (
            (test_case.get("automation_type") or "None") != "API"
            and test_case.get("execution") == "API"
        ):

            self.repository.update_automation(test_case_id, "API")

            test_case = manager.repository.get_test_case(test_case_id)

        automation_type = test_case.get("automation_type") or "None"

        # FINAL-END-TO-END-WORKSPACE-V2 section 32: readiness must
        # also account for a MATERIAL Test Environment Settings
        # change (e.g. a regenerated token landing in a {{variable}}
        # this request references, or an edited Base URL override) —
        # not only the request config itself. Computed once and
        # reused below both when CHECKING current readiness and when
        # a successful Validate MARKS the new baseline, so the two
        # always agree — see _api_env_fingerprint().
        env_fingerprint = (
            self._api_env_fingerprint(test_case) if automation_type == "API" else ""
        )

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

        validating_selected_source = source in ("AUTO", "UPLOADED", "MANUAL")

        def add_execution_configured():

            expected_tool = self.repository.execution_for_automation_type(
                automation_type
            )

            add(
                "execution_configured",
                bool(expected_tool) and test_case.get("execution") == expected_tool,
                "" if test_case.get("execution") in {"Playwright", "API", "SQL"} else
                "The central Test Case has no automation execution selected.",
            )

        if not validating_selected_source:

            add_execution_configured()

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

                # API-BULK-EXECUTE-LIFECYCLE-FIX (2026-10-02): this check
                # used to compare the WORKBOOK-NORMALIZED test_case["status"]
                # (collapsed by TestCaseRepository._with_test_types() ->
                # normalize_test_case() into "Not Executed"/"Pass"/"Fail" --
                # the LAST RESULT, never the lifecycle state) against the
                # literal string "Automated". That can never be true for any
                # API test case that has ever been executed (status becomes
                # "Pass"/"Fail" the moment last_result is set), which made
                # Execute Selected's GET /validate preflight unconditionally
                # block every previously-validated, currently-Active API
                # test case with the exact "has not been validated yet, or
                # it changed" message -- even though the individual Execute
                # button (execute_api(), which already reads
                # automation_lifecycle_status, see below) ran it successfully
                # moments earlier. Same root-cause class already fixed for
                # SQL single-execute / SQL bulk preflight (see
                # SqlAutomationWeb.execute() and TestCaseRepository.
                # list_all_test_cases()) and for the non-API branch directly
                # below (active_script_approved) -- this was the one
                # remaining branch still reading the raw "status" field.
                request_validated = self.repository.is_script_source_validated(
                    test_case, "AUTO", env_fingerprint=env_fingerprint
                )
                lifecycle_status = test_case.get(
                    "automation_lifecycle_status", test_case.get("status")
                )
                add(
                    "request_validated",
                    lifecycle_status == "Automated" and request_validated,
                    "" if lifecycle_status == "Automated" and request_validated
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
                # 2026-09-29 sync fix: read the pre-normalization lifecycle
                # status (see TestCaseRepository.get_test_case()) — plain
                # "status" here is the workbook-export execution-result
                # value (Pass/Fail/Not Executed) and can never equal
                # "Automated", even for a test case that IS Set Active,
                # which made this check block every Playwright/SQL test
                # case regardless of how current its validated hash was.
                lifecycle_status = test_case.get(
                    "automation_lifecycle_status", test_case.get("status")
                )
                add(
                    "active_script_approved",
                    lifecycle_status == "Automated" and source_validated,
                    "" if lifecycle_status == "Automated" and source_validated
                    else "The active script changed since validation, or has not "
                    "been validated and activated.",
                )

        elif automation_type == "API":

            # REQUEST-CENTRIC-LIFECYCLE-FINAL item 1/2 of the required
            # Validate checklist ("TC Execution Type = Automatable",
            # "Execution Tool = API Automation") must be checked by
            # the Validate BUTTON click too, not only by the separate
            # Execute-tab readiness panel — the frontend's Validate
            # button always calls this endpoint with
            # source=autoViewedSource ("AUTO"), i.e.
            # validating_selected_source is always True for API, so
            # without this branch these two classification checks
            # would never actually run for a user-initiated Validate.
            # The "is what was already validated still valid" checks
            # above (request_validated/active_script_approved) stay
            # out of this branch on purpose — they describe execute-
            # readiness, not "please validate me right now".
            add_execution_configured()

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
            script = {
                "AUTO": test_case.get("automation_script"),
                "UPLOADED": test_case.get("uploaded_script"),
                "MANUAL": test_case.get("recorded_script"),
            }.get(source) or ""
        else:
            script = TestExecutionManager.get_active_script(test_case)

        # ROUND-3-SYNC-RECONCILE: an explicit Validate (never the
        # read-only execute-readiness preflight, source=None) is the
        # one place this reconciles and PERSISTS stale
        # TRANSACTION_OPENER / PARENT_LOCATOR metadata left behind by a
        # repair that predates apply_script_repairs()'s own live
        # propagation (see TestExecutionManager.
        # reconcile_transaction_metadata()'s docstring). Preflight/
        # execute-readiness stays a pure read of whatever is already
        # persisted+validated — it never silently mutates the script;
        # it simply passes once Validate has reconciled and persisted
        # it. Only ever touches TRANSACTION_OPENER/PARENT_LOCATOR on
        # OTHER steps, only when the opener is unambiguous and
        # ownership was already proven (OWNERSHIP_ACCEPTED=true) —
        # a genuine conflict is left for the check below to report,
        # exactly as before.
        transaction_reconciliation = None
        if validating_selected_source and automation_type == "Playwright" and script:
            reconciled_script, reconcile_changes = manager.reconcile_transaction_metadata(script)
            if reconcile_changes:
                if source == "MANUAL":
                    manager.update_recorded_script(test_case_id, reconciled_script)
                elif source == "UPLOADED":
                    manager.repository.update_uploaded_script(test_case_id, reconciled_script)
                else:
                    manager.update_script(test_case_id, "Playwright", reconciled_script)
                script = reconciled_script
                transaction_reconciliation = reconcile_changes

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

        if transaction_reconciliation:

            add(
                "transaction_metadata_reconciled", True,
                "; ".join(
                    f"Step {change['step']} {change['field']} synced to the "
                    f"current opener ('{change['old']}' -> '{change['new']}')"
                    for change in transaction_reconciliation[:8]
                ),
            )

        if automation_type == "Playwright" and script:

            syntax_error = manager.check_script_syntax(script)

            add("syntax_valid", syntax_error is None, syntax_error or "")
            # A recording saved after an explicit "save anyway" confirmation carries a status header.
            # It stays a Draft: it must be reviewed/completed (and the header removed) before it can
            # pass Validate and be made Active.
            from Core.recording_drafts import is_marked_incomplete
            recording_complete_reported = False
            if is_marked_incomplete(script):
                recording_complete_reported = True
                add(
                    "recording_complete", False,
                    "This script was saved from an incomplete recording (timeout, crash, lost connection or "
                    "no interactions captured). Review and complete it, then remove the "
                    "'# QA_RECORDING_STATUS: INCOMPLETE' header lines to validate and activate it.",
                )
            # Task-1 reliability fix: skipped user actions are persisted with the recording and make it
            # Not Ready until the missing steps are restored and the correction is confirmed.
            from Core.recording_completeness import RecordingCompleteness
            completeness_source = source if validating_selected_source else None
            if completeness_source is None and (test_case.get("active_script_source") or "AUTO").upper() == "MANUAL":
                completeness_source = "MANUAL"
            if completeness_source == "MANUAL" and not recording_complete_reported:
                try:
                    completeness = RecordingCompleteness(self.repository).get_state(test_case_id, "MANUAL")
                except Exception:
                    completeness = {"incomplete": False, "current": None}
                if completeness["incomplete"]:
                    add(
                        "recording_complete", False,
                        RecordingCompleteness(self.repository).block_reason(test_case, "MANUAL")
                        or "This recording is incomplete.",
                    )
                elif (completeness.get("current") or {}).get("status") == "CORRECTED":
                    record = completeness["current"]
                    add(
                        "recording_complete", True,
                        f"{record['original_skipped_count']} skipped action(s) were recorded originally; "
                        "the missing steps were restored and the correction was confirmed.",
                    )
            locator_quality = manager.assess_playwright_script_quality(script)
            invalid_locators = [item for item in locator_quality if item["classification"] == "Invalid"]
            quality_summary = ", ".join(
                f"{name}: {sum(1 for item in locator_quality if item['classification'] == name)}"
                for name in ("Stable", "Acceptable", "High Risk", "Invalid")
            )
            invalid_detail = "; ".join(
                f"Step {item['step']} — {item['reason']}" for item in invalid_locators[:8]
            )
            add(
                "locator_quality_valid", not invalid_locators,
                quality_summary + ((". Affected: " + invalid_detail) if invalid_detail else ""),
            )
            high_risk_locators = [
                item for item in locator_quality if item["classification"] == "High Risk"
            ]
            if high_risk_locators:
                add(
                    "locator_quality_warning", True,
                    "; ".join(
                        f"Step {item['step']} â€” {item['reason']}"
                        for item in high_risk_locators[:8]
                    ),
                )
            semantic_failures = [
                item for item in locator_quality
                if not item.get("semantic_valid", True)
                or not item.get("parent_valid", True)
                or not item.get("action_valid", True)
            ]
            semantic_detail = []
            for item in semantic_failures[:8]:
                reasons = [
                    item.get("semantic_reason") if not item.get("semantic_valid", True) else "",
                    item.get("parent_reason") if not item.get("parent_valid", True) else "",
                    item.get("action_reason") if not item.get("action_valid", True) else "",
                ]
                semantic_detail.append(
                    f"Step {item['step']} â€” " + "; ".join(reason for reason in reasons if reason)
                )
            add(
                "semantic_consistency_valid", not semantic_failures,
                "; ".join(semantic_detail),
            )

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

            # Phase 5 — Manual Source Endpoint: a saved Method/URL with
            # no imported endpoint bound is a legitimate, ready-to-run
            # configuration, not a blocker — mirrors the same allowance
            # execute_api() makes (see its own Phase 5 comment).
            manual_url = str(request_config.get("url") or "").strip()

            if bound_endpoint:

                endpoint_bound_detail = ""

            elif manual_url:

                endpoint_bound_detail = ""

            elif not candidates:

                endpoint_bound_detail = (
                    "No imported API Collection endpoint matches this "
                    "test case's Domain / Module / Knowledge Name, and "
                    "no manual Request URL has been saved — import a "
                    "Postman Collection for this scope, or enter a "
                    "Method/URL directly in Request Configuration and "
                    "Save."
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

            add(
                "endpoint_bound", bool(bound_endpoint) or bool(manual_url),
                endpoint_bound_detail,
            )

            if bound_endpoint or manual_url:

                runner = ApiAutomationRunner()

                config = manager.environment_config.load()

                request_spec = None

                try:

                    request_spec = runner.build_request(
                        bound_endpoint or {}, config, request_config
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
                    bound_endpoint or {}, config, request_config
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
                    bound_endpoint or {}, config, request_config
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
                    or (bound_endpoint or {}).get("example_response_status")
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
                test_case_id, source, keep_active=(automation_type == "API"),
                env_fingerprint=env_fingerprint,
            )

        return {"ready": ready, "checks": checks, "request_preview": request_preview}

    # --------------------------------------------------
    # Execute — Playwright, background job (persisted)
    # --------------------------------------------------

    def can_auto_execute(self, test_case):

        # 2026-09-29 sync fix: see the matching comment on the
        # active_script_approved check above — "status" here is the
        # workbook-export execution-result value, never the raw
        # "Automated" lifecycle value, once a test case has gone through
        # TestCaseRepository.get_test_case()'s normalization.
        lifecycle_status = test_case.get(
            "automation_lifecycle_status", test_case.get("status")
        )
        return (
            test_case.get("automation_type") == "Playwright"
            and test_case.get("execution") == "Playwright"
            and lifecycle_status == "Automated"
            and self.repository.is_script_source_validated(
                test_case, test_case.get("active_script_source") or "AUTO"
            )
            and bool(TestExecutionManager.get_active_script(test_case))
            and not self._incomplete_recording_reason(test_case)
        )

    def _incomplete_recording_reason(self, test_case):
        """Task-1 reliability fix: why the script Execute would run is an incomplete recording (or None)."""
        from Core.recording_completeness import RecordingCompleteness

        return RecordingCompleteness(self.repository).block_reason(test_case, None)

    _SOURCE_LABELS = {
        "AUTO": "AI-generated", "MANUAL": "Recorded", "UPLOADED": "Uploaded",
    }

    def execute_block_reason(self, test_case):
        """
        Task-1 closure: say WHY Execute is refused. The old single message
        ("has no Playwright automation script available yet") was misleading
        when a script exists but is Draft / not validated / not the active
        source -- the operator then looked for a script that was already there.
        """
        if test_case.get("automation_type") != "Playwright":
            return (
                "Execute runs Playwright automation only - this test case's Automation "
                "Type isn't Playwright. Use Execute Against Real Server for an API test case."
            )
        if test_case.get("execution") != "Playwright":
            return (
                f"This test case's Execution is set to '{test_case.get('execution') or 'not set'}', "
                "not Playwright. Change Execution to Playwright, then Validate and Set Active the script."
            )
        scripts = {
            "AUTO": test_case.get("automation_script"),
            "MANUAL": test_case.get("recorded_script"),
            "UPLOADED": test_case.get("uploaded_script"),
        }
        present = [self._SOURCE_LABELS[k] for k, v in scripts.items() if (v or "").strip()]
        if not present:
            return (
                "This test case has no Playwright script yet - use Add Automation (AI), "
                "Record Script or Import Script first."
            )
        active = str(test_case.get("active_script_source") or "AUTO").upper()
        active_label = self._SOURCE_LABELS.get(active, active)
        lifecycle = test_case.get("automation_lifecycle_status", test_case.get("status"))
        found = " and ".join(present)
        if not (scripts.get(active) or "").strip():
            return (
                f"A Playwright script exists ({found}) but the active source ({active_label}) "
                "has no script. Open View Script, choose the script to run, Validate it and "
                "click Set Active."
            )
        incomplete_reason = self._incomplete_recording_reason(test_case)
        if incomplete_reason:
            return incomplete_reason
        if not self.repository.is_script_source_validated(test_case, active):
            return (
                f"A {active_label} Playwright script exists but it has not been validated "
                f"(status: {lifecycle or 'Draft'}). Open View Script, click Validate, then "
                "Set Active - Execute is enabled once the script is validated and active."
            )
        return (
            f"A {active_label} Playwright script exists but the test case is not activated "
            f"(status: {lifecycle or 'Draft'}). Open View Script and click Set Active - "
            "Execute is enabled once the validated script is active."
        )

    def start_execution(
        self, test_case_id, executed_by_user_id=None,
        executed_by_username=None, re_run_of=None,
    ):

        test_case = self.repository.get_test_case(test_case_id)

        if not test_case:

            raise ValueError(f"Test case {test_case_id} not found.")

        if not self.can_auto_execute(test_case):

            raise ValueError(self.execute_block_reason(test_case))

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
        test_case_version_id=None,
        test_case_id=None, status=None, automation_type=None, q=None,
        start_date=None, end_date=None,
        limit=50, offset=0,
    ):

        scoped_ids = None
        if version or document_type or source_knowledge_ids or test_case_document or test_case_version_id:
            workspace = self.list_workspace(
                domain=domain, module=module, knowledge_name=knowledge_name, version=version,
                document_type=document_type, source_knowledge_ids=source_knowledge_ids,
                test_case_document=test_case_document, test_case_version_id=test_case_version_id,
                automation_type=automation_type,
                limit=10000, offset=0,
            )
            scoped_ids = [row["id"] for row in workspace["test_cases"]]
        # Requirement 9 — Reports & Analytics date-range filter passes
        # straight through to AutomationExecutionRepository.list_runs(),
        # the ONE place run history is stored/queried (see that
        # method's docstring) — no second filtering path.
        return self.runs.list_runs(
            domain=domain, module=module, knowledge_name=knowledge_name,
            test_case_id=test_case_id, status=status,
            test_case_ids=scoped_ids,
            automation_type=automation_type, q=q,
            start_date=start_date, end_date=end_date,
            limit=limit, offset=offset,
        )

    def delete_runs(self, run_uuids):
        """Phase 8 — History popup's Clear action. See
        AutomationExecutionRepository.delete_runs() for the full
        behavior; this is a thin pass-through, same pattern as
        list_runs() above."""

        return self.runs.delete_runs(run_uuids)

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

    @staticmethod
    def _merge_secret_kv_rows(existing_rows, incoming_rows):
        """
        Row-level merge for a Headers/Query-Params/Path-Params list —
        see set_api_request_config()'s call site above. `existing_rows`
        is the RAW (unmasked) previously-stored list; `incoming_rows`
        is what the browser just sent, which for any row whose key
        looks sensitive may carry back get_api_request_config()'s mask
        sentinel ("******") rather than the real value if the operator
        never touched that particular row. Matches existing rows to
        incoming ones by key, in order (first not-yet-consumed match
        for that key), mirroring how the Params/Headers table itself
        is keyed — duplicate header names are rare and, worst case,
        fall back to the incoming value as-is.
        """

        existing_by_key = {}

        for row in existing_rows or []:

            key = (row or {}).get("key", "")

            existing_by_key.setdefault(key, []).append(dict(row or {}))

        merged = []

        for row in incoming_rows or []:

            row = dict(row or {})

            key = row.get("key", "")

            value = row.get("value", "")

            if is_sensitive_key(key) and value == MASK:

                candidates = existing_by_key.get(key) or []

                if candidates:

                    row["value"] = candidates.pop(0).get("value", "")

            merged.append(row)

        return merged

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

        # REQUEST-CENTRIC-LIFECYCLE-FINAL / SECRET SECURITY: a raw
        # Header/Query-Param/Path-Param row (e.g. "X-Client-Secret:
        # <real value>") is a resolved literal exactly like the auth
        # sub-fields above, and must never come back to the browser
        # unmasked — reuses the same key-name-based
        # Core.secret_masking rule already trusted for Request
        # Preview/Result/History (a "{{variable}}" reference is left
        # visible; a resolved literal for a sensitive-looking key name
        # is replaced with "******"). set_api_request_config() below
        # recognizes that same "******" sentinel on write-back and
        # reuses the real stored value instead of overwriting it.
        for field in ("headers", "query_params", "path_params"):

            config[field] = mask_headers(config.get(field) or [])

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

            elif key in ("headers", "query_params", "path_params"):

                # SECRET SECURITY: the UI only ever reads these rows
                # back through get_api_request_config(), which masks a
                # sensitive-looking key's resolved value to "******"
                # (see above). A save that round-trips that same
                # "******" sentinel for a row the operator never
                # touched must NOT overwrite the real stored secret
                # with the literal string "******" — reuse the
                # existing raw value at that key instead, exactly like
                # the auth sub-fields' "blank preserves existing
                # secret" rule just above. A deliberately typed new
                # value (anything other than the exact sentinel) is
                # always taken as-is.
                merged[key] = self._merge_secret_kv_rows(
                    existing.get(key) or [], request_config[key] or []
                )

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

    def _api_env_fingerprint(self, test_case):
        """
        FINAL-END-TO-END-WORKSPACE-V2 section 32: the extra piece of
        the API readiness fingerprint that comes from Test Environment
        Settings rather than the Test Case's own saved Request
        Configuration — so regenerating a token into a referenced
        {{variable}}, or editing the environment's Base URL override /
        auth type, correctly flips a validated Test Case back to
        "Needs Validation" even though nothing on the Test Case itself
        was touched.

        Deliberately narrow, to satisfy the other half of section 32
        ("avoid over-invalidating unrelated test cases on unrelated
        env variable changes"): only the {{variable}} NAMES this
        specific Test Case's bound endpoint + its own overrides
        actually reference are hashed in (reusing
        ApiAutomationRunner.find_unresolved_variables() — the exact
        same variable-reference scan execute_api() already relies on
        for its missing-variable check, so "what this TC depends on"
        can never drift out of sync between the two). Editing some
        OTHER remembered variable this TC never references leaves its
        fingerprint, and therefore its readiness, untouched.

        Returns "" when there's no bound endpoint yet (nothing
        environment-specific to fingerprint) — never raises, since a
        readiness check must never itself fail a Test Case that just
        isn't bound yet (a different, already-reported check).
        """

        bound_id = test_case.get("bound_api_endpoint_id")

        if not bound_id:

            return ""

        try:

            manager = TestExecutionManager()

            endpoint, _candidates = self._resolve_bound_endpoint(test_case, manager)

            if endpoint is None:

                return ""

            request_overrides = self._parsed_api_request_config(test_case)

            runner = ApiAutomationRunner()

            referenced_names = sorted(set(
                runner.find_unresolved_variables(endpoint, request_overrides)
            ))

            env = TestEnvironmentConfig().load()

            variables = env.get("api_variables") or {}

            secret_flags = env.get("api_variable_secrets") or {}

            # Every real API request is affected by these two
            # regardless of which {{variables}} it happens to
            # reference, so they're always included, not gated behind
            # referenced_names.
            parts = [
                f"base_url_override={env.get('api_base_url_override') or ''}",
                f"auth_type={env.get('api_auth_type') or ''}",
            ]

            for name in referenced_names:

                value = variables.get(name, "")

                is_secret = secret_flags.get(name)

                if is_secret is None:

                    is_secret = is_sensitive_key(name)

                # Runtime token values are deliberately excluded: the
                # request definition references the variable name, not the
                # current secret behind it. Presence and secret classification
                # still belong to environment readiness.
                parts.append(
                    f"var:{name}:defined={bool(value)}:secret={bool(is_secret)}"
                )

            return "|".join(parts)

        except Exception:

            # A readiness check must degrade gracefully, not 500 the
            # Execute tab, if anything here goes unexpectedly wrong —
            # falls back to "no environment contribution", which at
            # worst under-invalidates rather than blocking the
            # operator from seeing readiness at all.
            return ""

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
            test_case.get("execution") != "API"
        ):

            raise ValueError(
                "This Test Case is not eligible for API Automation."
            )

        lifecycle_status = test_case.get(
            "automation_lifecycle_status", test_case.get("status")
        )

        if lifecycle_status != "Automated":

            # REQUEST-CENTRIC-LIFECYCLE-FINAL EXECUTE section: exact
            # required wording — lifecycle_status != "Automated" means
            # either this Request Configuration has never been
            # validated, or it changed (any executable-field edit
            # demotes status back to Draft and clears the validated
            # fingerprint — see TestCaseRepository.
            # _update_api_config_field()) since the last successful
            # Validate.
            raise ValueError(
                "Request configuration has changed. Validate before "
                "execution."
            )

        # Section F: the per-Test-Case Params/Headers/Auth/Body/
        # Assertions/Extraction definition — the RAW, unmasked config
        # (never get_api_request_config()'s masked-for-display
        # version, which blanks the secret auth fields) since this is
        # what actually builds and sends the real outgoing request.
        # Fetched BEFORE endpoint resolution below (API-Automation-
        # Enhancement Phase 5) so a manually-entered Method/URL (no
        # imported Postman endpoint at all for this scope) can still
        # be recognized instead of always hard-failing.
        request_config = self._parsed_api_request_config(test_case)

        endpoint, endpoints = self._resolve_bound_endpoint(
            test_case, manager, explicit_endpoint_id=endpoint_id
        )

        if endpoint is None:

            manual_url = str(request_config.get("url") or "").strip()

            if manual_url:

                # Phase 5 — Manual Source Endpoint: no imported API
                # Collection endpoint is bound (none exists for this
                # scope, or none was selected), but the operator has
                # typed a Method/URL directly into the Request
                # Configuration bar and saved it. Proceed with an
                # empty synthetic endpoint — build_request() already
                # takes method/url entirely from request_overrides
                # whenever they're set (see its own docstring), so
                # nothing about the real outgoing request differs
                # from the imported-endpoint path below.
                endpoint = {}

            elif not endpoints:

                raise ValueError(
                    "No imported API Collection endpoint matches this "
                    "test case's Domain / Module / Knowledge Name, and "
                    "no manual Request URL has been saved for it — "
                    "either import a Postman Collection for this scope "
                    "(Knowledge Hub -> Upload -> Source Type: API "
                    "Collection) or enter a Method/URL directly in "
                    "Request Configuration and Save."
                )

            else:

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

        missing = runner.find_missing_variables(
            endpoint, config, request_config
        )

        # Phase 5 — Manual Source Endpoint: with no imported endpoint
        # (endpoint == {}), fall back to the per-Test-Case request
        # config's own manually-entered method/url so the Execute
        # tab's "Endpoint" summary line still shows something
        # meaningful instead of blank/None.
        endpoint_summary = {
            "id": endpoint.get("id"),
            "name": endpoint.get("name") or "Manual Request",
            "method": endpoint.get("method") or request_config.get("method") or "",
            "url": (
                endpoint.get("url_resolved") or endpoint.get("url_raw")
                or request_config.get("url") or ""
            ),
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

        # Phase 8 (API-Automation-Enhancement): every branch below now
        # persists the same evidence bundle — endpoint, status,
        # assertions, extracted variable NAMES, and (new) the actual
        # masked request/response — so the History "View" popup can
        # show full per-record evidence for ANY past run, including a
        # transport error or a "Blocked" (no expectation configured)
        # run, which previously persisted no stdout at all and were
        # structurally impossible to inspect afterward. request/
        # response_headers/response_body_text are already masked above
        # (result["request"] by send() itself; response_headers/
        # response_body_text just above at Section P) — safe to persist
        # as-is.
        evidence = {
            "endpoint": endpoint_summary,
            "status_code": result.get("status_code"),
            "expected_status": result.get("expected_status"),
            "status_check": result.get("status_check"),
            "assertions": result.get("assertions") or [],
            "extracted_variables": masked_extracted_variables,
            "request": result.get("request") or {},
            "response_headers": result.get("response_headers") or {},
            "response_body_text": result.get("response_body_text") or "",
        }

        if result.get("error"):

            finished = self.runs.mark_finished(run["run_uuid"], {
                "error": result["error"],
                "duration": (result.get("elapsed_ms") or 0) / 1000,
                "stderr": result["error"],
                "stdout": json.dumps(evidence),
            })

        elif result.get("auto_verdict") == "Pass":

            finished = self.runs.mark_finished(run["run_uuid"], {
                "success": True,
                "duration": (result.get("elapsed_ms") or 0) / 1000,
                "stdout": json.dumps(evidence),
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

            # The main Expected HTTP Status is validated independently
            # of the additional assertions; either one failing is FAIL,
            # and both are named in the failure text.
            status_check = result.get("status_check") or {}

            failure_parts = []

            if status_check.get("enforced") and status_check.get("passed") is False:

                failure_parts.append(
                    f"Expected HTTP Status: {status_check.get('message')}"
                )

            failure_parts.extend(
                f"{item.get('label') or item.get('type')}: "
                f"{item.get('message')}"
                for item in failed_assertions
            )

            if failure_parts:

                failure_detail = "; ".join(failure_parts)

            else:

                failure_detail = (
                    f"Expected HTTP {result.get('expected_status')}; "
                    f"received {result.get('status_code')}."
                )

            finished = self.runs.mark_finished(run["run_uuid"], {
                "success": False,
                "duration": (result.get("elapsed_ms") or 0) / 1000,
                "stderr": failure_detail,
                "stdout": json.dumps(evidence),
            })

            self.repository.update_result(test_case_id, "Fail")

        else:

            # Phase 8: this "Blocked" branch previously persisted no
            # stdout at all — a run that completed a real HTTP request
            # but had nothing configured to judge it Pass/Fail was
            # structurally impossible to inspect afterward. It now
            # carries the same evidence bundle as every other outcome.
            finished = self.runs.mark_finished(run["run_uuid"], {
                "error": "Blocked: no expected HTTP status or assertion "
                "is configured; the result cannot be judged Pass or "
                "Fail.",
                "duration": (result.get("elapsed_ms") or 0) / 1000,
                "stdout": json.dumps(evidence),
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

    def __init__(self, repository=None):

        self.repository = repository or TestCaseRepository()

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

        # SQL-AUTOMATION-LIFECYCLE root-cause fix: an unset assertion
        # field arrives here as None from a backend caller (e.g.
        # generate_sql()) but as "" from the frontend's Save Script
        # payload (reading an empty <input>.value is always "", never
        # null) — semantically identical ("no value configured"), but
        # textually different once serialized. TestCaseRepository.
        # update_automation() — shared with Playwright/API — decides
        # "script changed" via a plain text comparison of the
        # persisted automation_script JSON, so that textual difference
        # alone made a completely no-op Save Script (right after Set
        # Active) look like a real content change and silently
        # demoted Active back to Draft, which is what then made
        # Execute wrongly report "still Draft". Normalizing both to
        # None here, at the one place the envelope is built, fixes it
        # at the source without touching the shared text-diff logic
        # Playwright/API also rely on.
        assertion_value = assertion_value if assertion_value not in (None, "") else None
        assertion_column = assertion_column if assertion_column not in (None, "") else None

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

    def test_connection(self, fields=None):
        profile = self.env.load()
        for key, value in dict(fields or {}).items():
            if key == "sql_password" and value in (None, "", "********"):
                continue
            if value is not None:
                profile[key] = value

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

    def import_mapping(self, file_bytes, filename, test_case_version_id, test_case_file_id):
        from Core.automation_import_service import (
            AutomationImportService, SQL_CONTENT_ALIASES, SQL_CONTENT_LABEL,
        )
        result = AutomationImportService(self.repository).validate(
            file_bytes, filename, test_case_version_id, test_case_file_id,
            SQL_CONTENT_ALIASES, SQL_CONTENT_LABEL,
        )
        response = result.to_response()
        if not result.ok:
            return response
        saved = []
        without_script = []
        for row in result.valid_rows:
            if row["content"]:
                self.save_script(
                    row["matched_test_case"]["id"], row["content"],
                    "row_exists", None, None,
                )
                saved.append(row["test_case_id"])
            else:
                without_script.append(row["test_case_id"])
        response["saved_test_case_ids"] = saved
        response["mapped_without_script_ids"] = without_script
        return response

    def import_raw_sql(self, file_bytes, filename, test_case_version_id,
                       test_case_file_id, test_case_id):
        """Bind one raw SQL file to one test case in the selected workbook."""
        if not str(filename or "").lower().endswith(".sql"):
            raise ValueError("Raw SQL import requires a .sql file.")
        try:
            sql = bytes(file_bytes or b"").decode("utf-8-sig")
        except UnicodeDecodeError as error:
            raise ValueError("The SQL file must be UTF-8 text.") from error
        if not sql.strip():
            raise ValueError("The SQL file is empty.")
        from Core.sql_automation_runner import validate_readonly_sql
        validate_readonly_sql(sql)
        conn = self.repository.db.get_connection()
        conn.row_factory = self.repository._dict_factory
        try:
            file_row = conn.execute(
                "SELECT workbook_path FROM test_case_files WHERE id=? AND test_case_version_id=?",
                (int(test_case_file_id), int(test_case_version_id)),
            ).fetchone()
            version_row = conn.execute(
                "SELECT * FROM test_case_versions WHERE id=?",
                (int(test_case_version_id),),
            ).fetchone()
        finally:
            conn.close()
        if not file_row or not version_row:
            raise ValueError("The selected Test Cases workbook does not belong to the selected folder.")
        test_case = self.repository.get_test_case(int(test_case_id))
        if (not test_case
                or int(test_case.get("test_case_version_id") or 0) != int(test_case_version_id)
                or str(test_case.get("reviewed_workbook_path") or "") != str(file_row["workbook_path"] or "")):
            raise ValueError("The selected Test Case ID does not belong to the selected workbook.")
        if str(test_case.get("execution") or "").upper() != "SQL":
            raise ValueError("Only a Test Case whose Execution value is SQL can receive a SQL script.")

        central = CentralRepository()
        folder = central.build_automation_path(
            version_row["folder_path"], "SQL", version_row["version_number"],
            version_row["source_file_id"],
        )
        folder_preexisted = folder.is_dir()
        target = folder / (central.sanitize_segment(test_case.get("tc_id") or test_case.get("tc_number")) + ".sql")
        previous = target.read_bytes() if target.is_file() else None
        folder.mkdir(parents=True, exist_ok=True)
        descriptor, staged_name = tempfile.mkstemp(prefix=f".{target.stem}-", suffix=".tmp", dir=folder)
        try:
            with open(descriptor, "wb", closefd=True) as handle:
                handle.write(bytes(file_bytes or b""))
            Path(staged_name).replace(target)
            script_json = self._pack_script(sql, "row_exists", None, None)
            self.repository.update_sql_asset(int(test_case_id), script_json, str(target))
        except Exception:
            Path(staged_name).unlink(missing_ok=True)
            if previous is None:
                target.unlink(missing_ok=True)
            else:
                target.write_bytes(previous)
            if not folder_preexisted and folder.is_dir() and not any(folder.iterdir()):
                folder.rmdir()
            raise
        return {"ok": True, "test_case_id": int(test_case_id),
                "test_case_number": test_case.get("tc_number"),
                "repository_path": str(target), "file_name": target.name,
                "script_already_existed": previous is not None}

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
            test_case.get("execution") != "SQL"
        ):

            raise ValueError(
                "This Test Case is not eligible for SQL Automation."
            )

        # SQL-AUTOMATION-LIFECYCLE root-cause fix #2: TestCaseRepository.
        # get_test_case() runs normalize_test_case() -> normalize_status(),
        # which OVERWRITES "status" with the workbook-export execution-
        # result status (Pass/Fail/Not Executed) and explicitly collapses
        # the raw lifecycle value "Automated" itself into "Not Executed"
        # whenever last_result isn't Pass/Fail yet (i.e. every test case
        # that hasn't been executed even once). get_test_case() already
        # preserves the real pre-normalization value under
        # automation_lifecycle_status for exactly this reason (see its
        # own docstring/comment) — validate_for_execution()'s API-side
        # Execute check already reads it correctly via
        # test_case.get("automation_lifecycle_status", test_case.get("status")).
        # This SQL-side check was reading the plain, normalized "status"
        # directly, so it was UNCONDITIONALLY false ("Not Executed" !=
        # "Automated") immediately after a real, successful Set Active —
        # which is exactly why PATCH .../sql-active returned 200 while the
        # very next POST .../execute-sql still reported "still Draft",
        # regardless of Save Script order. Mirroring the already-correct
        # API pattern here fixes it without touching what "status" means
        # for any other consumer (dashboards, workbook export, etc.).
        lifecycle_status = test_case.get("automation_lifecycle_status", test_case.get("status"))

        if lifecycle_status != "Automated":

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


# ============================================================
# SQL Automation — "Upload SQL Script" (Rule 6)
# ============================================================


class SqlScriptsWeb:
    """
    Thin web-facing wrapper around Core/sql_script_repository.py's
    SqlScriptRepository, mirroring ApiCollectionsWeb's own shape
    exactly (see that class above) — upload takes raw file bytes plus
    original filename, writes them to a temp file, delegates to the
    Core repository, and cleans up the temp file whether the import
    succeeded or failed.

    This is a SEPARATE, ADDITIVE class from SqlAutomationWeb above —
    see Core/sql_script_repository.py's module docstring for why the
    two mechanisms are intentionally kept apart.
    """

    def __init__(self):

        self.repo = SqlScriptRepository()

    def upload_script(
        self, file_bytes, original_filename, test_case_version_id,
        display_name=None,
    ):

        if not file_bytes:

            raise ValueError("A .sql file is required.")

        safe_name = Path(original_filename or "script.sql").name or "script.sql"

        if not safe_name.lower().endswith(".sql"):

            raise ValueError("Only .sql files can be uploaded as a SQL Script.")

        temp_dir = tempfile.mkdtemp(prefix="qaais_sql_")

        temp_path = Path(temp_dir) / safe_name

        temp_path.write_bytes(file_bytes)

        try:

            result = self.repo.upload_script(
                file_path=str(temp_path),
                test_case_version_id=test_case_version_id,
                display_name=display_name,
            )

        finally:

            shutil.rmtree(temp_dir, ignore_errors=True)

        if not result.get("success"):

            raise ValueError(result.get("error") or "Upload failed.")

        return result

    def list_scripts(self):

        return self.repo.list_scripts()

    def get_script(self, script_id):

        return self.repo.get_script(script_id)

    def delete_script(self, script_id):

        ok = self.repo.delete_script(script_id)

        if not ok:

            raise ValueError(f"SQL script {script_id} not found.")

        return {"success": True}

    def validate_script(self, script_id, executed_by_user_id=None, executed_by_username=None):

        return self.repo.validate_script(
            script_id, executed_by_user_id=executed_by_user_id,
            executed_by_username=executed_by_username,
        )

    def execute_script(self, script_id, executed_by_user_id=None, executed_by_username=None):

        return self.repo.execute_script(
            script_id, executed_by_user_id=executed_by_user_id,
            executed_by_username=executed_by_username,
        )

    def list_history(self, script_id, limit=50):
        """Rule 6 — see SqlScriptRepository.list_history()."""

        return self.repo.list_history(script_id, limit=limit)
