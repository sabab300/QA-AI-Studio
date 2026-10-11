"""Web adapter for grounded QA Engineering generation and job history."""

import json
import threading
import uuid
from datetime import datetime, timedelta
from pathlib import Path

from Core.knowledge_web_repository import KnowledgeRepository
from Core.logger import Logger
from Core.test_case_repository import TestCaseRepository
from Database.db_manager import DatabaseManager
from Shared.document_hierarchy import DocumentHierarchy

_JOBS_LOCK = threading.RLock()


class QaEngineeringWeb:
    """Provides scoped generation, durable real logs, and reviewed saves."""

    def __init__(self):
        self.db = DatabaseManager()
        self.logger = Logger.get_logger()
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
        stale_before = (datetime.now() - timedelta(minutes=10)).isoformat()
        conn = self.db.get_connection()
        rows = conn.execute(
            "SELECT job_id, logs_json FROM qa_generation_jobs "
            "WHERE status IN ('queued','running') AND started_at<?",
            (stale_before,),
        ).fetchall()
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
        from Core.qa_engineering_version_service import QaEngineeringVersionService
        version_service = QaEngineeringVersionService()
        items = [
            version_service.get_source(item["id"])
            if not item.get("source_file_id") and item.get("source_type") not in {"API_COLLECTION", "API Collection"}
            else item
            for item in items
        ]
        if domain:
            items = [item for item in items if item.get("domain") == domain]
        if module:
            items = [item for item in items if item.get("module") == module]
        for item in items:
            item["display_name"] = self._source_display_name(item)
        return items

    @staticmethod
    def _resolve_source_file_names(scope_items, source_ids):
        """
        Resolve the selected knowledge item(s) into the exact
        `file_name` values RAGEngine.get_context() filters vector
        metadata against (see rag_engine.py's source_file_names
        matching on metadata["file_name"]).

        QA-AI-STUDIO-API-COLLECTION-KNOWLEDGE-QA-ENGINEERING-API-AUTOMATION-FINAL-FIX
        item 3/4 root cause: an API Collection's linked knowledge_items
        row is a placeholder with file_name="(imported API collection)"
        — that literal was never what got embedded. The REAL embedded
        chunks (ApiCollectionRepository._index_collection_for_search)
        are tagged with each collection's own uploaded file name
        (source_file_name). Using the placeholder here made the RAG
        filter match zero chunks for every API Collection scope,
        which QA Engineering surfaced as "No knowledge found." even
        though Manage Knowledge and API Automation both showed real,
        imported endpoints. For an API_COLLECTION item this now
        expands to every one of its linked collections' real source
        file names instead of the placeholder.
        """

        selected_ids = set(source_ids or [])
        selected = [item for item in scope_items if item.get("id") in selected_ids]
        names = []
        for item in selected:
            if item.get("source_type") == "API_COLLECTION" and item.get("api_collections"):
                for collection in item["api_collections"]:
                    name = collection.get("source_file_name")
                    if name:
                        names.append(name)
                continue
            name = item.get("file_name")
            if name:
                names.append(name)
        return names

    def _source_display_name(self, item):
        """Return the best real persisted source name without inventing labels."""
        candidates = [
            item.get("file_name"), item.get("source_name"), item.get("name"),
        ]
        for path_key in ("repository_path", "original_path"):
            raw_path = str(item.get(path_key) or "").strip()
            if raw_path:
                candidates.append(Path(raw_path).name)
        for collection in item.get("api_collections") or []:
            candidates.append(collection.get("name"))
        candidates.append(item.get("knowledge_name"))
        for candidate in candidates:
            value = str(candidate or "").strip()
            if value:
                return value
        self.logger.warning(
            "Knowledge source id=%s has no usable persisted display name.",
            item.get("id"),
        )
        return None

    def start_generation(self, payload):
        source = self.preflight_generation(payload)
        payload = dict(payload)
        job_id = uuid.uuid4().hex
        now = datetime.now().isoformat()
        logs = [self._event("Preparing", "Generation request accepted.")]
        conn = self.db.get_connection()
        try:
            conn.execute("BEGIN IMMEDIATE")
            owner = DocumentHierarchy(conn).resolve_source(source['id'])
            payload['document_version_id'] = owner['document_version_id']
            conn.execute("""
            INSERT INTO qa_generation_jobs
            (job_id,status,domain,module,knowledge_name,version,requested_count,
             started_at,message,payload_json,logs_json,document_version_id)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
        """, (job_id, "queued", payload.get("domain"), payload.get("module"),
                payload.get("knowledge_name"), payload.get("version"),
                str(payload.get("number_of_cases", "all")), now, "Queued",
                json.dumps(payload), json.dumps(logs), owner['document_version_id']))
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
        threading.Thread(target=self._generate, args=(job_id, payload), daemon=True).start()
        return {"job_id": job_id, "status": "queued"}

    def preflight_generation(self, payload):
        """Reject unusable selected Knowledge before starting the costly LLM job."""
        from Core.qa_engineering_version_service import QaEngineeringVersionService
        source_ids = payload.get("knowledge_source_ids") or []
        if len(source_ids) != 1:
            raise ValueError("AI generation requires exactly one selected source document.")
        source = QaEngineeringVersionService().get_source(source_ids[0])
        self.logger.info(
            "QA generation preflight source_id=%s status=%s file=%r",
            source.get("id"), source.get("status"), source.get("file_name"),
        )
        expected = {
            "domain": payload.get("domain"), "module": payload.get("module"),
            "knowledge_name": payload.get("knowledge_name"), "version": payload.get("version"),
            "document_type": payload.get("document_type"),
        }
        if any(str(source.get(key) or "") != str(value or "") for key, value in expected.items()):
            raise ValueError("The selected source document does not belong to the selected Knowledge hierarchy.")
        if str(source.get("status") or "").upper() == "FAILED_NO_TEXT":
            self.logger.warning(
                "QA generation preflight blocked source_id=%s reason=no_indexable_text",
                source.get("id"),
            )
            raise ValueError(
                "This document contains no extractable/indexed text. Upload or select a text-based "
                "document with indexed knowledge before generating Test Cases."
            )
        from Core.vector_store import VectorStore
        has_vectors = VectorStore().has_source_vectors(
            domain=source.get("domain"), module=source.get("module"),
            knowledge_name=source.get("knowledge_name"), version=source.get("version"),
            file_name=source.get("file_name"),
            document_type=source.get('document_type'), document_version_id=source.get('document_version_id'),
        )
        if not has_vectors:
            self.logger.warning(
                "QA generation preflight blocked source_id=%s reason=no_retrievable_vectors",
                source.get("id"),
            )
            raise ValueError(
                "The selected indexed source has no retrievable vectors. Re-index this source before generating Test Cases."
            )
        return source

    def get_job(self, job_id):
        conn = self.db.get_connection()
        conn.row_factory = self._dict_factory
        row = conn.execute("SELECT * FROM qa_generation_jobs WHERE job_id=?", (job_id,)).fetchone()
        conn.close()
        return self._decode_job(row) if row else None

    def list_jobs(self, limit=20, offset=0, q=None, started_from=None, started_to=None):
        conn = self.db.get_connection()
        conn.row_factory = self._dict_factory
        conditions, params = [], []
        if q:
            conditions.append("(job_id LIKE ? OR domain LIKE ? OR module LIKE ? OR knowledge_name LIKE ? OR message LIKE ?)")
            params.extend([f"%{q}%"] * 5)
        if started_from:
            conditions.append("started_at>=?"); params.append(started_from)
        if started_to:
            conditions.append("started_at<=?"); params.append(started_to)
        where = "WHERE " + " AND ".join(conditions) if conditions else ""
        total = conn.execute(f"SELECT COUNT(*) AS count FROM qa_generation_jobs {where}", params).fetchone()["count"]
        rows = conn.execute(
            f"SELECT * FROM qa_generation_jobs {where} ORDER BY started_at DESC LIMIT ? OFFSET ?",
            [*params, max(1, min(int(limit), 100)), max(0, int(offset))],
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
                document_version_id=payload.get("document_version_id"),
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
            from Core.test_case_generator import TestCaseGenerator, normalize_test_types
            requested = payload.get("number_of_cases", "all")
            batch_sizes = self._batch_sizes(requested)
            all_rows, references = [], []
            provider = model = None
            generator = TestCaseGenerator()
            source_ids = payload.get("knowledge_source_ids") or []
            source_names = self._resolve_source_file_names(
                self.list_scope(payload.get("domain"), payload.get("module")), source_ids
            )
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
                    document_type=payload.get('document_type'),
                    source_file_names=source_names,
                    output_formats=[], persist=False, create_exports=False,
                )
                if not result.get("success"):
                    raise RuntimeError(result.get("error") or "Generation failed.")
                rows = result.get("rows") or []
                for row in rows:
                    normalize_test_types(row, payload.get("test_types"))
                    row["source_knowledge_ids"] = list(source_ids)
                self._append_log(job_id, "Normalizing Test Types", f"Normalized {len(rows)} row(s) to canonical selected Test Types.")
                all_rows.extend(rows)
                references.extend(result.get("references") or [])
                provider, model = result.get("provider"), result.get("model")
                self._set_job(job_id, generated_count=len(all_rows))
                self._append_log(job_id, "Validated", f"{label} returned {len(rows)} structured test case(s).")
            if len(source_ids) != 1:
                raise RuntimeError("AI generation requires one managed Source File/Source Doc context.")
            from Core.qa_engineering_version_service import QaEngineeringVersionService
            version_service = QaEngineeringVersionService()
            created = version_service.create_generated_set(
                source_ids[0], all_rows, payload.get("test_case_document_name") or ""
            )
            persisted = version_service.load_version(
                created["test_case_version_id"], created["test_case_file_id"]
            )
            final_result = {
                "success": True, "test_types": payload["test_types"],
                "case_count": len(all_rows), "rows": persisted["test_cases"],
                "test_case_ids": created["test_case_ids"], "created": created,
                "references": references, "provider": provider, "model": model,
            }
            self._append_log(job_id, "Persisted", f"Created Test Cases V{created['version_number']} with {len(all_rows)} row(s).")
            self._set_job(job_id, status="finished", message="Completed",
                          generated_count=len(all_rows), result=final_result,
                          saved_ids=created["test_case_ids"],
                          finished_at=datetime.now().isoformat())
            self._append_log(job_id, "Completed", f"Generated and persisted {len(all_rows)} test case(s).")
        except Exception as error:
            message = str(error) or "Generation failed."
            self.logger.exception(
                "QA generation job failed job_id=%s source_ids=%s",
                job_id, payload.get("knowledge_source_ids") or [],
            )
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
            column = {"result": "result_json", "saved_ids": "saved_ids_json"}.get(key, key)
            columns.append(f"{column}=?")
            params.append(json.dumps(value) if key in {"result", "saved_ids"} else value)
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
