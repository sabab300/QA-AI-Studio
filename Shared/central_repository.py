"""Single authoritative filesystem resolver for QA AI Studio managed data."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sqlite3
import tempfile
from pathlib import Path
from Shared.hierarchy_values import canonical_source_type


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CONFIG_DIR = PROJECT_ROOT / "Config"
CONFIG_FILE = CONFIG_DIR / "central_repository.json"
DEFAULT_ROOT = PROJECT_ROOT / "AI-Web" / "Repository"


class CentralRepository:
    _RESERVED = {
        "CON", "PRN", "AUX", "NUL",
        *(f"COM{i}" for i in range(1, 10)),
        *(f"LPT{i}" for i in range(1, 10)),
    }

    def __init__(self, root: str | Path | None = None):
        self.root = self._absolute(root) if root else self.get_repository_root()
        self.root.mkdir(parents=True, exist_ok=True)

    @classmethod
    def get_repository_root(cls) -> Path:
        try:
            payload = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
            configured = str(payload.get("path") or "").strip()
            if configured:
                return cls._absolute(configured)
        except (OSError, ValueError, TypeError):
            pass
        return DEFAULT_ROOT.resolve()

    @staticmethod
    def _absolute(value: str | Path) -> Path:
        path = Path(str(value)).expanduser()
        if not path.is_absolute():
            path = PROJECT_ROOT / path
        return path.resolve()

    @classmethod
    def sanitize_segment(cls, value, label="path segment") -> str:
        original = str(value or "").strip()
        if not original or original in {".", ".."}:
            raise ValueError(f"Invalid {label}.")
        value = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", original).rstrip(" .")
        if not value or value in {".", ".."}:
            raise ValueError(f"Invalid {label}.")
        if value.split(".", 1)[0].upper() in cls._RESERVED:
            value = "_" + value
        if len(value) > 100:
            digest = hashlib.sha256(original.encode("utf-8")).hexdigest()[:10]
            value = f"{value[:85].rstrip(' ._-')}_{digest}"
        return value

    @classmethod
    def compact_segment(cls, prefix: str, value, label="path segment", max_length: int = 14) -> str:
        """Return a length-bounded physical segment while DB/UI retain the full value.

        Used only for the Domain/Module/Knowledge/Version/Document-Type/
        Source-Type hierarchy levels, whose real-world values are short
        business vocabulary and were never implicated in the V5-02
        random-hash complaint (only SD/TC segments were). Still bounded
        (never unbounded) to keep the overall path within Windows' path-
        length limits, but a truncation here is now silent/clean rather
        than hash-suffixed, matching `resolve_unique_segment()` below.
        """
        cleaned = cls.sanitize_segment(value, label)
        candidate = f"{prefix}-{cleaned}"
        if len(candidate) <= max_length:
            return candidate
        available = max(1, max_length - len(prefix) - 1)
        return f"{prefix}-{cleaned[:available].rstrip(' ._-')}"

    # ------------------------------------------------------------
    # V5-02 — stable, DB-backed identity for managed container/folder
    # names (Source Document "SD-*", Test Case version "TC-V*") that
    # replaces the old "truncate then always append a random-looking
    # content hash" behavior.
    #
    # WHY THE OLD HASH EXISTED: the original `compact_segment()`
    # appended a 6-hex-char sha256 digest whenever a segment exceeded
    # its max_length, purely to keep every physical segment SHORT
    # (max_length was as tight as 22-24 chars for SD/TC) so deeply
    # nested paths (Domain/Module/Knowledge/Version/DocType/SourceType/
    # SD-*/TC-V*/API-TC-* or SQL-TC-*/<file>) stay under Windows'
    # classic MAX_PATH (260 chars) even without long-path support
    # enabled. The hash was never meant as a *display* identity — it
    # was a last-resort disambiguator so two DIFFERENT long names that
    # truncate to the same short prefix wouldn't collide on disk.
    #
    # THE ACTUAL BUG: the digest was appended unconditionally on any
    # truncation, even when there was no real collision to disambiguate
    # — producing ugly, non-deterministic-looking suffixes (`_480ba5`)
    # on ordinary, uniquely-named documents that never collided with
    # anything else.
    #
    # THE FIX: keep the SAME max_length ceilings (so path-length safety
    # is UNCHANGED from before — no new risk of exceeding MAX_PATH),
    # but only fall back to a suffix when a REAL collision is detected
    # against another row's stable DB identity, and use a short,
    # human-readable numeric suffix ("-2", "-3", ...) instead of a
    # hash. The stable identity (`source_file_id`, already persisted
    # un-truncated on `knowledge_items`/`test_case_versions`) is the
    # source of truth for "is this the same logical container" — never
    # the physical folder name alone. This is the "replace filename-
    # based uniqueness with stable metadata/database identity" that
    # the V5-02 requirement asks for.
    #
    # API-TC-*/SQL-TC-* folder names and the generated XLSX workbook
    # filename are not touched here directly — they are built downstream
    # from the TC folder's own `.name` (see api_collection_repository.py
    # / sql_script_repository.py / qa_engineering_version_service.py),
    # so cleaning the TC segment here removes the hash from all of them
    # in one place, without a second hashing mechanism to fix.
    # ------------------------------------------------------------

    @staticmethod
    def _metadata_db_path() -> Path:
        return PROJECT_ROOT / "AI-Web" / "Database" / "metadata.db"

    @classmethod
    def _existing_segment_identities(cls, table: str, path_column: str, identity_column: str,
                                      parent: Path, container_is_path: bool) -> dict:
        """Map {existing physical segment name under `parent`: stable identity}.

        `container_is_path=True` means `path_column` already names the
        managed folder itself (e.g. test_case_versions.folder_path).
        `container_is_path=False` means `path_column` names a FILE
        inside the managed folder (e.g. knowledge_items.repository_path),
        so the folder is that file's parent directory.

        Returns {} (never raises) on any DB/schema issue — a fresh
        install with no metadata.db yet, or a table/column that hasn't
        been created — so this is always safe to call regardless of
        migration order.
        """
        database = cls._metadata_db_path()
        if not database.is_file():
            return {}
        result: dict = {}
        try:
            connection = sqlite3.connect(database)
        except sqlite3.Error:
            return {}
        try:
            tables = {row[0] for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )}
            if table not in tables:
                return {}
            columns = {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}
            if path_column not in columns or identity_column not in columns:
                return {}
            rows = connection.execute(
                f"SELECT {path_column},{identity_column} FROM {table} "
                f"WHERE {path_column} IS NOT NULL AND {path_column}<>''"
            ).fetchall()
            for stored_path, identity in rows:
                try:
                    resolved = Path(str(stored_path))
                except (OSError, ValueError):
                    continue
                folder = resolved if container_is_path else resolved.parent
                if folder.parent == parent:
                    result[folder.name] = str(identity or "")
        except sqlite3.Error:
            return {}
        finally:
            connection.close()
        return result

    @classmethod
    def resolve_unique_segment(cls, parent: Path, prefix: str, value, *, identity: str,
                                table: str, path_column: str, identity_column: str,
                                container_is_path: bool, label: str = "path segment",
                                max_length: int = 22) -> str:
        """Return the physical segment name to use for `value` under `parent`.

        Prefers the full readable cleaned value; truncates cleanly (no
        hash) when it would exceed `max_length`. Only appends a short
        numeric disambiguator when the resulting name already exists
        under `parent` AND belongs to a DIFFERENT stable `identity` —
        an actual collision between two unrelated documents, not merely
        a second file legitimately landing in the SAME logical
        container (which correctly reuses the same folder; see
        RepositoryManager.save_file()'s own handling of that case).
        """
        cleaned = cls.sanitize_segment(value, label)
        base = f"{prefix}-{cleaned}"
        if len(base) > max_length:
            available = max(1, max_length - len(prefix) - 1)
            base = f"{prefix}-{cleaned[:available].rstrip(' ._-')}"

        existing = cls._existing_segment_identities(
            table, path_column, identity_column, parent, container_is_path
        )
        identity = str(identity or "")
        candidate = base
        attempt = 1
        while candidate in existing and existing[candidate] != identity:
            attempt += 1
            candidate = f"{base}-{attempt}"
        return candidate

    @classmethod
    def validate_root(cls, value: str | Path) -> Path:
        root = cls._absolute(value)
        if root.exists() and not root.is_dir():
            raise ValueError("Central Repository Location must be a folder.")
        root.mkdir(parents=True, exist_ok=True)
        descriptor, probe_name = tempfile.mkstemp(prefix=".qaais-write-test-", dir=root)
        os.close(descriptor)
        probe = Path(probe_name)
        try:
            probe.write_bytes(b"qa-ai-studio")
            if probe.read_bytes() != b"qa-ai-studio":
                raise OSError("Repository read verification failed.")
        finally:
            probe.unlink(missing_ok=True)
        return root

    @classmethod
    def configure(cls, value: str | Path, migrate=True) -> dict:
        new_root = cls.validate_root(value)
        old_root = cls.get_repository_root()
        if new_root == old_root:
            return {"path": str(new_root), "migrated": False, "files_migrated": 0}
        files = [item for item in old_root.rglob("*") if item.is_file()] if old_root.exists() else []
        copied = []
        database = PROJECT_ROOT / "AI-Web" / "Database" / "metadata.db"
        database_backup = database.with_suffix(".central-migration-backup")
        try:
            if files and not migrate:
                raise ValueError("Managed repository data exists; migration is required.")
            for source in files:
                relative = source.relative_to(old_root)
                destination = new_root / relative
                if destination.exists():
                    if cls._digest(destination) != cls._digest(source):
                        raise FileExistsError(f"Migration conflict: {relative}")
                    continue
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, destination)
                copied.append(destination)
            for source in files:
                target = new_root / source.relative_to(old_root)
                if not target.is_file() or cls._digest(target) != cls._digest(source):
                    raise OSError(f"Migration verification failed: {source.name}")
            if database.is_file():
                shutil.copy2(database, database_backup)
            cls._rewrite_database_paths(old_root, new_root)
            CONFIG_DIR.mkdir(parents=True, exist_ok=True)
            temporary = CONFIG_FILE.with_suffix(".json.tmp")
            temporary.write_text(json.dumps({"path": str(new_root)}, indent=2), encoding="utf-8")
            os.replace(temporary, CONFIG_FILE)
            database_backup.unlink(missing_ok=True)
        except Exception:
            if database_backup.is_file():
                os.replace(database_backup, database)
            for destination in reversed(copied):
                destination.unlink(missing_ok=True)
            raise
        return {"path": str(new_root), "migrated": bool(files), "files_migrated": len(files)}

    @classmethod
    def _rewrite_database_paths(cls, old_root: Path, new_root: Path) -> None:
        database = PROJECT_ROOT / "AI-Web" / "Database" / "metadata.db"
        if not database.is_file():
            return
        connection = sqlite3.connect(database)
        try:
            connection.execute("BEGIN IMMEDIATE")
            tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            mappings = {
                "knowledge_items": ("repository_path", "original_path"),
                "knowledge_versions": ("repository_path",),
                "test_cases": ("artifact_path", "reviewed_workbook_path"),
                "automation_runs": ("script_path", "evidence_path", "report_path"),
            }
            old_text = str(old_root.resolve())
            for table, candidates in mappings.items():
                if table not in tables:
                    continue
                columns = {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}
                for column in candidates:
                    if column not in columns:
                        continue
                    rows = connection.execute(
                        f"SELECT rowid,{column} FROM {table} WHERE {column} IS NOT NULL AND {column}<>''"
                    ).fetchall()
                    for rowid, stored in rows:
                        try:
                            relative = Path(str(stored)).resolve().relative_to(old_root.resolve())
                        except (OSError, ValueError):
                            continue
                        connection.execute(
                            f"UPDATE {table} SET {column}=? WHERE rowid=?",
                            (str(new_root / relative), rowid),
                        )
                        if table == "knowledge_items" and "repository_relative_path" in columns:
                            connection.execute(
                                "UPDATE knowledge_items SET repository_relative_path=? WHERE rowid=?",
                                (str(relative), rowid),
                            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    @staticmethod
    def _digest(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    def hierarchy_root(self, domain, module, knowledge_name, version, document_type, source_type) -> Path:
        source_type = {
            "FILE": "Files", "FILES": "Files", "FOLDER": "Files",
            "MANUAL_UPLOAD": "Files", "SMART_UPLOAD": "Files",
            "API_COLLECTION": "API Collection", "URL_CAPTURE": "URL",
        }.get(str(source_type or "Files").upper(), source_type or "Files")
        parts = (
            ("D", domain, "domain"), ("M", module, "module"),
            ("K", knowledge_name, "knowledge name"),
            ("DT", document_type or "Other", "document type"), ("V", version, "version"),
            ("ST", source_type or "Files", "source type"),
        )
        path = self.root
        fields=('domain','module','knowledge_name','document_type','version','source_type')
        for (prefix, value, label),field in zip(parts,fields):
            path /= self._hierarchy_segment(path,prefix,value,label,field)
        return path

    def _hierarchy_segment(self,parent,prefix,value,label,field,max_length=14):
        """Reuse persisted ownership; new lossy names need deterministic identity.

        Root truncation previously merged distinct business names silently.
        Existing unambiguous compact segments remain unchanged. New truncated
        or sanitized names carry a bounded digest, never a display label.
        """
        original=str(value or '').strip();cleaned=self.sanitize_segment(original,label)
        existing={}
        database=self._metadata_db_path()
        if database.is_file():
            connection=sqlite3.connect('file:'+str(database)+'?mode=ro',uri=True)
            try:
                columns={row[1] for row in connection.execute('PRAGMA table_info(knowledge_items)')}
                if field in columns:
                    for stored,identity in connection.execute('SELECT repository_path,'+field+" FROM knowledge_items WHERE repository_path IS NOT NULL AND repository_path<>''"):
                        try:relative=Path(stored).resolve().relative_to(parent.resolve())
                        except (ValueError,OSError):continue
                        if relative.parts and relative.parts[0].startswith(prefix+'-'):
                            identity=canonical_source_type(identity) if field=='source_type' else str(identity or '')
                            existing.setdefault(relative.parts[0],set()).add(identity)
            finally:connection.close()
        owned=[segment for segment,owners in existing.items() if original in owners]
        if any(len(existing[segment])!=1 for segment in owned) or len(owned)>1:
            raise ValueError('Managed hierarchy segment has ambiguous persisted ownership; explicit migration is required.')
        if owned:return owned[0]
        candidate=self.compact_segment(prefix,original,label,max_length)
        lossy=cleaned!=original or len(prefix+'-'+cleaned)>max_length
        if lossy:
            suffix=hashlib.sha256(original.encode('utf8')).hexdigest()[:6]
            stem=cleaned[:max(1,max_length-len(prefix)-len(suffix)-2)].rstrip(' ._-')
            candidate=prefix+'-'+stem+'_'+suffix
        conflicts=[owners for segment,owners in existing.items() if segment.casefold()==candidate.casefold() and owners!={original}]
        if conflicts:raise ValueError('Managed hierarchy path belongs to another entity; no folder merge was applied.')
        return candidate

    def create_source_file_id(self, source_name: str) -> str:
        stem = self.sanitize_segment(Path(source_name).stem or "Source", "source name")
        return f"{stem}-V1"

    def build_source_path(self, *, domain, module, knowledge_name, version,
                          document_type, source_type, source_file_id) -> Path:
        managed_name = re.sub(r"-V\d+$", "", str(source_file_id), flags=re.IGNORECASE)
        parent = self.hierarchy_root(domain, module, knowledge_name, version, document_type, source_type)
        segment = self.resolve_unique_segment(
            parent, "SD", managed_name, identity=str(source_file_id),
            table="knowledge_items", path_column="repository_path",
            identity_column="source_file_id", container_is_path=False,
            label="source document name", max_length=22,
        )
        return parent / segment

    def build_test_case_version_path(self, source_path: str | Path, version: int, source_file_id: str) -> Path:
        source_base = re.sub(r"-V\d+$", "", str(source_file_id or "Source"), flags=re.IGNORECASE)
        parent = Path(source_path)
        segment = self.resolve_unique_segment(
            parent, f"TC-V{int(version)}", source_base, identity=str(source_file_id),
            table="test_case_versions", path_column="folder_path",
            identity_column="source_file_id", container_is_path=True,
            label="source base name", max_length=24,
        )
        return parent / segment

    @staticmethod
    def validate_excel_path(path: str | Path, limit: int = 240) -> Path:
        resolved = Path(path).resolve()
        if len(str(resolved)) > limit:
            raise ValueError(
                f"Managed XLSX path is {len(str(resolved))} characters; the Excel-safe limit is {limit}. "
                "The managed path could not be compacted below the Excel-safe limit."
            )
        return resolved

    def build_automation_path(self, test_case_path: str | Path, automation_type: str,
                              version: int, source_file_id: str) -> Path:
        names = {"Playwright": "Playwright", "API": "API", "API Automation": "API", "SQL": "SQL", "SQL Automation": "SQL"}
        prefix = names.get(str(automation_type), str(automation_type))
        return Path(test_case_path) / f"{prefix}-TC-V{int(version)}-{self.sanitize_segment(source_file_id)}"

    @staticmethod
    def ensure_path(path: str | Path) -> Path:
        path = Path(path)
        path.mkdir(parents=True, exist_ok=True)
        return path

    @staticmethod
    def get_next_test_case_version(source_path: str | Path) -> int:
        versions = []
        for child in Path(source_path).iterdir() if Path(source_path).is_dir() else ():
            match = re.match(r"(?:TC|Test Cases)-V(\d+)-", child.name)
            if match:
                versions.append(int(match.group(1)))
        return max(versions, default=0) + 1

    def relative(self, path: str | Path) -> str:
        return str(Path(path).resolve().relative_to(self.root.resolve()))

    def resolve_managed(self, stored_path: str | Path, relative_path: str = "") -> Path:
        if relative_path:
            return (self.root / relative_path).resolve()
        path = Path(str(stored_path or ""))
        return path.resolve() if path.is_absolute() else (self.root / path).resolve()
