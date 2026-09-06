"""Web adapter for grounded QA Engineering generation and job history."""

import json
import threading
import uuid
from datetime import datetime

from Core.knowledge_web_repository import KnowledgeRepository
from Core.test_case_repository import TestCaseRepository
from Database.db_manager import DatabaseManager

_JOBS_LOCK = threading.RLock()


class QaEngineeringWeb:
    """Provides scoped generation, durable real logs, and reviewed saves."""

    def __init__(self):
        self.db = DatabaseManager()
        self.ensure_schema()

    def ensure_schema(self):
        conn = self.db.get_connection()
        conn.execute("""
            CREATE TABLE IF NOT EXISTS qa_generation_jobs (
                job_id TEXT PRIMARY KEY, status TEXT NOT NULL,
                domain TEXT, module TEXT, knowledge_name TEXT, version TEXT,
                requested_count TEXT, generated_count INTEGER DEFAULT 0,
                started_at TEXT NOT NULL, finished_at TEXT,
                message TEXT, error TEXT, payload_json TEXT, result_json TEXT,
                saved_ids_json TEXT DEFAULT '[]', logs_json TEXT DEFAULT '[]'
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_qa_generation_started ON qa_generation_jobs(started_at DESC)")
        conn.commit()
        conn.close()

    def reconcile_stale_jobs(self):
        now = datetime.now().isoformat()
        conn = self.db.get_connection()
        rows = conn.execute("SELECT job_id, logs_json FROM qa_generation_jobs WHERE status IN ('queued','running')").fetchall()
        for job_id, raw_logs in rows:
            logs = self._loads(raw_logs, [])
            logs.append(self._event("Failed", "Generation interrupted by server restart."))
            conn.execute(
                "UPDATE qa_generation_jobs SET status='error', finished_at=?, message=?, error=?, logs_json=? WHERE job_id=?",
                (now, "Interrupted", "Generation interrupted by server restart.", json.dumps(logs), job_id),
            )
        conn.commit()
        conn.close()

    def list_scope(self, domain=None, module=None):
        items = KnowledgeRepository().list_items()
        if domain:
            items = [item for item in items if item.get("domain") == domain]
        if module:
            items = [item for item in items if item.get("module") == module]
        return items

    def start_generation(self, payload):
        job_id = uuid.uuid4().hex
        now = datetime.now().isoformat()
        logs = [self._event("Preparing", "Generation request accepted.")]
        conn = self.db.get_connection()
        conn.execute("""
            INSERT INTO qa_generation_jobs
            (job_id,status,domain,module,knowledge_name,version,requested_count,
             started_at,message,payload_json,logs_json)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)
        """, (job_id, "queued", payload.get("domain"), payload.get("module"),
                payload.get("knowledge_name"), payload.get("version"),
                str(payload.get("number_of_cases", "all")), now, "Queued",
                json.dumps(payload), json.dumps(logs)))
        conn.commit()
        conn.close()
        threading.Thread(target=self._generate, args=(job_id, payload), daemon=True).start()
        return {"job_id": job_id, "status": "queued"}

    def get_job(self, job_id):
        conn = self.db.get_connection()
        conn.row_factory = self._dict_factory
        row = conn.execute("SELECT * FROM qa_generation_jobs WHERE job_id=?", (job_id,)).fetchone()
        conn.close()
        return self._decode_job(row) if row else None

    def list_jobs(self, limit=20, offset=0):
        conn = self.db.get_connection()
        conn.row_factory = self._dict_factory
        total = conn.execute("SELECT COUNT(*) AS count FROM qa_generation_jobs").fetchone()["count"]
        rows = conn.execute(
            "SELECT * FROM qa_generation_jobs ORDER BY started_at DESC LIMIT ? OFFSET ?",
            (max(1, min(int(limit), 100)), max(0, int(offset))),
        ).fetchall()
        conn.close()
        return {"jobs": [self._decode_job(row, include_result=False) for row in rows], "total": total}

    def save_job(self, job_id, rows):
        with _JOBS_LOCK:
            job = self.get_job(job_id)
            if not job:
                raise ValueError("Generation job not found.")
            if job.get("status") != "finished":
                raise ValueError("Generation must finish successfully before it can be saved.")
            if job.get("saved_ids"):
                return list(job["saved_ids"])
            payload = job.get("payload") or {}
            saved_ids = TestCaseRepository().save_generated_cases(
                payload.get("domain"), payload.get("module"), payload.get("knowledge_name"),
                payload.get("version"), rows, strict=True,
                source_knowledge_ids=payload.get("knowledge_source_ids"),
                test_case_document_name=payload.get("test_case_document_name"),
                document_type=payload.get("document_type"),
            )
            conn = self.db.get_connection()
            conn.execute(
                "UPDATE qa_generation_jobs SET saved_ids_json=?, message=? WHERE job_id=?",
                (json.dumps(saved_ids), f"Saved {len(saved_ids)} reviewed test cases.", job_id),
            )
            conn.commit()
            conn.close()
            self._append_log(job_id, "Saved", f"Saved {len(saved_ids)} reviewed test cases.")
            return saved_ids

    def _generate(self, job_id, payload):
        self._set_job(job_id, status="running", message="Loading Knowledge")
        self._append_log(job_id, "Loading Knowledge", "Retrieving grounded Knowledge context.")
        try:
            from Core.test_case_generator import TestCaseGenerator
            requested = payload.get("number_of_cases", "all")
            batch_sizes = self._batch_sizes(requested)
            all_rows, references = [], []
            provider = model = None
            generator = TestCaseGenerator()
            source_ids = payload.get("knowledge_source_ids") or []
            source_names = [
                item.get("file_name") for item in self.list_scope(payload.get("domain"), payload.get("module"))
                if item.get("id") in set(source_ids)
            ]
            self._append_log(
                job_id, "Source Scope",
                f"Document Type: {payload.get('document_type')}; selected sources: "
                + (", ".join(source_names) if source_names else "none")
            )
            for index, batch_size in enumerate(batch_sizes, start=1):
                label = f"Batch {index} of {len(batch_sizes)}" if len(batch_sizes) > 1 else "Generation"
                self._set_job(job_id, message=f"{label}: calling AI model")
                self._append_log(job_id, "Generating", f"{label} requested {batch_size} test case(s).")
                result = generator.generate(
                    requirement=("Generate comprehensive QA test cases grounded only in the selected "
                                 f"Knowledge '{payload['knowledge_name']}' under "
                                 f"{payload['domain']} / {payload['module']}."),
                    number_of_cases=batch_size, domain=payload["domain"],
                    module=payload["module"], knowledge_name=payload["knowledge_name"],
                    version=payload.get("version"), test_types=payload["test_types"],
                    source_file_names=source_names,
                    output_formats=[], persist=False, create_exports=False,
                )
                if not result.get("success"):
                    raise RuntimeError(result.get("error") or "Generation failed.")
                rows = result.get("rows") or []
                all_rows.extend(rows)
                references.extend(result.get("references") or [])
                provider, model = result.get("provider"), result.get("model")
                self._set_job(job_id, generated_count=len(all_rows))
                self._append_log(job_id, "Validated", f"{label} returned {len(rows)} structured test case(s).")
            final_result = {
                "success": True, "test_types": payload["test_types"],
                "case_count": len(all_rows), "rows": all_rows, "test_case_ids": [],
                "references": references, "provider": provider, "model": model,
            }
            self._set_job(job_id, status="finished", message="Completed",
                          generated_count=len(all_rows), result=final_result,
                          finished_at=datetime.now().isoformat())
            self._append_log(job_id, "Completed", f"Generated {len(all_rows)} draft test case(s).")
        except Exception as error:
            message = str(error) or "Generation failed."
            self._set_job(job_id, status="error", message="Failed", error=message,
                          finished_at=datetime.now().isoformat())
            self._append_log(job_id, "Failed", message)

    @staticmethod
    def _batch_sizes(requested):
        if requested == "all":
            return ["all"]
        remaining, sizes = int(requested), []
        while remaining > 0:
            size = min(10, remaining)
            sizes.append(size)
            remaining -= size
        return sizes

    def _set_job(self, job_id, **values):
        columns, params = [], []
        for key, value in values.items():
            columns.append(f"{'result_json' if key == 'result' else key}=?")
            params.append(json.dumps(value) if key == "result" else value)
        params.append(job_id)
        conn = self.db.get_connection()
        conn.execute(f"UPDATE qa_generation_jobs SET {', '.join(columns)} WHERE job_id=?", params)
        conn.commit()
        conn.close()

    def _append_log(self, job_id, stage, message):
        with _JOBS_LOCK:
            conn = self.db.get_connection()
            row = conn.execute("SELECT logs_json FROM qa_generation_jobs WHERE job_id=?", (job_id,)).fetchone()
            logs = self._loads(row[0], []) if row else []
            logs.append(self._event(stage, message))
            conn.execute("UPDATE qa_generation_jobs SET logs_json=? WHERE job_id=?", (json.dumps(logs), job_id))
            conn.commit()
            conn.close()

    @staticmethod
    def _event(stage, message):
        return {"timestamp": datetime.now().isoformat(), "stage": stage, "message": message}

    @staticmethod
    def _loads(value, default):
        try:
            return json.loads(value) if value else default
        except (TypeError, ValueError):
            return default

    def _decode_job(self, row, include_result=True):
        job = dict(row)
        job["payload"] = self._loads(job.pop("payload_json", None), {})
        result = self._loads(job.pop("result_json", None), None)
        if include_result:
            job["result"] = result
        job["saved_ids"] = self._loads(job.pop("saved_ids_json", None), [])
        job["logs"] = self._loads(job.pop("logs_json", None), [])
        return job

    @staticmethod
    def _dict_factory(cursor, row):
        return {column[0]: row[index] for index, column in enumerate(cursor.description)}
