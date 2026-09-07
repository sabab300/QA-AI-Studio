# Create: AI/Core/test_case_repository.py

"""
QA AI Studio
Test Case Repository

Version: 1.0

Persists individual generated test cases (TC001, TC002...) so they
can be listed, statused, and executed from QA Automation — instead
of only ever existing inside an exported Excel/Word/PDF file.

Uses the same physical database (Database/metadata.db) and the same
flat domain/module TEXT columns already used by knowledge_items,
via Database.db_manager_v3.DatabaseManager (the connection object
already used successfully by metadata_manager.py).

Self-initializing: creates its own table on first use, following
the same pattern as other Core modules in this project (e.g.
memory_manager.py's ALTER TABLE ADD COLUMN pattern) rather than
touching either of the two existing (and currently inconsistent)
schema files.
"""

from datetime import datetime
import json
import hashlib
import re
import shutil
import sqlite3
import tempfile
from pathlib import Path

from Database.db_manager import DatabaseManager
from Core.logger import Logger


class TestCaseRepository:

    def __init__(self):

        self.db = DatabaseManager()

        self.logger = Logger.get_logger()

        self.ensure_schema()

    # --------------------------------------------------
    # Domain/Module ID resolution
    # --------------------------------------------------

    def _resolve_ids(self, domain, module):
        """
        Lazy import to avoid any import-order issues between this
        module and metadata_manager.py — both are Core modules and
        neither currently imports the other, but this keeps it safe
        regardless of future changes.
        """

        from Core.metadata_manager import MetadataManager

        metadata = MetadataManager()

        domain_id = metadata.get_or_create_domain(domain)

        module_id = metadata.get_or_create_module(domain, module)

        return domain_id, module_id

    # --------------------------------------------------
    # Schema
    # --------------------------------------------------

    def ensure_schema(self):

        conn = self.db.get_connection()

        cursor = conn.cursor()

        existing_columns = {
            row[1] for row in cursor.execute("PRAGMA table_info(test_cases)").fetchall()
        }

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS test_cases
            (
                id INTEGER PRIMARY KEY AUTOINCREMENT,

                tc_number TEXT NOT NULL,

                domain TEXT NOT NULL,
                module TEXT NOT NULL,
                knowledge_name TEXT NOT NULL,
                version TEXT,

                domain_id INTEGER,
                module_id INTEGER,

                scenario TEXT,
                importance TEXT,
                test_type TEXT,
                test_case TEXT,
                pre_conditions TEXT,
                steps TEXT,
                expected_result TEXT,

                status TEXT DEFAULT 'Manual',

                automation_type TEXT DEFAULT 'None',
                automation_script TEXT,

                last_result TEXT DEFAULT 'Not Run',
                last_run_date TEXT,

                created_date TEXT,
                modified_date TEXT,

                UNIQUE(domain, module, knowledge_name, tc_number)
            )
            """
        )

        cursor.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_test_cases_scope
            ON test_cases(domain, module, knowledge_name)
            """
        )

        # For a test_cases table that already existed before these
        # columns were added — CREATE TABLE IF NOT EXISTS above is
        # a no-op on an existing table, so this catches it.
        for migration in (
            "ALTER TABLE test_cases ADD COLUMN domain_id INTEGER",
            "ALTER TABLE test_cases ADD COLUMN module_id INTEGER",
            # Manual Recording (Playwright's own codegen recorder,
            # captured against this test case by hand) — kept in its
            # own column so it never overwrites the AI-generated
            # automation_script. active_script_source says which one
            # Execute actually runs; defaults to 'AUTO' so every
            # existing row keeps behaving exactly as it does today
            # until someone records a script and switches it.
            "ALTER TABLE test_cases ADD COLUMN recorded_script TEXT",
            "ALTER TABLE test_cases ADD COLUMN active_script_source "
            "TEXT DEFAULT 'AUTO'",
            "ALTER TABLE test_cases ADD COLUMN execution_type TEXT DEFAULT 'Manual'",
            "ALTER TABLE test_cases ADD COLUMN execution_tool TEXT DEFAULT ''",
            "ALTER TABLE test_cases ADD COLUMN source_knowledge_ids TEXT DEFAULT '[]'",
            "ALTER TABLE test_cases ADD COLUMN test_case_document_name TEXT DEFAULT ''",
            "ALTER TABLE test_cases ADD COLUMN tc_id TEXT",
            "ALTER TABLE test_cases ADD COLUMN review_state TEXT DEFAULT 'Reviewed/Saved'",
            "ALTER TABLE test_cases ADD COLUMN artifact_path TEXT DEFAULT ''",
            "ALTER TABLE test_cases ADD COLUMN document_type TEXT DEFAULT ''",
            "ALTER TABLE test_cases ADD COLUMN source_type TEXT DEFAULT ''",
            "ALTER TABLE test_cases ADD COLUMN legacy_origin TEXT",
            "ALTER TABLE test_cases ADD COLUMN reviewed_workbook_path TEXT DEFAULT ''",
        ):

            try:

                cursor.execute(migration)

            except Exception:

                pass

        if "execution_type" not in existing_columns:
            cursor.execute(
                """UPDATE test_cases
                   SET execution_type = CASE
                       WHEN status = 'Automated' THEN 'Automatable'
                       ELSE 'Manual' END"""
            )
        if "execution_tool" not in existing_columns:
            cursor.execute(
                """UPDATE test_cases SET execution_tool = CASE
                       WHEN automation_type IN ('Playwright','API','SQL') THEN automation_type
                       ELSE '' END"""
            )

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS test_case_sources (
                test_case_id INTEGER NOT NULL,
                knowledge_source_id INTEGER NOT NULL,
                file_name TEXT,
                source_type TEXT,
                PRIMARY KEY(test_case_id, knowledge_source_id),
                FOREIGN KEY(test_case_id) REFERENCES test_cases(id) ON DELETE CASCADE
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS test_case_id_sequences (
                prefix TEXT PRIMARY KEY,
                next_value INTEGER NOT NULL
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS qa_execution_tools (
                code TEXT PRIMARY KEY, label TEXT NOT NULL UNIQUE,
                automation_type TEXT NOT NULL UNIQUE, enabled INTEGER NOT NULL DEFAULT 1,
                sort_order INTEGER NOT NULL
            )
        """)
        cursor.executemany(
            "INSERT OR IGNORE INTO qa_execution_tools(code,label,automation_type,sort_order) VALUES(?,?,?,?)",
            (("playwright", "Playwright", "Playwright", 1),
             ("api", "API Automation", "API", 2),
             ("sql", "SQL Automation", "SQL", 3)),
        )
        cursor.execute("UPDATE test_cases SET execution_tool='API Automation' WHERE execution_tool='API'")
        cursor.execute("UPDATE test_cases SET execution_tool='SQL Automation' WHERE execution_tool='SQL'")
        cursor.execute("CREATE UNIQUE INDEX IF NOT EXISTS uq_test_cases_tc_id ON test_cases(tc_id) WHERE tc_id IS NOT NULL")
        cursor.execute("CREATE UNIQUE INDEX IF NOT EXISTS uq_test_cases_legacy_origin ON test_cases(legacy_origin) WHERE legacy_origin IS NOT NULL")
        self._migrate_legacy_desktop_rows(cursor)
        self._backfill_canonical_ids(cursor)
        self._backfill_source_relations(cursor)

        conn.commit()

        conn.close()

    def list_execution_tools(self):
        conn = self.db.get_connection()
        conn.row_factory = self._dict_factory
        rows = conn.execute(
            "SELECT code,label,automation_type FROM qa_execution_tools WHERE enabled=1 ORDER BY sort_order"
        ).fetchall()
        conn.close()
        return rows

    # --------------------------------------------------
    # Save generated cases (bulk, from TestCaseGenerator)
    # --------------------------------------------------

    def save_generated_cases(

        self,

        domain,

        module,

        knowledge_name,

        version,

        rows,

        strict=False,
        source_knowledge_ids=None,
        test_case_document_name=None,
        document_type=None,
        source_type=None,

    ):

        if not rows:

            return []

        scope = {
            "domain": domain, "module": module, "knowledge_name": knowledge_name,
            "version": version or "", "document_type": document_type or "",
            "source_type": source_type or "", "source_knowledge_ids": source_knowledge_ids or [],
            "test_case_document_name": test_case_document_name or "",
        }
        if source_knowledge_ids:
            result = self.review_save(
                scope, [{"action": "insert", "values": row} for row in rows]
            )
            return result["inserted_ids"]

        domain_id, module_id = self._resolve_ids(domain, module)

        conn = self.db.get_connection()

        cursor = conn.cursor()

        now = datetime.now().isoformat()

        next_number = self._next_tc_number(
            cursor,
            domain,
            module,
            knowledge_name
        )

        saved_ids = []

        for row in rows:

            tc_number = f"TC{next_number:03d}"

            try:

                cursor.execute(
                    """
                    INSERT INTO test_cases
                    (
                        tc_number, domain, module, knowledge_name, version,
                        domain_id, module_id,
                        scenario, importance, test_type, test_case,
                        pre_conditions, steps, expected_result,
                        status, automation_type, last_result,
                        execution_type, execution_tool, source_knowledge_ids,
                        test_case_document_name,
                        created_date, modified_date
                    )
                    VALUES
                    (?,?,?,?,?, ?,?, ?,?,?,?, ?,?,?, 'Manual','None','Not Run', ?,?,?,?, ?,?)
                    """,
                    (
                        tc_number,
                        domain,
                        module,
                        knowledge_name,
                        version or "",
                        domain_id,
                        module_id,
                        row.get("scenario", ""),
                        row.get("importance", ""),
                        self._serialize_test_types(row),
                        row.get("test_case", ""),
                        row.get("pre_conditions", ""),
                        row.get("steps", ""),
                        row.get("expected_result", ""),
                        self._execution_type(row),
                        self._execution_tool(row),
                        json.dumps(source_knowledge_ids or []),
                        test_case_document_name or "",
                        now,
                        now,
                    )
                )

                saved_ids.append(cursor.lastrowid)

                next_number += 1

            except Exception:

                # Extremely unlikely UNIQUE collision (concurrent
                # generation) — skip this row rather than fail
                # the whole batch.
                self.logger.exception(
                    f"Could not save test case {tc_number}, skipping."
                )

                if strict:

                    conn.rollback()

                    conn.close()

                    raise

        conn.commit()

        conn.close()

        self.logger.info(
            f"Saved {len(saved_ids)} test case(s) to the database "
            f"for {domain} / {module} / {knowledge_name}."
        )

        return saved_ids


    def _next_tc_number(self, cursor, domain, module, knowledge_name):

        cursor.execute(
            """
            SELECT tc_number
            FROM test_cases
            WHERE domain=? AND module=? AND knowledge_name=?
            ORDER BY id DESC
            LIMIT 1
            """,
            (domain, module, knowledge_name)
        )

        row = cursor.fetchone()

        if not row:

            return 1

        try:

            return int(row[0].replace("TC", "")) + 1

        except (ValueError, AttributeError):

            return 1

    def review_save(self, scope, operations):
        """Apply only selected changes in one DB transaction with canonical artifacts."""
        if not operations:
            return {"inserted_ids": [], "updated_ids": [], "deleted_ids": [], "items": []}
        sources = self._source_records(scope.get("source_knowledge_ids") or [])
        requested = {int(value) for value in scope.get("source_knowledge_ids") or []}
        if not sources or {item["id"] for item in sources} != requested:
            raise ValueError("Every selected row requires valid persisted Knowledge sources.")
        self._validate_scope_sources(scope, sources)
        domain_id, module_id = self._resolve_ids(scope["domain"], scope["module"])
        conn = self.db.get_connection(); conn.row_factory = self._dict_factory
        cursor = conn.cursor(); now = datetime.now().isoformat()
        inserted, updated, deleted, created, staged = [], [], [], [], []
        try:
            cursor.execute("BEGIN IMMEDIATE")
            version_row = cursor.execute(
                "SELECT id FROM knowledge_versions WHERE knowledge_item_id=? AND version=? ORDER BY id LIMIT 1",
                (sources[0]["id"], scope.get("version") or ""),
            ).fetchone()
            scope = dict(scope)
            scope["_version_id"] = (version_row or {}).get("id", 0) if isinstance(version_row, dict) else (version_row[0] if version_row else 0)
            for operation in operations:
                action, values = operation.get("action"), operation.get("values") or {}
                if action == "insert":
                    self._validate_case(values, cursor)
                    tc_id = self._allocate_tc_id(cursor, scope, sources)
                    cursor.execute("""
                        INSERT INTO test_cases
                        (tc_number,tc_id,domain,module,knowledge_name,version,domain_id,module_id,
                         scenario,importance,test_type,test_case,pre_conditions,steps,expected_result,
                         status,automation_type,last_result,execution_type,execution_tool,
                         source_knowledge_ids,test_case_document_name,document_type,source_type,
                         review_state,created_date,modified_date)
                        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'Manual','None','Not Run',?,?,?,?,?,?,
                               'Reviewed/Saved',?,?)
                    """, (tc_id,tc_id,scope["domain"],scope["module"],scope["knowledge_name"],scope.get("version") or "",
                            domain_id,module_id,values.get("scenario", ""),values.get("importance", ""),
                            self._serialize_test_types(values),values.get("test_case", ""),values.get("pre_conditions", ""),
                            values.get("steps", ""),values.get("expected_result", ""),self._execution_type(values),
                            self._execution_tool(values, cursor),json.dumps(sorted(requested)),
                            scope.get("test_case_document_name") or "",scope.get("document_type") or sources[0].get("document_type") or "",
                            self._combined_source_type(sources),now,now))
                    test_case_id = cursor.lastrowid; inserted.append(test_case_id)
                    self._replace_sources(cursor, test_case_id, sources)
                    artifact = self._write_artifact(dict(values, id=test_case_id, tc_id=tc_id, **scope), sources)
                    created.append(artifact)
                    cursor.execute("UPDATE test_cases SET artifact_path=? WHERE id=?", (str(artifact), test_case_id))
                elif action == "update":
                    test_case_id = int(operation.get("id") or 0)
                    current = cursor.execute("SELECT * FROM test_cases WHERE id=?", (test_case_id,)).fetchone()
                    if not current: raise ValueError(f"Test Case id={test_case_id} was not found.")
                    self._validate_case(values, cursor)
                    old_sources = self._sources_for_case(cursor, test_case_id) or sources
                    old_path = Path(current.get("artifact_path") or "")
                    if old_path.is_file():
                        backup = Path(tempfile.mktemp(prefix="qa_tc_", suffix=".json")); shutil.copy2(old_path, backup); staged.append((old_path, backup, False))
                    cursor.execute("""UPDATE test_cases SET scenario=?,importance=?,test_type=?,test_case=?,pre_conditions=?,
                        steps=?,expected_result=?,execution_type=?,execution_tool=?,review_state='Reviewed/Saved',modified_date=? WHERE id=?""",
                        (values.get("scenario", ""),values.get("importance", ""),self._serialize_test_types(values),
                         values.get("test_case", ""),values.get("pre_conditions", ""),values.get("steps", ""),
                         values.get("expected_result", ""),self._execution_type(values),self._execution_tool(values, cursor),now,test_case_id))
                    merged = dict(current); merged.update(values)
                    artifact = self._write_artifact(merged, old_sources)
                    cursor.execute("UPDATE test_cases SET artifact_path=? WHERE id=?", (str(artifact), test_case_id)); updated.append(test_case_id)
                elif action == "delete":
                    test_case_id = int(operation.get("id") or 0)
                    current = cursor.execute("SELECT artifact_path FROM test_cases WHERE id=?", (test_case_id,)).fetchone()
                    if not current: raise ValueError(f"Test Case id={test_case_id} was not found.")
                    old_path = Path(current.get("artifact_path") or "")
                    if old_path.is_file():
                        backup = Path(tempfile.mktemp(prefix="qa_tc_delete_", suffix=".json")); shutil.move(old_path, backup); staged.append((old_path, backup, True))
                    cursor.execute("DELETE FROM test_cases WHERE id=?", (test_case_id,)); deleted.append(test_case_id)
                else:
                    raise ValueError("Unsupported Review / Save action.")
            conn.commit()
            for _, backup, _ in staged:
                if backup.is_file(): backup.unlink()
            ids = inserted + updated
            return {"inserted_ids": inserted, "updated_ids": updated, "deleted_ids": deleted,
                    "items": [self.get_test_case(value) for value in ids]}
        except Exception:
            conn.rollback()
            for path in created:
                if path.is_file(): path.unlink()
            for original, backup, _ in staged:
                if backup.is_file(): original.parent.mkdir(parents=True, exist_ok=True); shutil.copy2(backup, original); backup.unlink()
            raise
        finally:
            conn.close()

    def _source_records(self, source_ids):
        if not source_ids: return []
        conn = self.db.get_connection(); conn.row_factory = self._dict_factory
        marks = ",".join("?" for _ in source_ids)
        rows = conn.execute(f"SELECT * FROM knowledge_items WHERE id IN ({marks}) ORDER BY id", [int(v) for v in source_ids]).fetchall()
        conn.close(); return rows

    @staticmethod
    def _validate_scope_sources(scope, sources):
        for source in sources:
            for key in ("domain", "module", "knowledge_name", "version", "document_type"):
                expected = str(scope.get(key) or "")
                actual = str(source.get(key) or "")
                if expected and actual != expected:
                    raise ValueError(f"Selected source {source['id']} does not match {key}.")

    def _validate_case(self, values, cursor):
        if not str(values.get("importance") or "").strip(): raise ValueError("Importance is required.")
        if not str(values.get("execution_type") or "").strip(): raise ValueError("Execution Type is required.")
        if not str(values.get("scenario") or "").strip(): raise ValueError("Scenario is required.")
        if not str(values.get("pre_conditions") or "").strip(): raise ValueError("Preconditions are required.")
        if not str(values.get("test_case") or "").strip(): raise ValueError("Test Case is required.")
        if not str(values.get("steps") or "").strip(): raise ValueError("Test Steps are required.")
        if not str(values.get("expected_result") or "").strip(): raise ValueError("Expected Result is required.")
        if not self._serialize_test_types(values): raise ValueError("At least one Test Type is required.")
        if self._execution_type(values) == "Automatable" and not self._execution_tool(values, cursor):
            raise ValueError("Execution Tool is required for an Automatable Test Case.")

    def set_reviewed_workbook(self, test_case_ids, relative_path):
        """Associate the reviewed workbook with exactly its saved batch."""
        ids = [int(value) for value in test_case_ids]
        if not ids:
            return
        conn = self.db.get_connection()
        try:
            marks = ",".join("?" for _ in ids)
            conn.execute(
                f"UPDATE test_cases SET reviewed_workbook_path=? WHERE id IN ({marks})",
                [str(relative_path), *ids],
            )
            conn.commit()
        finally:
            conn.close()

    @staticmethod
    def _segment(value):
        cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", str(value or "NA")).strip("._")
        return cleaned[:100] or "NA"

    def _allocate_tc_id(self, cursor, scope, sources):
        domain_id = sources[0].get("domain_id") or "0"; module_id = sources[0].get("module_id") or "0"
        knowledge_id = min(item["id"] for item in sources)
        version_row = cursor.execute("SELECT id FROM knowledge_versions WHERE knowledge_item_id=? AND version=? ORDER BY id LIMIT 1", (knowledge_id, scope.get("version") or "")).fetchone()
        version_id = (version_row or {}).get("id") if isinstance(version_row, dict) else (version_row[0] if version_row else "0")
        prefix = "TC_" + "_".join((f"D{domain_id}", f"M{module_id}", f"KN{knowledge_id}", f"V{version_id}",
                                      self._segment(scope.get("document_type") or "DOC").upper(), self._source_code(self._combined_source_type(sources))))
        row = cursor.execute("SELECT next_value FROM test_case_id_sequences WHERE prefix=?", (prefix,)).fetchone()
        sequence = int((row or {}).get("next_value", 1) if isinstance(row, dict) else (row[0] if row else 1))
        cursor.execute("INSERT INTO test_case_id_sequences(prefix,next_value) VALUES(?,?) ON CONFLICT(prefix) DO UPDATE SET next_value=excluded.next_value", (prefix, sequence + 1))
        return f"{prefix}_{sequence:04d}"

    def _backfill_canonical_ids(self, cursor):
        rows = cursor.execute("SELECT * FROM test_cases WHERE tc_id IS NULL OR tc_id='' ORDER BY id").fetchall()
        columns = [item[0] for item in cursor.description] if cursor.description else []
        for raw in rows:
            row = raw if isinstance(raw, dict) else dict(zip(columns, raw))
            scope = {"domain": row.get("domain"), "module": row.get("module"), "knowledge_name": row.get("knowledge_name"),
                     "version": row.get("version"), "document_type": row.get("document_type") or "LEGACY"}
            try: source_ids = [int(value) for value in json.loads(row.get("source_knowledge_ids") or "[]")]
            except (TypeError, ValueError): source_ids = []
            marks = ",".join("?" for _ in source_ids)
            source_rows = cursor.execute(f"SELECT * FROM knowledge_items WHERE id IN ({marks}) ORDER BY id", source_ids).fetchall() if source_ids else []
            source_columns = [item[0] for item in cursor.description] if source_rows and not isinstance(source_rows[0], dict) else []
            sources = [item if isinstance(item, dict) else dict(zip(source_columns, item)) for item in source_rows]
            if sources:
                canonical = self._allocate_tc_id(cursor, scope, sources)
                cursor.execute("UPDATE test_cases SET tc_id=? WHERE id=?", (canonical, row["id"]))
                continue
            prefix = "TC_" + "_".join((f"D{row.get('domain_id') or 0}", f"M{row.get('module_id') or 0}", "KN0", "V0",
                                         self._segment(scope["document_type"]).upper(), "UNK"))
            seq = cursor.execute("SELECT next_value FROM test_case_id_sequences WHERE prefix=?", (prefix,)).fetchone()
            number = int(seq[0]) if seq else 1
            cursor.execute("INSERT INTO test_case_id_sequences(prefix,next_value) VALUES(?,?) ON CONFLICT(prefix) DO UPDATE SET next_value=excluded.next_value", (prefix, number + 1))
            cursor.execute("UPDATE test_cases SET tc_id=? WHERE id=?", (f"{prefix}_{number:04d}", row["id"]))

    def _migrate_legacy_desktop_rows(self, cursor):
        canonical = (Path(__file__).resolve().parent.parent / "Database" / "metadata.db").resolve()
        if not hasattr(self.db, "db_path") or Path(self.db.db_path).resolve() != canonical:
            return
        legacy = Path(__file__).resolve().parents[2] / "AI" / "Database" / "metadata.db"
        if not legacy.is_file() or legacy.resolve() == canonical:
            return
        source = sqlite3.connect(legacy); source.row_factory = sqlite3.Row
        try:
            exists = source.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='test_cases'").fetchone()
            if not exists: return
            target_columns = {row[1] for row in cursor.execute("PRAGMA table_info(test_cases)").fetchall()}
            for row in source.execute("SELECT * FROM test_cases ORDER BY id"):
                item = dict(row)
                origin = "desktop:" + hashlib.sha256("|".join(str(item.get(key) or "") for key in ("id","domain","module","knowledge_name","tc_number","created_date")).encode()).hexdigest()
                values = {key: value for key, value in item.items() if key in target_columns and key not in {"id","tc_id","artifact_path","source_knowledge_ids"}}
                values["legacy_origin"] = origin; values["tc_id"] = None; values["source_knowledge_ids"] = "[]"
                columns = list(values)
                cursor.execute(f"INSERT OR IGNORE INTO test_cases({','.join(columns)}) VALUES({','.join('?' for _ in columns)})", [values[key] for key in columns])
        finally:
            source.close()

    @staticmethod
    def _backfill_source_relations(cursor):
        rows = cursor.execute("SELECT id,source_knowledge_ids FROM test_cases").fetchall()
        for row in rows:
            test_case_id, raw_ids = (row.get("id"), row.get("source_knowledge_ids")) if isinstance(row, dict) else row
            try: source_ids = [int(value) for value in json.loads(raw_ids or "[]")]
            except (TypeError, ValueError): source_ids = []
            for source_id in source_ids:
                source = cursor.execute("SELECT file_name,source_type FROM knowledge_items WHERE id=?", (source_id,)).fetchone()
                if source:
                    file_name, source_type = (source.get("file_name"), source.get("source_type")) if isinstance(source, dict) else source
                    cursor.execute("INSERT OR IGNORE INTO test_case_sources(test_case_id,knowledge_source_id,file_name,source_type) VALUES(?,?,?,?)", (test_case_id,source_id,file_name,source_type))

    @staticmethod
    def _source_code(value):
        return {"Files":"FIL","Folder":"FIL","URL":"URL","API Collection":"API","API_COLLECTION":"API",
                "Images":"IMG","Git Repository":"GIT","SQL Script":"SQL","SQA Script":"SQA","MULTI":"MULTI"}.get(value, "UNK")

    @staticmethod
    def _combined_source_type(sources):
        aliases = {"FILE":"Files","Folder":"Files","MANUAL_UPLOAD":"Files","SMART_UPLOAD":"Files",
                   "API_COLLECTION":"API Collection","URL_CAPTURE":"URL"}
        values = {aliases.get(str(item.get("source_type") or ""), str(item.get("source_type") or "")) for item in sources}
        return next(iter(values)) if len(values) == 1 else "MULTI"

    @staticmethod
    def _replace_sources(cursor, test_case_id, sources):
        cursor.execute("DELETE FROM test_case_sources WHERE test_case_id=?", (test_case_id,))
        cursor.executemany("INSERT INTO test_case_sources(test_case_id,knowledge_source_id,file_name,source_type) VALUES(?,?,?,?)",
                           [(test_case_id,item["id"],item.get("file_name"),item.get("source_type")) for item in sources])

    @staticmethod
    def _sources_for_case(cursor, test_case_id):
        return cursor.execute("SELECT knowledge_source_id AS id,file_name,source_type FROM test_case_sources WHERE test_case_id=? ORDER BY knowledge_source_id", (test_case_id,)).fetchall()

    def _artifact_folder(self, scope, sources):
        root = Path(__file__).resolve().parent.parent / "Repository"
        first = sources[0]; source_type = self._combined_source_type(sources)
        source_name = first.get("file_name") if len(sources) == 1 else "Sources_" + "_".join(str(item["id"]) for item in sources)
        version_id = f"V{scope.get('_version_id') or 0}"
        return root / f"D{first.get('domain_id') or 0}" / self._segment(scope.get("domain")) / f"M{first.get('module_id') or 0}" / self._segment(scope.get("module")) / f"KN{min(item['id'] for item in sources)}" / self._segment(scope.get("knowledge_name")) / version_id / self._segment(scope.get("version")) / "Documents" / self._segment(scope.get("document_type") or first.get("document_type")) / self._segment(source_type) / self._segment(source_name) / "Test Cases"

    def _write_artifact(self, row, sources):
        existing = Path(row.get("artifact_path") or "")
        if existing.is_absolute() and existing.name:
            path = existing; path.parent.mkdir(parents=True, exist_ok=True)
        else:
            folder = self._artifact_folder(row, sources); folder.mkdir(parents=True, exist_ok=True)
            path = folder / f"{self._segment(row.get('tc_id') or row.get('tc_number'))}.json"
        payload = {key: value for key, value in row.items() if not str(key).startswith("_")}
        payload["sources"] = [{"source_id": item["id"], "file_name": item.get("file_name"), "source_type": item.get("source_type")} for item in sources]
        temporary = path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        temporary.replace(path); return path


    # --------------------------------------------------
    # List
    # --------------------------------------------------

    def list_test_cases(self, domain, module, knowledge_name):

        conn = self.db.get_connection()

        conn.row_factory = self._dict_factory

        cursor = conn.cursor()

        cursor.execute(
            """
            SELECT *
            FROM test_cases
            WHERE domain=? AND module=? AND knowledge_name=?
            ORDER BY
                CAST(REPLACE(tc_number,'TC','') AS INTEGER)
            """,
            (domain, module, knowledge_name)
        )

        rows = cursor.fetchall()

        conn.close()

        return [self._with_sources(self._with_test_types(row)) for row in rows]

    # --------------------------------------------------
    # WEB PORT ADDITION: cross-scope listing.
    #
    # Every existing caller (Desktop and Web alike) only ever lists
    # test cases for ONE already-selected Domain/Module/Knowledge Name
    # — there was no landing page that needed to show automation
    # assets across every scope at once. The Web "Automation
    # Workspace" (see AGENTS.md / the QA Automation task spec, section
    # 7) is exactly that kind of landing page, so it needs a real
    # cross-scope query instead of requiring a scope to be picked
    # first. Purely additive — does not change list_test_cases()'s
    # existing behavior or signature.
    # --------------------------------------------------

    def list_all_test_cases(
        self, domain=None, module=None, knowledge_name=None, version=None,
        status=None, automation_type=None, execution_type=None, execution_tool=None,
        document_type=None, q=None,
        limit=200, offset=0,
    ):
        """
        Returns {"test_cases": [...], "total": N} — total is the
        count BEFORE limit/offset, for real pagination.
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

        if version:
            conditions.append("version=?")
            params.append(version)

        if status:

            conditions.append("status=?")

            params.append(status)

        if automation_type:

            conditions.append("automation_type=?")

            params.append(automation_type)

        if execution_type:
            conditions.append("execution_type=?")
            params.append(execution_type)

        if execution_tool:
            conditions.append("execution_tool=?")
            params.append(execution_tool)

        if document_type:
            conditions.append("document_type=?")
            params.append(document_type)

        if q:

            conditions.append(
                "(tc_number LIKE ? OR test_case LIKE ? OR scenario LIKE ?)"
            )

            like = f"%{q}%"

            params.extend([like, like, like])

        where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""

        conn = self.db.get_connection()

        conn.row_factory = self._dict_factory

        cursor = conn.cursor()

        cursor.execute(
            f"SELECT COUNT(*) as c FROM test_cases {where_clause}", params
        )

        total = cursor.fetchone()["c"]

        cursor.execute(
            f"""
            SELECT *
            FROM test_cases
            {where_clause}
            ORDER BY modified_date DESC, id DESC
            LIMIT ? OFFSET ?
            """,
            params + [limit, offset],
        )

        rows = cursor.fetchall()

        conn.close()

        return {"test_cases": [self._with_sources(self._with_test_types(row)) for row in rows], "total": total}

    def list_distinct_scopes(self):
        """
        Distinct Domain / Module / Knowledge Name combinations that
        currently have at least one test case — backs the Automation
        Workspace's filter dropdowns with only scopes that actually
        HAVE automation assets, rather than every Knowledge Hub
        domain/module (most of which have no automation yet).
        """

        conn = self.db.get_connection()

        conn.row_factory = self._dict_factory

        cursor = conn.cursor()

        cursor.execute(
            """
            SELECT DISTINCT domain, module, knowledge_name
            FROM test_cases
            ORDER BY domain, module, knowledge_name
            """
        )

        rows = cursor.fetchall()

        conn.close()

        return rows

    def list_context_test_cases(self, scope):
        """Return persisted rows visible in one QA Engineering source context."""
        result = self.list_all_test_cases(
            domain=scope.get("domain"), module=scope.get("module"),
            knowledge_name=scope.get("knowledge_name"), version=scope.get("version"),
            limit=10000,
        )
        source_ids = {int(value) for value in scope.get("source_knowledge_ids") or []}
        document_type = scope.get("document_type") or ""
        return [
            row for row in result["test_cases"]
            if (not document_type or row.get("document_type") == document_type)
            and source_ids.intersection(int(value) for value in row.get("source_knowledge_ids") or [])
        ]

    def delete_test_case(self, test_case_id):
        """
        Used by the "delete" permission action (Admin-only by
        default — see Core/user_repository.py's DEFAULT_ROLES, no
        seeded role grants 'delete' on 'automation' except Admin).
        Returns True if a row was actually deleted.
        """

        conn = self.db.get_connection()

        cursor = conn.cursor()

        cursor.execute("DELETE FROM test_cases WHERE id=?", (test_case_id,))

        deleted = cursor.rowcount > 0

        conn.commit()

        conn.close()

        return deleted

    def create_test_case(self, domain, module, knowledge_name, version, row):
        saved = self.save_generated_cases(
            domain, module, knowledge_name, version, [row]
        )
        return self.get_test_case(saved[0]) if saved else None

    def update_test_case(self, test_case_id, values):
        allowed = {
            "scenario", "importance", "test_type", "test_case",
            "pre_conditions", "steps", "expected_result", "status",
            "execution_type", "execution_tool", "test_case_document_name",
        }
        updates = {key: values[key] for key in allowed if key in values}
        if "test_types" in values:
            updates["test_type"] = self._serialize_test_types(values)
        if "execution_type" in updates:
            updates["execution_type"] = self._execution_type(updates)
            if updates["execution_type"] == "Manual":
                updates["execution_tool"] = ""
        if "execution_tool" in updates:
            updates["execution_tool"] = self._execution_tool(updates)
        if not updates:
            return self.get_test_case(test_case_id)
        updates["modified_date"] = datetime.now().isoformat()
        conn = self.db.get_connection()
        cursor = conn.cursor()
        assignments = ", ".join(f"{key}=?" for key in updates)
        cursor.execute(
            f"UPDATE test_cases SET {assignments} WHERE id=?",
            [*updates.values(), test_case_id],
        )
        changed = cursor.rowcount > 0
        conn.commit()
        conn.close()
        return self.get_test_case(test_case_id) if changed else None


    @staticmethod
    def _dict_factory(cursor, row):

        columns = [col[0] for col in cursor.description]

        return {
            columns[i]: row[i]
            for i in range(len(columns))
        }


    # --------------------------------------------------
    # Updates
    # --------------------------------------------------

    def update_status(self, test_case_id, status):

        self._update_field(
            test_case_id,
            "status",
            status
        )


    def update_automation(

        self,

        test_case_id,

        automation_type,

        automation_script=None

    ):

        conn = self.db.get_connection()

        cursor = conn.cursor()

        now = datetime.now().isoformat()

        cursor.execute(
            """
            UPDATE test_cases
            SET automation_type=?,
                automation_script=COALESCE(?, automation_script),
                status='Draft',
                execution_type='Automatable',
                execution_tool=?,
                modified_date=?
            WHERE id=?
            """,
            (
                automation_type,
                automation_script,
                self.execution_tool_for_automation_type(automation_type),
                now,
                test_case_id,
            )
        )

        conn.commit()

        conn.close()

    def update_recorded_script(self, test_case_id, recorded_script):
        """
        Stores a script captured by hand via Playwright's own
        codegen recorder ("Record Manually"), kept in its own column
        so it never overwrites the AI-generated automation_script —
        active_script_source decides which one actually runs on
        Execute. Also marks this test case Automated/Playwright,
        same as an AI-generated script would, since either way it
        now has something Execute can run.
        """

        conn = self.db.get_connection()

        cursor = conn.cursor()

        now = datetime.now().isoformat()

        cursor.execute(
            """
            UPDATE test_cases
            SET recorded_script=?,
                automation_type='Playwright',
                status='Draft',
                execution_type='Automatable',
                execution_tool='Playwright',
                modified_date=?
            WHERE id=?
            """,
            (
                recorded_script,
                now,
                test_case_id,
            )
        )

        conn.commit()

        conn.close()


    def set_active_script_source(self, test_case_id, source):
        """
        `source`: 'AUTO' (the AI-generated script) or 'MANUAL' (the
        hand-recorded one).
        """

        self._update_field(
            test_case_id,
            "active_script_source",
            source
        )

    def update_result(self, test_case_id, result):

        conn = self.db.get_connection()

        cursor = conn.cursor()

        now = datetime.now().isoformat()

        cursor.execute(
            """
            UPDATE test_cases
            SET last_result=?,
                last_run_date=?,
                modified_date=?
            WHERE id=?
            """,
            (
                result,
                now,
                now,
                test_case_id,
            )
        )

        conn.commit()

        conn.close()


    def get_test_case(self, test_case_id):

        conn = self.db.get_connection()

        conn.row_factory = self._dict_factory

        cursor = conn.cursor()

        cursor.execute(
            "SELECT * FROM test_cases WHERE id=?",
            (test_case_id,)
        )

        row = cursor.fetchone()

        conn.close()

        return self._with_sources(self._with_test_types(row)) if row else None

    @staticmethod
    def _serialize_test_types(values):
        supplied = values.get("test_types")
        if not supplied:
            supplied = str(values.get("test_type") or "").split(",")
        ordered = []
        for value in supplied:
            cleaned = str(value).strip()
            if cleaned and cleaned not in ordered:
                ordered.append(cleaned)
        return ", ".join(ordered)

    @staticmethod
    def _with_test_types(row):
        item = dict(row)
        item["test_types"] = [value.strip() for value in str(item.get("test_type") or "").split(",") if value.strip()]
        try:
            item["source_knowledge_ids"] = json.loads(item.get("source_knowledge_ids") or "[]")
        except (TypeError, ValueError):
            item["source_knowledge_ids"] = []
        return item

    def _with_sources(self, item):
        conn = self.db.get_connection(); conn.row_factory = self._dict_factory
        item["sources"] = conn.execute(
            "SELECT knowledge_source_id AS source_id,file_name,source_type FROM test_case_sources WHERE test_case_id=? ORDER BY knowledge_source_id",
            (item["id"],),
        ).fetchall()
        conn.close(); return item

    @staticmethod
    def _execution_type(values):
        value = str(values.get("execution_type") or "Manual").strip()
        return value if value in {"Manual", "Automatable"} else "Manual"

    def _execution_tool(self, values, cursor=None):
        if self._execution_type(values) == "Manual":
            return ""
        value = str(values.get("execution_tool") or "").strip()
        close = False
        if cursor is None:
            conn = self.db.get_connection(); cursor = conn.cursor(); close = True
        rows = cursor.execute(
            "SELECT label,automation_type FROM qa_execution_tools WHERE enabled=1"
        ).fetchall()
        if close: conn.close()
        aliases = {}
        for row in rows:
            label, automation_type = (row.get("label"), row.get("automation_type")) if isinstance(row, dict) else row
            aliases[label] = label; aliases[automation_type] = label
        return aliases.get(value, "")

    def execution_tool_for_automation_type(self, automation_type):
        conn = self.db.get_connection()
        row = conn.execute(
            "SELECT label FROM qa_execution_tools WHERE enabled=1 AND automation_type=?",
            (automation_type,),
        ).fetchone()
        conn.close()
        return row[0] if row else ""


    def _update_field(self, test_case_id, field, value):

        conn = self.db.get_connection()

        cursor = conn.cursor()

        now = datetime.now().isoformat()

        cursor.execute(
            f"UPDATE test_cases SET {field}=?, modified_date=? WHERE id=?",
            (value, now, test_case_id)
        )

        conn.commit()

        conn.close()
