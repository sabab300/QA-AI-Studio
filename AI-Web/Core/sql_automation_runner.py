"""Read-only SQL validation, execution, and result assertions."""

import re
import sqlite3
import time
from pathlib import Path


class SqlValidationError(ValueError):
    pass


_BLOCKED = re.compile(
    r"\b(insert|update|delete|merge|replace|upsert|create|alter|drop|truncate|attach|detach|vacuum|reindex|grant|revoke|execute|exec|call|copy|load|pragma)\b",
    re.IGNORECASE,
)


def validate_readonly_sql(sql):
    cleaned = str(sql or "").strip()
    cleaned = re.sub(r"/\*.*?\*/", " ", cleaned, flags=re.DOTALL)
    cleaned = re.sub(r"--[^\r\n]*", " ", cleaned).strip()
    if cleaned.endswith(";"):
        cleaned = cleaned[:-1].rstrip()
    if not cleaned:
        raise SqlValidationError("SQL query is required.")
    if ";" in cleaned:
        raise SqlValidationError("Only one SQL statement is allowed.")
    if not re.match(r"^(select|with)\b", cleaned, re.IGNORECASE):
        raise SqlValidationError("Only SELECT or read-only WITH queries are allowed.")
    blocked = _BLOCKED.search(cleaned)
    if blocked:
        raise SqlValidationError(f"Read-only SQL cannot contain {blocked.group(1).upper()}.")
    return cleaned


class SqlAutomationRunner:
    def test_connection(self, profile):
        connection = None
        try:
            connection = self._connect(profile)
            return {"success": True, "message": "Connection succeeded.", "error": None}
        except Exception as error:
            return {"success": False, "message": None, "error": str(error)}
        finally:
            if connection is not None:
                connection.close()

    def execute_query(self, profile, sql):
        cleaned = validate_readonly_sql(sql)
        started = time.monotonic()
        connection = None
        try:
            connection = self._connect(profile)
            cursor = connection.cursor()
            cursor.execute(cleaned)
            columns = [item[0] for item in (cursor.description or [])]
            max_rows = self._positive_int(profile.get("sql_max_rows"), 200, maximum=10_000)
            rows = cursor.fetchmany(max_rows + 1)
            truncated = len(rows) > max_rows
            rows = rows[:max_rows]
            return {
                "success": True,
                "columns": columns,
                "rows": [list(row) for row in rows],
                "row_count": len(rows),
                "truncated": truncated,
                "duration_seconds": round(time.monotonic() - started, 4),
                "error": None,
            }
        except Exception as error:
            return {
                "success": False, "columns": [], "rows": [], "row_count": 0,
                "truncated": False,
                "duration_seconds": round(time.monotonic() - started, 4),
                "error": str(error),
            }
        finally:
            if connection is not None:
                connection.close()

    def evaluate_assertion(self, assertion_type, expected, column, result):
        rows = result.get("rows") or []
        columns = result.get("columns") or []
        kind = assertion_type or "row_exists"
        actual = None
        passed = False
        if kind == "row_exists":
            passed = bool(rows)
        elif kind == "no_rows":
            passed = not rows
        elif kind == "row_count_equals":
            actual = result.get("row_count", len(rows))
            passed = str(actual) == str(expected)
        elif kind == "scalar_equals":
            actual = rows[0][0] if rows and rows[0] else None
            passed = str(actual) == str(expected)
        elif kind == "value_equals":
            if not column or column not in columns:
                return {"outcome": "Fail", "message": f"Column '{column or ''}' was not returned."}
            index = columns.index(column)
            actual = rows[0][index] if rows else None
            passed = str(actual) == str(expected)
        else:
            return {"outcome": "Fail", "message": f"Unsupported assertion type: {kind}"}
        detail = f" ({actual!r} compared with {expected!r})" if actual is not None else ""
        return {"outcome": "Pass" if passed else "Fail", "message": f"{kind}: {'passed' if passed else 'failed'}{detail}"}

    def _connect(self, profile):
        db_type = str(profile.get("sql_db_type") or "sqlite").lower()
        timeout = self._positive_int(profile.get("sql_timeout_seconds"), 30, maximum=300)
        if db_type == "sqlite":
            raw_path = str(profile.get("sql_sqlite_path") or "").strip()
            if not raw_path:
                raise ValueError("SQLite file path is required.")
            path = Path(raw_path).expanduser().resolve()
            if not path.is_file():
                raise ValueError(f"SQLite database does not exist: {path}")
            return sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True, timeout=timeout)
        if db_type == "postgresql":
            try:
                import psycopg
            except ImportError as error:
                raise RuntimeError("PostgreSQL execution requires the optional 'psycopg' package.") from error
            return psycopg.connect(host=profile.get("sql_host"), port=profile.get("sql_port") or 5432, dbname=profile.get("sql_database"), user=profile.get("sql_username"), password=profile.get("sql_password"), connect_timeout=timeout)
        if db_type == "mysql":
            try:
                import mysql.connector
            except ImportError as error:
                raise RuntimeError("MySQL execution requires the optional 'mysql-connector-python' package.") from error
            return mysql.connector.connect(host=profile.get("sql_host"), port=int(profile.get("sql_port") or 3306), database=profile.get("sql_database"), user=profile.get("sql_username"), password=profile.get("sql_password"), connection_timeout=timeout)
        if db_type == "sqlserver":
            try:
                import pyodbc
            except ImportError as error:
                raise RuntimeError("SQL Server execution requires the optional 'pyodbc' package.") from error
            connection_string = f"DRIVER={{ODBC Driver 18 for SQL Server}};SERVER={profile.get('sql_host')},{profile.get('sql_port') or 1433};DATABASE={profile.get('sql_database')};UID={profile.get('sql_username')};PWD={profile.get('sql_password')};TrustServerCertificate=yes"
            return pyodbc.connect(connection_string, timeout=timeout)
        raise ValueError(f"Unsupported SQL database type: {db_type}")

    @staticmethod
    def _positive_int(value, default, maximum):
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            parsed = default
        return min(max(parsed, 1), maximum)
