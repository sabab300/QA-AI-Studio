"""Application service: universal script import on top of the existing repository.

Stateless by design: ``commit`` re-analyses the same uploaded bytes and checks
each reviewed test's source fingerprint, so nothing the reviewer did not see can
be saved and no server-side preview state is required.

Persistence reuses the EXISTING atomic, stable-identity primitive
``TestCaseRepository.update_playwright_scripts_atomically`` (UPLOADED slot).
That primitive resets the slot's validated-hash when the script changes, so an
imported script is never executable until it is validated/activated through the
normal lifecycle (Create/Import -> Validate -> Review -> Save -> Execute).
"""
from __future__ import annotations

import json
from pathlib import Path

from Core.automation_import_service import normalize_test_case_id, AutomationImportError
from Core.playwright_script_lifecycle import revision_context
from Core.script_import.batch import Decision, ScriptImportEngine, norm_id
from Core.script_import.runtime import RuntimeProbe, run_native_test

APPLICATION_ROOT = Path(__file__).resolve().parent.parent.parent
ARCHIVE_ROOT = APPLICATION_ROOT / "Output" / "ScriptImports"


class ScriptImportService:
    def __init__(self, repository, archive_root: Path | str | None = None, engine: ScriptImportEngine | None = None):
        self.repository = repository
        self.archive_root = Path(archive_root) if archive_root else ARCHIVE_ROOT
        self.engine = engine or ScriptImportEngine(RuntimeProbe())

    # ---------------------------------------------------------------- lookups
    def _version_rows(self, version_id: int, file_id: int) -> dict:
        from Core.automation_import_service import AutomationImportService
        return AutomationImportService(self.repository)._persisted_test_case_ids(int(version_id), int(file_id))

    def existing_ids(self, version_id: int, file_id: int) -> dict:
        rows = self._version_rows(version_id, file_id)
        ids = sorted({int(r["id"]) for r in rows.values()})
        has_script = {}
        if ids:
            conn = self.repository.db.get_connection()
            try:
                marks = ",".join("?" for _ in ids)
                for row in conn.execute(f"SELECT id, uploaded_script FROM test_cases WHERE id IN ({marks})", ids).fetchall():
                    has_script[int(row[0])] = bool((row[1] or "").strip())
            finally:
                conn.close()
        return {key: {"id": int(r["id"]), "tc_number": r.get("tc_number") or r.get("tc_id"),
                      "has_script": has_script.get(int(r["id"]), False)} for key, r in rows.items()}

    def _previous_uploaded_script(self, db_id: int) -> str:
        conn = self.repository.db.get_connection()
        try:
            row = conn.execute("SELECT uploaded_script FROM test_cases WHERE id=?", (int(db_id),)).fetchone()
            return (row[0] if row else "") or ""
        finally:
            conn.close()

    # ---------------------------------------------------------------- analyse
    def analyze(self, files, forced_formats, version_id: int, file_id: int) -> dict:
        existing = self.existing_ids(version_id, file_id)
        preview = self.engine.analyze(files, forced_formats, existing)
        data = preview.to_dict()
        data["native_runtime"] = dict(zip(("ready", "detail"), self.engine.probe.native_ready()))
        data["existing_test_case_count"] = len(existing)
        return data

    # ----------------------------------------------------------------- commit
    def commit(self, files, decisions: list[dict], version_id: int, file_id: int, *,
               all_or_nothing: bool = False, forced_formats=None, user: str = "") -> dict:
        existing = self.existing_ids(version_id, file_id)
        preview = self.engine.analyze(files, forced_formats, existing)
        by_key = {t.key: t for t in preview.tests}
        parsed, stale = [], []
        for raw in decisions or []:
            key = raw.get("key")
            test = by_key.get(key)
            if test is None:
                stale.append({"key": key, "reason": "Unknown test in this upload."})
                continue
            expected = raw.get("source_fingerprint")
            if expected and expected != test.source_fingerprint:
                stale.append({"key": key, "reason": "The uploaded source changed since it was reviewed; re-run the preview."})
                continue
            parsed.append(Decision(
                key=key, include=bool(raw.get("include", True)), final_test_id=raw.get("final_test_id") or None,
                existing_test_case_id=raw.get("existing_test_case_id"), create_new=bool(raw.get("create_new")),
                duplicate_resolution=raw.get("duplicate_resolution"), accept_conversion=bool(raw.get("accept_conversion")),
                acknowledge_review=bool(raw.get("acknowledge_review")), authorize_replace=bool(raw.get("authorize_replace")),
                execution_mode=raw.get("execution_mode") or "python",
            ))
        # a renamed/mapped test is re-matched against the workbook before planning
        for d in parsed:
            if d.final_test_id and not d.existing_test_case_id:
                match = existing.get(norm_id(d.final_test_id))
                if match:
                    by_key[d.key].notes["existing_test_case"] = match
        plan = self.engine.build_plan(preview, parsed, all_or_nothing)
        # creating new test cases belongs to the Test Cases workbook lifecycle, not to script import
        kept = []
        for item in plan.items:
            if item.create_new or not item.existing_test_case_id:
                plan.rejected.append({"key": item.test.key, "reason": "Creating a new test case from a script import is not supported; "
                                      "the Test Case ID must exist in the selected Test Cases version."})
            else:
                kept.append(item)
        plan.items = kept
        plan.rejected = stale + plan.rejected

        def persist(item):
            if item.execution_mode == "native-node":
                return {"archived_only": True, "previous_script": None,
                        "note": "Native Node test archived with the import history; the standard Execute button runs the Python engine only."}
            previous = self._previous_uploaded_script(item.existing_test_case_id)
            with revision_context("IMPORT", user, f"Script import batch {preview.batch_id}"):
                self.repository.update_playwright_scripts_atomically([(item.existing_test_case_id, item.script)])
            return {"previous_script": previous}

        def restore(item, info):
            if item.execution_mode == "native-node":
                return
            with revision_context("IMPORT_ROLLBACK", user, f"Rolled back script import batch {preview.batch_id}"):
                self.repository.update_playwright_scripts_atomically([(item.existing_test_case_id, info.get("previous_script") or "")])

        source_files = {name: data for name, data in files}
        report = self.engine.commit(preview, plan, persist, self.archive_root, restore=restore, user=user, source_files=source_files)
        report["stale_or_unknown"] = stale
        return report

    # ---------------------------------------------------------------- history
    def history(self, limit: int = 50) -> list[dict]:
        out = []
        if not self.archive_root.exists():
            return out
        for manifest in sorted(self.archive_root.glob("*/manifest.json"), key=lambda p: p.stat().st_mtime, reverse=True)[:limit]:
            try:
                data = json.loads(manifest.read_text(encoding="utf-8"))
            except ValueError:
                continue
            out.append({"batch_id": data.get("batch_id"), "started_at": data.get("started_at"), "user": data.get("user"),
                        "summary": data.get("summary"), "all_or_nothing": data.get("all_or_nothing")})
        return out

    def history_item(self, batch_id: str) -> dict | None:
        safe = "".join(c for c in batch_id if c.isalnum())
        path = self.archive_root / safe / "manifest.json"
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    # ----------------------------------------------------------- native runs
    def run_native(self, files, key: str, forced_formats=None, timeout_ms: int = 60000) -> dict:
        preview = self.engine.analyze(files, forced_formats, {})
        test = next((t for t in preview.tests if t.key == key), None)
        if test is None or not test.native_source:
            raise AutomationImportError("No native source exists for that test.")
        result = run_native_test(test.native_source, typescript=test.source_format == "playwright_typescript", timeout_ms=timeout_ms)
        result["test_id"], result["source_filename"], result["source_fingerprint"] = test.test_id, test.source_filename, test.source_fingerprint
        return dict(result)
