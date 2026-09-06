"""Persistent, masked configuration for SQL Automation target databases."""

import json
from pathlib import Path

from Core.logger import Logger


SUPPORTED_DB_TYPES = ("sqlite", "postgresql", "mysql", "sqlserver")
CONFIG_PATH = Path(__file__).resolve().parent.parent / "Config" / "sql_environment_config.json"
DEFAULT_CONFIG = {
    "sql_db_type": "sqlite",
    "sql_sqlite_path": "",
    "sql_host": "",
    "sql_port": "",
    "sql_database": "",
    "sql_username": "",
    "sql_password": "",
    "sql_extra_params": {},
    "sql_timeout_seconds": "30",
    "sql_max_rows": "200",
    "sql_notes": "",
}
_SECRET_FIELDS = {"sql_password"}
_MASK = "********"


class SqlEnvironmentConfig:
    def __init__(self):
        self.logger = Logger.get_logger()
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)

    def load(self):
        if not CONFIG_PATH.exists():
            return dict(DEFAULT_CONFIG)
        try:
            with CONFIG_PATH.open("r", encoding="utf-8") as handle:
                loaded = json.load(handle)
            result = dict(DEFAULT_CONFIG)
            if isinstance(loaded, dict):
                result.update(loaded)
            return result
        except Exception:
            self.logger.exception("Could not read SQL environment configuration; using defaults.")
            return dict(DEFAULT_CONFIG)

    def save(self, **fields):
        unknown = set(fields) - set(DEFAULT_CONFIG)
        if unknown:
            raise ValueError(f"Unknown SQL environment field(s): {', '.join(sorted(unknown))}")
        current = self.load()
        for key, value in fields.items():
            if key in _SECRET_FIELDS and value in (None, "", _MASK):
                continue
            if value is not None:
                current[key] = value
        db_type = str(current.get("sql_db_type") or "").lower()
        if db_type not in SUPPORTED_DB_TYPES:
            raise ValueError(f"Unsupported SQL database type: {db_type or '(blank)'}")
        current["sql_db_type"] = db_type
        with CONFIG_PATH.open("w", encoding="utf-8") as handle:
            json.dump(current, handle, indent=2)
        return current

    def get_masked(self):
        result = self.load()
        for key in _SECRET_FIELDS:
            result[key] = _MASK if result.get(key) else ""
        result["ready"] = self._is_ready(result)
        return result

    def update_masked(self, fields):
        return self.save(**dict(fields or {}))

    @staticmethod
    def _is_ready(profile):
        if profile.get("sql_db_type") == "sqlite":
            return bool(str(profile.get("sql_sqlite_path") or "").strip())
        return bool(profile.get("sql_host") and profile.get("sql_database"))
