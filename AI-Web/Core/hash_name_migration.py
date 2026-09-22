"""
V5-02 — safe, idempotent migration of existing hash-suffixed managed
folder names (e.g. "SD-V0.3 CRF-DPP_480ba5", "TC-V1-V0.3 CRF-DP_480ba5")
to the new clean naming scheme introduced in Shared/central_repository.py
(CentralRepository.resolve_unique_segment), WITHOUT losing or silently
overwriting any data.

NOT auto-run anywhere. This module is deliberately not imported by
Web/main.py's startup path. It is meant to be invoked explicitly —
e.g. from a one-off admin script or an explicitly-triggered admin
endpoint — with dry_run=True first so a human can review exactly what
it proposes to do before dry_run=False actually renames anything on
disk and rewrites database rows.

WHAT IT DOES, IN ORDER (top of the hierarchy down, so a parent rename's
side effect of relocating its own children is accounted for before
those children are inspected for their OWN hash suffix):

  1. Find every `knowledge_items` row whose `repository_path`'s parent
     folder name ends in a `_xxxxxx` hash suffix (an "SD-*" folder).
     Group by that physical folder (several files can share one SD
     folder) so it is renamed exactly once.
  2. For each such folder, compute the clean target name using the
     SAME collision-safe logic new uploads now use
     (CentralRepository.resolve_unique_segment), excluding the folder
     being renamed from its own collision check (a folder is never a
     "collision" with itself).
  3. If the target name is already taken by a DIFFERENT identity, this
     folder is left untouched and reported as an unresolved collision
     — never silently overwritten or merged.
  4. Otherwise: rename the folder on disk, then — in the SAME
     transaction — rewrite every table/column in `_PATH_TABLES` whose
     stored path starts with the old folder path, replacing that
     PREFIX with the new folder path (preserving everything after it,
     e.g. a still-hashed TC-* subfolder or a filename). If the DB
     transaction fails for any reason, the physical rename is undone
     so disk and DB never diverge.
  5. Repeat the same process for `test_case_versions.folder_path`
     values ("TC-V*" folders) — by this point any parent SD folder
     hash has already been cleaned, so this only ever needs to look at
     the TC folder's own name. Because "API-TC-*"/"SQL-TC-*"
     subfolders are literally named "API-" + the TC folder's own
     `.name` / "SQL-" + the TC folder's own `.name` (see
     api_collection_repository.py / sql_script_repository.py), a
     hashed TC folder rename leaves its API-TC-*/SQL-TC-* children with
     a now-stale OWN name (they are not auto-renamed by the OS rename
     of their parent — only their PATH moves, not their own name) —
     so this migration explicitly renames any such child folder to
     match, and rewrites `api_collections.repository_path` /
     `sql_scripts.repository_path` rows the same prefix-preserving way.

Idempotent: a second run after a successful run finds zero hash-suffixed
folders left and does nothing. Safe to call `run_migration(dry_run=True)`
as many times as needed to review the plan.
"""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path

from Shared.central_repository import CentralRepository, PROJECT_ROOT

_HASH_SUFFIX = re.compile(r"_[0-9a-f]{6}$")
DB_PATH = PROJECT_ROOT / "AI-Web" / "Database" / "metadata.db"

# Every table/column that may hold a path pointing at (or inside) a
# managed SD-*/TC-V*/API-TC-*/SQL-TC-* folder. When such a folder is
# renamed, any stored path starting with the OLD folder path has that
# prefix rewritten to the NEW folder path — everything after the
# prefix (subfolder names, filenames) is preserved exactly.
_PATH_TABLES = [
    ("knowledge_items", ["repository_path", "original_path"]),
    ("knowledge_versions", ["repository_path"]),
    ("test_case_versions", ["folder_path", "workbook_path"]),
    ("test_case_files", ["workbook_path"]),
    ("test_cases", ["artifact_path", "reviewed_workbook_path"]),
    ("api_collections", ["repository_path"]),
    ("sql_scripts", ["repository_path"]),
    ("automation_runs", ["script_path", "evidence_path", "report_path"]),
]


def _clean_name(name: str) -> str | None:
    """Return `name` with its trailing `_xxxxxx` hash suffix removed,
    or None if it has no such suffix (nothing to migrate)."""
    if _HASH_SUFFIX.search(name):
        return _HASH_SUFFIX.sub("", name)
    return None


def _rewrite_prefix(conn: sqlite3.Connection, old_path: Path, new_path: Path) -> int:
    """Rewrite every stored path in `_PATH_TABLES` that starts with
    `old_path` to start with `new_path` instead. Returns the number of
    rows updated. Must run inside a transaction the caller controls."""
    old_text = str(old_path)
    new_text = str(new_path)
    updated = 0
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    for table, columns in _PATH_TABLES:
        if table not in tables:
            continue
        existing_columns = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
        for column in columns:
            if column not in existing_columns:
                continue
            rows = conn.execute(
                f"SELECT rowid,{column} FROM {table} "
                f"WHERE {column} IS NOT NULL AND {column}<>''"
            ).fetchall()
            for rowid, stored in rows:
                stored = str(stored)
                if stored == old_text or stored.startswith(old_text + "\\") or stored.startswith(old_text + "/"):
                    replacement = new_text + stored[len(old_text):]
                    conn.execute(f"UPDATE {table} SET {column}=? WHERE rowid=?", (replacement, rowid))
                    updated += 1
        # knowledge_items also carries a repository-ROOT-RELATIVE path
        # cache that must move in lock-step with repository_path.
        if table == "knowledge_items" and "repository_relative_path" in existing_columns:
            repo = CentralRepository()
            rows = conn.execute(
                "SELECT rowid,repository_path FROM knowledge_items "
                "WHERE repository_path IS NOT NULL AND repository_path<>''"
            ).fetchall()
            for rowid, stored in rows:
                stored = str(stored)
                if stored.startswith(new_text):
                    try:
                        relative = str(Path(stored).resolve().relative_to(repo.root.resolve()))
                    except (OSError, ValueError):
                        continue
                    conn.execute(
                        "UPDATE knowledge_items SET repository_relative_path=? WHERE rowid=?",
                        (relative, rowid),
                    )
    return updated


def _rename_one(conn: sqlite3.Connection, old_folder: Path, new_folder: Path, report: dict, kind: str):
    if not old_folder.is_dir():
        report["skipped_missing"].append({"old": str(old_folder), "kind": kind})
        return
    if new_folder.exists():
        report["skipped_collisions"].append({
            "old": str(old_folder), "target": str(new_folder), "kind": kind,
            "reason": "Target name already exists on disk and was not created by this migration.",
        })
        return
    try:
        old_folder.rename(new_folder)
    except OSError as exc:
        report["errors"].append({"old": str(old_folder), "target": str(new_folder), "kind": kind, "error": str(exc)})
        return
    try:
        updated = _rewrite_prefix(conn, old_folder, new_folder)
    except Exception as exc:
        # Undo the physical rename so disk and DB never diverge.
        try:
            new_folder.rename(old_folder)
        except OSError:
            pass
        report["errors"].append({
            "old": str(old_folder), "target": str(new_folder), "kind": kind,
            "error": f"DB update failed, physical rename rolled back: {exc}",
        })
        return
    report["applied"].append({
        "old": str(old_folder), "new": str(new_folder), "kind": kind, "rows_updated": updated,
    })


def _find_hashed_sd_folders(conn: sqlite3.Connection) -> dict:
    """{old_sd_folder_path: representative_source_file_id}, deduped —
    several knowledge_items rows can share one SD folder."""
    found = {}
    rows = conn.execute(
        "SELECT repository_path, source_file_id FROM knowledge_items "
        "WHERE repository_path IS NOT NULL AND repository_path<>''"
    ).fetchall()
    for repository_path, source_file_id in rows:
        folder = Path(str(repository_path)).parent
        if _HASH_SUFFIX.search(folder.name):
            found.setdefault(folder, str(source_file_id or ""))
    return found


def _find_hashed_tc_folders(conn: sqlite3.Connection) -> dict:
    """{old_tc_folder_path: source_file_id} from test_case_versions —
    read AFTER SD folders have already been renamed, so folder_path
    values reflect any already-applied SD rename."""
    found = {}
    rows = conn.execute(
        "SELECT folder_path, source_file_id FROM test_case_versions "
        "WHERE folder_path IS NOT NULL AND folder_path<>''"
    ).fetchall()
    for folder_path, source_file_id in rows:
        folder = Path(str(folder_path))
        if _HASH_SUFFIX.search(folder.name):
            found[folder] = str(source_file_id or "")
    return found


def _migrate_automation_subfolder(conn: sqlite3.Connection, tc_old: Path, tc_new: Path,
                                   prefix: str, table: str, report: dict):
    """After a TC folder rename, its "API-<old name>"/"SQL-<old name>"
    child (if any) still has the STALE old name as its OWN folder name
    (an OS rename of the parent moves it, but does not rename it) —
    rename that child to "<prefix>-<new tc name>" too, and rewrite the
    matching repository table's stored paths the same way."""
    old_child = tc_new / f"{prefix}-{tc_old.name}"
    if not old_child.is_dir():
        return
    new_child = tc_new / f"{prefix}-{tc_new.name}"
    _rename_one(conn, old_child, new_child, report, kind=f"{prefix}-TC automation subfolder ({table})")


def _plan_workbook_files(old_folder: Path, new_name: str) -> list[tuple[Path, Path]]:
    """A Test Cases workbook filename is generated as
    f"{tc_folder.name}-TC{n}.xlsx" at creation time (see
    qa_engineering_version_service.py's `_create_set()`), so it embeds
    the TC folder's OWN name at that moment. Renaming the TC folder
    moves this file but does not touch its OWN filename — so any
    existing workbook whose name still starts with the OLD (hashed)
    folder name must be renamed too, or "Test Cases workbook names"
    would keep the hash suffix forever despite the folder being clean.
    Returns [(old_file_path, new_file_path), ...], computed from the
    OLD folder's current listing so it is safe to call before any
    rename has happened (dry_run planning)."""
    old_prefix = old_folder.name
    if old_prefix == new_name:
        return []
    pairs = []
    for file_path in sorted(old_folder.glob(f"{old_prefix}-*.xlsx")):
        new_file_name = new_name + file_path.name[len(old_prefix):]
        pairs.append((file_path, old_folder.parent / new_name / new_file_name))
    return pairs


def _migrate_workbook_files(conn: sqlite3.Connection, tc_old_name: str, tc_new: Path, report: dict):
    """Apply the workbook-file renames planned by `_plan_workbook_files`
    against the ALREADY-RENAMED new TC folder, and rewrite every table
    that stores the workbook's path (test_case_versions.workbook_path,
    test_case_files.workbook_path, test_cases.reviewed_workbook_path)
    the same prefix-preserving way `_rewrite_prefix` uses elsewhere."""
    if tc_old_name == tc_new.name:
        return
    for file_path in sorted(tc_new.glob(f"{tc_old_name}-*.xlsx")):
        new_name = tc_new.name + file_path.name[len(tc_old_name):]
        new_file_path = tc_new / new_name
        if new_file_path.exists():
            report["skipped_collisions"].append({
                "old": str(file_path), "target": str(new_file_path), "kind": "TC workbook file",
                "reason": "Target filename already exists and was not created by this migration.",
            })
            continue
        try:
            file_path.rename(new_file_path)
        except OSError as exc:
            report["errors"].append({
                "old": str(file_path), "target": str(new_file_path), "kind": "TC workbook file", "error": str(exc),
            })
            continue
        updated = _rewrite_prefix(conn, file_path, new_file_path)
        report["applied"].append({
            "old": str(file_path), "new": str(new_file_path), "kind": "TC workbook file", "rows_updated": updated,
        })


def run_migration(dry_run: bool = True) -> dict:
    """Plan (dry_run=True) or apply (dry_run=False) the hash-suffix
    cleanup. Returns a report dict:
      {dry_run, planned: [...], applied: [...], skipped_collisions: [...],
       skipped_missing: [...], errors: [...]}
    `planned` is always populated (even when dry_run=False, listing what
    WAS applied) so callers get one consistent shape to render/log.
    """
    report = {
        "dry_run": dry_run, "planned": [], "applied": [],
        "skipped_collisions": [], "skipped_missing": [], "errors": [],
    }
    if not DB_PATH.is_file():
        report["errors"].append({"error": "metadata.db not found; nothing to migrate."})
        return report

    repo = CentralRepository()
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys=OFF")
    try:
        # ---- Pass 1: SD-* folders ----
        for old_folder, source_file_id in sorted(_find_hashed_sd_folders(conn).items(), key=lambda kv: str(kv[0])):
            managed_name = re.sub(r"-V\d+$", "", source_file_id, flags=re.IGNORECASE) if source_file_id else _clean_name(old_folder.name) or old_folder.name
            parent = old_folder.parent
            target_name = repo.resolve_unique_segment(
                parent, "SD", managed_name, identity=source_file_id,
                table="knowledge_items", path_column="repository_path",
                identity_column="source_file_id", container_is_path=False,
                label="source document name", max_length=22,
            )
            # Never treat "matches my own current name's clean form" as
            # a collision with itself — exclude self by checking the
            # candidate isn't just re-deriving the same folder we're
            # standing on (resolve_unique_segment already skips this
            # because the identity check above only flags a DIFFERENT
            # identity; our own row's identity matches itself).
            new_folder = parent / target_name
            report["planned"].append({"old": str(old_folder), "target": str(new_folder), "kind": "SD"})
            if not dry_run:
                _rename_one(conn, old_folder, new_folder, report, kind="SD")
                if not dry_run:
                    conn.commit()

        # ---- Pass 2: TC-V* folders (after any SD rename above) ----
        for old_folder, source_file_id in sorted(_find_hashed_tc_folders(conn).items(), key=lambda kv: str(kv[0])):
            source_base = re.sub(r"-V\d+$", "", source_file_id or "Source", flags=re.IGNORECASE)
            parent = old_folder.parent
            version_match = re.match(r"TC-V(\d+)-", old_folder.name)
            version = version_match.group(1) if version_match else "1"
            target_name = repo.resolve_unique_segment(
                parent, f"TC-V{version}", source_base, identity=source_file_id,
                table="test_case_versions", path_column="folder_path",
                identity_column="source_file_id", container_is_path=True,
                label="source base name", max_length=24,
            )
            new_folder = parent / target_name
            report["planned"].append({"old": str(old_folder), "target": str(new_folder), "kind": "TC"})
            for wf_old, wf_new in _plan_workbook_files(old_folder, target_name):
                report["planned"].append({"old": str(wf_old), "target": str(wf_new), "kind": "TC workbook file"})
            if not dry_run:
                old_folder_name = old_folder.name
                _rename_one(conn, old_folder, new_folder, report, kind="TC")
                conn.commit()
                if new_folder.is_dir():
                    _migrate_automation_subfolder(conn, old_folder, new_folder, "API", "api_collections", report)
                    _migrate_automation_subfolder(conn, old_folder, new_folder, "SQL", "sql_scripts", report)
                    _migrate_workbook_files(conn, old_folder_name, new_folder, report)
                    conn.commit()
    finally:
        conn.close()

    return report
