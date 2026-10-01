"""
V5 shared automation-import validation service.

Playwright (V5-04), API mapping-mode (V5-05 Mode B) and SQL structured
mapping (V5-07 Mode A) imports all need the exact same behavior: read a
supported file format (.xlsx/.json/.txt), normalize its header/keys onto
a Test Case ID column (MANDATORY) + one type-specific content column
(OPTIONAL — "Script" for Playwright/SQL, "API / Script" for API),
validate every Test Case ID against the SELECTED Test Cases workbook's
real PERSISTED test cases (never the physical XLSX re-parsed client-side
— the workbook was already loaded into `test_cases` when its Test Cases
version was created/imported), and report a detailed, row-level
breakdown so a validation failure can show total/valid/invalid/unknown/
duplicate counts instead of a bare 400 (V5 "IMPORT ERROR UX").

Import here is always ATOMIC per the V5 default: a caller must check
`result.ok` and persist NOTHING when it is False — .ok is False if
there is any malformed row, any unknown Test Case ID, or any duplicate
Test Case ID in the file, or if the file contains zero usable rows.

This module deliberately does NOT touch the two special formats V5
calls out as staying on their OWN path, never routed through this
shared validator:
  - a real Postman/API collection JSON (api_collection_repository.py's
    own existing parser handles that — V5-05 Mode A);
  - a raw .sql file with no mapping columns at all, bound explicitly to
    one selected Test Case ID by the caller instead (V5-07 Mode B).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from io import BytesIO

SUPPORTED_EXTENSIONS = (".xlsx", ".json", ".txt")

# Every header/key alias this service matches onto "Test Case ID",
# normalized via _normalize_header() below so "Test Case ID", "TC ID",
# "TestCaseID", "Test_Case_Id", "tc_id" etc. all resolve identically —
# reused across every adapter rather than re-implemented per type.
_TEST_CASE_ID_ALIASES = ("testcaseid", "tcid", "testcase", "tcnumber", "tc")

# Type-specific content-column aliases (all normalized the same way).
PLAYWRIGHT_CONTENT_ALIASES = ("script", "playwrightscript")
API_CONTENT_ALIASES = ("apiscript", "api", "script", "apirequest", "request")
SQL_CONTENT_ALIASES = ("script", "sqlscript", "sql")

PLAYWRIGHT_CONTENT_LABEL = "Script"
API_CONTENT_LABEL = "API / Script"
SQL_CONTENT_LABEL = "Script"


def _normalize_header(text) -> str:
    """Same case/spacing/punctuation-insensitive normalization
    TestExecutionManager._IMPORT_FIELD_ALIASES already uses (see
    Core/test_execution_manager.py), reused here so header matching
    behaves identically across every import path in the app instead of
    drifting between two subtly different normalizers."""
    return re.sub(r"[^a-z0-9]", "", str(text or "").lower())


def normalize_test_case_id(value) -> str:
    """Punctuation/case-insensitive key used to MATCH an imported Test
    Case ID against a persisted one — "TC-001", "TC001", "tc 001" all
    resolve to the same key, since the app's own persisted tc_number
    format ("TC001") has no hyphen but users naturally type one."""
    return re.sub(r"[^A-Za-z0-9]", "", str(value or "")).upper()


@dataclass
class ImportRowError:
    row_number: int  # 1-based counting the header as row 1, matching what the user sees in Excel
    reason: str


@dataclass
class ImportValidationResult:
    ok: bool
    total_rows: int
    valid_rows: list = field(default_factory=list)   # [{"test_case_id","matched_test_case","content"}, ...] (only meaningful when ok)
    unknown_ids: list = field(default_factory=list)
    duplicate_ids: list = field(default_factory=list)
    row_errors: list = field(default_factory=list)    # [ImportRowError, ...]
    content_field_label: str = ""

    def to_response(self) -> dict:
        """Shape for the "Import blocked" UX (V5 IMPORT ERROR UX
        section): total/valid/invalid rows, unknown IDs, duplicate IDs
        — never a bare 400 Bad Request."""
        valid_count = len(self.valid_rows) if self.ok else 0
        invalid_count = max(0, self.total_rows - valid_count)
        return {
            "ok": self.ok,
            "total_rows": self.total_rows,
            "valid_rows": valid_count,
            "invalid_rows": invalid_count,
            "unknown_test_case_ids": sorted(set(self.unknown_ids)),
            "duplicate_test_case_ids": sorted(set(self.duplicate_ids)),
            "row_errors": [f"Row {error.row_number}: {error.reason}" for error in self.row_errors],
        }


class AutomationImportError(ValueError):
    """A file the parser cannot read AT ALL (wrong type, corrupt,
    unreadable, empty). Distinct from a per-row validation failure,
    which is reported inside ImportValidationResult rather than
    raised, so the caller can always show the detailed breakdown."""


def _read_xlsx_rows(data: bytes) -> list[dict]:
    from openpyxl import load_workbook
    try:
        workbook = load_workbook(BytesIO(data), data_only=True)
    except Exception as exc:  # noqa: BLE001 - surfaced as a clean import error, not a 500
        raise AutomationImportError(f"Could not read the Excel file: {exc}") from None
    sheet = workbook.worksheets[0]
    rows_iter = sheet.iter_rows(values_only=True)
    try:
        header_row = next(rows_iter)
    except StopIteration:
        raise AutomationImportError("The sheet is empty.")
    headers = [str(cell or "").strip() for cell in header_row]
    rows = []
    for values in rows_iter:
        if values is None or all(v is None or str(v).strip() == "" for v in values):
            continue
        rows.append({
            (headers[i] if i < len(headers) and headers[i] else f"col{i}"): values[i]
            for i in range(len(values))
        })
    return rows


def _read_json_rows(data: bytes) -> list[dict]:
    try:
        parsed = json.loads(data.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AutomationImportError(f"Could not parse JSON: {exc}") from None
    if isinstance(parsed, dict):
        for key in ("rows", "mappings", "data", "items"):
            candidate = parsed.get(key)
            if isinstance(candidate, list):
                parsed = candidate
                break
        else:
            raise AutomationImportError(
                'JSON must be a list of {"Test Case ID": ..., ...} objects, '
                'or an object with a "rows"/"mappings"/"data" list.'
            )
    if not isinstance(parsed, list):
        raise AutomationImportError('JSON must be a list of {"Test Case ID": ..., ...} objects.')
    rows = []
    for entry in parsed:
        if not isinstance(entry, dict):
            raise AutomationImportError("Every JSON row must be an object with named fields.")
        rows.append(entry)
    return rows


def _read_txt_rows(data: bytes) -> list[dict]:
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise AutomationImportError(f"Could not decode text file: {exc}") from None
    lines = [line for line in text.splitlines() if line.strip() != ""]
    if not lines:
        raise AutomationImportError("The file is empty.")
    delimiter = "\t" if "\t" in lines[0] else ("," if "," in lines[0] else None)
    if delimiter is None:
        raise AutomationImportError(
            "Plain text import must be tab- or comma-delimited with a header row."
        )
    header = [cell.strip() for cell in lines[0].split(delimiter)]
    rows = []
    for line in lines[1:]:
        cells = [cell.strip() for cell in line.split(delimiter)]
        rows.append({
            (header[i] if i < len(header) and header[i] else f"col{i}"): (cells[i] if i < len(cells) else "")
            for i in range(len(cells))
        })
    return rows


def read_mapping_file(data: bytes, filename: str) -> list[dict]:
    """Read a supported (.xlsx/.json/.txt) mapping file into a list of
    raw {header: value} row dicts. No field normalization or Test Case
    ID cross-validation yet — see normalize_rows()/AutomationImportService.validate()."""
    suffix = ("." + filename.rsplit(".", 1)[-1].lower()) if "." in filename else ""
    if suffix == ".xlsx":
        return _read_xlsx_rows(data)
    if suffix == ".json":
        return _read_json_rows(data)
    if suffix == ".txt":
        return _read_txt_rows(data)
    raise AutomationImportError(
        f'Unsupported file type "{suffix or filename}". Supported: {", ".join(SUPPORTED_EXTENSIONS)}.'
    )


def normalize_rows(raw_rows: list[dict], content_field_aliases: tuple) -> tuple[list[dict], list[ImportRowError]]:
    """Map each raw row's headers/keys onto ("test_case_id", "content")
    via normalized alias matching. A row with no usable Test Case ID at
    all becomes a row_error (malformed) rather than being silently
    dropped, per V5's "Report malformed rows" requirement."""
    normalized_rows = []
    row_errors = []
    for index, raw in enumerate(raw_rows):
        row_number = index + 2  # the header is row 1 in the user's file
        id_value = None
        content_value = None
        for key, value in raw.items():
            normalized_key = _normalize_header(key)
            if id_value is None and normalized_key in _TEST_CASE_ID_ALIASES:
                id_value = value
            if content_value is None and normalized_key in content_field_aliases:
                content_value = value
        test_case_id = str(id_value if id_value is not None else "").strip()
        if not test_case_id:
            row_errors.append(ImportRowError(row_number, "Missing or empty Test Case ID."))
            continue
        content = str(content_value).strip() if content_value not in (None, "") else None
        normalized_rows.append({"row_number": row_number, "test_case_id": test_case_id, "content": content})
    return normalized_rows, row_errors


class AutomationImportService:
    """Shared Test-Case-ID mapping import validator for Playwright/API/
    SQL (V5 cross-cutting "SHARED IMPORT SERVICE" requirement)."""

    def __init__(self, repository=None):
        if repository is None:
            from Core.test_case_repository import TestCaseRepository
            repository = TestCaseRepository()
        self.repository = repository

    def _persisted_test_case_ids(self, test_case_version_id: int, test_case_file_id: int) -> dict:
        """{normalized Test Case ID: persisted test_cases row} for
        every test case in the SELECTED Test Cases workbook/version —
        matched against BOTH tc_number ("TC001", the number shown in
        the grid's "TC #" column) and tc_id (the internal allocated
        id), whichever a persisted row has, so either style of
        imported ID resolves correctly."""
        connection = self.repository.db.get_connection()
        file_row = connection.execute(
            "SELECT workbook_path FROM test_case_files WHERE id=? AND test_case_version_id=?",
            (int(test_case_file_id), int(test_case_version_id)),
        ).fetchone()
        connection.close()
        if not file_row:
            raise AutomationImportError("The selected Test Cases XLSX does not belong to the selected TC folder.")
        result = self.repository.list_all_test_cases(
            test_case_version_id=test_case_version_id,
            reviewed_workbook_path=file_row[0], limit=10000, offset=0,
        )
        valid_ids = {}
        for row in result.get("test_cases", []):
            for candidate in (row.get("tc_number"), row.get("tc_id")):
                if candidate:
                    valid_ids[normalize_test_case_id(candidate)] = row
        return valid_ids

    def validate(self, file_bytes: bytes, filename: str, test_case_version_id: int,
                 test_case_file_id: int,
                 content_field_aliases: tuple, content_field_label: str) -> ImportValidationResult:
        raw_rows = read_mapping_file(file_bytes, filename)
        if not raw_rows:
            return ImportValidationResult(
                ok=False, total_rows=0,
                row_errors=[ImportRowError(1, "The file contains no data rows.")],
                content_field_label=content_field_label,
            )

        normalized_rows, row_errors = normalize_rows(raw_rows, content_field_aliases)
        valid_ids = self._persisted_test_case_ids(test_case_version_id, test_case_file_id)

        seen_keys = set()
        duplicate_ids = []
        unknown_ids = []
        valid_rows = []
        for row in normalized_rows:
            key = normalize_test_case_id(row["test_case_id"])
            if key in seen_keys:
                duplicate_ids.append(row["test_case_id"])
                continue
            seen_keys.add(key)
            matched = valid_ids.get(key)
            if not matched:
                unknown_ids.append(row["test_case_id"])
                continue
            valid_rows.append({
                "test_case_id": row["test_case_id"],
                "matched_test_case": matched,
                "content": row["content"],
            })

        ok = not row_errors and not unknown_ids and not duplicate_ids and bool(valid_rows)
        return ImportValidationResult(
            ok=ok, total_rows=len(raw_rows), valid_rows=valid_rows,
            unknown_ids=unknown_ids, duplicate_ids=duplicate_ids, row_errors=row_errors,
            content_field_label=content_field_label,
        )


def generate_sample_template(
    content_column_label: str, filename_hint: str = "Import_Template",
    example_test_case_id: str = "TC-EXAMPLE-DELETE-ME",
    example_content: str = "-- example row: delete this line before importing --",
) -> bytes:
    """ONE shared template-generation utility (V5 "SAMPLE TEMPLATE
    RULE") — every adapter (Playwright/API/SQL) calls this with only
    its own content column label, so the three sample templates can
    never drift out of shape from one another.

    The example row uses an ID ("TC-EXAMPLE-DELETE-ME") that is
    guaranteed not to match any real persisted Test Case ID format, so
    if a user forgets to delete it before importing, AutomationImportService
    .validate() reports it as an UNKNOWN Test Case ID and atomically
    blocks the import — it can never be silently imported as real data.
    """
    from openpyxl import Workbook

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Import Template"
    sheet.append(["Test Case ID", content_column_label])
    sheet.append([example_test_case_id, example_content])
    sheet.column_dimensions["A"].width = 26
    sheet.column_dimensions["B"].width = 60
    buffer = BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def generate_playwright_json_template(test_case_id: str, script: str) -> bytes:
    """Build the protected Playwright-only JSON template/import artifact."""
    return (
        json.dumps(
            [{"Test Case ID": str(test_case_id), "Script": script or ""}],
            ensure_ascii=False, indent=2,
        ) + "\n"
    ).encode("utf-8")
