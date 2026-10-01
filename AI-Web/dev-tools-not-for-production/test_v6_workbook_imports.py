"""Deterministic workbook-isolation integration test on a DB copy."""

from pathlib import Path
from io import BytesIO
import json
import logging
import shutil
import sqlite3
import tempfile
import sys
from unittest.mock import patch

from fastapi import HTTPException, UploadFile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from Core.automation_import_service import (
    AutomationImportService, PLAYWRIGHT_CONTENT_ALIASES,
    PLAYWRIGHT_CONTENT_LABEL,
)
from Core.api_collection_repository import ApiCollectionRepository
from Core.automation_execution_repository import AutomationExecutionRepository
from Core.dashboard_repository import DashboardRepository
from Core.automation_web_repository import SqlAutomationWeb, TestCasesWeb
from Core.test_case_repository import TestCaseRepository
from Web.routers import automation_router


class TemporaryDatabase:
    def __init__(self, path): self.db_path = Path(path)
    def get_connection(self):
        connection = sqlite3.connect(self.db_path)
        connection.execute("PRAGMA foreign_keys=ON")
        return connection


def main():
    source = Path(__file__).resolve().parents[1] / "Database" / "metadata.db"
    folder = Path(tempfile.mkdtemp(prefix="qaais-v6-import-"))
    target = folder / "metadata.db"
    shutil.copy2(source, target)
    try:
        repository = TestCaseRepository.__new__(TestCaseRepository)
        repository.db = TemporaryDatabase(target)
        repository.logger = logging.getLogger("v6-import-test")
        repository.ensure_schema()
        connection = repository.db.get_connection()
        connection.row_factory = sqlite3.Row
        selected = connection.execute(
            """SELECT tf.id AS file_id,tf.test_case_version_id,tf.workbook_path,
                      tc.id AS test_case_id,tc.tc_number
               FROM test_case_files tf JOIN test_cases tc
                 ON tc.test_case_version_id=tf.test_case_version_id
                AND tc.reviewed_workbook_path=tf.workbook_path
               ORDER BY tf.id LIMIT 1"""
        ).fetchone()
        assert selected, "No persisted workbook/test case fixture is available."
        other_path = str(folder / "Workbook-B.xlsx")
        connection.execute(
            "INSERT INTO test_case_files(test_case_version_id,file_name,workbook_path,sha256,created_date) VALUES(?,?,?,?,datetime('now'))",
            (selected["test_case_version_id"], "Workbook-B.xlsx", other_path, "fixture"),
        )
        other_file_id = connection.execute("SELECT last_insert_rowid()").fetchone()[0]
        columns = [row[1] for row in connection.execute("PRAGMA table_info(test_cases)") if row[1] != "id"]
        source_case = dict(connection.execute("SELECT * FROM test_cases WHERE id=?", (selected["test_case_id"],)).fetchone())
        source_case.update(tc_number="TC-WORKBOOK-B-001", tc_id="TC-WORKBOOK-B-001",
                           reviewed_workbook_path=other_path, automation_script="")
        connection.execute(
            f"INSERT INTO test_cases ({','.join(columns)}) VALUES ({','.join('?' for _ in columns)})",
            [source_case.get(column) for column in columns],
        )
        other_case_id = connection.execute("SELECT last_insert_rowid()").fetchone()[0]
        tc_folder = folder / "TC-Folder"
        tc_folder.mkdir()
        connection.execute(
            "UPDATE test_case_versions SET folder_path=? WHERE id=?",
            (str(tc_folder), selected["test_case_version_id"]),
        )
        connection.commit(); connection.close()

        api_repository = ApiCollectionRepository()
        api_repository.db = repository.db
        api_repository._index_collection_for_search = lambda **_kwargs: 0

        def import_collection(file_name, file_id, suffix):
            source_file = folder / file_name
            source_file.write_text(json.dumps({
                "info": {"name": f"Workbook {suffix} Collection"},
                "item": [{
                    "name": f"Workbook {suffix} endpoint",
                    "request": {"method": "GET", "url": f"https://example.test/{suffix.lower()}"},
                }],
            }), encoding="utf-8")
            return api_repository.import_postman_collection(
                source_file, test_case_version_id=selected["test_case_version_id"],
                test_case_file_id=file_id,
            )

        imported_a = import_collection("a.postman_collection.json", selected["file_id"], "A")
        assert imported_a["success"] and imported_a["endpoints_saved"] == 1
        collection_id = imported_a["collection_id"]
        a_endpoints = api_repository.get_endpoints_for_test_case_file(selected["file_id"])
        assert [row["url_raw"] for row in a_endpoints] == ["https://example.test/a"]

        before_rejection = list(tc_folder.rglob("*"))
        rejected = import_collection("invalid.postman_collection.json", 999999999, "Invalid")
        assert not rejected["success"]
        assert "does not belong" in rejected["error"]
        assert list(tc_folder.rglob("*")) == before_rejection

        imported_b = import_collection("b.postman_collection.json", other_file_id, "B")
        assert imported_b["success"] and imported_b["endpoints_saved"] == 1
        b_endpoints = api_repository.get_endpoints_for_test_case_file(other_file_id)
        assert [row["url_raw"] for row in b_endpoints] == ["https://example.test/b"]
        assert [row["url_raw"] for row in api_repository.get_endpoints_for_test_case_file(selected["file_id"])] == ["https://example.test/a"]

        check = repository.db.get_connection()
        persisted_file_id = check.execute(
            "SELECT test_case_file_id FROM api_collections WHERE id=?", (collection_id,)
        ).fetchone()[0]
        check.close()
        assert persisted_file_id == selected["file_id"]

        dashboard = DashboardRepository.__new__(DashboardRepository)
        dashboard.db = repository.db
        dashboard.runs = AutomationExecutionRepository()
        dashboard.runs.db = repository.db
        dash_connection = repository.db.get_connection()
        dash_connection.row_factory = dashboard._dict_factory
        hierarchy = dashboard._workbook_hierarchy(dash_connection)
        coverage = dashboard._knowledge_coverage(dash_connection)
        overview = dashboard._overview(dash_connection)
        dash_connection.close()
        assert hierarchy and all(row.get("file_name") for row in hierarchy)
        assert all("test_case_count" in row for row in hierarchy)
        assert "by_domain_module_knowledge" in coverage
        assert overview["test_cases"] > 0

        service = AutomationImportService(repository)
        def validate(text, file_id):
            return service.validate(text.encode(), "mapping.txt", selected["test_case_version_id"],
                                    file_id, PLAYWRIGHT_CONTENT_ALIASES,
                                    PLAYWRIGHT_CONTENT_LABEL)
        valid = validate(f"Test Case ID,Script\n{selected['tc_number']},page.goto('https://example.test')", selected["file_id"])
        wrong_workbook = validate("Test Case ID,Script\nTC-WORKBOOK-B-001,page.reload()", selected["file_id"])
        unknown = validate("Test Case ID,Script\nUNKNOWN,page.reload()", selected["file_id"])
        duplicate = validate(f"Test Case ID,Script\n{selected['tc_number']},a\n{selected['tc_number']},b", selected["file_id"])
        assert valid.ok
        assert not wrong_workbook.ok and wrong_workbook.unknown_ids == ["TC-WORKBOOK-B-001"]
        assert not unknown.ok and unknown.unknown_ids == ["UNKNOWN"]
        assert not duplicate.ok and duplicate.duplicate_ids

        imported = TestCasesWeb(repository).import_playwright_mapping(
            f"Test Case ID,Script\n{selected['tc_number']},page.goto('https://example.test')".encode(),
            "mapping.txt", selected["test_case_version_id"], selected["file_id"],
        )
        assert imported["ok"] and imported["saved_test_case_ids"] == [selected["tc_number"]]
        persisted = repository.get_test_case(selected["test_case_id"])
        assert "example.test" in (persisted.get("uploaded_script") or "")
        assert persisted.get("execution") == "Playwright"
        assert repository.get_test_case(other_case_id).get("automation_script") in (None, "")
        imported_b_mapping = TestCasesWeb(repository).import_playwright_mapping(
            b"Test Case ID,Script\nTC-WORKBOOK-B-001,page.goto('https://example.test/b')",
            "mapping-b.txt", selected["test_case_version_id"], other_file_id,
        )
        assert imported_b_mapping["ok"]
        assert imported_b_mapping["saved_test_case_ids"] == ["TC-WORKBOOK-B-001"]
        assert "example.test/b" in (
            repository.get_test_case(other_case_id).get("uploaded_script") or ""
        )

        def route_upload(payload, filename="mapping.json"):
            upload = UploadFile(filename=filename, file=BytesIO(payload))
            with patch.object(
                automation_router, "TestCasesWeb", return_value=TestCasesWeb(repository)
            ), patch.object(automation_router, "_audit"):
                return automation_router.import_playwright_mapping(
                    file=upload,
                    test_case_version_id=selected["test_case_version_id"],
                    test_case_file_id=selected["file_id"],
                    current_user={"id": 1, "username": "test"},
                )

        valid_json = json.dumps([{
            "Test Case ID": selected["tc_number"],
            "Script": "page.goto('https://example.test/json')",
        }]).encode()
        assert route_upload(valid_json)["ok"]
        try:
            route_upload(b"{not-json")
        except HTTPException as error:
            assert error.status_code == 400 and "Could not parse JSON" in str(error.detail)
        else:
            raise AssertionError("Invalid Playwright JSON did not return HTTP 400.")
        try:
            route_upload(json.dumps([{
                "Test Case ID": "NOT-IN-SELECTED-WORKBOOK", "Script": "page.reload()",
            }]).encode())
        except HTTPException as error:
            assert error.status_code == 400 and "selected Test Cases XLSX" in str(error.detail)
        else:
            raise AssertionError("Unknown workbook Test Case ID did not return HTTP 400.")
        sql_web = SqlAutomationWeb(repository)
        sql_result = sql_web.import_mapping(
            f"Test Case ID,Script\n{selected['tc_number']},SELECT 1".encode(),
            "sql-mapping.txt", selected["test_case_version_id"], selected["file_id"],
        )
        assert sql_result["ok"] and sql_result["saved_test_case_ids"] == [selected["tc_number"]]
        persisted_sql = repository.get_test_case(selected["test_case_id"])
        assert "SELECT 1" in (persisted_sql.get("automation_script") or "")
        assert persisted_sql.get("execution") == "SQL"
        raw_result = sql_web.import_raw_sql(
            b"SELECT 2", "raw.sql", selected["test_case_version_id"],
            selected["file_id"], selected["test_case_id"],
        )
        assert raw_result["ok"]
        assert "SELECT 2" in (repository.get_test_case(selected["test_case_id"]).get("automation_script") or "")
        try:
            sql_web.import_raw_sql(
                b"SELECT 3", "raw.sql", selected["test_case_version_id"],
                selected["file_id"], other_case_id,
            )
        except ValueError:
            pass
        else:
            raise AssertionError("Raw SQL accepted a test case from another workbook.")
        print("valid_selected_workbook: PASS")
        print("other_workbook_rejected: PASS")
        print("unknown_rejected: PASS")
        print("duplicate_atomic_rejection: PASS")
        print("script_persisted_and_retrievable: PASS")
        print("second_workbook_script_persisted_and_isolated: PASS")
        print("playwright_valid_json_http_success: PASS")
        print("playwright_invalid_json_http_400_detail: PASS")
        print("playwright_unknown_id_http_400_detail: PASS")
        print("sql_structured_mapping_persisted: PASS")
        print("raw_sql_explicit_binding: PASS")
        print("raw_sql_cross_workbook_rejected: PASS")
        print("api_collection_workbook_id_persisted: PASS")
        print("api_endpoint_workbook_isolation: PASS")
        print("api_invalid_workbook_atomic_rejection: PASS")
        print("api_second_workbook_isolation: PASS")
        print("dashboard_workbook_hierarchy_and_counts: PASS")
        print("dashboard_coverage_canonical_execution: PASS")
    finally:
        shutil.rmtree(folder, ignore_errors=True)


if __name__ == "__main__": main()
