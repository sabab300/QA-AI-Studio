"""Transactional Test Case version/workbook persistence for QA Engineering."""

from __future__ import annotations

import hashlib
import json
import shutil
import sys
import uuid
from datetime import datetime
from pathlib import Path

from Core.test_case_repository import TestCaseRepository
from Core.test_case_workbook import normalize_execution, normalize_test_case, stage_workbook, validate_import

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
from Shared.central_repository import CentralRepository
from Shared.document_hierarchy import DocumentHierarchy


class QaEngineeringVersionService:
    def __init__(self):
        self.repository = TestCaseRepository()
        self.db = self.repository.db
        self.central = CentralRepository()

    @staticmethod
    def _dict_factory(cursor, row):
        return {column[0]: row[index] for index, column in enumerate(cursor.description)}

    def get_source(self, source_id):
        rows = self.repository._source_records([int(source_id)])
        if not rows:
            raise ValueError("Selected Source File was not found.")
        source = rows[0]
        if not source.get("source_file_id") and source.get("source_type") not in {
            "API_COLLECTION", "API Collection"
        }:
            source = self._adopt_legacy_file_source(source)
        return source

    def _adopt_legacy_file_source(self, source):
        """Move one pre-Central-Repository file into its managed Source File folder."""
        original = self.central.resolve_managed(source.get("repository_path"), "")
        if not original.is_file():
            raise ValueError(
                f"Legacy Knowledge source '{source.get('file_name') or source['id']}' is unavailable."
            )
        source_file_id = self.central.create_source_file_id(
            source.get("file_name") or original.name
        )
        folder = self.central.build_source_path(
            domain=source.get("domain"), module=source.get("module"),
            knowledge_name=source.get("knowledge_name"), version=source.get("version") or "1.0",
            document_type=source.get("document_type") or "Other",
            source_type=source.get("source_type") or "Files", source_file_id=source_file_id,
        )
        destination = folder / original.name
        if destination.exists():
            raise ValueError(f"Managed Source File already exists: {source_file_id}")
        folder.mkdir(parents=True, exist_ok=False)
        moved = False
        conn = self.db.get_connection()
        try:
            references = conn.execute(
                "SELECT COUNT(*) FROM knowledge_items WHERE repository_path=? AND id<>?",
                (str(source.get("repository_path") or ""), int(source["id"])),
            ).fetchone()[0]
            try:
                original.resolve().relative_to(self.central.root.resolve())
                inside_repository = True
            except ValueError:
                inside_repository = False
            if inside_repository and not references:
                original.replace(destination)
                moved = True
            else:
                shutil.copy2(original, destination)
            relative = self.central.relative(destination)
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                "UPDATE knowledge_items SET repository_path=?,repository_relative_path=?,source_file_id=? WHERE id=?",
                (str(destination), relative, source_file_id, int(source["id"])),
            )
            conn.execute(
                "UPDATE knowledge_versions SET repository_path=? WHERE knowledge_item_id=?",
                (str(destination), int(source["id"])),
            )
            conn.commit()
        except Exception:
            conn.rollback()
            if moved and destination.exists():
                original.parent.mkdir(parents=True, exist_ok=True)
                destination.replace(original)
            elif destination.exists():
                destination.unlink(missing_ok=True)
            try:
                folder.rmdir()
            except OSError:
                pass
            raise
        finally:
            conn.close()
        source.update({
            "repository_path": str(destination),
            "repository_relative_path": relative,
            "source_file_id": source_file_id,
        })
        return source

    def source_context(self, source_id):
        source = self.get_source(source_id)
        source_file_id = str(source.get("source_file_id") or f"LEGACY-{source['id']}")
        source_path = self.central.resolve_managed(
            source.get("repository_path"), source.get("repository_relative_path") or ""
        )
        return source, source_file_id, source_path.parent

    def list_versions(self, source_id):
        conn = self.db.get_connection(); conn.row_factory = self._dict_factory
        versions = conn.execute(
            "SELECT * FROM test_case_versions WHERE source_knowledge_id=? ORDER BY version_number",
            (int(source_id),),
        ).fetchall()
        for version in versions:
            version["files"] = conn.execute(
                "SELECT id,file_name,sha256 FROM test_case_files WHERE test_case_version_id=? ORDER BY id",
                (version["id"],),
            ).fetchall()
        conn.close()
        return versions

    def load_version(self, version_id, file_id=None):
        conn = self.db.get_connection(); conn.row_factory = self._dict_factory
        version = conn.execute("SELECT * FROM test_case_versions WHERE id=?", (int(version_id),)).fetchone()
        if not version:
            conn.close(); raise ValueError("Test Cases Version was not found.")
        if file_id:
            file_row = conn.execute(
                "SELECT * FROM test_case_files WHERE id=? AND test_case_version_id=?",
                (int(file_id), int(version_id)),
            ).fetchone()
        else:
            file_row = conn.execute(
                "SELECT * FROM test_case_files WHERE test_case_version_id=? ORDER BY id LIMIT 1",
                (int(version_id),),
            ).fetchone()
        if not file_row:
            conn.close(); raise ValueError("The selected Test Cases XLSX was not found.")
        if not Path(file_row["workbook_path"]).is_file():
            conn.close(); raise ValueError("The selected managed workbook is unavailable.")
        rows = conn.execute(
            "SELECT * FROM test_cases WHERE test_case_version_id=? ORDER BY id", (int(version_id),)
        ).fetchall()
        conn.close()
        return {"version": version, "file": file_row,
                "test_cases": [self.repository._with_sources(self.repository._with_test_types(row)) for row in rows]}

    def create_generated_set(self, source_id, rows, requested_name=""):
        return self._create_set(source_id, rows, requested_name, "AI Generated")

    def create_revision(self, version_id, file_id, operations, requested_name=""):
        """Create the next immutable Test Cases revision from reviewed edits."""
        loaded = self.load_version(version_id, file_id)
        rows = []
        by_id = {}
        for existing in loaded["test_cases"]:
            values = dict(existing)
            values["test_types"] = values.get("test_types") or []
            rows.append(values)
            by_id[int(existing["id"])] = values
        for operation in operations:
            action = operation.get("action")
            values = dict(operation.get("values") or {})
            if action == "insert":
                rows.append(values)
            elif action in {"update", "delete"}:
                row_id = int(operation.get("id") or 0)
                current = by_id.get(row_id)
                if current is None:
                    raise ValueError(f"Test Case id={row_id} is outside the selected version.")
                if action == "delete":
                    rows.remove(current)
                else:
                    current.update(values)
            else:
                raise ValueError("Unsupported Review / Save action.")
        if not rows:
            raise ValueError("A Test Cases revision must contain at least one Test Case.")
        return self._create_set(
            loaded["version"]["source_knowledge_id"], rows, requested_name,
            "Reviewed Revision",
        )

    def import_set(self, source_id, file_bytes, filename):
        rows = validate_import(file_bytes, filename)
        return self._create_set(source_id, rows, filename, "External Import", file_bytes)

    def _create_set(self, source_id, rows, requested_name, origin, source_workbook_bytes=None):
        if not rows:
            raise ValueError("At least one Test Case is required.")
        source, source_file_id, source_folder = self.source_context(source_id)
        scope = {
            "domain": source.get("domain"), "module": source.get("module"),
            "knowledge_name": source.get("knowledge_name"), "version": source.get("version"),
            "document_type": source.get("document_type"),
            "source_knowledge_ids": [int(source_id)],
        }
        conn = self.db.get_connection(); conn.row_factory = self._dict_factory
        cursor = conn.cursor(); staging = final_folder = None
        try:
            cursor.execute("BEGIN IMMEDIATE")
            owner = DocumentHierarchy(conn).resolve_source(source_id)
            source["document_version_id"] = owner["document_version_id"]
            for row in rows:
                self.repository._validate_case(row, cursor)
            db_next_row = cursor.execute(
                "SELECT COALESCE(MAX(version_number),0)+1 FROM test_case_versions WHERE source_knowledge_id=?",
                (int(source_id),),
            ).fetchone()
            db_next = next(iter(db_next_row.values()))
            version_number = max(int(db_next), self.central.get_next_test_case_version(source_folder))
            final_folder = self.central.build_test_case_version_path(source_folder, version_number, source_file_id)
            if final_folder.exists():
                raise ValueError("The next Test Cases version folder already exists.")
            staging = source_folder / f".creating-test-cases-{uuid.uuid4().hex}"
            staging.mkdir(parents=True)
            folder_base = final_folder.name
            sequence = 1
            workbook_name = f"{folder_base}-TC{sequence}.xlsx"
            final_workbook = final_folder / workbook_name
            self.central.validate_excel_path(final_workbook)
            now = datetime.now().isoformat()
            cursor.execute(
                "INSERT INTO test_case_versions(source_knowledge_id,source_file_id,version_number,folder_path,workbook_path,created_date,document_version_id) VALUES(?,?,?,?,?,?,?)",
                (int(source_id), source_file_id, version_number, str(final_folder), str(final_workbook), now, owner["document_version_id"]),
            )
            version_id = cursor.lastrowid
            persisted = []
            for values in rows:
                persisted.append(self._insert_case(cursor, scope, source, values, version_id, workbook_name, now))
            if source_workbook_bytes is None:
                staged_workbook = stage_workbook(persisted, staging / workbook_name)
                staged_workbook.replace(staging / workbook_name)
            else:
                # The complete import was validated before this transaction. Preserve the
                # user's canonical workbook byte-for-byte for the newly created version.
                (staging / workbook_name).write_bytes(source_workbook_bytes)
            sha256 = self._sha256(staging / workbook_name)
            cursor.execute(
                "INSERT INTO test_case_files(test_case_version_id,file_name,workbook_path,sha256,created_date) VALUES(?,?,?,?,?)",
                (version_id, workbook_name, str(final_workbook), sha256, now),
            )
            file_id = cursor.lastrowid
            cursor.execute(
                "UPDATE test_cases SET reviewed_workbook_path=? WHERE test_case_version_id=?",
                (str(final_workbook), version_id),
            )
            staging.replace(final_folder)
            try:
                conn.commit()
            except Exception:
                shutil.rmtree(final_folder, ignore_errors=True)
                raise
            return {"test_case_version_id": version_id, "version_number": version_number,
                    "test_case_file_id": file_id, "file_name": workbook_name,
                    "source_knowledge_id": int(source_id), "source_file_id": source_file_id,
                    "test_case_ids": [row["id"] for row in persisted], "origin": origin}
        except Exception:
            conn.rollback()
            if staging and staging.exists(): shutil.rmtree(staging, ignore_errors=True)
            raise
        finally:
            conn.close()

    def apply_operations(self, version_id, file_id, operations):
        loaded = self.load_version(version_id, file_id)
        workbook = Path(loaded["file"]["workbook_path"])
        source = self.get_source(loaded["version"]["source_knowledge_id"])
        scope = {key: source.get(key) for key in ("domain", "module", "knowledge_name", "version", "document_type")}
        scope["source_knowledge_ids"] = [source["id"]]
        conn = self.db.get_connection(); conn.row_factory = self._dict_factory
        cursor = conn.cursor(); staged = backup = None; workbook_replaced = False
        now = datetime.now().isoformat()
        try:
            cursor.execute("BEGIN IMMEDIATE")
            owner = DocumentHierarchy(conn).resolve_source(source["id"])
            source["document_version_id"] = owner["document_version_id"]
            for operation in operations:
                action = operation.get("action"); values = operation.get("values") or {}
                if action == "insert":
                    self.repository._validate_case(values, cursor)
                    self._insert_case(cursor, scope, source, values, int(version_id), workbook.name, now)
                elif action == "update":
                    test_case_id = int(operation.get("id") or 0)
                    current = cursor.execute(
                        "SELECT id FROM test_cases WHERE id=? AND test_case_version_id=?",
                        (test_case_id, int(version_id)),
                    ).fetchone()
                    if not current: raise ValueError(f"Test Case id={test_case_id} is outside the selected version.")
                    self.repository._validate_case(values, cursor)
                    cursor.execute("""UPDATE test_cases SET scenario=?,importance=?,test_type=?,test_case=?,pre_conditions=?,steps=?,expected_result=?,actual_result=?,execution=?,status=?,bug_id=?,comments=?,review_state='Reviewed/Saved',modified_date=? WHERE id=?""",
                                   (values.get("scenario", ""), values.get("importance", ""), self.repository._serialize_test_types(values), values.get("test_case", ""), values.get("pre_conditions", ""), values.get("steps", ""), values.get("expected_result", ""), values.get("actual_result", ""), normalize_execution(values), values.get("status", ""), values.get("bug_id", ""), values.get("comments", ""), now, test_case_id))
                elif action == "delete":
                    test_case_id = int(operation.get("id") or 0)
                    deleted = cursor.execute(
                        "DELETE FROM test_cases WHERE id=? AND test_case_version_id=?",
                        (test_case_id, int(version_id)),
                    ).rowcount
                    if not deleted: raise ValueError(f"Test Case id={test_case_id} is outside the selected version.")
                else:
                    raise ValueError("Unsupported Review / Save action.")
            rows = cursor.execute(
                "SELECT * FROM test_cases WHERE test_case_version_id=? ORDER BY id", (int(version_id),)
            ).fetchall()
            staged = stage_workbook(rows, workbook)
            backup = workbook.with_name(f".{workbook.name}.{uuid.uuid4().hex}.backup")
            shutil.copy2(workbook, backup)
            staged.replace(workbook)
            workbook_replaced = True
            cursor.execute(
                "UPDATE test_case_files SET sha256=? WHERE id=? AND test_case_version_id=?",
                (self._sha256(workbook), int(file_id), int(version_id)),
            )
            try:
                conn.commit()
            except Exception:
                backup.replace(workbook)
                raise
            backup.unlink(missing_ok=True)
            return self.load_version(version_id, file_id)
        except Exception:
            conn.rollback()
            if staged and staged.exists(): staged.unlink(missing_ok=True)
            if workbook_replaced and backup and backup.exists(): backup.replace(workbook)
            elif backup and backup.exists(): backup.unlink(missing_ok=True)
            raise
        finally:
            conn.close()

    def _insert_case(self, cursor, scope, source, values, version_id, workbook_name, now):
        values = normalize_test_case(values)
        tc_id = str(values.get("tc_number") or "").strip()
        if tc_id:
            duplicate = cursor.execute(
                "SELECT id FROM test_cases WHERE tc_id=? AND test_case_version_id=?",
                (tc_id, int(version_id)),
            ).fetchone()
            if duplicate: raise ValueError(f"Test Case ID '{tc_id}' already exists.")
        else:
            tc_id = self.repository._allocate_tc_id(cursor, scope, [source])
        columns = (
            "tc_number", "tc_id", "domain", "module", "knowledge_name", "version",
            "domain_id", "module_id", "scenario", "importance", "test_type", "test_case",
            "pre_conditions", "steps", "expected_result", "status", "automation_type",
            "last_result", "execution", "source_knowledge_ids",
            "test_case_document_name", "document_type", "source_type", "review_state",
            "created_date", "modified_date", "test_case_version_id", "reviewed_workbook_path",
            "actual_result", "bug_id", "comments", "document_version_id",
        )
        persisted_values = (
            tc_id, tc_id, scope["domain"], scope["module"], scope["knowledge_name"],
            scope.get("version") or "", source.get("domain_id"), source.get("module_id"),
            values.get("scenario", ""), values.get("importance", ""),
            self.repository._serialize_test_types(values), values.get("test_case", ""),
            values.get("pre_conditions", ""), values.get("steps", ""),
            values.get("expected_result", ""), values.get("status", ""), "None", "Not Run",
            normalize_execution(values),
            json.dumps([source["id"]]), workbook_name, source.get("document_type") or "",
            source.get("source_type") or "", "Reviewed/Saved", now, now, int(version_id), "",
            values.get("actual_result", ""), values.get("bug_id", ""), values.get("comments", ""), source["document_version_id"],
        )
        cursor.execute(
            f"INSERT INTO test_cases ({','.join(columns)}) VALUES ({','.join('?' for _ in columns)})",
            persisted_values,
        )
        test_case_id = cursor.lastrowid
        self.repository._replace_sources(cursor, test_case_id, [source])
        row = cursor.execute("SELECT * FROM test_cases WHERE id=?", (test_case_id,)).fetchone()
        return row

    @staticmethod
    def _sha256(path):
        digest = hashlib.sha256()
        with Path(path).open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""): digest.update(chunk)
        return digest.hexdigest()
