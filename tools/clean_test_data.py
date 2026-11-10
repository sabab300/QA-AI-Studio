#!/usr/bin/env python3
"""QA AI Studio - remove test data safely (backup first, nothing is hard-deleted).

WHAT IT DOES
    * Clears every knowledge / test-case / automation / impact-analysis table in
      Database/metadata.db (domains, modules, knowledge, documents, test cases,
      API/SQL/Playwright assets, impact analyses, embedding queue ...).
    * Moves the vector index (Database/chroma_db) aside, so the AI search starts empty.
    * Moves everything inside the Central Repository folder (and the old
      AI-Web/Repository folder, and generated files under AI-Web/Output) aside.

WHAT IT KEEPS
    * Users, roles, permissions and sign-in settings (you can still log in).
    * AI model/provider settings, audit logs, schema/migration bookkeeping.
    * web_secret.key, Config/*.json, Models, Prompts, Templates, source code and Output/Logs.

SAFETY
    * DRY RUN by default. Nothing changes unless you pass --apply.
    * Before anything changes, both databases are copied to a backup folder
      (<project>/_cleanup_backup_<timestamp>/), and every folder is MOVED into
      that same backup folder, not deleted. You delete the backup yourself when
      you are happy.  The backup contains user accounts - keep it out of Git/ZIPs.
    * Refuses to run while QA AI Studio is running (database locked / port open).
    * If the database step fails it restores the database copy and stops.

USAGE (from the project folder, with the application STOPPED):
    python tools\\clean_test_data.py              # preview only
    python tools\\clean_test_data.py --apply      # do it (asks you to type CLEAN)

OPTIONS
    --apply              perform the cleanup
    --yes                do not ask for the typed confirmation
    --clear-audit-logs   also clear audit_logs (kept by default)
    --keep-table NAME    keep one more table untouched (repeatable)
    --project PATH       project folder (default: the folder above this script)
    --port N             port the web app uses (default 8000) for the "is it running" check
"""
from __future__ import annotations

import argparse
import json
import shutil
import socket
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

# Tables that hold accounts, permissions, AI settings and bookkeeping. Never cleared by default.
PRESERVE_TABLES = {
    "users", "roles", "user_roles", "role_permissions", "permissions", "user_sessions", "sessions",
    "audit_logs", "ci_migrations", "sqlite_sequence",
    "llm_providers", "llm_models", "llm_settings", "llm_audit", "llm_usage",
    "mcp_integrations", "mcp_tool_calls",
    "app_settings", "settings", "schema_version", "schema_migrations",
}
PRESERVE_PREFIXES = ("sqlite_", "llm_", "mcp_")
PRESERVE_CONTAINS = ("migration",)

# Standard generated-output folders under AI-Web/Output that the app expects to exist.
OUTPUT_KEEP = {"Logs"}
OUTPUT_RECREATE = {"AutomationRuns", "Evidence", "RecorderDiagnostics", "RecordingDrafts", "ScriptImports", "Test Cases"}


def human(size: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size} B"


def folder_stats(path: Path) -> tuple[int, int]:
    files = total = 0
    for item in path.rglob("*"):
        try:
            if item.is_file():
                files += 1
                total += item.stat().st_size
        except OSError:
            continue
    return files, total


def is_preserved(table: str, keep: set[str], clear_audit: bool) -> bool:
    name = table.lower()
    if name == "audit_logs" and clear_audit:
        return False
    if name in {k.lower() for k in keep}:
        return True
    return name in PRESERVE_TABLES or name.startswith(PRESERVE_PREFIXES) or any(t in name for t in PRESERVE_CONTAINS)


def port_open(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.5):
            return True
    except OSError:
        return False


def central_root(project: Path) -> Path | None:
    config = project / "Config" / "central_repository.json"
    try:
        value = str(json.loads(config.read_text(encoding="utf-8")).get("path") or "").strip()
    except (OSError, ValueError):
        value = ""
    if not value:
        return None
    path = Path(value).expanduser()
    return path if path.is_absolute() else (project / path)


def plan_tables(db: Path, keep: set[str], clear_audit: bool):
    connection = sqlite3.connect(db)
    try:
        names = [row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
        clear, preserve = [], []
        for name in names:
            count = connection.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0]
            (preserve if is_preserved(name, keep, clear_audit) else clear).append((name, count))
        return clear, preserve
    finally:
        connection.close()


def plan_folders(project: Path):
    """Return [(label, source_path, recreate_empty_dir)] for every item that will be moved aside."""
    web = project / "AI-Web"
    items = []
    root = central_root(project)
    seen = set()

    def add_children(label, folder, keep=()):
        if not folder or not folder.is_dir() or folder.resolve() in seen:
            return
        seen.add(folder.resolve())
        for child in sorted(folder.iterdir(), key=lambda p: p.name.lower()):
            if child.name in keep or child.name.startswith(".qaais-write-test"):
                continue
            items.append((f"{label}/{child.name}", child, label == "output" and child.name in OUTPUT_RECREATE))

    add_children("central_repository", root)
    add_children("ai_web_repository", web / "Repository")
    add_children("output", web / "Output", keep=OUTPUT_KEEP)
    chroma = web / "Database" / "chroma_db"
    if chroma.exists():
        items.append(("database/chroma_db", chroma, True))
    return items


def main() -> int:
    parser = argparse.ArgumentParser(description="Remove QA AI Studio test data (optional backup).")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--no-backup", action="store_true", help="Permanently clean test data without creating backups")
    parser.add_argument("--yes", action="store_true")
    parser.add_argument("--clear-audit-logs", action="store_true")
    parser.add_argument("--keep-table", action="append", default=[])
    parser.add_argument("--project", default=str(Path(__file__).resolve().parents[1]))
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()

    project = Path(args.project).resolve()
    web = project / "AI-Web"
    metadata = web / "Database" / "metadata.db"
    platform = web / "Database" / "ai_platform.db"
    if not metadata.is_file():
        print(f"Cannot find {metadata}. Run this from the QA AI Agent project folder or pass --project.")
        return 2

    keep = set(args.keep_table)
    clear, preserve = plan_tables(metadata, keep, args.clear_audit_logs)
    folders = plan_folders(project)

    print("=" * 72)
    print("QA AI Studio - test data cleanup", "(APPLY)" if args.apply else "(DRY RUN - nothing will change)")
    print("Project:", project)
    print("=" * 72)
    print("\nDATABASE  metadata.db - tables that will be CLEARED:")
    for name, count in clear:
        print(f"   {name:<34}{count:>8} rows")
    print("\nDATABASE  tables that are KEPT (accounts, permissions, AI settings, logs):")
    for name, count in preserve:
        print(f"   {name:<34}{count:>8} rows")
    print("\nFOLDERS that will be " + ("PERMANENTLY DELETED:" if args.no_backup else "MOVED ASIDE into the backup folder:"))
    total_files = 0
    for label, path, _ in folders:
        files, size = folder_stats(path) if path.is_dir() else (1, path.stat().st_size)
        total_files += files
        print(f"   {label:<58}{files:>7} files  {human(size):>9}")
    if not folders:
        print("   (nothing found)")
    print(f"\n   Central Repository location: {central_root(project) or '(default AI-Web/Repository)'}")

    if not args.apply:
        print("\nThis was a preview. To perform the cleanup, STOP the application and run:")
        print("   python tools\\clean_test_data.py --apply" + (" --no-backup" if args.no_backup else ""))
        return 0

    # ---- safety checks ----
    if port_open(args.port):
        print(f"\nSTOP: something is listening on port {args.port}. Close QA AI Studio first, then run again.")
        return 3
    for db in (metadata, platform):
        if not db.is_file():
            continue
        probe = sqlite3.connect(db, timeout=1)
        try:
            probe.execute("BEGIN EXCLUSIVE")
            probe.rollback()
        except sqlite3.OperationalError:
            print(f"\nSTOP: {db.name} is in use. Close QA AI Studio first, then run again.")
            return 3
        finally:
            probe.close()

    if not args.yes:
        answer = input("\nType CLEAN and press Enter to continue (anything else cancels): ").strip()
        if answer != "CLEAN":
            print("Cancelled. Nothing was changed.")
            return 1

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = None if args.no_backup else project / f"_cleanup_backup_{stamp}"
    moved_dir = None if backup is None else backup / "moved_folders"
    if moved_dir is not None:
        moved_dir.mkdir(parents=True)
    report = [f"QA AI Studio cleanup {stamp}", f"project: {project}", ""]

    # 1. database backup (SQLite online backup API: consistent copy)
    if backup is not None:
        for db in (metadata, platform):
            if db.is_file():
                source = sqlite3.connect(db)
                target = sqlite3.connect(backup / db.name)
                try:
                    source.backup(target)
                finally:
                    target.close()
                    source.close()
        print(f"\n[1/4] Database copies saved in {backup}")
    else:
        print("\n[1/4] No backup requested; deleted data cannot be recovered")

    # 2. clear tables
    connection = sqlite3.connect(metadata)
    try:
        connection.execute("PRAGMA foreign_keys=OFF")
        connection.execute("BEGIN IMMEDIATE")
        # Temporarily suspend triggers belonging to reset tables only. SQLite DDL
        # is transactional: any error rolls back both data and trigger changes.
        clear_names = {name.lower() for name, _ in clear}
        triggers = connection.execute(
            "SELECT name, sql FROM sqlite_master WHERE type='trigger' "
            "AND sql IS NOT NULL AND lower(tbl_name) IN ("
            + ",".join("?" for _ in clear_names) + ") ORDER BY name",
            tuple(sorted(clear_names)),
        ).fetchall() if clear_names else []
        for trigger_name, _ in triggers:
            connection.execute('DROP TRIGGER "' + trigger_name.replace('"', '""') + '"')
        has_sequence = connection.execute("SELECT 1 FROM sqlite_master WHERE name='sqlite_sequence'").fetchone()
        for name, count in clear:
            connection.execute(f'DELETE FROM "{name}"')
            if has_sequence:
                connection.execute("DELETE FROM sqlite_sequence WHERE name=?", (name,))
            report.append(f"cleared {name}: {count} rows")
        for _, trigger_sql in triggers:
            connection.execute(trigger_sql)
        remaining = [(name, connection.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0])
                     for name, _ in clear]
        if any(count for _, count in remaining):
            raise RuntimeError(f"Cleanup verification failed: {remaining}")
        broken = connection.execute("PRAGMA foreign_key_check").fetchall()
        if broken:
            raise RuntimeError(f"foreign key check found {len(broken)} problems")
        connection.commit()
        connection.execute("VACUUM")
    except Exception as error:
        connection.rollback()
        connection.close()
        if backup is not None:
            shutil.copy2(backup / metadata.name, metadata)
            print(f"\nDatabase step failed ({error}). Database restored; no folders were touched.")
        else:
            print(f"\nDatabase step failed ({error}). No folders were touched; no backup exists.")
        return 4
    connection.close()
    print(f"[2/4] Cleared {len(clear)} tables; kept {len(preserve)} tables")

    # 3. move folders aside
    failed = []
    for label, path, recreate in folders:
        try:
            if args.no_backup:
                if path.is_dir() and not path.is_symlink():
                    shutil.rmtree(path)
                else:
                    path.unlink()
                report.append(f"deleted {path}")
            else:
                target = moved_dir / label.replace("/", "__")
                shutil.move(str(path), str(target))
                report.append(f"moved {path} -> {target}")
            if recreate:
                path.mkdir(parents=True, exist_ok=True)
        except OSError as error:
            failed.append((path, error))
            report.append(f"FAILED {path}: {error}")
    root = central_root(project)
    (root or web / "Repository").mkdir(parents=True, exist_ok=True)
    (web / "Database" / "chroma_db").mkdir(parents=True, exist_ok=True)
    print(f"[3/4] {'Deleted' if args.no_backup else 'Moved'} {len(folders) - len(failed)} of {len(folders)} folders")

    if backup is not None:
        (backup / "REPORT.txt").write_text("\n".join(report), encoding="utf-8")
        print(f"[4/4] Report written to {backup / 'REPORT.txt'}")
    else:
        print("[4/4] No backup folder or report file created")

    if failed:
        print("\nThese could not be moved (a file is probably open in Explorer, Excel or an editor):")
        for path, error in failed:
            print(f"   {path}  ->  {error}")
        print("Close those windows and run the same command again; it is safe to repeat.")
        return 5

    print("\nDone. Start QA AI Studio and sign in as before. Domains, modules and documents are now empty.")
    if backup is not None:
        print(f"Everything removed is still in {backup} - delete that folder when you are satisfied.")
    else:
        print("Selected old data was permanently deleted without creating a backup.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

