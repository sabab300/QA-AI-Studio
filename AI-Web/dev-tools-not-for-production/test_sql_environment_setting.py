"""Focused SQL environment-setting checks without touching saved user config."""

import sqlite3
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from Core.sql_automation_runner import SqlAutomationRunner
from Core.sql_environment_config import SqlEnvironmentConfig


def main():
    with tempfile.TemporaryDirectory(prefix="qaais-sql-env-") as temp:
        root = Path(temp)
        missing = root / "missing.db"
        runner = SqlAutomationRunner()
        failed = runner.test_connection({"sql_db_type": "sqlite", "sql_sqlite_path": str(missing)})
        assert not failed["success"] and "does not exist" in failed["error"]
        assert not missing.exists()

        database = root / "valid.db"
        connection = sqlite3.connect(database)
        connection.execute("CREATE TABLE fixture (id INTEGER PRIMARY KEY, value TEXT)")
        connection.execute("INSERT INTO fixture(value) VALUES ('ready')")
        connection.commit(); connection.close()
        profile = {
            "sql_db_type": "sqlite", "sql_sqlite_path": str(database),
            "sql_timeout_seconds": "30", "sql_max_rows": "200",
        }
        assert runner.test_connection(profile)["success"]
        selected = runner.execute_query(profile, "SELECT id,value FROM fixture")
        assert selected["success"] and selected["rows"] == [[1, "ready"]]

        config_path = root / "sql_environment_config.json"
        with patch("Core.sql_environment_config.CONFIG_PATH", config_path):
            config = SqlEnvironmentConfig()
            config.save(**profile)
            reopened = SqlEnvironmentConfig().load()
            assert reopened["sql_sqlite_path"] == str(database)
            assert reopened["sql_db_type"] == "sqlite"

    print("SQL-01 nonexistent path fails clearly PASS")
    print("SQL-02 connection test creates no database PASS")
    print("SQL-03 existing SQLite connection PASS")
    print("SQL-04/05 save and reopen persistence PASS")
    print("SQL-06 read-only SELECT PASS")


if __name__ == "__main__":
    main()
