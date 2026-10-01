"""Focused SQL Test Case filtering and managed-file persistence checks."""

import logging
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from Core.automation_web_repository import SqlAutomationWeb, TestCasesWeb
from Core.test_case_repository import TestCaseRepository


class TemporaryDatabase:
    def __init__(self, path): self.db_path = Path(path)
    def get_connection(self):
        connection = sqlite3.connect(self.db_path)
        connection.execute("PRAGMA foreign_keys=ON")
        return connection


def main():
    project = Path(__file__).resolve().parents[1]
    root = Path(tempfile.mkdtemp(prefix="qaais-sql-storage-"))
    database = root / "metadata.db"
    shutil.copy2(project / "Database" / "metadata.db", database)
    try:
        repository = TestCaseRepository.__new__(TestCaseRepository)
        repository.db = TemporaryDatabase(database)
        repository.logger = logging.getLogger("sql-storage-test")
        repository.ensure_schema()
        connection = repository.db.get_connection(); connection.row_factory = sqlite3.Row
        fixture = connection.execute(
            """SELECT tf.id file_id,tf.test_case_version_id,tf.workbook_path
               FROM test_case_files tf JOIN test_cases tc
                 ON tc.test_case_version_id=tf.test_case_version_id
                AND tc.reviewed_workbook_path=tf.workbook_path
               GROUP BY tf.id HAVING COUNT(tc.id)>=3 ORDER BY tf.id LIMIT 1"""
        ).fetchone()
        assert fixture
        rows = connection.execute(
            "SELECT id FROM test_cases WHERE test_case_version_id=? AND reviewed_workbook_path=? ORDER BY id LIMIT 3",
            (fixture["test_case_version_id"], fixture["workbook_path"]),
        ).fetchall()
        assert len(rows) == 3
        sql_id, playwright_id, api_id = [row["id"] for row in rows]
        connection.execute("UPDATE test_cases SET execution='SQL' WHERE id=?", (sql_id,))
        connection.execute("UPDATE test_cases SET execution='Playwright' WHERE id=?", (playwright_id,))
        connection.execute("UPDATE test_cases SET execution='API' WHERE id=?", (api_id,))
        tc_folder = root / "TC-V2-Fixture"; tc_folder.mkdir()
        connection.execute(
            "UPDATE test_case_versions SET folder_path=?,version_number=2,source_file_id='Fixture-V1' WHERE id=?",
            (str(tc_folder), fixture["test_case_version_id"]),
        )
        connection.commit(); connection.close()

        listed = TestCasesWeb(repository).list_workspace(
            test_case_version_id=fixture["test_case_version_id"],
            test_case_file_id=fixture["file_id"], automation_type="SQL", limit=200,
        )["test_cases"]
        listed_ids = {row["id"] for row in listed}
        assert sql_id in listed_ids
        assert playwright_id not in listed_ids and api_id not in listed_ids
        assert all(row.get("execution") == "SQL" for row in listed)

        web = SqlAutomationWeb(repository)
        multiline = b"WITH values_cte AS (\n  SELECT 1 AS value\n)\nSELECT value FROM values_cte\n"
        result = web.import_raw_sql(
            multiline, "fixture.sql", fixture["test_case_version_id"],
            fixture["file_id"], sql_id,
        )
        path = Path(result["repository_path"])
        assert path.is_file() and path.suffix == ".sql"
        assert path.read_bytes() == multiline
        assert repository.get_test_case(sql_id)["sql_script_path"] == str(path)
        first_files = list(path.parent.iterdir())
        updated = multiline.replace(b"SELECT value", b"SELECT value + 1")
        web.import_raw_sql(updated, "renamed.sql", fixture["test_case_version_id"], fixture["file_id"], sql_id)
        assert path.read_bytes() == updated and list(path.parent.iterdir()) == first_files

        for rejected_id in (playwright_id, api_id):
            try:
                web.import_raw_sql(multiline, "forged.sql", fixture["test_case_version_id"], fixture["file_id"], rejected_id)
            except ValueError as error:
                assert "Execution value is SQL" in str(error)
            else:
                raise AssertionError("Non-SQL Test Case accepted")

        before = list(tc_folder.rglob("*"))
        try:
            web.import_raw_sql(multiline, "wrong.sql", fixture["test_case_version_id"], 999999, sql_id)
        except ValueError:
            pass
        assert list(tc_folder.rglob("*")) == before

        original_update = repository.update_sql_asset
        try:
            with patch.object(repository, "update_sql_asset", side_effect=sqlite3.OperationalError("forced DB failure")):
                web.import_raw_sql(multiline.replace(b"1", b"2"), "fixture.sql", fixture["test_case_version_id"], fixture["file_id"], sql_id)
        except sqlite3.OperationalError:
            pass
        assert path.read_bytes() == updated
        repository.update_sql_asset = original_update

        print("SQL-FILTER-01..05 PASS")
        print("SQL-STORAGE-01..08 PASS")
        print("SQL-PATH", path)
    finally:
        shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    main()
