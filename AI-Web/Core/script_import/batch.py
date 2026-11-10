"""Batch import: analyse -> preview -> plan -> atomic per-test commit + history."""
from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from Core.script_import.chrome_recorder import extract_chrome_recorder
from Core.script_import.detect import decode_bytes, detect_format
from Core.script_import.js_ext import extract_js
from Core.script_import.js_to_py import convert_js_test
from Core.script_import.models import (
    ExtractedTest, FORMAT_CHROME_RECORDER, FORMAT_JAVASCRIPT, FORMAT_PYTHON, FORMAT_QA_JSON,
    FORMAT_TYPESCRIPT, FORMAT_UNKNOWN, ALL_FORMATS, STATUS_CONVERSION_REQUIRED, STATUS_INVALID,
    STATUS_NEEDS_REVIEW, STATUS_VALID, SEVERITY_ERROR, SEVERITY_INFO, SEVERITY_WARNING, fingerprint,
    mask_secrets,
)
from Core.script_import.python_ext import extract_python, extract_qa_json
from Core.script_import.runtime import RuntimeProbe

MAX_FILE_BYTES = 5 * 1024 * 1024


def norm_id(value) -> str:
    return re.sub(r"[^A-Za-z0-9]", "", str(value or "")).upper()


@dataclass
class FileResult:
    filename: str
    format: str = FORMAT_UNKNOWN
    confidence: str = "none"
    reasons: list = field(default_factory=list)
    alternatives: list = field(default_factory=list)
    forced: bool = False
    error: str | None = None
    test_keys: list = field(default_factory=list)
    size: int = 0
    sha256: str = ""

    def to_dict(self):
        return self.__dict__.copy()


@dataclass
class ImportPreview:
    batch_id: str
    created_at: str
    files: list
    tests: list
    summary: dict

    def to_dict(self, include_sources=True):
        return {"batch_id": self.batch_id, "created_at": self.created_at,
                "files": [f.to_dict() for f in self.files],
                "tests": [t.to_dict(include_sources) for t in self.tests],
                "summary": self.summary}


@dataclass
class Decision:
    key: str
    include: bool = True
    final_test_id: str | None = None          # user-resolved ID (rename / map)
    existing_test_case_id: int | None = None   # DB id of the test case to bind to
    create_new: bool = False                   # authorised creation of a new test case
    duplicate_resolution: str | None = None    # 'skip' | 'rename' | 'replace' | 'map_existing'
    accept_conversion: bool = False            # reviewer accepted the converted script
    acknowledge_review: bool = False           # reviewer accepted a NEEDS REVIEW item
    authorize_replace: bool = False            # explicit authorisation to replace an existing script
    execution_mode: str = "python"             # 'python' (converted/native python) | 'native-node'


@dataclass
class PlanItem:
    test: ExtractedTest
    final_test_id: str
    existing_test_case_id: int | None
    create_new: bool
    replace_existing: bool
    execution_mode: str
    script: str | None


@dataclass
class ImportPlan:
    items: list
    rejected: list           # [{key, reason}]
    all_or_nothing: bool = False


class ScriptImportEngine:
    def __init__(self, runtime_probe: RuntimeProbe | None = None):
        self.probe = runtime_probe or RuntimeProbe()

    # ------------------------------------------------------------------ analyse
    def analyze(self, files, forced_formats: dict | None = None, existing_ids: dict | None = None) -> ImportPreview:
        """files: [(filename, bytes)]. existing_ids: {normalised_id: {"id": db_id, "tc_number": str, "has_script": bool}}."""
        forced_formats = forced_formats or {}
        existing_ids = existing_ids or {}
        results: list[FileResult] = []
        tests: list[ExtractedTest] = []
        for filename, data in files:
            fr = FileResult(filename=filename, size=len(data or b""))
            results.append(fr)
            if not data:
                fr.error = "The file is empty."
                continue
            if len(data) > MAX_FILE_BYTES:
                fr.error = f"The file is larger than the {MAX_FILE_BYTES // (1024 * 1024)} MB import limit."
                continue
            fr.sha256 = fingerprint(data.decode("latin-1"))
            text = decode_bytes(data)
            det = detect_format(filename, text)
            fr.format, fr.confidence, fr.reasons, fr.alternatives = det["format"], det["confidence"], det["reasons"], det["alternatives"]
            forced = forced_formats.get(filename)
            if forced:
                if forced not in ALL_FORMATS:
                    fr.error = f"Unsupported format override '{forced}'."
                    continue
                fr.format, fr.forced = forced, True
                fr.reasons = fr.reasons + [f"Format overridden by the user to {forced}."]
            try:
                extracted = self._extract(filename, text, fr.format)
            except Exception as err:  # never let one bad file abort the batch
                fr.error = f"Could not read this file as {fr.format}: {type(err).__name__}: {err}"
                continue
            if fr.format == FORMAT_UNKNOWN:
                fr.error = "Unrecognised format. Choose the correct format to continue."
                continue
            for t in extracted:
                fr.test_keys.append(t.key)
            tests.extend(extracted)
        self._cross_validate(tests, existing_ids)
        summary = self._summary(results, tests)
        return ImportPreview(uuid.uuid4().hex[:12], datetime.now(timezone.utc).isoformat(), results, tests, summary)

    def _extract(self, filename: str, text: str, fmt: str) -> list[ExtractedTest]:
        if fmt == FORMAT_PYTHON:
            return extract_python(text, filename)
        if fmt == FORMAT_QA_JSON:
            data = json.loads(text)
            rows = data if isinstance(data, list) else data.get("tests", [])
            return extract_qa_json(rows, filename)
        if fmt == FORMAT_CHROME_RECORDER:
            return extract_chrome_recorder(json.loads(text), filename)
        if fmt in (FORMAT_JAVASCRIPT, FORMAT_TYPESCRIPT):
            ts = fmt == FORMAT_TYPESCRIPT
            tests = extract_js(text, filename, fmt)
            for t in tests:
                if t.native_source is None:
                    continue
                ok, msg, verified = self.probe.check_js_syntax(t.native_source, typescript=ts)
                if not ok:
                    t.status = STATUS_INVALID
                    t.add(SEVERITY_ERROR, "SYNTAX_ERROR", f"Syntax error: {msg}")
                    continue
                if not verified:
                    t.add(SEVERITY_INFO, "SYNTAX_UNVERIFIED", msg or "Syntax was not machine-checked.")
                convert_js_test(t, ts)
                ready, why = self.probe.native_ready()
                t.requires = sorted(set(t.requires) | {"node", "@playwright/test"})
                if t.executable_source is None:
                    t.add(SEVERITY_WARNING if ready else SEVERITY_ERROR if False else SEVERITY_WARNING, "NATIVE_ONLY",
                          "Not convertible to the QA AI Studio replay format; it can only run in the isolated native "
                          "Node runtime (no self-healing)." + ("" if ready else f" Native runtime unavailable here: {why}"))
            return tests
        raise ValueError(f"Unsupported format {fmt}")

    # ------------------------------------------------------------- validation
    def _cross_validate(self, tests: list[ExtractedTest], existing_ids: dict):
        seen: dict[str, ExtractedTest] = {}
        for t in tests:
            key = norm_id(t.test_id)
            if not key:
                continue
            if key in seen and not (t.status == STATUS_INVALID):
                first = seen[key]
                t.add(SEVERITY_ERROR, "DUPLICATE_ID",
                      f"Test Case ID '{t.test_id}' also appears in '{first.source_filename}' (test {first.index_in_file + 1}). "
                      "Rename it, skip it, or map it before saving.")
                first.add(SEVERITY_ERROR, "DUPLICATE_ID",
                          f"Test Case ID '{first.test_id}' is repeated in '{t.source_filename}'.") if not any(i.code == "DUPLICATE_ID" for i in first.issues) else None
                t.notes["duplicate_in_batch"] = True
                first.notes["duplicate_in_batch"] = True
            else:
                seen[key] = t
            match = existing_ids.get(key)
            if match:
                t.notes["existing_test_case"] = match
                t.add(SEVERITY_INFO, "EXISTING_TEST_CASE",
                      f"Matches existing test case {match.get('tc_number') or t.test_id}"
                      + (" which already has a script; saving requires explicit replace authorisation." if match.get("has_script") else "."))
            elif not t.id_generated and existing_ids:
                t.add(SEVERITY_WARNING, "UNKNOWN_TEST_CASE",
                      f"'{t.test_id}' does not match a test case in the selected Test Cases version. "
                      "Map it to an existing test case or authorise creating a new one.")
        for t in tests:
            if t.notes.get("duplicate_in_batch") and t.status in (STATUS_VALID, STATUS_CONVERSION_REQUIRED):
                t.status = STATUS_NEEDS_REVIEW
            if t.status == STATUS_VALID and t.errors:
                t.status = STATUS_NEEDS_REVIEW

    @staticmethod
    def _summary(files, tests):
        counts = {s: 0 for s in (STATUS_VALID, STATUS_CONVERSION_REQUIRED, STATUS_NEEDS_REVIEW, STATUS_INVALID)}
        for t in tests:
            counts[t.status] = counts.get(t.status, 0) + 1
        return {"files": len(files), "files_with_errors": sum(1 for f in files if f.error),
                "tests": len(tests), "by_status": counts,
                "duplicate_ids": sorted({t.test_id for t in tests if t.notes.get("duplicate_in_batch")})}

    # ------------------------------------------------------------------- plan
    def build_plan(self, preview: ImportPreview, decisions: list[Decision], all_or_nothing: bool = False) -> ImportPlan:
        by_key = {t.key: t for t in preview.tests}
        items, rejected = [], []
        used_ids: dict[str, str] = {}
        for d in decisions:
            t = by_key.get(d.key)
            if t is None:
                rejected.append({"key": d.key, "reason": "Unknown test in this import batch."})
                continue
            if not d.include or d.duplicate_resolution == "skip":
                rejected.append({"key": d.key, "reason": "Skipped by the reviewer."})
                continue
            if t.status == STATUS_INVALID:
                rejected.append({"key": d.key, "reason": "INVALID tests cannot be imported: " + "; ".join(i.message for i in t.errors)[:300]})
                continue
            final_id = (d.final_test_id or t.test_id).strip()
            nid = norm_id(final_id)
            if t.notes.get("duplicate_in_batch") and not d.final_test_id and d.duplicate_resolution not in ("rename", "map_existing", "replace"):
                rejected.append({"key": d.key, "reason": f"Duplicate Test Case ID '{t.test_id}' must be resolved (rename / map_existing / skip)."})
                continue
            if nid in used_ids and used_ids[nid] != d.key:
                rejected.append({"key": d.key, "reason": f"Test Case ID '{final_id}' is already used by another test in this import."})
                continue
            if t.id_generated and not (d.final_test_id or d.existing_test_case_id or d.create_new):
                rejected.append({"key": d.key, "reason": "No Test Case ID in the source; map it to an existing test case or provide an ID."})
                continue
            if t.status == STATUS_CONVERSION_REQUIRED and not d.accept_conversion:
                rejected.append({"key": d.key, "reason": "Conversion must be reviewed and accepted before saving."})
                continue
            if t.status == STATUS_NEEDS_REVIEW and not d.acknowledge_review:
                rejected.append({"key": d.key, "reason": "NEEDS REVIEW items require explicit reviewer acknowledgement."})
                continue
            blocking = [i for i in t.errors if i.code != "DUPLICATE_ID"]
            if blocking:
                rejected.append({"key": d.key, "reason": "Unresolved error: " + blocking[0].message})
                continue
            mode = d.execution_mode
            if mode == "native-node":
                if not t.native_source:
                    rejected.append({"key": d.key, "reason": "No native source is available for this test."})
                    continue
                script = t.native_source
            else:
                if not t.executable_source:
                    rejected.append({"key": d.key, "reason": "No validated executable (converted) script exists for this test; "
                                     "it can only be archived or run natively."})
                    continue
                script = t.executable_source
            match = t.notes.get("existing_test_case") or {}
            existing_id = d.existing_test_case_id or match.get("id")
            replace = bool(existing_id and match.get("has_script"))
            if replace and not d.authorize_replace:
                rejected.append({"key": d.key, "reason": "Test case already has a script; replacing it requires explicit authorisation (never silent)."})
                continue
            if not existing_id and not d.create_new:
                rejected.append({"key": d.key, "reason": f"'{final_id}' is not an existing test case; creating a new one requires explicit authorisation."})
                continue
            used_ids[nid] = d.key
            items.append(PlanItem(t, final_id, existing_id, d.create_new and not existing_id, replace, mode, script))
        return ImportPlan(items, rejected, all_or_nothing)

    # ----------------------------------------------------------------- commit
    def commit(self, preview: ImportPreview, plan: ImportPlan, persist, archive_root, *, restore=None,
               user: str = "", source_files: dict | None = None) -> dict:
        """persist(item) -> dict(previous_script=...), raises on failure. restore(item, previous) undoes it."""
        root = Path(archive_root) / preview.batch_id
        (root / "originals").mkdir(parents=True, exist_ok=True)
        (root / "tests").mkdir(exist_ok=True)
        for name, data in (source_files or {}).items():
            (root / "originals" / re.sub(r"[^\w.\-]", "_", name)).write_bytes(data)
        started = datetime.now(timezone.utc).isoformat()
        results, persisted = [], []
        failure = None
        for item in plan.items:
            t = item.test
            safe = re.sub(r"[^\w.\-]", "_", item.final_test_id)
            ext = {"playwright_javascript": "js", "playwright_typescript": "ts", "chrome_recorder_json": "json"}.get(t.source_format, "py")
            (root / "tests" / f"{safe}.original.{ext}").write_text(t.original_source, encoding="utf-8")
            if t.executable_source:
                (root / "tests" / f"{safe}.converted.py").write_text(t.executable_source, encoding="utf-8")
            entry = {"key": t.key, "test_id": item.final_test_id, "source_filename": t.source_filename,
                     "source_format": t.source_format, "status_at_import": t.status,
                     "execution_mode": item.execution_mode,
                     "source_fingerprint": t.source_fingerprint, "validation_fingerprint": t.validation_fingerprint,
                     "script_fingerprint": fingerprint(item.script or ""), "replaced_existing": item.replace_existing}
            if failure is not None and plan.all_or_nothing:
                entry["outcome"] = "NOT_IMPORTED"
                entry["reason"] = "A previous test failed and the batch is all-or-nothing."
                results.append(entry)
                continue
            try:
                info = persist(item) or {}
                entry["outcome"] = "ARCHIVED" if info.get("archived_only") else "IMPORTED"
                if info.get("archived_only"):
                    entry["note"] = info.get("note", "Archived with the import history; not attached to a test case.")
                entry["previous_script_fingerprint"] = fingerprint(info.get("previous_script") or "") if info.get("previous_script") is not None else None
                persisted.append((item, info))
            except Exception as err:
                entry["outcome"] = "FAILED"
                entry["reason"] = f"{type(err).__name__}: {err}"[:400]
                failure = err
            results.append(entry)
        rolled_back = []
        if failure is not None and plan.all_or_nothing:
            for item, info in reversed(persisted):
                try:
                    if restore is not None:
                        restore(item, info)
                        rolled_back.append(item.test.key)
                except Exception as err:  # keep going; report honestly
                    for e in results:
                        if e["key"] == item.test.key:
                            e["rollback_error"] = f"{type(err).__name__}: {err}"[:300]
            for e in results:
                if e["key"] in rolled_back:
                    e["outcome"] = "ROLLED_BACK"
        counts = {"IMPORTED": 0, "ARCHIVED": 0, "FAILED": 0, "ROLLED_BACK": 0, "NOT_IMPORTED": 0}
        for e in results:
            counts[e["outcome"]] = counts.get(e["outcome"], 0) + 1
        report = {
            "batch_id": preview.batch_id, "started_at": started, "finished_at": datetime.now(timezone.utc).isoformat(),
            "user": user, "all_or_nothing": plan.all_or_nothing,
            "summary": {"files": len(preview.files), "extracted": len(preview.tests),
                        "selected": len(plan.items) + len(plan.rejected) if False else len(plan.items),
                        "imported": counts["IMPORTED"], "archived": counts["ARCHIVED"], "failed": counts["FAILED"], "rolled_back": counts["ROLLED_BACK"],
                        "rejected": len(plan.rejected) + sum(1 for t in preview.tests if t.key not in {i.test.key for i in plan.items} and t.key not in {r['key'] for r in plan.rejected})},
            "items": results, "rejected": plan.rejected,
        }
        (root / "manifest.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        return report
