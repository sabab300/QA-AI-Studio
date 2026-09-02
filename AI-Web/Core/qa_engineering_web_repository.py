"""Web adapter for the Desktop QA Engineering test-case generation flow."""

import threading
import uuid
from datetime import datetime

from Core.knowledge_web_repository import KnowledgeRepository


_JOBS = {}
_JOBS_LOCK = threading.Lock()


class QaEngineeringWeb:
    """Provides scoped knowledge selection and non-blocking AI generation."""

    def list_scope(self, domain=None, module=None):
        items = KnowledgeRepository().list_items()
        if domain:
            items = [item for item in items if item.get("domain") == domain]
        if module:
            items = [item for item in items if item.get("module") == module]
        return items

    def start_generation(self, payload):
        job_id = uuid.uuid4().hex
        with _JOBS_LOCK:
            _JOBS[job_id] = {
                "job_id": job_id,
                "status": "queued",
                "started_at": datetime.now().isoformat(),
                "finished_at": None,
                "result": None,
                "error": None,
            }

        thread = threading.Thread(
            target=self._generate, args=(job_id, payload), daemon=True
        )
        thread.start()
        return {"job_id": job_id, "status": "queued"}

    def get_job(self, job_id):
        with _JOBS_LOCK:
            job = _JOBS.get(job_id)
            return dict(job) if job else None

    def _generate(self, job_id, payload):
        with _JOBS_LOCK:
            _JOBS[job_id]["status"] = "running"

        try:
            # Deferred import keeps API startup light and lets read-only scope
            # selection work even before the optional AI packages are installed.
            from Core.test_case_generator import TestCaseGenerator

            requirement = payload.get("requirement") or (
                "Generate comprehensive QA test cases for "
                f"'{payload['knowledge_name']}' under the {payload['module']} "
                f"module of the {payload['domain']} domain."
            )
            result = TestCaseGenerator().generate(
                requirement=requirement,
                number_of_cases=payload.get("number_of_cases", "all"),
                domain=payload["domain"],
                module=payload["module"],
                knowledge_name=payload["knowledge_name"],
                version=payload.get("version"),
                test_types=payload["test_types"],
                output_formats=payload["output_formats"],
            )
            if not result.get("success"):
                raise RuntimeError(result.get("error") or "Generation failed.")
            with _JOBS_LOCK:
                _JOBS[job_id].update(
                    status="finished", result=result,
                    finished_at=datetime.now().isoformat(),
                )
        except Exception as error:
            with _JOBS_LOCK:
                _JOBS[job_id].update(
                    status="error", error=str(error),
                    finished_at=datetime.now().isoformat(),
                )
