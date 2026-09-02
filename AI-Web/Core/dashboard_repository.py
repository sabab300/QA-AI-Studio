"""Read-only, database-backed data source for the web dashboard."""

import sqlite3

from Config.settings import AI_MODE, LLM_MODEL
from Database.db_manager import DatabaseManager


class DashboardRepository:
    """Keeps dashboard queries small, resilient, and independent of UI data."""

    def __init__(self):
        self.db = DatabaseManager()

    def snapshot(self, recent_limit=20, activity_limit=20):
        conn = self.db.get_connection()
        conn.row_factory = self._dict_factory
        try:
            return {
                "metrics": self._metrics(conn),
                "recent_runs": self._recent_runs(conn, recent_limit),
                "recent_activity": self._recent_activity(conn, activity_limit),
            }
        finally:
            conn.close()

    @staticmethod
    def _dict_factory(cursor, row):
        return {column[0]: row[index] for index, column in enumerate(cursor.description)}

    @staticmethod
    def _has_table(conn, table_name):
        return conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table_name,)
        ).fetchone() is not None

    def _scalar(self, conn, query, default=0):
        if not query:
            return default
        row = conn.execute(query).fetchone()
        if row is None:
            return default
        return next(iter(row.values())) if isinstance(row, dict) else row[0]

    def _metrics(self, conn):
        has_knowledge = self._has_table(conn, "knowledge_items")
        has_domains = self._has_table(conn, "domains")
        has_modules = self._has_table(conn, "modules")
        has_cases = self._has_table(conn, "test_cases")

        return {
            "knowledge_items": self._scalar(conn, "SELECT COUNT(*) FROM knowledge_items") if has_knowledge else 0,
            "domains": self._scalar(conn, "SELECT COUNT(*) FROM domains") if has_domains else 0,
            "modules": self._scalar(conn, "SELECT COUNT(*) FROM modules") if has_modules else 0,
            "versions": self._scalar(conn, "SELECT COUNT(DISTINCT version) FROM knowledge_items WHERE COALESCE(version, '') <> ''") if has_knowledge else 0,
            "test_cases": self._scalar(conn, "SELECT COUNT(*) FROM test_cases") if has_cases else 0,
            "last_results": self._last_results(conn) if has_cases else {"Pass": 0, "Fail": 0, "Blocked": 0, "Not Run": 0},
            "ai_mode": AI_MODE,
            "llm_model": LLM_MODEL,
        }

    def _last_results(self, conn):
        counts = {"Pass": 0, "Fail": 0, "Blocked": 0, "Not Run": 0}
        for row in conn.execute("SELECT COALESCE(last_result, 'Not Run') AS result, COUNT(*) AS count FROM test_cases GROUP BY COALESCE(last_result, 'Not Run')"):
            result = row["result"] if isinstance(row, dict) else row[0]
            count = row["count"] if isinstance(row, dict) else row[1]
            if result in counts:
                counts[result] = count
        return counts

    def _recent_runs(self, conn, limit):
        if not self._has_table(conn, "test_cases"):
            return []
        return conn.execute(
            """
            SELECT id AS test_case_id, tc_number, domain, module, last_result AS result,
                   last_run_date AS run_at
            FROM test_cases
            WHERE last_run_date IS NOT NULL
            ORDER BY last_run_date DESC LIMIT ?
            """,
            (limit,),
        ).fetchall()

    def _recent_activity(self, conn, limit):
        if not self._has_table(conn, "audit_logs"):
            return []
        return conn.execute(
            """
            SELECT id, username, action, resource, detail, created_date
            FROM audit_logs ORDER BY id DESC LIMIT ?
            """,
            (limit,),
        ).fetchall()
