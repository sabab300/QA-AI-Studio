"""Isolated QA Engineering version, import, and DB/XLSX synchronization checks."""

import hashlib
import sqlite3
import sys
import tempfile
from pathlib import Path

WEB_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = WEB_ROOT.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(WEB_ROOT))

from Database.schema import ALL_TABLES, KNOWLEDGE_ITEMS_MIGRATION
from Core.qa_engineering_version_service import QaEngineeringVersionService
import Core.qa_engineering_version_service as version_module
from Core.test_case_repository import TestCaseRepository
from Core.test_case_workbook import HEADERS, SHEET_NAME, validate_import, workbook_bytes
from Shared.central_repository import CentralRepository


class TempDatabase:
    def __init__(self, path): self.db_path = Path(path)
    def get_connection(self):
        connection = sqlite3.connect(self.db_path)
        connection.execute("PRAGMA foreign_keys=ON")
        return connection


def digest(path): return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def row(title):
    return {"scenario": title, "importance": "High", "test_type": "Functional",
            "test_types": ["Functional"], "test_case": title,
            "pre_conditions": "Ready", "steps": "Perform action",
            "expected_result": "Action succeeds", "execution_type": "Manual",
            "execution_tool": "", "status": "Manual"}


def run_checks():
    with tempfile.TemporaryDirectory(prefix="qaais-qe-sync-") as temporary:
        root = Path(temporary); database = root / "metadata.db"
        temp_db = TempDatabase(database)
        connection = temp_db.get_connection()
        for statement in ALL_TABLES: connection.execute(statement)
        for migration in KNOWLEDGE_ITEMS_MIGRATION:
            try: connection.execute(migration)
            except sqlite3.OperationalError: pass
        connection.commit(); connection.close()

        repository = TestCaseRepository.__new__(TestCaseRepository)
        repository.db = temp_db; repository.logger = None
        repository._migrate_legacy_desktop_rows = lambda cursor: None
        repository.ensure_schema()

        central = CentralRepository(root / "central")
        connection = temp_db.get_connection()
        connection.execute("INSERT INTO domains(id,name) VALUES(1,'D1')")
        connection.execute("INSERT INTO modules(id,domain_id,name) VALUES(1,1,'M1')")
        source_ids = []
        for number in (1, 2):
            source_id = f"SOURCE-{number}"
            folder = central.build_source_path(domain="D1", module="M1", knowledge_name="KN1",
                                               version="1.0", document_type="CRF", source_type="Files",
                                               source_file_id=source_id)
            folder.mkdir(parents=True); document = folder / f"source-{number}.pdf"; document.write_bytes(b"source")
            connection.execute("""INSERT INTO knowledge_items
                (domain,module,knowledge_name,version,knowledge_path,file_name,repository_path,sha256,
                 source_type,document_type,domain_id,module_id,source_file_id,repository_relative_path)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                ("D1","M1","KN1","1.0","D1/M1/KN1/1.0",document.name,str(document),str(number),
                 "Files","CRF",1,1,source_id,central.relative(document)))
            source_ids.append(connection.execute("SELECT last_insert_rowid()").fetchone()[0])
        connection.commit(); connection.close()

        service = QaEngineeringVersionService.__new__(QaEngineeringVersionService)
        service.repository = repository; service.db = temp_db; service.central = central

        legacy_folder = central.root / "Legacy" / "Documents"
        legacy_folder.mkdir(parents=True)
        legacy_document = legacy_folder / "legacy.pdf"
        legacy_document.write_bytes(b"legacy")
        connection = temp_db.get_connection()
        connection.execute("""INSERT INTO knowledge_items
            (domain,module,knowledge_name,version,knowledge_path,file_name,repository_path,sha256,
             source_type,document_type,domain_id,module_id)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
            ("D1","M1","Legacy KN","1.0","legacy","legacy.pdf",str(legacy_document),"legacy",
             "Files","CRF",1,1))
        legacy_id = connection.execute("SELECT last_insert_rowid()").fetchone()[0]
        connection.commit(); connection.close()
        adopted = service.get_source(legacy_id)
        assert adopted["source_file_id"] and not adopted["source_file_id"].startswith("LEGACY-")
        adopted_path = Path(adopted["repository_path"])
        assert adopted_path.is_file() and adopted_path.read_bytes() == b"legacy"
        assert adopted_path.parent.name.startswith("SD-")
        assert not legacy_document.exists()

        created = service.create_generated_set(source_ids[0], [row("Original")], "testCases_v1.xlsx")
        assert created["version_number"] == 1
        loaded = service.load_version(created["test_case_version_id"], created["test_case_file_id"])
        workbook = Path(loaded["file"]["workbook_path"])
        assert workbook.is_file() and len(loaded["test_cases"]) == 1
        assert workbook.name == Path(loaded["version"]["folder_path"]).name + "-TC1.xlsx"
        assert len(str(workbook.resolve())) <= 240
        inspection_book = __import__("openpyxl").load_workbook(workbook, read_only=True)
        try:
            assert tuple(HEADERS) == tuple(
                inspection_book[SHEET_NAME][1][i].value for i in range(len(HEADERS))
            )
        finally:
            inspection_book.close()

        added = service.apply_operations(created["test_case_version_id"], created["test_case_file_id"],
                                         [{"action":"insert","values":row("Added")}])
        assert len(added["test_cases"]) == 2
        added_id = next(item["id"] for item in added["test_cases"] if item["test_case"] == "Added")
        changed = row("Edited")
        edited = service.apply_operations(created["test_case_version_id"], created["test_case_file_id"],
                                          [{"action":"update","id":added_id,"values":changed}])
        assert any(item["test_case"] == "Edited" for item in edited["test_cases"])
        deleted = service.apply_operations(created["test_case_version_id"], created["test_case_file_id"],
                                           [{"action":"delete","id":added_id}])
        assert len(deleted["test_cases"]) == 1
        assert len(validate_import(workbook.read_bytes(), workbook.name)) == 1

        original_case = deleted["test_cases"][0]
        revised_values = row("Reviewed Revision")
        revision = service.create_revision(
            created["test_case_version_id"], created["test_case_file_id"],
            [{"action": "update", "id": original_case["id"], "values": revised_values}],
            "reviewed.xlsx",
        )
        assert revision["version_number"] == 2
        revised = service.load_version(revision["test_case_version_id"], revision["test_case_file_id"])
        assert revised["test_cases"][0]["test_case"] == "Reviewed Revision"
        assert revised["test_cases"][0]["tc_id"] != original_case["tc_id"]
        assert service.load_version(created["test_case_version_id"], created["test_case_file_id"])["test_cases"][0]["test_case"] == "Original"

        valid_import = workbook_bytes([row("Imported")])
        imported = service.import_set(source_ids[0], valid_import, "external.xlsx")
        assert imported["version_number"] == 3
        imported_file = service.load_version(
            imported["test_case_version_id"], imported["test_case_file_id"]
        )["file"]["workbook_path"]
        assert Path(imported_file).read_bytes() == valid_import
        versions_before = len(service.list_versions(source_ids[0]))
        try:
            service.import_set(source_ids[0], b"not an xlsx", "invalid.xlsx")
            raise AssertionError("Invalid workbook was accepted")
        except ValueError:
            pass
        assert len(service.list_versions(source_ids[0])) == versions_before

        from openpyxl import Workbook
        malformed_book = Workbook()
        malformed_sheet = malformed_book.active
        malformed_sheet.title = SHEET_NAME
        malformed_sheet.append(list(HEADERS[:-1]))
        malformed_sheet.append(["TC100"] + ["value"] * (len(HEADERS) - 2))
        malformed_path = root / "missing-status.xlsx"
        malformed_book.save(malformed_path)
        malformed_book.close()
        try:
            service.import_set(source_ids[0], malformed_path.read_bytes(), malformed_path.name)
            raise AssertionError("Workbook missing a canonical column was accepted")
        except ValueError as error:
            assert "Missing required canonical column" in str(error)
        assert len(service.list_versions(source_ids[0])) == versions_before

        second = service.create_generated_set(source_ids[1], [row("Isolated")], "isolated.xlsx")
        second_file = Path(service.load_version(second["test_case_version_id"], second["test_case_file_id"])["file"]["workbook_path"])
        second_digest = digest(second_file)
        service.apply_operations(created["test_case_version_id"], created["test_case_file_id"],
                                 [{"action":"insert","values":row("Source A only")}])
        assert digest(second_file) == second_digest

        original_digest = digest(workbook)
        original_rows = len(service.load_version(created["test_case_version_id"], created["test_case_file_id"])["test_cases"])
        real_stage_workbook = version_module.stage_workbook
        version_module.stage_workbook = lambda rows, destination: (_ for _ in ()).throw(OSError("injected write failure"))
        try:
            try:
                service.apply_operations(created["test_case_version_id"], created["test_case_file_id"],
                                         [{"action":"insert","values":row("Must roll back")}])
                raise AssertionError("Injected workbook failure was reported as success")
            except OSError as error:
                assert "injected write failure" in str(error)
        finally:
            version_module.stage_workbook = real_stage_workbook
        assert digest(workbook) == original_digest
        assert len(service.load_version(created["test_case_version_id"], created["test_case_file_id"])["test_cases"]) == original_rows

        restarted = QaEngineeringVersionService.__new__(QaEngineeringVersionService)
        restarted.repository = repository
        restarted.db = temp_db
        restarted.central = CentralRepository(root / "central")
        restarted_versions = restarted.list_versions(source_ids[0])
        assert [item["version_number"] for item in restarted_versions] == [1, 2, 3]
        assert restarted.load_version(
            imported["test_case_version_id"], imported["test_case_file_id"]
        )["test_cases"][0]["test_case"] == "Imported"


if __name__ == "__main__":
    run_checks()
    print("QA Engineering version/workbook synchronization: PASS")
