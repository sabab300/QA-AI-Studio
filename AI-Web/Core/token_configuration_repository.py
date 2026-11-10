"""Durable templates for API Automation token requests."""

from __future__ import annotations

from datetime import datetime
import json
import sqlite3

from Database.db_manager import DatabaseManager


class TokenConfigurationRepository:
    _REQUEST_KEYS = {
        "method", "url", "query_params", "path_params", "headers", "auth",
        "body", "body_mode", "body_params", "timeout_seconds", "verify_ssl",
    }
    _METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"}

    def __init__(self):
        self.db = DatabaseManager()
        self._ensure_schema()

    @staticmethod
    def _dict_factory(cursor, row):
        return {column[0]: row[index] for index, column in enumerate(cursor.description)}

    def _ensure_schema(self):
        connection = self.db.get_connection()
        try:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS api_token_configurations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    token_name TEXT NOT NULL COLLATE NOCASE UNIQUE,
                    request_config_json TEXT NOT NULL,
                    extract_type TEXT NOT NULL DEFAULT 'json_path',
                    extract_path TEXT NOT NULL DEFAULT '',
                    save_to_variable TEXT NOT NULL,
                    created_date TEXT NOT NULL,
                    modified_date TEXT NOT NULL
                )
                """
            )
            connection.commit()
        finally:
            connection.close()

    @classmethod
    def _normalize(cls, token_name, request_config, extract_type, extract_path, save_to_variable):
        name = str(token_name or "").strip()
        if not name:
            raise ValueError("Token Name is required.")
        config = {
            key: value for key, value in dict(request_config or {}).items()
            if key in cls._REQUEST_KEYS
        }
        method = str(config.get("method") or "POST").strip().upper()
        if method not in cls._METHODS:
            raise ValueError("A supported HTTP Method is required.")
        config["method"] = method
        config["url"] = str(config.get("url") or "").strip()
        if not config["url"]:
            raise ValueError("Request URL is required.")
        variable = str(save_to_variable or "").strip()
        if not variable:
            raise ValueError("Save to variable is required.")
        extraction = str(extract_type or "json_path").strip()
        if extraction not in {"json_path", "header"}:
            raise ValueError("Extract type must be json_path or header.")
        path = str(extract_path or "").strip()
        if extraction == "header" and not path:
            raise ValueError("A response header name is required for header extraction.")

        if config.get("body_mode") == "raw_json" and str(config.get("body") or "").strip():
            try:
                json.loads(config["body"])
            except (TypeError, ValueError) as error:
                raise ValueError(f"Body must be valid JSON before it can be saved: {error}") from error
        return name, config, extraction, path, variable

    @staticmethod
    def _serialize(row):
        item = dict(row)
        item["request_config"] = json.loads(item.pop("request_config_json") or "{}")
        return item

    def list_all(self):
        connection = self.db.get_connection()
        connection.row_factory = self._dict_factory
        try:
            rows = connection.execute(
                "SELECT * FROM api_token_configurations ORDER BY token_name COLLATE NOCASE,id"
            ).fetchall()
            return [self._serialize(row) for row in rows]
        finally:
            connection.close()

    def create(self, token_name, request_config, extract_type, extract_path, save_to_variable):
        name, config, extraction, path, variable = self._normalize(
            token_name, request_config, extract_type, extract_path, save_to_variable
        )
        now = datetime.now().isoformat()
        connection = self.db.get_connection()
        try:
            cursor = connection.execute(
                """INSERT INTO api_token_configurations
                   (token_name,request_config_json,extract_type,extract_path,
                    save_to_variable,created_date,modified_date)
                   VALUES(?,?,?,?,?,?,?)""",
                (name, json.dumps(config, sort_keys=True), extraction, path, variable, now, now),
            )
            connection.commit()
            config_id = cursor.lastrowid
        except sqlite3.IntegrityError as error:
            connection.rollback()
            raise ValueError(f"A token configuration named '{name}' already exists.") from error
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()
        return self.get(config_id)

    def get(self, config_id):
        connection = self.db.get_connection()
        connection.row_factory = self._dict_factory
        try:
            row = connection.execute(
                "SELECT * FROM api_token_configurations WHERE id=?", (int(config_id),)
            ).fetchone()
            return self._serialize(row) if row else None
        finally:
            connection.close()

    def update(self, config_id, token_name, request_config, extract_type, extract_path, save_to_variable):
        if not self.get(config_id):
            raise ValueError("Token configuration was not found.")
        name, config, extraction, path, variable = self._normalize(
            token_name, request_config, extract_type, extract_path, save_to_variable
        )
        connection = self.db.get_connection()
        try:
            connection.execute(
                """UPDATE api_token_configurations SET token_name=?,request_config_json=?,
                   extract_type=?,extract_path=?,save_to_variable=?,modified_date=? WHERE id=?""",
                (name, json.dumps(config, sort_keys=True), extraction, path, variable,
                 datetime.now().isoformat(), int(config_id)),
            )
            connection.commit()
        except sqlite3.IntegrityError as error:
            connection.rollback()
            raise ValueError(f"A token configuration named '{name}' already exists.") from error
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()
        return self.get(config_id)

    def delete(self, config_id):
        connection = self.db.get_connection()
        try:
            deleted = connection.execute(
                "DELETE FROM api_token_configurations WHERE id=?", (int(config_id),)
            ).rowcount
            connection.commit()
            return bool(deleted)
        finally:
            connection.close()
