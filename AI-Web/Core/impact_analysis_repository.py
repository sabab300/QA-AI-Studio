"""
QA AI Studio
Impact Analysis Repository

Version: 1.0

QA-AI-STUDIO-IMPACT-ANALYSIS-V1: durable, queryable-by-identity storage
for Impact Analysis results. Follows the exact same
CREATE TABLE IF NOT EXISTS + column-migration idiom as
Core/memory_manager.py's MemoryManager (same SQLiteManager, same
metadata.db) rather than introducing a new persistence layer.

Not reusing ai_memory directly: that table's shape (prompt/response/
intent/domain/module/knowledge_name/confidence) is built for one
question -> one text answer. Impact Analysis's result is a source
identity, a target identity, a findings array, and coverage counts --
squeezing that into ai_memory.response as an opaque blob would make it
unqueryable by source/target identity later. This table stores the
identity fields as real columns (mirroring qa_generation_jobs' own
payload_json/result_json column discipline in
Core/qa_engineering_web_repository.py) plus one JSON column for the
full structured findings.
"""

import json
from datetime import datetime

from Core.sqlite_manager import SQLiteManager
from Core.logger import Logger


class ImpactAnalysisRepository:

    TABLE_NAME = "ai_impact_analysis"

    def __init__(self):

        self.logger = Logger.get_logger()
        self.database = SQLiteManager()

        self._create_table()
        self._migrate()

    # --------------------------------------------------
    # Create Table
    # --------------------------------------------------

    def _create_table(self):

        query = f"""
        CREATE TABLE IF NOT EXISTS {self.TABLE_NAME}
        (
            id INTEGER PRIMARY KEY AUTOINCREMENT,

            session_id TEXT,

            source_domain TEXT,
            source_module TEXT,
            source_knowledge_name TEXT,
            source_version TEXT,
            source_document_type TEXT,
            source_file_name TEXT,

            target_domain TEXT,
            target_module TEXT,

            question TEXT,

            confirmed_count INTEGER,
            potential_count INTEGER,
            undocumented_count INTEGER,

            result_json TEXT,

            execution_time REAL,

            created_at TEXT
        )
        """

        self.database.execute_non_query(query)

    # --------------------------------------------------
    # Database Migration
    # --------------------------------------------------

    def _migrate(self):

        columns = {
            "session_id": "TEXT",
            "source_domain": "TEXT",
            "source_module": "TEXT",
            "source_knowledge_name": "TEXT",
            "source_version": "TEXT",
            "source_document_type": "TEXT",
            "source_file_name": "TEXT",
            "target_domain": "TEXT",
            "target_module": "TEXT",
            "question": "TEXT",
            "confirmed_count": "INTEGER",
            "potential_count": "INTEGER",
            "undocumented_count": "INTEGER",
            "result_json": "TEXT",
            "execution_time": "REAL",
        }

        for column, datatype in columns.items():

            if not self.database.column_exists(self.TABLE_NAME, column):

                self.logger.info(
                    f"Adding impact analysis column: {column}"
                )

                self.database.execute_non_query(
                    f"""
                    ALTER TABLE {self.TABLE_NAME}
                    ADD COLUMN {column} {datatype}
                    """
                )

    # --------------------------------------------------
    # Save
    # --------------------------------------------------

    def save(
        self,
        session_id,
        source,
        target,
        question,
        result,
        execution_time=0,
    ):

        findings = result.get("findings", []) if isinstance(result, dict) else []

        confirmed = sum(1 for f in findings if f.get("classification") == "CONFIRMED IMPACT")
        potential = sum(1 for f in findings if f.get("classification") == "POTENTIAL IMPACT")
        undocumented = sum(1 for f in findings if f.get("classification") == "NO DOCUMENTED IMPACT")

        query = f"""
        INSERT INTO {self.TABLE_NAME}
        (
            session_id,
            source_domain, source_module, source_knowledge_name,
            source_version, source_document_type, source_file_name,
            target_domain, target_module,
            question,
            confirmed_count, potential_count, undocumented_count,
            result_json,
            execution_time,
            created_at
        )
        VALUES
        (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """

        return self.database.execute_insert(
            query,
            (
                session_id,
                source.get("domain"), source.get("module"), source.get("knowledge_name"),
                source.get("version"), source.get("document_type"), source.get("source_document"),
                target.get("domain"), target.get("module"),
                question,
                confirmed, potential, undocumented,
                json.dumps(result),
                execution_time,
                datetime.now().isoformat(),
            ),
        )

    # --------------------------------------------------
    # Recent
    # --------------------------------------------------

    def get_recent(self, session_id="default", limit=10):

        query = f"""
        SELECT
            id, source_domain, source_module, source_knowledge_name,
            source_version, source_file_name, target_domain, target_module,
            question, confirmed_count, potential_count, undocumented_count,
            result_json, created_at
        FROM {self.TABLE_NAME}
        WHERE session_id=?
        ORDER BY id DESC
        LIMIT ?
        """

        rows = self.database.fetch_all(query, (session_id, limit))

        results = []

        for row in rows:
            results.append({
                "id": row[0],
                "source_domain": row[1],
                "source_module": row[2],
                "source_knowledge_name": row[3],
                "source_version": row[4],
                "source_file_name": row[5],
                "target_domain": row[6],
                "target_module": row[7],
                "question": row[8],
                "confirmed_count": row[9],
                "potential_count": row[10],
                "undocumented_count": row[11],
                "result": json.loads(row[12]) if row[12] else None,
                "created_at": row[13],
            })

        return results

    # --------------------------------------------------
    # Get One
    # --------------------------------------------------

    def get(self, analysis_id):

        row = self.database.fetch_one(
            f"SELECT result_json FROM {self.TABLE_NAME} WHERE id=?",
            (analysis_id,),
        )

        if not row or not row[0]:
            return None

        return json.loads(row[0])

    def get_for_session(self, analysis_id, session_id):
        """Read an owned result for export; never expose another user's history."""
        row = self.database.fetch_one(
            f"SELECT result_json FROM {self.TABLE_NAME} WHERE id=? AND session_id=?",
            (analysis_id, session_id),
        )
        return json.loads(row[0]) if row and row[0] else None
