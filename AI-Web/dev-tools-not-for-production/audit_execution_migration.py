"""Audit and simulate the canonical test_cases.execution migration.

Never mutates metadata.db. A temporary copy is migrated and verified.
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path
import shutil
import sqlite3
import tempfile


ALIASES = {
    "manual": "Manual", "manual execution": "Manual",
    "playwright": "Playwright", "api": "API", "api automation": "API",
    "sql": "SQL", "sql automation": "SQL", "automatable": None, "": None,
}


def normalized(value):
    text = str(value or "").strip()
    return ALIASES.get(text.casefold(), f"UNKNOWN:{text}")


def audit(connection):
    rows = connection.execute(
        "SELECT id, execution_type, execution_tool FROM test_cases ORDER BY id"
    ).fetchall()
    categories, type_values, tool_values = Counter(), Counter(), Counter()
    conflicts, unknown = [], []
    resolved = {}
    for row_id, legacy_type, legacy_tool in rows:
        legacy_type = str(legacy_type or "").strip()
        legacy_tool = str(legacy_tool or "").strip()
        type_values[legacy_type] += 1
        tool_values[legacy_tool] += 1
        type_value, tool_value = normalized(legacy_type), normalized(legacy_tool)
        if str(type_value).startswith("UNKNOWN") or str(tool_value).startswith("UNKNOWN"):
            unknown.append((row_id, legacy_type, legacy_tool))
        if not legacy_type and not legacy_tool:
            categories["both_null"] += 1; value = None
        elif legacy_type and not legacy_tool:
            categories["only_execution_type"] += 1; value = type_value
        elif legacy_tool and not legacy_type:
            categories["only_execution_tool"] += 1; value = tool_value
        elif type_value == tool_value:
            categories["both_same"] += 1; value = type_value
        elif legacy_type.casefold() == "automatable" and tool_value in {"Playwright", "API", "SQL"}:
            categories["both_compatible"] += 1; value = tool_value
        else:
            categories["both_conflicting"] += 1
            conflicts.append((row_id, legacy_type, legacy_tool, type_value, tool_value))
            value = None
        resolved[row_id] = value
    return rows, resolved, categories, type_values, tool_values, conflicts, unknown


def simulate(source):
    temporary = Path(tempfile.mkdtemp(prefix="qaais-execution-migration-"))
    target = temporary / "metadata.db"
    shutil.copy2(source, target)
    connection = sqlite3.connect(target)
    try:
        before = connection.execute("SELECT COUNT(*) FROM test_cases").fetchone()[0]
        baseline_foreign_keys = connection.execute("PRAGMA foreign_key_check").fetchall()
        rows, resolved, categories, type_values, tool_values, conflicts, unknown = audit(connection)
        if conflicts or unknown:
            raise RuntimeError(f"Unresolved conflicts={conflicts!r}; unknown={unknown!r}")
        connection.execute("BEGIN IMMEDIATE")
        columns = {row[1] for row in connection.execute("PRAGMA table_info(test_cases)")}
        if "execution" not in columns:
            connection.execute("ALTER TABLE test_cases ADD COLUMN execution TEXT")
        connection.executemany(
            "UPDATE test_cases SET execution=? WHERE id=?",
            [(value, row_id) for row_id, value in resolved.items()],
        )
        connection.execute("ALTER TABLE test_cases DROP COLUMN execution_type")
        connection.execute("ALTER TABLE test_cases DROP COLUMN execution_tool")
        connection.commit()
        after = connection.execute("SELECT COUNT(*) FROM test_cases").fetchone()[0]
        final_columns = {row[1] for row in connection.execute("PRAGMA table_info(test_cases)")}
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        foreign_keys = connection.execute("PRAGMA foreign_key_check").fetchall()
        counts = dict(connection.execute(
            "SELECT COALESCE(execution, '<NULL>'), COUNT(*) FROM test_cases GROUP BY execution"
        ).fetchall())
        assert before == after
        assert "execution" in final_columns
        assert "execution_type" not in final_columns and "execution_tool" not in final_columns
        assert integrity == "ok"
        assert foreign_keys == baseline_foreign_keys
        return {
            "before": before, "after": after, "categories": dict(categories),
            "execution_type_values": dict(type_values),
            "execution_tool_values": dict(tool_values), "execution_counts": counts,
            "conflicts": conflicts, "unknown": unknown, "integrity": integrity,
            "foreign_key_violations_before": len(baseline_foreign_keys),
            "foreign_key_violations_after": len(foreign_keys),
            "final_columns": sorted(final_columns),
        }
    finally:
        connection.close()
        shutil.rmtree(temporary, ignore_errors=True)


if __name__ == "__main__":
    database = Path(__file__).resolve().parents[1] / "Database" / "metadata.db"
    for key, value in simulate(database).items():
        print(f"{key}: {value}")
