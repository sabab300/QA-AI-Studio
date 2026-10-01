"""Focused V6 stable Playwright identity/storage integration checks.

Runs only against a temporary copy of metadata.db and temporary TC folders.
"""

from io import BytesIO
import json
import logging
from pathlib import Path
import shutil
import sqlite3
import sys
import tempfile
from unittest.mock import patch

from openpyxl import Workbook

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from Core.automation_import_service import (
    AutomationImportService, PLAYWRIGHT_CONTENT_ALIASES, PLAYWRIGHT_CONTENT_LABEL,
    generate_playwright_json_template,
)
from Core.test_case_repository import TestCaseRepository
from Shared.central_repository import CentralRepository


class TemporaryDatabase:
    def __init__(self, path):
        self.db_path = Path(path)

    def get_connection(self):
        connection = sqlite3.connect(self.db_path)
        connection.execute("PRAGMA foreign_keys=ON")
        return connection


def mapping_bytes(test_case_id, script):
    book = Workbook()
    sheet = book.active
    sheet.append(["Test Case ID", "Playwright Script"])
    sheet.append([test_case_id, script])
    stream = BytesIO(); book.save(stream)
    return stream.getvalue()


def main():
    project = Path(__file__).resolve().parents[1]
    temp_root = Path(tempfile.mkdtemp(prefix="qaais-playwright-stable-"))
    database = temp_root / "metadata.db"
    shutil.copy2(project / "Database" / "metadata.db", database)
    try:
        repository = TestCaseRepository.__new__(TestCaseRepository)
        repository.db = TemporaryDatabase(database)
        repository.logger = logging.getLogger("playwright-stable-test")
        repository.ensure_schema()

        connection = repository.db.get_connection(); connection.row_factory = sqlite3.Row
        fixture = connection.execute(
            """SELECT tc.tc_id,v.source_knowledge_id
               FROM test_cases tc JOIN test_case_versions v ON v.id=tc.test_case_version_id
               WHERE tc.tc_id<>'' GROUP BY tc.tc_id,v.source_knowledge_id
               HAVING COUNT(DISTINCT tc.test_case_version_id)>=2
               ORDER BY COUNT(*) DESC LIMIT 1"""
        ).fetchone()
        assert fixture, "A two-version stable Test Case fixture is required."
        tc_id, source_id = fixture["tc_id"], fixture["source_knowledge_id"]
        versions = connection.execute(
            """SELECT DISTINCT v.* FROM test_case_versions v JOIN test_cases tc
               ON tc.test_case_version_id=v.id
               WHERE v.source_knowledge_id=? AND tc.tc_id=? ORDER BY v.version_number""",
            (source_id, tc_id),
        ).fetchall()
        for version in versions:
            folder = temp_root / f"TC-V{version['version_number']}-fixture"
            folder.mkdir()
            connection.execute(
                "UPDATE test_case_versions SET folder_path=? WHERE id=?",
                (str(folder), version["id"]),
            )
        connection.commit()
        rows = connection.execute(
            """SELECT tc.id,tc.test_case_version_id FROM test_cases tc
               JOIN test_case_versions v ON v.id=tc.test_case_version_id
               WHERE v.source_knowledge_id=? AND tc.tc_id=? ORDER BY tc.test_case_version_id""",
            (source_id, tc_id),
        ).fetchall()
        first_id, second_id = rows[0]["id"], rows[1]["id"]
        connection.close()

        script_a = "from playwright.sync_api import Page\n\ndef test_a(page: Page):\n    page.goto('https://example.test/a')\n"
        script_b = "from playwright.sync_api import Page\n\ndef test_b(page: Page):\n    page.goto('https://example.test/b')\n"
        uploaded_script = (
            "from playwright.sync_api import Page\n\n"
            "def test_uploaded(page: Page):\n"
            "    page.goto('https://example.test/uploaded')\n"
            "    page.get_by_role('button', name='Continue').click()\n"
        )
        repository.update_automation(first_id, "Playwright", script_a)
        assert repository.get_test_case(second_id)["automation_script"] == script_a  # PW-STABLE-01
        repository.update_automation(second_id, "Playwright", script_b)
        assert repository.get_test_case(first_id)["automation_script"] == script_b  # PW-STABLE-02
        assert repository.get_test_case(second_id)["automation_script"] == script_b
        repository.update_playwright_scripts_atomically([(first_id, uploaded_script)])
        assert repository.get_test_case(first_id)["uploaded_script"] == uploaded_script
        assert repository.get_test_case(second_id)["uploaded_script"] == uploaded_script
        assert repository.get_test_case(first_id)["automation_script"] == script_b

        connection = repository.db.get_connection(); connection.row_factory = sqlite3.Row
        latest = connection.execute(
            "SELECT * FROM test_case_versions WHERE source_knowledge_id=? ORDER BY version_number DESC LIMIT 1",
            (source_id,),
        ).fetchone()
        future_folder = temp_root / f"TC-V{latest['version_number'] + 1}-fixture"; future_folder.mkdir()
        connection.execute(
            """INSERT INTO test_case_versions
               (source_knowledge_id,source_file_id,version_number,folder_path,workbook_path,created_date)
               VALUES(?,?,?,?,?,datetime('now'))""",
            (source_id, latest["source_file_id"], latest["version_number"] + 1,
             str(future_folder), str(future_folder / "future.xlsx")),
        )
        future_version_id = connection.execute("SELECT last_insert_rowid()").fetchone()[0]
        original = dict(connection.execute("SELECT * FROM test_cases WHERE id=?", (first_id,)).fetchone())
        columns = [row[1] for row in connection.execute("PRAGMA table_info(test_cases)") if row[1] != "id"]
        original.update(test_case_version_id=future_version_id,
                        reviewed_workbook_path=str(future_folder / "future.xlsx"),
                        automation_script=None, recorded_script=None,
                        automation_type="None", execution="Manual")
        connection.execute(
            f"INSERT INTO test_cases ({','.join(columns)}) VALUES ({','.join('?' for _ in columns)})",
            [original.get(column) for column in columns],
        )
        future_id = connection.execute("SELECT last_insert_rowid()").fetchone()[0]
        connection.commit(); connection.close()
        assert repository.get_test_case(future_id)["automation_script"] == script_b  # PW-STABLE-03
        assert repository.get_test_case(future_id)["uploaded_script"] == uploaded_script
        repository.set_reviewed_workbook(
            [future_id], str(future_folder / "future.xlsx"), future_version_id,
        )

        connection = repository.db.get_connection()
        absent = connection.execute(
            "SELECT COUNT(*) FROM test_cases WHERE test_case_version_id=? AND tc_id='TC-ABSENT'",
            (future_version_id,),
        ).fetchone()[0]
        assert absent == 0  # PW-STABLE-04: stable lookup never creates missing cases.
        columns = [row[1] for row in connection.execute("PRAGMA table_info(test_cases)") if row[1] != "id"]
        new_case = dict(original); new_case.update(tc_id="TC-NEW-999", tc_number="TC-NEW-999")
        connection.execute(
            f"INSERT INTO test_cases ({','.join(columns)}) VALUES ({','.join('?' for _ in columns)})",
            [new_case.get(column) for column in columns],
        )
        new_id = connection.execute("SELECT last_insert_rowid()").fetchone()[0]
        connection.commit(); connection.close()
        assert not repository.get_test_case(new_id).get("automation_script")  # PW-STABLE-05

        # A no-op stable save also materializes a newly-created version folder.
        repository.update_automation(future_id, "Playwright", script_b)
        connection = repository.db.get_connection(); connection.row_factory = sqlite3.Row
        contexts = connection.execute(
            """SELECT DISTINCT v.* FROM test_case_versions v JOIN test_cases tc
               ON tc.test_case_version_id=v.id WHERE v.source_knowledge_id=? AND tc.tc_id=?""",
            (source_id, tc_id),
        ).fetchall(); connection.close()
        central = CentralRepository()
        expected_files = []
        uploaded_json_files = []
        for context in contexts:
            folder = central.build_automation_path(
                context["folder_path"], "Playwright", context["version_number"], context["source_file_id"]
            )
            expected = folder / (central.sanitize_segment(tc_id, "Test Case ID") + ".py")
            expected_files.append(expected)
            assert expected.read_text(encoding="utf-8") == script_b  # PW-STORAGE-01/03
            uploaded_json = folder / (central.sanitize_segment(tc_id, "Test Case ID") + ".uploaded.json")
            uploaded_json_files.append(uploaded_json)
            stored_mapping = json.loads(uploaded_json.read_text(encoding="utf-8"))
            assert stored_mapping == [{"Test Case ID": tc_id, "Script": uploaded_script}]
            assert not (folder / (central.sanitize_segment(tc_id, "Test Case ID") + ".uploaded.py")).exists()

        updated_uploaded_script = uploaded_script.replace("/uploaded", "/uploaded-v2")
        repository.update_playwright_scripts_atomically([(second_id, updated_uploaded_script)])
        for json_path in uploaded_json_files:
            assert json.loads(json_path.read_text(encoding="utf-8")) == [
                {"Test Case ID": tc_id, "Script": updated_uploaded_script}
            ]
        uploaded_script = updated_uploaded_script

        before = {path: path.read_bytes() for path in expected_files}
        connection = repository.db.get_connection()
        selected_file = connection.execute(
            "SELECT id FROM test_case_files WHERE test_case_version_id=? ORDER BY id LIMIT 1",
            (versions[0]["id"],),
        ).fetchone()
        connection.close()
        assert selected_file, "A persisted workbook file fixture is required."
        rejected = AutomationImportService(repository).validate(
            mapping_bytes("TC-UNKNOWN", "print('must not persist')"), "invalid.xlsx",
            versions[0]["id"], selected_file[0],
            PLAYWRIGHT_CONTENT_ALIASES, PLAYWRIGHT_CONTENT_LABEL,
        )
        assert not rejected.ok
        assert all(path.read_bytes() == before[path] for path in expected_files)  # PW-STORAGE-02

        connection = repository.db.get_connection()
        other = connection.execute(
            """SELECT tc.id FROM test_cases tc JOIN test_case_versions v
               ON v.id=tc.test_case_version_id
               WHERE v.source_knowledge_id=? AND tc.tc_id<>? AND tc.tc_id<>''
               ORDER BY tc.id LIMIT 1""", (source_id, tc_id),
        ).fetchone()
        connection.close()
        assert other, "A second Test Case is required for batch rollback."
        original_materialize = repository._materialize_playwright_script
        calls = {"count": 0}

        def fail_second(*args, **kwargs):
            calls["count"] += 1
            if calls["count"] == 2:
                raise OSError("forced physical storage failure")
            return original_materialize(*args, **kwargs)

        try:
            with patch.object(repository, "_materialize_playwright_script", fail_second):
                repository.update_playwright_scripts_atomically([
                    (first_id, "print('must roll back first')\n"),
                    (other[0], "print('must fail second')\n"),
                ])
            raise AssertionError("Forced physical failure was not raised.")
        except OSError as error:
            assert "forced physical" in str(error)
        assert repository.get_test_case(first_id)["automation_script"] == script_b
        assert repository.get_test_case(first_id)["uploaded_script"] == uploaded_script
        assert all(path.read_bytes() == before[path] for path in expected_files)

        other_uploaded = "print('second complete mapping')\n"
        repository.update_playwright_scripts_atomically([
            (first_id, uploaded_script), (other[0], other_uploaded),
        ])
        other_case = repository.get_test_case(other[0])
        assert other_case["uploaded_script"] == other_uploaded
        other_stem = central.sanitize_segment(other_case["tc_id"], "Test Case ID")
        other_json_files = list(temp_root.rglob(other_stem + ".uploaded.json"))
        assert other_json_files
        assert all(
            json.loads(path.read_text(encoding="utf-8"))
            == [{"Test Case ID": other_case["tc_id"], "Script": other_uploaded}]
            for path in other_json_files
        )

        assert TestCaseRepository._serialize_test_types({"test_types": ["Functional", "Positive"]}) == "Functional, Positive"
        assert TestCaseRepository._with_test_types({"test_type": "Functional, Positive"})["test_types"] == ["Functional", "Positive"]
        repository.update_test_case(first_id, {"test_types": ["Functional", "Positive"]})
        assert repository.get_test_case(first_id)["test_types"] == ["Functional", "Positive"]
        repository.update_test_case(first_id, {"test_types": ["Functional", "Negative"]})
        assert repository.get_test_case(first_id)["test_types"] == ["Functional", "Negative"]

        template_bytes = generate_playwright_json_template(tc_id, uploaded_script)
        template_rows = json.loads(template_bytes.decode("utf-8"))
        assert template_rows == [{"Test Case ID": tc_id, "Script": uploaded_script}]
        round_trip = AutomationImportService(repository).validate(
            template_bytes, "playwright-script-template.json",
            versions[0]["id"], selected_file[0],
            PLAYWRIGHT_CONTENT_ALIASES, PLAYWRIGHT_CONTENT_LABEL,
        )
        assert round_trip.ok, round_trip.to_dict()
        assert TestCaseRepository._canonical_script_text(round_trip.valid_rows[0]["content"]) \
            == TestCaseRepository._canonical_script_text(uploaded_script)
        print("PW-STORE example JSON:", uploaded_json_files[0])
        print(
            "PW-STORE integrity:", tc_id,
            len(uploaded_script),
            len(repository.get_test_case(first_id)["uploaded_script"]),
            len(json.loads(uploaded_json_files[0].read_text(encoding="utf-8"))[0]["Script"]),
        )
        print("PW-STABLE-01..05 PASS")
        print("PW-STORAGE-01..03 PASS")
        print("PW-UPLOADED-01..04 separate DB/JSON-only/cross-version/rollback PASS")
        print("PW-JSON-01..05 real TC ID + multiline JSON round-trip PASS")
        print("QA-EDIT-01..03 backend save/reopen PASS")
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)


if __name__ == "__main__":
    main()
