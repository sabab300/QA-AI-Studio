# Create: AI/migrate_phase1_consistency.py
#
# One-time migration script for Phase 1 (metadata/database
# consistency). Run this ONCE after installing all the other Phase
# 1 files (schema.py, db_manager.py, metadata_manager.py,
# test_case_repository.py, main.py) and after deleting
# schema_v3.py + db_manager_v3.py.
#
# HOW TO RUN:
#   1) Open a terminal
#   2) cd into your AI folder (same place as main.py)
#   3) Activate your venv
#   4) Run:  python migrate_phase1_consistency.py
#
# Safe to run more than once — every step checks before it acts and
# skips anything already done. It does NOT delete any of your
# knowledge, test cases, or scripts — it only adds/fixes
# relationships between existing rows.
#
# BACK UP Database/metadata.db before running, as routine good
# practice before any migration — copy the file somewhere safe.

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from Database.db_manager import DatabaseManager
from Core.metadata_manager import MetadataManager


def step_1_initialize_schema():

    print("Step 1: Ensuring all tables and columns exist...")

    DatabaseManager().initialize_database()

    # test_cases is a separate, self-initializing table (owned by
    # TestCaseRepository, not the main schema) — just instantiating
    # it runs its own ensure_schema(), which is what actually adds
    # domain_id/module_id there. Without this, Step 2 below would
    # try to update a column on test_cases that doesn't exist yet.
    from Core.test_case_repository import TestCaseRepository

    TestCaseRepository()

    print("  Done.")


def step_2_rebuild_modules_with_constraint():

    print()
    print("Step 2: Adding UNIQUE(domain_id, name) to modules table...")

    db = DatabaseManager()

    conn = db.get_connection()

    cursor = conn.cursor()

    # Does modules already have the constraint? Check by trying to
    # find its CREATE statement in sqlite_master.
    cursor.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='modules'"
    )

    row = cursor.fetchone()

    if row and "UNIQUE(domain_id, name)" in (row[0] or ""):

        print("  Already has the constraint, skipping.")

        conn.close()

        return

    # De-duplicate first: if two module rows share the same
    # (domain_id, name), keep the OLDEST (lowest id), and remember
    # a mapping so we can repoint anything referencing the newer
    # duplicate's id before we drop it.
    cursor.execute(
        """
        SELECT domain_id, name, GROUP_CONCAT(id)
        FROM modules
        GROUP BY domain_id, name
        HAVING COUNT(*) > 1
        """
    )

    duplicate_groups = cursor.fetchall()

    id_remap = {}

    for domain_id, name, id_list in duplicate_groups:

        ids = sorted(int(x) for x in id_list.split(","))

        keep_id = ids[0]

        for duplicate_id in ids[1:]:

            id_remap[duplicate_id] = keep_id

        print(
            f"  Found duplicate module '{name}' under domain_id "
            f"{domain_id}: keeping id {keep_id}, merging "
            f"{ids[1:]} into it."
        )

    # Repoint knowledge_items/test_cases that referenced a
    # duplicate module_id, onto the one we're keeping.
    for old_id, new_id in id_remap.items():

        cursor.execute(
            "UPDATE knowledge_items SET module_id=? WHERE module_id=?",
            (new_id, old_id),
        )

        cursor.execute(
            "UPDATE test_cases SET module_id=? WHERE module_id=?",
            (new_id, old_id),
        )

    # Rebuild the table with the constraint (SQLite can't ALTER
    # TABLE to add a UNIQUE constraint to an existing table).
    cursor.execute(
        """
        CREATE TABLE modules_new
        (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            domain_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            description TEXT,
            status TEXT DEFAULT 'Active',
            created_date TEXT,
            modified_date TEXT,
            UNIQUE(domain_id, name),
            FOREIGN KEY(domain_id) REFERENCES domains(id) ON DELETE CASCADE
        )
        """
    )

    duplicate_ids = set(id_remap.keys())

    cursor.execute("SELECT * FROM modules")

    columns = [description[0] for description in cursor.description]

    all_rows = cursor.fetchall()

    id_index = columns.index("id")

    kept_rows = [
        row for row in all_rows if row[id_index] not in duplicate_ids
    ]

    placeholders = ",".join("?" for _ in columns)

    cursor.executemany(
        f"INSERT INTO modules_new ({','.join(columns)}) "
        f"VALUES ({placeholders})",
        kept_rows,
    )

    cursor.execute("DROP TABLE modules")

    cursor.execute("ALTER TABLE modules_new RENAME TO modules")

    conn.commit()

    conn.close()

    print(
        f"  Rebuilt modules table. Kept {len(kept_rows)} row(s), "
        f"merged {len(duplicate_ids)} duplicate(s)."
    )


def step_3_backfill_knowledge_items():

    print()
    print("Step 3: Backfilling domain_id/module_id on knowledge_items...")

    db = DatabaseManager()

    metadata = MetadataManager()

    conn = db.get_connection()

    cursor = conn.cursor()

    cursor.execute(
        """
        SELECT id, domain, module
        FROM knowledge_items
        WHERE domain_id IS NULL OR module_id IS NULL
        """
    )

    rows = cursor.fetchall()

    conn.close()

    print(f"  Found {len(rows)} row(s) needing backfill.")

    fixed = 0

    for row_id, domain, module in rows:

        if not domain or not module:

            print(
                f"  Skipping row {row_id} — missing domain or "
                f"module text, can't resolve."
            )

            continue

        domain_id = metadata.get_or_create_domain(domain)

        module_id = metadata.get_or_create_module(domain, module)

        conn2 = db.get_connection()

        cursor2 = conn2.cursor()

        cursor2.execute(
            "UPDATE knowledge_items SET domain_id=?, module_id=? WHERE id=?",
            (domain_id, module_id, row_id),
        )

        conn2.commit()

        conn2.close()

        fixed += 1

    print(f"  Backfilled {fixed} row(s).")


def step_4_backfill_test_cases():

    print()
    print("Step 4: Backfilling domain_id/module_id on test_cases...")

    db = DatabaseManager()

    metadata = MetadataManager()

    conn = db.get_connection()

    cursor = conn.cursor()

    cursor.execute(
        """
        SELECT id, domain, module
        FROM test_cases
        WHERE domain_id IS NULL OR module_id IS NULL
        """
    )

    rows = cursor.fetchall()

    conn.close()

    print(f"  Found {len(rows)} row(s) needing backfill.")

    fixed = 0

    for row_id, domain, module in rows:

        if not domain or not module:

            print(
                f"  Skipping test case {row_id} — missing domain "
                f"or module text, can't resolve."
            )

            continue

        domain_id = metadata.get_or_create_domain(domain)

        module_id = metadata.get_or_create_module(domain, module)

        conn2 = db.get_connection()

        cursor2 = conn2.cursor()

        cursor2.execute(
            "UPDATE test_cases SET domain_id=?, module_id=? WHERE id=?",
            (domain_id, module_id, row_id),
        )

        conn2.commit()

        conn2.close()

        fixed += 1

    print(f"  Backfilled {fixed} row(s).")


def main():

    print("=" * 60)
    print("QA AI Studio — Phase 1 Consistency Migration")
    print("=" * 60)

    step_1_initialize_schema()

    step_2_rebuild_modules_with_constraint()

    step_3_backfill_knowledge_items()

    step_4_backfill_test_cases()

    print()
    print("=" * 60)
    print("Migration complete.")
    print("=" * 60)


if __name__ == "__main__":

    main()