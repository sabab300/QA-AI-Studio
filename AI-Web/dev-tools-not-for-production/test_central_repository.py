"""Focused non-destructive checks for shared central repository behavior."""

import json
import sqlite3
import tempfile
from pathlib import Path

import sys

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

import Shared.central_repository as central_module
from Shared.central_repository import CentralRepository


def run_checks():
    with tempfile.TemporaryDirectory(prefix="qaais-central-test-") as temporary:
        sandbox = Path(temporary)
        old_root = sandbox / "old"
        new_root = sandbox / "new"
        config = sandbox / "central_repository.json"
        old_root.mkdir()
        (old_root / "managed.txt").write_text("managed", encoding="utf-8")
        central_module.CONFIG_FILE = config
        central_module.CONFIG_DIR = config.parent
        central_module.DEFAULT_ROOT = old_root
        central_module.PROJECT_ROOT = sandbox / "project-without-database"

        repository = CentralRepository()
        source_id = repository.create_source_file_id("CRF-2633-21022023.pdf")
        source_path = repository.build_source_path(
            domain="PSW Core", module="SD Export",
            knowledge_name="SD Export Sea FCL", version="1.0",
            document_type="CRF", source_type="Files",
            source_file_id=source_id,
        )
        assert [part.split("-", 1)[0] for part in source_path.parts[-7:-1]] == ["D", "M", "K", "V", "DT", "ST"]
        assert source_path.name.startswith("SD-")
        assert CentralRepository.sanitize_segment('A:B/C*D?') == "A_B_C_D_"

        result = CentralRepository.configure(new_root, migrate=True)
        assert result["migrated"] and result["files_migrated"] == 1
        assert (new_root / "managed.txt").read_text(encoding="utf-8") == "managed"
        assert (old_root / "managed.txt").is_file()  # old data retained
        assert json.loads(config.read_text(encoding="utf-8"))["path"] == str(new_root.resolve())

        tc_path = repository.build_test_case_version_path(source_path, 2, source_id)
        assert tc_path.name.startswith("TC-V2-")
        automation_path = repository.build_automation_path(tc_path, "Playwright", 2, source_id)
        assert automation_path.name == f"Playwright-TC-V2-{source_id}"

        sys.path.insert(0, str(PROJECT_ROOT / "AI-Web"))
        from Core.repository_manager import RepositoryManager
        upload = sandbox / "CRF-2633-21022023.pdf"
        upload.write_bytes(b"source")
        stored = RepositoryManager().save_file(
            str(upload), "PSW Core", "SD Export", "SD Export Sea FCL",
            "1.0", "CRF", "Files",
        )
        stored_path = Path(stored["repository_path"])
        assert stored_path.is_file()
        assert stored_path.parent.name.startswith("SD-")
        relative = Path(stored["repository_relative_path"])
        assert not relative.is_absolute() and relative.name == upload.name

    database = PROJECT_ROOT / "AI-Web" / "Database" / "metadata.db"
    connection = sqlite3.connect(database)
    knowledge_columns = {row[1] for row in connection.execute("PRAGMA table_info(knowledge_items)")}
    test_case_columns = {row[1] for row in connection.execute("PRAGMA table_info(test_cases)")}
    tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    connection.close()
    assert {"source_file_id", "repository_relative_path"} <= knowledge_columns
    assert "test_case_version_id" in test_case_columns
    assert "test_case_versions" in tables


if __name__ == "__main__":
    run_checks()
    print("Central repository checks: PASS")
