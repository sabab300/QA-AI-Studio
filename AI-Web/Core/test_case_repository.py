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
import sys
import time
from pathlib import Path

from Database.db_manager import DatabaseManager
from Core.logger import Logger
from Core.test_case_workbook import EXECUTION_VALUES, normalize_execution, normalize_test_case

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
from Shared.central_repository import CentralRepository
from Shared.document_hierarchy import DocumentHierarchy


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
            "ALTER TABLE test_cases ADD COLUMN uploaded_script TEXT",
            "ALTER TABLE test_cases ADD COLUMN active_script_source "
            "TEXT DEFAULT 'AUTO'",
            "ALTER TABLE test_cases ADD COLUMN execution TEXT DEFAULT 'Manual'",
            "ALTER TABLE test_cases ADD COLUMN source_knowledge_ids TEXT DEFAULT '[]'",
            "ALTER TABLE test_cases ADD COLUMN test_case_document_name TEXT DEFAULT ''",
            "ALTER TABLE test_cases ADD COLUMN tc_id TEXT",
            "ALTER TABLE test_cases ADD COLUMN review_state TEXT DEFAULT 'Reviewed/Saved'",
            "ALTER TABLE test_cases ADD COLUMN artifact_path TEXT DEFAULT ''",
            "ALTER TABLE test_cases ADD COLUMN document_type TEXT DEFAULT ''",
            "ALTER TABLE test_cases ADD COLUMN source_type TEXT DEFAULT ''",
            "ALTER TABLE test_cases ADD COLUMN legacy_origin TEXT",
            "ALTER TABLE test_cases ADD COLUMN reviewed_workbook_path TEXT DEFAULT ''",
            "ALTER TABLE test_cases ADD COLUMN auto_script_validated_hash TEXT DEFAULT ''",
            "ALTER TABLE test_cases ADD COLUMN manual_script_validated_hash TEXT DEFAULT ''",
            "ALTER TABLE test_cases ADD COLUMN uploaded_script_validated_hash TEXT DEFAULT ''",
            "ALTER TABLE test_cases ADD COLUMN sql_script_path TEXT DEFAULT ''",
            "ALTER TABLE test_cases ADD COLUMN test_case_version_id INTEGER",
            "ALTER TABLE test_cases ADD COLUMN actual_result TEXT DEFAULT ''",
            "ALTER TABLE test_cases ADD COLUMN bug_id TEXT DEFAULT ''",
            "ALTER TABLE test_cases ADD COLUMN comments TEXT DEFAULT ''",
            # API Automation endpoint binding — persists which real,
            # imported API Collection endpoint (api_endpoints.id) this
            # Test Case is bound to for execution, so "never silently
            # guess" (QA-AI-STUDIO-REMAINING-MODULES-END-TO-END
            # requirement A) only has to be resolved once per Test
            # Case rather than re-prompted on every single Execute.
            # NULL means "not yet bound". See set_bound_api_endpoint()
            # / automation_web_repository.py's execute_api().
            "ALTER TABLE test_cases ADD COLUMN bound_api_endpoint_id INTEGER",
            # Explicit "expected HTTP status" assertion for API
            # Automation — lets a deliberate negative test (e.g.
            # expect 400) Pass, and an unexpected 200 Fail, instead of
            # always judging Pass/Fail off the bound endpoint's own
            # recorded (happy-path) example_response_status. NULL
            # means "no explicit assertion — fall back to the
            # endpoint's own example status". See
            # set_api_expected_status_code() / ApiAutomationRunner.send().
            "ALTER TABLE test_cases ADD COLUMN api_expected_status_code INTEGER",
            # Structured, per-Test-Case API request definition (section
            # F, QA-AI-STUDIO-API-AUTOMATION-END-TO-END-DEVICE-
            # FINALIZATION) -- query/path param overrides, header
            # overrides, an auth override, a body override, the fuller
            # assertion list (beyond the single api_expected_status_code
            # above), and response-value extraction rules -- stored as
            # one JSON blob. '' means "not yet configured", which falls
            # back to the bound endpoint's own imported values exactly
            # as before this column existed. See set_api_request_config()
            # / Core/automation_web_repository.py's get/set_api_request_
            # config() (masked-on-read for auth secrets) and
            # Core/api_automation_runner.py's build_request()/
            # evaluate_assertions()/extract_variables().
            "ALTER TABLE test_cases ADD COLUMN api_request_config_json TEXT DEFAULT ''",
        ):

            try:

                cursor.execute(migration)

            except Exception:

                pass

        # Older databases have a table-level UNIQUE(domain, module,
        # knowledge_name, tc_number) constraint.  A named-index drop cannot
        # remove the SQLite auto-index backing that constraint, and it makes a
        # legitimate TC001 in V2 conflict with TC001 in V1.  Rebuild only that
        # table, in one transaction, preserving every column and primary key.
        self._migrate_versioned_test_case_identity(conn)
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_test_cases_scope "
            "ON test_cases(domain, module, knowledge_name)"
        )

        # The sole compatibility boundary for databases created before the
        # canonical execution column. All application code below reads/writes
        # only `execution`; legacy columns are inspected only here.
        if "execution" not in existing_columns:
            legacy = {row[1] for row in cursor.execute("PRAGMA table_info(test_cases)")}
            if {"execution_type", "execution_tool"}.issubset(legacy):
                cursor.execute(
                    """UPDATE test_cases SET execution = CASE
                           WHEN execution_tool IN ('Playwright') THEN 'Playwright'
                           WHEN execution_tool IN ('API','API Automation') THEN 'API'
                           WHEN execution_tool IN ('SQL','SQL Automation') THEN 'SQL'
                           WHEN execution_type IN ('Manual','Manual Execution') THEN 'Manual'
                           ELSE NULL END"""
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
        # Versioned rows are unique inside their immutable Test Cases version.
        # Legacy rows have no version identity, so retain their former logical
        # scope rule explicitly as a compatibility constraint.
        cursor.execute("DROP INDEX IF EXISTS uq_test_cases_tc_id")
        cursor.execute("CREATE UNIQUE INDEX IF NOT EXISTS uq_test_cases_version_number ON test_cases(test_case_version_id,tc_number) WHERE test_case_version_id IS NOT NULL")
        cursor.execute("CREATE UNIQUE INDEX IF NOT EXISTS uq_test_cases_legacy_scope_number ON test_cases(domain,module,knowledge_name,tc_number) WHERE test_case_version_id IS NULL")
        cursor.execute("CREATE UNIQUE INDEX IF NOT EXISTS uq_test_cases_version_tc_id ON test_cases(test_case_version_id,tc_id) WHERE tc_id IS NOT NULL AND test_case_version_id IS NOT NULL")
        cursor.execute("CREATE UNIQUE INDEX IF NOT EXISTS uq_test_cases_legacy_tc_id ON test_cases(tc_id) WHERE tc_id IS NOT NULL AND test_case_version_id IS NULL")
        cursor.execute("CREATE UNIQUE INDEX IF NOT EXISTS uq_test_cases_legacy_origin ON test_cases(legacy_origin) WHERE legacy_origin IS NOT NULL")
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS test_case_versions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_knowledge_id INTEGER NOT NULL,
                source_file_id TEXT NOT NULL,
                version_number INTEGER NOT NULL,
                folder_path TEXT NOT NULL,
                workbook_path TEXT NOT NULL,
                created_date TEXT NOT NULL,
                UNIQUE(source_knowledge_id, version_number),
                FOREIGN KEY(source_knowledge_id) REFERENCES knowledge_items(id)
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS test_case_files (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                test_case_version_id INTEGER NOT NULL,
                file_name TEXT NOT NULL,
                workbook_path TEXT NOT NULL,
                sha256 TEXT NOT NULL,
                created_date TEXT NOT NULL,
                UNIQUE(test_case_version_id, file_name),
                FOREIGN KEY(test_case_version_id) REFERENCES test_case_versions(id) ON DELETE CASCADE
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS playwright_automation_assets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                stable_scope_key TEXT NOT NULL,
                source_knowledge_id INTEGER,
                tc_id TEXT NOT NULL,
                automation_script TEXT,
                uploaded_script TEXT,
                recorded_script TEXT,
                active_script_source TEXT NOT NULL DEFAULT 'AUTO',
                auto_script_validated_hash TEXT NOT NULL DEFAULT '',
                uploaded_script_validated_hash TEXT NOT NULL DEFAULT '',
                manual_script_validated_hash TEXT NOT NULL DEFAULT '',
                lifecycle_status TEXT NOT NULL DEFAULT 'Draft',
                modified_date TEXT NOT NULL,
                UNIQUE(stable_scope_key, tc_id)
            )
        """)
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_playwright_assets_source_tc "
            "ON playwright_automation_assets(source_knowledge_id,tc_id)"
        )
        for migration in (
            "ALTER TABLE playwright_automation_assets ADD COLUMN uploaded_script TEXT",
            "ALTER TABLE playwright_automation_assets ADD COLUMN uploaded_script_validated_hash TEXT NOT NULL DEFAULT ''",
        ):
            try:
                cursor.execute(migration)
            except sqlite3.OperationalError:
                pass
        # A short-lived development backfill admitted any non-empty
        # automation_script, including API/SQL envelopes. Keep this migration
        # self-correcting and strictly Playwright-only.
        cursor.execute(
            """DELETE FROM playwright_automation_assets AS asset
               WHERE NOT EXISTS (
                 SELECT 1 FROM test_cases tc
                 JOIN test_case_versions v ON v.id=tc.test_case_version_id
                 WHERE v.source_knowledge_id=asset.source_knowledge_id
                   AND tc.tc_id=asset.tc_id
                   AND (tc.automation_type='Playwright' OR tc.recorded_script IS NOT NULL)
               )"""
        )
        self._backfill_playwright_assets(cursor)
        self._cleanup_redundant_uploaded_scripts(cursor)
        self._migrate_legacy_desktop_rows(cursor)
        self._backfill_canonical_ids(cursor)
        self._backfill_source_relations(cursor)

        DocumentHierarchy(conn).ensure_schema()
        conn.commit()

        conn.close()

    @staticmethod
    def _playwright_identity(cursor, row):
        """Return the smallest identity that survives TC workbook revisions.

        tc_id values are hierarchy-derived for generated cases, but real legacy/
        imported data contains the same tc_id under unrelated source documents.
        The immutable Knowledge source id therefore scopes the exact stable id.
        """
        version_id = row.get("test_case_version_id") if isinstance(row, dict) else None
        if version_id:
            version = cursor.execute(
                "SELECT source_knowledge_id FROM test_case_versions WHERE id=?",
                (int(version_id),),
            ).fetchone()
            if version:
                source_id = version.get("source_knowledge_id") if isinstance(version, dict) else version[0]
                return f"source:{int(source_id)}", int(source_id), str(row.get("tc_id") or "").strip()
        try:
            source_ids = json.loads(row.get("source_knowledge_ids") or "[]")
        except (TypeError, ValueError):
            source_ids = []
        if len(source_ids) == 1 and row.get("tc_id"):
            source_id = int(source_ids[0])
            return f"source:{source_id}", source_id, str(row["tc_id"]).strip()
        return None

    def _backfill_playwright_assets(self, cursor):
        cursor.connection.row_factory = self._dict_factory
        query_cursor = cursor.connection.cursor()
        rows = query_cursor.execute(
            """SELECT * FROM test_cases
               WHERE tc_id IS NOT NULL AND tc_id<>''
                 AND (automation_type='Playwright' OR recorded_script IS NOT NULL)
               ORDER BY COALESCE(modified_date,created_date,'') DESC,id DESC"""
        ).fetchall()
        for row in rows:
            identity = self._playwright_identity(query_cursor, row)
            if not identity or not (row.get("automation_script") or row.get("recorded_script")):
                continue
            scope_key, source_id, tc_id = identity
            query_cursor.execute(
                """INSERT OR IGNORE INTO playwright_automation_assets
                   (stable_scope_key,source_knowledge_id,tc_id,automation_script,
                    uploaded_script,recorded_script,active_script_source,auto_script_validated_hash,
                    uploaded_script_validated_hash,manual_script_validated_hash,lifecycle_status,modified_date)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                (scope_key, source_id, tc_id, row.get("automation_script"), None,
                 row.get("recorded_script"), row.get("active_script_source") or "AUTO",
                 row.get("auto_script_validated_hash") or "", "",
                 row.get("manual_script_validated_hash") or "",
                 row.get("status") if row.get("status") in {"Draft", "Automated"} else "Draft",
                 row.get("modified_date") or row.get("created_date") or datetime.now().isoformat()),
            )

    def _cleanup_redundant_uploaded_scripts(self, cursor):
        """Remove only proven JSON-derived uploaded `.py` duplicates.

        Generated `{tc_id}.py` and recorded `{tc_id}.recorded.py` files are
        deliberately outside this migration. A candidate is removed only when
        its UTF-8 content exactly matches the stable uploaded-script value and
        the corresponding managed JSON artifact exists.
        """
        cursor.connection.row_factory = self._dict_factory
        assets = cursor.connection.execute(
            "SELECT source_knowledge_id,tc_id,uploaded_script "
            "FROM playwright_automation_assets WHERE uploaded_script IS NOT NULL"
        ).fetchall()
        central = CentralRepository()
        for asset in assets:
            contexts = cursor.connection.execute(
                """SELECT DISTINCT v.version_number,v.folder_path,v.source_file_id
                   FROM test_cases tc JOIN test_case_versions v
                     ON v.id=tc.test_case_version_id
                   WHERE v.source_knowledge_id=? AND tc.tc_id=?""",
                (asset["source_knowledge_id"], asset["tc_id"]),
            ).fetchall()
            stem = central.sanitize_segment(asset["tc_id"], "Test Case ID")
            for context in contexts:
                folder = central.build_automation_path(
                    context["folder_path"], "Playwright",
                    context["version_number"], context["source_file_id"],
                )
                json_path = folder / f"{stem}.uploaded.json"
                duplicate = folder / f"{stem}.uploaded.py"
                if not (json_path.is_file() and duplicate.is_file()):
                    continue
                try:
                    duplicate_text = duplicate.read_text(encoding="utf-8")
                except (OSError, UnicodeDecodeError):
                    continue
                if self._canonical_script_text(duplicate_text) == self._canonical_script_text(
                    asset["uploaded_script"]
                ):
                    duplicate.unlink()

    @staticmethod
    def _migrate_versioned_test_case_identity(conn):
        row = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='test_cases'"
        ).fetchone()
        create_sql = (row[0] if row else "") or ""
        obsolete = re.compile(
            r",?\s*UNIQUE\s*\(\s*domain\s*,\s*module\s*,\s*knowledge_name\s*,\s*tc_number\s*\)\s*,?",
            re.IGNORECASE,
        )
        if not obsolete.search(create_sql):
            return

        replacement_sql = obsolete.sub("", create_sql)
        replacement_sql = re.sub(r",\s*\)", "\n            )", replacement_sql)
        replacement_sql = re.sub(
            r"^(\s*CREATE\s+TABLE\s+)(?:IF\s+NOT\s+EXISTS\s+)?(?:\"test_cases\"|`test_cases`|\[test_cases\]|test_cases)",
            r"\1test_cases_versioned_identity",
            replacement_sql,
            count=1,
            flags=re.IGNORECASE,
        )
        columns = [row[1] for row in conn.execute("PRAGMA table_info(test_cases)")]
        quoted = ",".join('"' + name.replace('"', '""') + '"' for name in columns)
        before = conn.execute("SELECT COUNT(*) FROM test_cases").fetchone()[0]
        foreign_keys = conn.execute("PRAGMA foreign_keys").fetchone()[0]
        conn.commit()
        conn.execute("PRAGMA foreign_keys=OFF")
        try:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(replacement_sql)
            conn.execute(
                f"INSERT INTO test_cases_versioned_identity ({quoted}) SELECT {quoted} FROM test_cases"
            )
            after = conn.execute("SELECT COUNT(*) FROM test_cases_versioned_identity").fetchone()[0]
            if before != after:
                raise RuntimeError(
                    f"Test Case identity migration row-count mismatch: {before} != {after}."
                )
            conn.execute("DROP TABLE test_cases")
            conn.execute("ALTER TABLE test_cases_versioned_identity RENAME TO test_cases")
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.execute(f"PRAGMA foreign_keys={'ON' if foreign_keys else 'OFF'}")

    def list_execution_values(self):
        return [
            {"code": "manual", "label": "Manual", "automation_type": "None"},
            {"code": "playwright", "label": "Playwright", "automation_type": "Playwright"},
            {"code": "api", "label": "API", "automation_type": "API"},
            {"code": "sql", "label": "SQL", "automation_type": "SQL"},
        ]

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
        force_direct_insert=False,
        document_version_id=None,

    ):

        if not rows:

            return []

        # Desktop delegates to this canonical repository. Resolve its complete
        # typed context to real sources rather than reverting to unowned rows.
        if not source_knowledge_ids and document_type and version:
            scoped=self.db.get_connection()
            try:
                source_knowledge_ids=[row[0] for row in scoped.execute('SELECT id FROM knowledge_items WHERE domain=? AND module=? AND knowledge_name=? AND document_type=? AND version=? ORDER BY id',
                    (domain,module,knowledge_name,document_type,version)).fetchall()]
            finally:scoped.close()
            if not source_knowledge_ids:raise ValueError('No Knowledge source exists in the selected Document Type/version.')

        scope = {
            "domain": domain, "module": module, "knowledge_name": knowledge_name,
            "version": version or "", "document_type": document_type or "",
            "source_type": source_type or "", "source_knowledge_ids": source_knowledge_ids or [],
            "test_case_document_name": test_case_document_name or "",
            "document_version_id":document_version_id,
        }
        # QA-AI-STUDIO-API-COLLECTION-KNOWLEDGE-QA-ENGINEERING-API-AUTOMATION-FINAL-FIX
        # item 6/7: review_save() requires a real knowledge_versions
        # row for the selected source(s) and writes a reviewed-file
        # artifact -- correct for the human QA Engineering Draft ->
        # Review -> Save workflow, but an API Collection's placeholder
        # knowledge_items row (created by
        # ApiCollectionRepository._get_or_create_linked_knowledge_item)
        # has no such knowledge_versions row. force_direct_insert lets
        # a caller that already knows exactly what it wants persisted
        # (generate_automation_from_collection()) use the same simple,
        # direct INSERT below even while also supplying
        # source_knowledge_ids/test_case_document_name/document_type,
        # instead of being silently routed into review_save() and
        # failing (or being rejected) there. Every OTHER existing
        # caller is unaffected: this only changes behavior when the
        # new flag is explicitly passed True.
        if source_knowledge_ids and not force_direct_insert:
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

        direct_sources = []
        if source_knowledge_ids:
            marks = ",".join("?" for _ in source_knowledge_ids)
            cursor.execute(
                f"SELECT * FROM knowledge_items WHERE id IN ({marks}) ORDER BY id",
                [int(value) for value in source_knowledge_ids],
            )
            columns = [item[0] for item in cursor.description]
            direct_sources = [
                item if isinstance(item, dict) else dict(zip(columns, item))
                for item in cursor.fetchall()
            ]

        document_version_id = None
        if source_knowledge_ids:
            try:
                if {item['id'] for item in direct_sources} != {int(value) for value in source_knowledge_ids}:
                    raise ValueError('Every selected Knowledge source must exist.')
                self._validate_scope_sources(scope, direct_sources)
                resolved_owner = self._bind_source_owner(cursor, direct_sources)
                if document_version_id is not None and int(document_version_id)!=resolved_owner:
                    raise ValueError('Selected document version does not match the source owner.')
                document_version_id=resolved_owner
            except Exception:
                conn.rollback(); conn.close(); raise

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
                        execution, source_knowledge_ids,
                        test_case_document_name, document_type, source_type,
                        created_date, modified_date
                    )
                    VALUES
                    (?,?,?,?,?, ?,?, ?,?,?,?, ?,?,?, 'Manual','None','Not Run', ?,?,?,?,?, ?,?)
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
                        normalize_execution(row),
                        json.dumps(source_knowledge_ids or []),
                        test_case_document_name or "",
                        document_type or "",
                        source_type or "",
                        now,
                        now,
                    )
                )

                test_case_id = cursor.lastrowid
                if document_version_id is not None:
                    cursor.execute('UPDATE test_cases SET document_version_id=? WHERE id=?', (document_version_id,test_case_id))
                    self._replace_sources(cursor,test_case_id,direct_sources)
                saved_ids.append(test_case_id)

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
            document_version_id = self._bind_source_owner(cursor, sources)
            if scope.get('document_version_id') is not None and int(scope['document_version_id'])!=document_version_id:
                raise ValueError('Selected document version does not match the source owner.')
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
                         status,automation_type,last_result,execution,
                         source_knowledge_ids,test_case_document_name,document_type,source_type,
                         review_state,created_date,modified_date)
                        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'Manual','None','Not Run',?,?,?,?,?,
                               'Reviewed/Saved',?,?)
                    """, (tc_id,tc_id,scope["domain"],scope["module"],scope["knowledge_name"],scope.get("version") or "",
                            domain_id,module_id,values.get("scenario", ""),values.get("importance", ""),
                            self._serialize_test_types(values),values.get("test_case", ""),values.get("pre_conditions", ""),
                            values.get("steps", ""),values.get("expected_result", ""),normalize_execution(values),
                            json.dumps(sorted(requested)),
                            scope.get("test_case_document_name") or "",scope.get("document_type") or sources[0].get("document_type") or "",
                            self._combined_source_type(sources),now,now))
                    test_case_id = cursor.lastrowid; inserted.append(test_case_id)
                    cursor.execute('UPDATE test_cases SET document_version_id=? WHERE id=?', (document_version_id,test_case_id))
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
                        steps=?,expected_result=?,execution=?,review_state='Reviewed/Saved',modified_date=? WHERE id=?""",
                        (values.get("scenario", ""),values.get("importance", ""),self._serialize_test_types(values),
                         values.get("test_case", ""),values.get("pre_conditions", ""),values.get("steps", ""),
                         values.get("expected_result", ""),normalize_execution(values),now,test_case_id))
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
        identities={tuple(str(source.get(key) or '') for key in ('domain','module','knowledge_name','document_type','version')) for source in sources}
        if len(identities)>1:
            raise ValueError('Selected sources must belong to one Document Type and Version.')
        for source in sources:
            for key in ("domain", "module", "knowledge_name", "version", "document_type"):
                expected = str(scope.get(key) or "")
                actual = str(source.get(key) or "")
                if expected and actual != expected:
                    raise ValueError(f"Selected source {source['id']} does not match {key}.")

    @staticmethod
    def _bind_source_owner(cursor, sources):
        hierarchy=DocumentHierarchy(cursor.connection)
        owners={hierarchy.resolve_source(source['id'])['document_version_id'] for source in sources}
        if len(owners)!=1:
            raise ValueError('Selected sources do not share one canonical document version.')
        return next(iter(owners))

    def _validate_case(self, values, cursor):
        values = normalize_test_case(values)
        if not str(values.get("importance") or "").strip(): raise ValueError("Importance is required.")
        if values.get("execution") not in EXECUTION_VALUES: raise ValueError("Execution is required and must be Manual, Playwright, API, or SQL.")
        if not str(values.get("scenario") or "").strip(): raise ValueError("Scenario is required.")
        if not str(values.get("pre_conditions") or "").strip(): raise ValueError("Preconditions are required.")
        if not str(values.get("test_case") or "").strip(): raise ValueError("Test Case is required.")
        if not str(values.get("steps") or "").strip(): raise ValueError("Test Steps are required.")
        if not str(values.get("expected_result") or "").strip(): raise ValueError("Expected Result is required.")
        if not self._serialize_test_types(values): raise ValueError("At least one Test Type is required.")

    def set_reviewed_workbook(self, test_case_ids, relative_path, test_case_version_id=None):
        """Associate the reviewed workbook with exactly its saved batch."""
        ids = [int(value) for value in test_case_ids]
        if not ids:
            return
        conn = self.db.get_connection(); conn.row_factory = self._dict_factory
        file_changes = []
        try:
            conn.execute("BEGIN IMMEDIATE")
            marks = ",".join("?" for _ in ids)
            if test_case_version_id:
                conn.execute(
                    f"UPDATE test_cases SET reviewed_workbook_path=?,test_case_version_id=? WHERE id IN ({marks})",
                    [str(relative_path), int(test_case_version_id), *ids],
                )
            else:
                conn.execute(
                    f"UPDATE test_cases SET reviewed_workbook_path=? WHERE id IN ({marks})",
                    [str(relative_path), *ids],
                )
            # A newly-associated TC revision immediately inherits and
            # materializes any stable Playwright asset for the exact source +
            # tc_id. Missing/new ids remain untouched.
            for test_case_id in ids:
                row = conn.execute("SELECT * FROM test_cases WHERE id=?", (test_case_id,)).fetchone()
                asset = self._stable_playwright_asset(conn, row) if row else None
                if not asset:
                    continue
                if asset.get("automation_script"):
                    self._update_stable_playwright_script(
                        test_case_id, asset["automation_script"], conn=conn,
                        all_changes=file_changes,
                    )
                if asset.get("uploaded_script"):
                    self._update_stable_playwright_script(
                        test_case_id, asset["uploaded_script"], source="UPLOADED",
                        conn=conn, all_changes=file_changes,
                    )
                if asset.get("recorded_script"):
                    self._update_stable_playwright_script(
                        test_case_id, asset["recorded_script"], source="MANUAL",
                        conn=conn, all_changes=file_changes,
                    )
            conn.commit()
        except Exception:
            conn.rollback()
            self._restore_playwright_files(file_changes)
            raise
        finally:
            conn.close()

    def prepare_reviewed_workbook(self, source_knowledge_id, requested_name):
        """Return one stable versioned workbook path for a source context."""
        source = self._source_records([source_knowledge_id])
        if not source:
            raise ValueError("Selected Source File no longer exists.")
        source = source[0]
        source_file_id = str(source.get("source_file_id") or "").strip()
        if not source_file_id:
            source_file_id = f"LEGACY-{source['id']}"
        central = CentralRepository()
        repository_path = central.resolve_managed(
            source.get("repository_path"), source.get("repository_relative_path") or ""
        )
        source_folder = repository_path.parent
        conn = self.db.get_connection(); conn.row_factory = self._dict_factory
        try:
            existing = conn.execute(
                "SELECT * FROM test_case_versions WHERE source_knowledge_id=? ORDER BY version_number DESC LIMIT 1",
                (int(source_knowledge_id),),
            ).fetchone()
            if existing and Path(existing["workbook_path"]).is_file():
                existing["_created"] = False
                return existing
            version = central.get_next_test_case_version(source_folder)
            folder = central.ensure_path(
                central.build_test_case_version_path(source_folder, version, source_file_id)
            )
            name = CentralRepository.sanitize_segment(
                Path(requested_name or f"testCases_v{version}.xlsx").stem,
                "test case workbook name",
            ) + ".xlsx"
            workbook = folder / name
            now = datetime.now().isoformat()
            conn.execute(
                "INSERT INTO test_case_versions(source_knowledge_id,source_file_id,version_number,folder_path,workbook_path,created_date) VALUES(?,?,?,?,?,?)",
                (int(source_knowledge_id), source_file_id, version, str(folder), str(workbook), now),
            )
            conn.commit()
            created = conn.execute("SELECT * FROM test_case_versions WHERE id=last_insert_rowid()").fetchone()
            created["_created"] = True
            return created
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def list_test_case_versions(self, source_knowledge_id):
        conn = self.db.get_connection(); conn.row_factory = self._dict_factory
        rows = conn.execute(
            "SELECT * FROM test_case_versions WHERE source_knowledge_id=? ORDER BY version_number",
            (int(source_knowledge_id),),
        ).fetchall()
        conn.close()
        return rows

    def discard_empty_test_case_version(self, version_id):
        conn = self.db.get_connection(); conn.row_factory = self._dict_factory
        row = conn.execute("SELECT * FROM test_case_versions WHERE id=?", (int(version_id),)).fetchone()
        if row and not Path(row["workbook_path"]).is_file():
            conn.execute("DELETE FROM test_case_versions WHERE id=?", (int(version_id),))
            conn.commit()
            try:
                Path(row["folder_path"]).rmdir()
            except OSError:
                pass
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
        # ROOT CAUSE 4 (confirmed 2026-09-09 by reproducing an actual
        # POST /api/automation/test-cases/import against the real
        # downloaded sample template): test_case_id_sequences is a
        # SEPARATELY tracked counter, not derived from test_cases
        # itself -- so any tc_id that ever entered test_cases WITHOUT
        # going through this method (a legacy/Desktop migration row,
        # a restored/partial backup, a manual data fix) leaves the
        # counter behind reality. The next legitimate call here then
        # proposes a tc_id that's already taken, and INSERT raises a
        # raw sqlite3 "UNIQUE constraint failed: test_cases.tc_id" --
        # which review_save()'s caller surfaces verbatim as the
        # Import 400 body, not a useful, row-specific reason. Since
        # this method is the ONLY place a tc_id for a given prefix is
        # minted, self-heal here: skip forward past any value that's
        # already actually in use before handing one out, and persist
        # the counter one past whatever was actually allocated -- so
        # a drifted counter recovers silently on the very next call
        # instead of failing the whole import/generate.
        while cursor.execute(
            "SELECT 1 FROM test_cases WHERE tc_id=? LIMIT 1", (f"{prefix}_{sequence:04d}",)
        ).fetchone():
            sequence += 1
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

        return [self._with_sources(self._with_test_types(self._with_stable_playwright(row))) for row in rows]

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
        status=None, automation_type=None, execution=None,
        document_type=None, test_case_version_id=None, reviewed_workbook_path=None, q=None,
        limit=200, offset=0,
    ):
        """
        Returns {"test_cases": [...], "total": N} — total is the
        count BEFORE limit/offset, for real pagination.

        test_case_version_id — Rule 4 (QA-AI-STUDIO-FINAL-ARCHITECTURE-
        UI-AUTOMATION-COMPLETION-V4): the exact hierarchy's "Source
        Files" level is a TC-* folder (a real test_case_versions row),
        and every test_cases row generated/imported under it already
        carries this FK (see set_reviewed_workbook()/QaEngineeringVersion
        Service.load_version(), which both key off it). Filtering here
        lets callers resolve the modern TC-folder selection directly at
        the SQL level instead of only through the legacy source_knowledge
        _ids/test_case_document grouping.
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

        if test_case_version_id:
            conditions.append("test_case_version_id=?")
            params.append(test_case_version_id)

        if reviewed_workbook_path:
            conditions.append("reviewed_workbook_path=?")
            params.append(str(reviewed_workbook_path))

        if status:

            conditions.append("status=?")

            params.append(status)

        if automation_type:

            conditions.append("automation_type=?")

            params.append(automation_type)

        if execution:
            conditions.append("execution=?")
            params.append(normalize_execution({"execution": execution}))

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

        return {"test_cases": [self._with_sources(self._with_test_types(self._with_stable_playwright(row))) for row in rows], "total": total}

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
            "pre_conditions", "steps", "expected_result", "actual_result", "status",
            "bug_id", "comments", "execution",
            "execution", "test_case_document_name",
        }
        updates = {key: values[key] for key in allowed if key in values}
        if "test_types" in values:
            updates["test_type"] = self._serialize_test_types(values)
        if "execution" in updates:
            updates["execution"] = normalize_execution(values)
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


    @staticmethod
    def _canonical_script_text(value):
        """Compare editor/persisted script text without false lifecycle changes.

        Browsers normalise textarea line endings to ``\n`` while scripts
        produced on Windows are commonly persisted with ``\r\n``.  A
        trailing final newline is also editor-formatting, not an executable
        change.  Treat those representation-only differences as equal so a
        no-op Save cannot demote an already Active script back to Draft.
        """
        text = (value or "").replace("\r\n", "\n").replace("\r", "\n")
        return text.rstrip("\n")

    def _stable_playwright_asset(self, conn, row):
        identity = self._playwright_identity(conn.cursor(), row)
        if not identity:
            return None
        scope_key, _, tc_id = identity
        conn.row_factory = self._dict_factory
        return conn.execute(
            "SELECT * FROM playwright_automation_assets "
            "WHERE stable_scope_key=? AND tc_id=?",
            (scope_key, tc_id),
        ).fetchone()

    def _with_stable_playwright(self, row):
        if not row:
            return row
        item = dict(row)
        # Stable Playwright slots remain available if the case later returns
        # to Playwright, but must never overwrite an explicitly selected SQL
        # or API asset/lifecycle in this canonical getter.
        if item.get('execution') in ('SQL','API') or item.get('automation_type') in ('SQL','API'):
            return item
        conn = self.db.get_connection(); conn.row_factory = self._dict_factory
        try:
            asset = self._stable_playwright_asset(conn, item)
        finally:
            conn.close()
        if not asset:
            return item
        for field in (
            "automation_script", "uploaded_script", "recorded_script", "active_script_source",
            "auto_script_validated_hash", "uploaded_script_validated_hash",
            "manual_script_validated_hash",
        ):
            item[field] = asset.get(field)
        if item.get("automation_script") or item.get("uploaded_script") or item.get("recorded_script"):
            item["automation_type"] = "Playwright"
            item["execution"] = "Playwright"
        if asset.get("lifecycle_status") in {"Draft", "Automated"}:
            item["status"] = asset["lifecycle_status"]
        return item

    @staticmethod
    def _restore_playwright_files(changes):
        for path, previous, folder_created in reversed(changes):
            try:
                if previous is None:
                    if path.is_file():
                        path.unlink()
                else:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(previous)
                if folder_created and path.parent.is_dir() and not any(path.parent.iterdir()):
                    path.parent.rmdir()
            except OSError:
                pass

    def _materialize_playwright_script(self, conn, source_id, tc_id, script, source="AUTO"):
        """Write one executable script into every existing matching TC folder.

        SQLite remains authoritative. Files are synchronized copies for the
        Central Repository hierarchy, staged and replace-written so a failed
        write can be restored before the database transaction is committed.
        """
        contexts = conn.execute(
            """SELECT DISTINCT v.version_number,v.folder_path,v.source_file_id
               FROM test_cases tc
               JOIN test_case_versions v ON v.id=tc.test_case_version_id
               WHERE v.source_knowledge_id=? AND tc.tc_id=?""",
            (int(source_id), tc_id),
        ).fetchall()
        central = CentralRepository()
        filename_stem = central.sanitize_segment(tc_id, "Test Case ID")
        filename = filename_stem
        if source == "MANUAL":
            filename += ".recorded"
        filename += ".py"
        changes = []
        try:
            for raw in contexts:
                version_number, folder_path, source_file_id = (
                    (raw.get("version_number"), raw.get("folder_path"), raw.get("source_file_id"))
                    if isinstance(raw, dict) else raw
                )
                automation_folder = central.build_automation_path(
                    folder_path, "Playwright", version_number, source_file_id
                )
                folder_created = not automation_folder.exists()
                automation_folder.mkdir(parents=True, exist_ok=True)
                if source == "UPLOADED":
                    mapping_target = automation_folder / f"{filename_stem}.uploaded.json"
                    mapping_text = json.dumps(
                        [{"Test Case ID": tc_id, "Script": script or ""}],
                        ensure_ascii=False, indent=2,
                    ) + "\n"
                    targets = [(mapping_target, mapping_text)]
                else:
                    targets = [(automation_folder / filename, script or "")]
                for target_index, (write_target, content) in enumerate(targets):
                    previous = write_target.read_bytes() if write_target.is_file() else None
                    changes.append((write_target, previous, folder_created and target_index == 0))
                    descriptor, staged_name = tempfile.mkstemp(
                        prefix=f".{write_target.stem}-", suffix=".tmp", dir=automation_folder
                    )
                    try:
                        with open(descriptor, "w", encoding="utf-8", newline="\n", closefd=True) as handle:
                            handle.write(content)
                        staged_path = Path(staged_name)
                        for attempt in range(3):
                            try:
                                staged_path.replace(write_target)
                                break
                            except PermissionError:
                                if attempt == 2:
                                    raise
                                time.sleep(0.05 * (attempt + 1))
                    except Exception:
                        Path(staged_name).unlink(missing_ok=True)
                        raise
                if source == "UPLOADED":
                    duplicate = automation_folder / f"{filename_stem}.uploaded.py"
                    if duplicate.is_file():
                        duplicate_bytes = duplicate.read_bytes()
                        try:
                            duplicate_text = duplicate_bytes.decode("utf-8")
                        except UnicodeDecodeError:
                            duplicate_text = None
                        if (
                            duplicate_text is not None
                            and self._canonical_script_text(duplicate_text)
                            == self._canonical_script_text(script)
                        ):
                            changes.append((duplicate, duplicate_bytes, False))
                            duplicate.unlink()
            return changes
        except Exception:
            self._restore_playwright_files(changes)
            raise

    def _update_stable_playwright_script(
        self, test_case_id, script, source="AUTO", conn=None, all_changes=None,
    ):
        owns_connection = conn is None
        conn = conn or self.db.get_connection(); conn.row_factory = self._dict_factory
        changes = []
        try:
            if owns_connection:
                conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM test_cases WHERE id=?", (test_case_id,)).fetchone()
            if not row:
                raise ValueError(f"Test case {test_case_id} not found.")
            identity = self._playwright_identity(conn.cursor(), row)
            if not identity:
                return None
            scope_key, source_id, tc_id = identity
            current = conn.execute(
                "SELECT * FROM playwright_automation_assets WHERE stable_scope_key=? AND tc_id=?",
                (scope_key, tc_id),
            ).fetchone()
            fields = {
                "AUTO": ("automation_script", "auto_script_validated_hash"),
                "UPLOADED": ("uploaded_script", "uploaded_script_validated_hash"),
                "MANUAL": ("recorded_script", "manual_script_validated_hash"),
            }
            if source not in fields:
                raise ValueError("Playwright script source must be AUTO, UPLOADED, or MANUAL.")
            script_field, _ = fields[source]
            active_source = (current or row).get("active_script_source") or "AUTO"
            previous_script = (current or row).get(script_field)
            changed = self._canonical_script_text(previous_script) != self._canonical_script_text(script)
            lifecycle = (current or {}).get("lifecycle_status") or (
                row.get("status") if row.get("status") in {"Draft", "Automated"} else "Draft"
            )
            if changed and active_source.upper() == source:
                lifecycle = "Draft"
            now = datetime.now().isoformat()
            auto_script = (current or row).get("automation_script")
            uploaded_script = (current or row).get("uploaded_script")
            manual_script = (current or row).get("recorded_script")
            auto_hash = (current or row).get("auto_script_validated_hash") or ""
            uploaded_hash = (current or row).get("uploaded_script_validated_hash") or ""
            manual_hash = (current or row).get("manual_script_validated_hash") or ""
            if source == "MANUAL":
                manual_script = script
                if changed: manual_hash = ""
            elif source == "UPLOADED":
                uploaded_script = script
                if changed: uploaded_hash = ""
            else:
                auto_script = script
                if changed: auto_hash = ""
            changes = self._materialize_playwright_script(
                conn, source_id, tc_id, script, source=source
            )
            if all_changes is not None:
                all_changes.extend(changes)
            conn.execute(
                """INSERT INTO playwright_automation_assets
                   (stable_scope_key,source_knowledge_id,tc_id,automation_script,
                    uploaded_script,recorded_script,active_script_source,auto_script_validated_hash,
                    uploaded_script_validated_hash,manual_script_validated_hash,lifecycle_status,modified_date)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(stable_scope_key,tc_id) DO UPDATE SET
                    automation_script=excluded.automation_script,
                    uploaded_script=excluded.uploaded_script,
                    recorded_script=excluded.recorded_script,
                    active_script_source=excluded.active_script_source,
                    auto_script_validated_hash=excluded.auto_script_validated_hash,
                    uploaded_script_validated_hash=excluded.uploaded_script_validated_hash,
                    manual_script_validated_hash=excluded.manual_script_validated_hash,
                    lifecycle_status=excluded.lifecycle_status,
                    modified_date=excluded.modified_date""",
                (scope_key, source_id, tc_id, auto_script, uploaded_script, manual_script, active_source,
                 auto_hash, uploaded_hash, manual_hash, lifecycle, now),
            )
            rows = conn.execute(
                """SELECT tc.id FROM test_cases tc JOIN test_case_versions v
                   ON v.id=tc.test_case_version_id
                   WHERE v.source_knowledge_id=? AND tc.tc_id=?""",
                (source_id, tc_id),
            ).fetchall()
            ids = [item.get("id") if isinstance(item, dict) else item[0] for item in rows]
            if ids:
                marks = ",".join("?" for _ in ids)
                conn.execute(
                    f"""UPDATE test_cases SET automation_script=?,uploaded_script=?,recorded_script=?,
                         active_script_source=?,auto_script_validated_hash=?,uploaded_script_validated_hash=?,
                         manual_script_validated_hash=?,automation_type='Playwright',
                         execution='Playwright',status=?,modified_date=? WHERE id IN ({marks})""",
                    (auto_script, uploaded_script, manual_script, active_source, auto_hash, uploaded_hash, manual_hash,
                     lifecycle, now, *ids),
                )
            if owns_connection:
                conn.commit()
            return changed
        except Exception:
            if owns_connection:
                conn.rollback()
                self._restore_playwright_files(changes)
            raise
        finally:
            if owns_connection:
                conn.close()

    def update_playwright_scripts_atomically(self, updates):
        """Persist a validated multi-row Playwright mapping as one unit."""
        conn = self.db.get_connection(); conn.row_factory = self._dict_factory
        file_changes = []
        try:
            conn.execute("BEGIN IMMEDIATE")
            for test_case_id, script in updates:
                result = self._update_stable_playwright_script(
                    test_case_id, script, source="UPLOADED", conn=conn,
                    all_changes=file_changes,
                )
                if result is None:
                    raise ValueError(
                        f"Test case {test_case_id} has no stable Knowledge source identity."
                    )
            conn.commit()
        except Exception:
            conn.rollback()
            self._restore_playwright_files(file_changes)
            raise
        finally:
            conn.close()

    @classmethod
    def script_fingerprint(cls, value):
        text = cls._canonical_script_text(value)
        return hashlib.sha256(text.encode("utf-8")).hexdigest() if text else ""

    @staticmethod
    def _api_config_text(
        bound_api_endpoint_id, api_expected_status_code, env_fingerprint="",
        manual_config_text="",
    ):
        """
        Canonical "content" for an API Automation asset's readiness
        fingerprint. Per the required architecture ("Selenium/browser
        script text must NOT control API execution readiness"), what
        must be Validated/Activated for an API test case is the real
        request config — which real, imported endpoint it's bound to,
        and what HTTP status the assertion expects — NOT the
        documentation-only text in automation_script. Reusing the same
        auto_script_validated_hash column/hash mechanism already built
        (see mark_script_validated()/is_script_source_validated()
        below) for this second kind of "content" avoids a parallel,
        duplicate validated/active bookkeeping system.

        `env_fingerprint` (FINAL-END-TO-END-WORKSPACE-V2 section 32):
        an opaque string, computed by the caller (see
        TestCasesWeb._api_env_fingerprint() in
        automation_web_repository.py — this module intentionally
        knows nothing about Test Environment Settings/variable
        resolution itself), folded into the same fingerprint so a
        material environment change invalidates readiness exactly
        like a request-config change already does. Defaults to ""
        so every non-API caller, and any API caller that hasn't been
        updated to compute one, keeps the exact prior fingerprint.

        `manual_config_text` (API-Automation-Enhancement Phase 5 fix):
        the test case's raw saved api_request_config_json, folded in
        so a Manual Source Endpoint test case (bound_api_endpoint_id
        is None/empty by definition) still gets a fingerprint that
        actually changes when its manually-typed Method/URL/Headers/
        Body change — before this, a manual-mode test case's
        fingerprint depended ONLY on bound_api_endpoint_id/expected
        status, so editing its manual URL after a successful Validate
        silently left it looking still-validated. Defaults to "" so
        every existing bound-endpoint caller's fingerprint is
        unchanged (endpoint id + expected status already uniquely
        identify a bound endpoint's config).
        """
        return (
            f"endpoint={bound_api_endpoint_id or ''}"
            f"|expected_status={api_expected_status_code or ''}"
            f"|manual_config={manual_config_text or ''}"
            f"|env={env_fingerprint or ''}"
        )

    def is_script_source_validated(self, test_case, source, env_fingerprint=""):
        source = str(source or "AUTO").upper()
        is_api = str(test_case.get("automation_type") or "").upper() == "API"
        if is_api and source == "AUTO":
            bound_id = test_case.get("bound_api_endpoint_id")
            manual_config_text = test_case.get("api_request_config_json") or ""
            # API-Automation-Enhancement Phase 5 fix: a Manual Source
            # Endpoint test case (no imported Postman endpoint bound at
            # all — see execute_api()'s own manual_url fallback) has no
            # bound_api_endpoint_id, so the old `bool(bound_api_endpoint_id)`
            # gate below made is_script_source_validated() — and
            # therefore the "request_validated" readiness check and the
            # whole Validate -> Save -> Execute lifecycle — permanently
            # False for EVERY manually-configured API test case, no
            # matter how many times Validate was clicked. A manual URL
            # saved in the Request Configuration is just as much real,
            # checkable "content" as a bound endpoint id, so it now
            # counts too.
            has_manual_url = False
            if manual_config_text:
                try:
                    has_manual_url = bool((json.loads(manual_config_text) or {}).get("url"))
                except (TypeError, ValueError):
                    has_manual_url = False
            content = self._api_config_text(
                bound_id,
                test_case.get("api_expected_status_code"),
                env_fingerprint,
                manual_config_text,
            )
            return (
                bool(bound_id or has_manual_url)
                and test_case.get("auto_script_validated_hash")
                == self.script_fingerprint(content)
            )
        script_field = {
            "AUTO": "automation_script", "UPLOADED": "uploaded_script",
            "MANUAL": "recorded_script",
        }.get(source, "automation_script")
        hash_field = {
            "AUTO": "auto_script_validated_hash",
            "UPLOADED": "uploaded_script_validated_hash",
            "MANUAL": "manual_script_validated_hash",
        }.get(source, "auto_script_validated_hash")
        return (
            bool(test_case.get(script_field))
            and test_case.get(hash_field)
            == self.script_fingerprint(test_case.get(script_field))
        )

    def mark_script_validated(
        self, test_case_id, source, keep_active=False, env_fingerprint="",
    ):
        source = str(source or "AUTO").upper()
        if source not in {"AUTO", "UPLOADED", "MANUAL"}:
            raise ValueError("source must be 'AUTO', 'UPLOADED', or 'MANUAL'.")
        conn = self.db.get_connection()
        conn.row_factory = self._dict_factory
        try:
            row = conn.execute(
                "SELECT * FROM test_cases WHERE id=?",
                (test_case_id,),
            ).fetchone()
            if not row:
                raise ValueError(f"Test case {test_case_id} not found.")
            row = dict(row)
            stable_asset = self._stable_playwright_asset(conn, row)
            if stable_asset:
                for stable_field in (
                    "automation_script", "uploaded_script", "recorded_script", "active_script_source",
                    "auto_script_validated_hash", "uploaded_script_validated_hash",
                    "manual_script_validated_hash",
                ):
                    row[stable_field] = stable_asset.get(stable_field)
                row["automation_type"] = "Playwright"
                row["status"] = stable_asset.get("lifecycle_status") or row.get("status")
            is_api = str(row.get("automation_type") or "").upper() == "API"
            if is_api and source == "AUTO":
                # API readiness content is the bound endpoint + expected
                # status (or, for a Manual Source Endpoint test case with
                # no bound endpoint, the saved manual request config) —
                # not the (documentation-only) generated script text —
                # see _api_config_text() above.
                script = self._api_config_text(
                    row.get("bound_api_endpoint_id"),
                    row.get("api_expected_status_code"),
                    env_fingerprint,
                    row.get("api_request_config_json") or "",
                )
            else:
                script = {
                    "AUTO": row.get("automation_script"),
                    "UPLOADED": row.get("uploaded_script"),
                    "MANUAL": row.get("recorded_script"),
                }[source]
            field = {
                "AUTO": "auto_script_validated_hash",
                "UPLOADED": "uploaded_script_validated_hash",
                "MANUAL": "manual_script_validated_hash",
            }[source]
            keep_automated = (
                keep_active
                and (row.get("active_script_source") or "AUTO").upper()
                == source
            )
            status = "Automated" if keep_automated else row.get("status")
            fingerprint = self.script_fingerprint(script)
            identity = self._playwright_identity(conn.cursor(), row) if not is_api else None
            asset = stable_asset if identity else None
            if asset:
                scope_key, source_id, tc_id = identity
                conn.execute(
                    f"""UPDATE playwright_automation_assets
                        SET {field}=?,lifecycle_status=?,modified_date=?
                        WHERE stable_scope_key=? AND tc_id=?""",
                    (fingerprint, status, datetime.now().isoformat(), scope_key, tc_id),
                )
                conn.execute(
                    f"""UPDATE test_cases SET {field}=?,status=?,modified_date=?
                        WHERE id IN (SELECT tc.id FROM test_cases tc
                        JOIN test_case_versions v ON v.id=tc.test_case_version_id
                        WHERE v.source_knowledge_id=? AND tc.tc_id=?)""",
                    (fingerprint, status, datetime.now().isoformat(), source_id, tc_id),
                )
                conn.commit()
                return
            conn.execute(
                f"""
                UPDATE test_cases
                SET {field}=?, status=?, modified_date=?
                WHERE id=?
                """,
                (
                    fingerprint, status,
                    datetime.now().isoformat(), test_case_id,
                ),
            )
            conn.commit()
        finally:
            conn.close()

    # --------------------------------------------------
    # API Automation endpoint binding + assertion config
    # --------------------------------------------------

    def _update_api_config_field(self, test_case_id, field, value):
        """
        Shared body for set_bound_api_endpoint()/
        set_api_expected_status_code(): persists one API config field
        and — same rule as update_automation() applies to script
        content — demotes an AUTO-active Test Case back to Draft and
        clears its validated-config fingerprint whenever the value
        ACTUALLY changes, so a stale Validate/Set Active can never
        silently carry over a different endpoint or assertion. A
        no-op write (unchanged value) leaves Active state untouched.
        """
        conn = self.db.get_connection()
        cursor = conn.cursor()
        now = datetime.now().isoformat()
        cursor.execute(
            f"""
            SELECT {field},status,active_script_source,automation_type,execution
            FROM test_cases WHERE id=?
            """,
            (test_case_id,),
        )
        current = cursor.fetchone()
        if current is None:
            conn.close()
            return self.get_test_case(test_case_id)
        (
            current_value, current_status, active_source,
            automation_type, execution,
        ) = current
        changed = (current_value or None) != (value or None)
        active_changed = changed and (active_source or "AUTO").upper() == "AUTO"
        next_status = "Draft" if active_changed else (current_status or "Draft")
        # REQUEST-CENTRIC-LIFECYCLE-FINAL: a Test Case worked on
        # purely through the API Request workspace (bind endpoint /
        # expected status / request config) never used to get its
        # legacy automation_type column stamped to "API" — the only
        # code path that ever wrote automation_type was update_automation()
        # (the now-hidden-for-API "Save Script" flow), so a Request-
        # only workflow left automation_type stuck at "None" forever,
        # even once the Central Test Case was already classified
        # Execution Type=Automatable / Execution Tool=API Automation.
        # Self-heal here, at the single shared choke point every
        # Request-workspace write already goes through: once the
        # operator has classified this Test Case as API Automation,
        # any Request-workspace save keeps the legacy automation_type
        # label in sync so Validate/Execute correctly recognize it as
        # an API test case. This never sets Execution Tool itself —
        # only mirrors a classification the operator already made —
        # and never touches Playwright/SQL test cases (canonical execution
        # for those is never "API Automation").
        next_automation_type = automation_type
        if (
            execution == "API"
            and (automation_type or "None") != "API"
        ):
            next_automation_type = "API"
        cursor.execute(
            f"""
            UPDATE test_cases
            SET {field}=?,
                status=?,
                automation_type=?,
                auto_script_validated_hash=CASE WHEN ? THEN '' ELSE auto_script_validated_hash END,
                modified_date=?
            WHERE id=?
            """,
            (
                value, next_status, next_automation_type,
                1 if changed else 0, now, test_case_id,
            ),
        )
        conn.commit()
        conn.close()
        return self.get_test_case(test_case_id)

    def set_bound_api_endpoint(self, test_case_id, endpoint_id):
        """
        Persists (or, with endpoint_id=None, clears) which real,
        imported API Collection endpoint this Test Case is bound to
        for API Automation execution. Callers are responsible for
        having already validated endpoint_id belongs to this Test
        Case's own Domain/Module/Knowledge Name scope — this method
        does not re-validate, matching update_status()/
        update_automation()'s existing "caller validates, repository
        persists" split.
        """

        return self._update_api_config_field(
            test_case_id, "bound_api_endpoint_id", endpoint_id,
        )

    def set_api_expected_status_code(self, test_case_id, expected_status_code):
        """
        Persists the explicit "expected HTTP status" assertion for an
        API-type Test Case (e.g. 400 for a deliberate negative test).
        None clears it back to "no explicit assertion — fall back to
        the bound endpoint's own imported example_response_status".
        """

        return self._update_api_config_field(
            test_case_id, "api_expected_status_code", expected_status_code,
        )

    def set_api_request_config(self, test_case_id, config_json_text):
        """
        Persists the full structured per-Test-Case API request
        definition (section F) as a JSON text blob -- see the
        api_request_config_json column migration above. '' clears it
        back to "not yet configured". Reuses _update_api_config_field()
        so -- exactly like set_bound_api_endpoint()/
        set_api_expected_status_code() -- a real change here also
        demotes an AUTO-active Test Case back to Draft and clears its
        validated-config fingerprint, since the request Execute will
        actually send just changed.
        """

        return self._update_api_config_field(
            test_case_id, "api_request_config_json", config_json_text or "",
        )

    def update_automation(

        self,

        test_case_id,

        automation_type,

        automation_script=None

    ):
        """Persist an AI/manual automation script.

        A real content/tool change makes the script Draft, because it
        needs a fresh Validate -> Set Active cycle.  A no-op Save of
        the exact already-persisted script MUST NOT demote an Active
        script back to Draft; that was the root cause of Execute
        Selected reporting "saved script is a Draft" immediately
        after a successful Set Active.

        Returns True when executable script configuration changed.
        """

        if str(automation_type or "") == "Playwright" and automation_script is not None:
            stable_result = self._update_stable_playwright_script(
                test_case_id, automation_script, source="AUTO"
            )
            if stable_result is not None:
                return stable_result

        conn = self.db.get_connection()
        cursor = conn.cursor()
        now = datetime.now().isoformat()

        cursor.execute(
            "SELECT automation_type,automation_script,status,active_script_source FROM test_cases WHERE id=?",
            (test_case_id,),
        )
        current = cursor.fetchone()
        if current is None:
            conn.close()
            return False

        current_type, current_script, current_status, active_source = current
        script_changed = (
            automation_script is not None
            and self._canonical_script_text(current_script)
            != self._canonical_script_text(automation_script)
        )
        type_changed = (current_type or "None") != (automation_type or "None")
        changed = script_changed or type_changed
        active_changed = changed and (active_source or "AUTO").upper() == "AUTO"
        next_status = "Draft" if active_changed else (current_status or "Draft")

        cursor.execute(
            """
            UPDATE test_cases
            SET automation_type=?,
                automation_script=COALESCE(?, automation_script),
                status=?,
                auto_script_validated_hash=CASE WHEN ? THEN '' ELSE auto_script_validated_hash END,
                execution=?,
                modified_date=?
            WHERE id=?
            """,
            (
                automation_type,
                automation_script,
                next_status,
                1 if changed else 0,
                self.execution_for_automation_type(automation_type),
                now,
                test_case_id,
            )
        )

        conn.commit()
        conn.close()
        return changed

    def update_sql_asset(self, test_case_id, script_json, repository_path):
        """Atomically bind one validated SQL script and its managed file."""
        conn = self.db.get_connection()
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT status,active_script_source FROM test_cases WHERE id=?",
                (int(test_case_id),),
            ).fetchone()
            if not row:
                raise ValueError(f"Test case {test_case_id} not found.")
            conn.execute(
                """UPDATE test_cases SET automation_type='SQL',execution='SQL',
                   automation_script=?,sql_script_path=?,status='Draft',
                   auto_script_validated_hash='',modified_date=? WHERE id=?""",
                (script_json, str(repository_path), datetime.now().isoformat(), int(test_case_id)),
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
        return self.get_test_case(test_case_id)

    def update_recorded_script(self, test_case_id, recorded_script):
        """Persist the Manual/Recorded script.

        A changed script becomes Draft.  Re-saving byte-for-byte the
        same script is a no-op for lifecycle state and therefore does
        not undo a prior Set Active.  Returns True when script content
        changed.
        """

        stable_result = self._update_stable_playwright_script(
            test_case_id, recorded_script, source="MANUAL"
        )
        if stable_result is not None:
            return stable_result

        conn = self.db.get_connection()
        cursor = conn.cursor()
        now = datetime.now().isoformat()

        cursor.execute(
            "SELECT recorded_script,status,active_script_source FROM test_cases WHERE id=?",
            (test_case_id,),
        )
        current = cursor.fetchone()
        if current is None:
            conn.close()
            return False

        current_script, current_status, active_source = current
        changed = (
            self._canonical_script_text(current_script)
            != self._canonical_script_text(recorded_script)
        )
        active_changed = changed and (active_source or "AUTO").upper() == "MANUAL"
        next_status = "Draft" if active_changed else (current_status or "Draft")

        cursor.execute(
            """
            UPDATE test_cases
            SET recorded_script=?,
                automation_type='Playwright',
                status=?,
                manual_script_validated_hash=CASE WHEN ? THEN '' ELSE manual_script_validated_hash END,
                execution='Playwright',
                modified_date=?
            WHERE id=?
            """,
            (recorded_script, next_status, 1 if changed else 0, now, test_case_id)
        )

        conn.commit()
        conn.close()
        return changed

    def update_uploaded_script(self, test_case_id, uploaded_script):
        """Persist an explicitly uploaded Playwright source separately."""
        stable_result = self._update_stable_playwright_script(
            test_case_id, uploaded_script, source="UPLOADED"
        )
        if stable_result is not None:
            return stable_result
        conn = self.db.get_connection(); cursor = conn.cursor()
        now = datetime.now().isoformat()
        current = cursor.execute(
            "SELECT uploaded_script,status,active_script_source FROM test_cases WHERE id=?",
            (test_case_id,),
        ).fetchone()
        if current is None:
            conn.close(); return False
        changed = self._canonical_script_text(current[0]) != self._canonical_script_text(uploaded_script)
        active_changed = changed and (current[2] or "AUTO").upper() == "UPLOADED"
        cursor.execute(
            """UPDATE test_cases SET uploaded_script=?,automation_type='Playwright',
               execution='Playwright',status=?,uploaded_script_validated_hash=CASE
               WHEN ? THEN '' ELSE uploaded_script_validated_hash END,modified_date=? WHERE id=?""",
            (uploaded_script, "Draft" if active_changed else (current[1] or "Draft"),
             1 if changed else 0, now, test_case_id),
        )
        conn.commit(); conn.close(); return changed


    def set_active_script_source(self, test_case_id, source):
        """
        `source`: 'AUTO' (the AI-generated script) or 'MANUAL' (the
        hand-recorded one).
        """

        conn = self.db.get_connection(); conn.row_factory = self._dict_factory
        try:
            row = conn.execute("SELECT * FROM test_cases WHERE id=?", (test_case_id,)).fetchone()
            identity = self._playwright_identity(conn.cursor(), row) if row else None
            asset = self._stable_playwright_asset(conn, row) if identity else None
            if asset:
                scope_key, source_id, tc_id = identity
                conn.execute(
                    "UPDATE playwright_automation_assets SET active_script_source=?,lifecycle_status='Automated',modified_date=? "
                    "WHERE stable_scope_key=? AND tc_id=?",
                    (source, datetime.now().isoformat(), scope_key, tc_id),
                )
                conn.execute(
                    """UPDATE test_cases SET active_script_source=?,status='Automated' WHERE id IN (
                       SELECT tc.id FROM test_cases tc JOIN test_case_versions v
                       ON v.id=tc.test_case_version_id
                       WHERE v.source_knowledge_id=? AND tc.tc_id=?)""",
                    (source, source_id, tc_id),
                )
                conn.commit()
                return
        finally:
            conn.close()
        self._update_field(test_case_id, "active_script_source", source)

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

        if not row:

            return None

        item = self._with_stable_playwright(row)

        # 2026-09-29 sync fix: _with_test_types() -> normalize_test_case()
        # -> normalize_status() (Core/test_case_workbook.py) overwrites
        # "status" with the WORKBOOK-EXPORT execution-result status
        # (Pass/Fail/Not Executed) -- and normalize_status() explicitly
        # collapses the literal automation-lifecycle value "Automated"
        # itself into "Not Executed" (it's listed alongside "draft"/
        # "manual"/"pending" as a value with no dedicated run result).
        # That silently broke every Playwright/SQL automation-lifecycle
        # readiness check in AutomationWebRepository
        # (validate_for_execution()'s active_script_approved check,
        # can_auto_execute()) — they read THIS SAME "status" field
        # expecting the raw "Draft"/"Automated" value that
        # _with_stable_playwright() just set two lines above, so a test
        # case that had actually been Set Active could never again read
        # as ready, even with a perfectly matching validated hash.
        # Preserve the pre-normalization value under
        # automation_lifecycle_status so those two call sites can read
        # the real value, without changing what "status" means for any
        # existing execution-result consumer (dashboards, workbook
        # export, etc. all keep seeing exactly what they saw before).
        item["automation_lifecycle_status"] = item.get("status")

        return self._with_sources(self._with_test_types(item))

    @staticmethod
    def _serialize_test_types(values):
        supplied = values.get("test_types")
        if isinstance(supplied, str):
            try:
                decoded = json.loads(supplied)
                supplied = decoded if isinstance(decoded, list) else supplied.split(",")
            except (TypeError, ValueError):
                supplied = supplied.split(",")
        if not supplied:
            legacy = str(values.get("test_type") or "").strip()
            if legacy.startswith("["):
                try:
                    decoded = json.loads(legacy)
                    supplied = decoded if isinstance(decoded, list) else legacy.split(",")
                except (TypeError, ValueError):
                    supplied = legacy.split(",")
            else:
                supplied = legacy.split(",")
        ordered = []
        for value in supplied:
            cleaned = str(value).strip()
            if cleaned and cleaned not in ordered:
                ordered.append(cleaned)
        return ", ".join(ordered)

    @staticmethod
    def _with_test_types(row):
        item = normalize_test_case(row)
        raw_types = str(item.get("test_type") or "").strip()
        parsed_types = None
        if raw_types.startswith("["):
            try:
                candidate = json.loads(raw_types)
                if isinstance(candidate, list):
                    parsed_types = candidate
            except (TypeError, ValueError):
                pass
        item["test_types"] = [
            str(value).strip() for value in (parsed_types or raw_types.split(","))
            if str(value).strip()
        ]
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
    def execution_for_automation_type(automation_type):
        return {"Playwright": "Playwright", "API": "API", "SQL": "SQL"}.get(
            str(automation_type or ""), "Manual"
        )


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
