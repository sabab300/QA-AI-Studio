# Create: AI/Core/automation_web_repository.py

"""
QA AI Studio — Web
QA Automation Web Repository (Milestone 3, partial)

Version: 1.0

Thin web-facing wrappers around the SAME Core classes the desktop
app's QA Automation hub already uses (ApiCollectionRepository,
GitService, TestCaseRepository, TestExecutionManager, PlaywrightRunner)
— none of those files change in behavior to support this, beyond the
one additive `force_headless` constructor flag documented in
Core/playwright_runner.py and Core/test_execution_manager.py (a
server has no display; the desktop app's scripts assume one).

Scope covered here: API Collections (Postman import/browse/edit),
Git Automation (status/commit/pull/push/merge/compare/conflicts),
Test Case management (list/import from Excel/update status & scripts),
and script Execute as a background job (a real browser run can take
up to ~2 minutes — a synchronous HTTP request/response is the wrong
shape for that, so this exposes a start-job / poll-status pair
instead, backed by a simple in-process job table).

Deliberately NOT covered here (see delivery notes for why): AI-
generated script creation is exposed (generate_automation() is a
blocking LLM call, acceptable as a synchronous endpoint), but the
Playwright CODEGEN RECORDER (record_manual_script()) and the
INTERACTIVE locator-repair run (execute_playwright_interactive())
both need a live, human-drivable browser window / a bidirectional
channel — those need a WebSocket-based remote-control bridge, a
separate piece of work, not a REST wrapper like this one.
"""

import json
import shutil
import tempfile
import threading
import uuid
from datetime import datetime
from pathlib import Path

from Core.api_collection_repository import ApiCollectionRepository
from Core.git_service import GitService
from Core.test_case_repository import TestCaseRepository
from Core.test_execution_manager import TestExecutionManager

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
# Test Cases + Execute (background job)
# ============================================================

# In-process job table for Execute runs. Fine for a single-server
# deployment (matches "start on my PC / LAN" from the requirement);
# a shared multi-instance deployment would need this backed by a
# real queue/DB table instead of a module-level dict — flagged in
# delivery notes as a follow-up, not a blocker for the stated
# single-machine/LAN deployment target.
_EXECUTION_JOBS = {}
_EXECUTION_JOBS_LOCK = threading.Lock()


class TestCasesWeb:

    def __init__(self):

        self.repository = TestCaseRepository()

    def list_test_cases(self, domain, module, knowledge_name):

        return self.repository.list_test_cases(domain, module, knowledge_name)

    def get_test_case(self, test_case_id):

        return self.repository.get_test_case(test_case_id)

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

    def set_status(self, test_case_id, status):

        self.repository.update_status(test_case_id, status)

        return self.get_test_case(test_case_id)

    def generate_automation(self, test_case_id, automation_type, domain, module, knowledge_name, version=None):

        manager = TestExecutionManager()

        script = manager.generate_automation(
            test_case_id=test_case_id, automation_type=automation_type,
            domain=domain, module=module, knowledge_name=knowledge_name,
            version=version,
        )

        return {"test_case": self.get_test_case(test_case_id), "script": script}

    # --------------------------------------------------
    # Execute — background job (see module docstring: a real browser
    # run can take up to ~2 minutes, the wrong shape for a normal
    # request/response).
    # --------------------------------------------------

    def start_execution(self, test_case_id):

        test_case = self.repository.get_test_case(test_case_id)

        if not test_case:

            raise ValueError(f"Test case {test_case_id} not found.")

        job_id = uuid.uuid4().hex

        with _EXECUTION_JOBS_LOCK:

            _EXECUTION_JOBS[job_id] = {
                "job_id": job_id,
                "test_case_id": test_case_id,
                "status": "running",
                "started_at": datetime.now().isoformat(),
                "finished_at": None,
                "result": None,
                "error": None,
            }

        thread = threading.Thread(
            target=self._run_job, args=(job_id, test_case_id), daemon=True,
        )

        thread.start()

        return {"job_id": job_id, "status": "running"}

    def _run_job(self, job_id, test_case_id):

        try:

            # force_headless=True: this runs server-side, no display —
            # see PlaywrightRunner's docstring for why this is safe
            # and necessary (the generated/recorded script's own
            # headless=False is overridden regardless of what it says).
            manager = TestExecutionManager(force_headless=True)

            result = manager.execute_playwright(test_case_id)

            with _EXECUTION_JOBS_LOCK:

                _EXECUTION_JOBS[job_id]["status"] = "finished"
                _EXECUTION_JOBS[job_id]["result"] = result
                _EXECUTION_JOBS[job_id]["finished_at"] = datetime.now().isoformat()

        except Exception as error:

            with _EXECUTION_JOBS_LOCK:

                _EXECUTION_JOBS[job_id]["status"] = "error"
                _EXECUTION_JOBS[job_id]["error"] = str(error)
                _EXECUTION_JOBS[job_id]["finished_at"] = datetime.now().isoformat()

    def get_job(self, job_id):

        with _EXECUTION_JOBS_LOCK:

            job = _EXECUTION_JOBS.get(job_id)

            return dict(job) if job else None
