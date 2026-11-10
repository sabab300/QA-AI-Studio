"""Canonical QA Engineering XLSX contract and safe workbook I/O."""

from __future__ import annotations

import os
import re
import tempfile
from io import BytesIO
from pathlib import Path

from openpyxl import Workbook, load_workbook


SHEET_NAME = "Test Cases"
COLUMNS = (
    ("tc_number", "Test Case ID"),
    ("scenario", "Requirement/Scenario"),
    ("importance", "Importance"),
    ("test_type", "Test Types"),
    ("test_case", "Test Case"),
    ("pre_conditions", "Preconditions"),
    ("steps", "Test Steps"),
    ("expected_result", "Expected Result"),
    ("actual_result", "Actual Result"),
    ("execution", "Execution Type"),
    ("status", "Status"),
    ("comments", "Comments"),
)
PUBLISH_COLUMNS = COLUMNS[:-1] + (
    ("publishing_status", "Publish"),
    ("clickup_id", "Clickup ID"),
    ("testmanager_id", "TestManager ID"),
) + COLUMNS[-1:]
# One template-token contract for mapping UI, validation, preview and publish.
TEMPLATE_TOKEN_ALIASES = {
    "requirement_scenario": "scenario",
    "test_cases": "test_case",
    "execution_type": "execution",
}
DEFAULT_CLICKUP_DESCRIPTION_TEMPLATE = """Test Case:
{{tc_number}}

Scenario:
{{scenario}}

Test Case Details:
{{test_case}}

Expected Result:
{{expected_result}}

Actual Result:
{{actual_result}}

Execution Type:
{{execution}}
Status:
{{status}}

Source:
QA AI Studio"""


def canonical_template_token(token: str) -> str:
    value = str(token or "").strip()
    return TEMPLATE_TOKEN_ALIASES.get(value, value)


def normalize_template_tokens(template: str) -> str:
    return re.sub(
        r"{{\s*([^{}]+?)\s*}}",
        lambda match: "{{" + canonical_template_token(match.group(1)) + "}}",
        str(template or ""),
    )
HEADERS = tuple(label for _, label in COLUMNS)
HEADER_TO_FIELD = {label.casefold(): field for field, label in COLUMNS}
HEADER_TO_FIELD.update({
    "execution": "execution",
    "method": "execution",
    "execution method": "execution",
    "result": "status",
})
REQUIRED_FIELDS = (
    "scenario", "test_type", "pre_conditions", "test_case", "steps",
    "expected_result", "importance", "execution",
)
EXECUTION_VALUES = ("Manual", "Playwright", "API", "SQL")
STATUS_VALUES = ("Not Executed", "Pass", "Fail")


def normalize_execution(values) -> str:
    """Map legacy names/values to the canonical Execution Type field."""
    value = str(
        values.get("execution") or values.get("execution_type")
        or values.get("execution_method") or values.get("method") or ""
    ).strip()
    aliases = {"api automation": "API", "sql automation": "SQL",
               "manual execution": "Manual", "manual": "Manual",
               "playwright": "Playwright", "api": "API", "sql": "SQL"}
    normalized = aliases.get(value.casefold(), value)
    if normalized in EXECUTION_VALUES:
        return normalized
    if normalized.casefold() in {"", "not executed", "not run", "none"}:
        return "Manual"
    return normalized


def normalize_status(values) -> str:
    """Return the canonical Test Case execution status, preserving run fields."""
    explicit = values.get("test_case_status") or values.get("execution_result") or values.get("result")
    stored_status = values.get("status")
    status_word = str(stored_status or "").strip().casefold().replace("_", " ")
    raw = explicit or (stored_status if status_word in {"pass", "fail", "not executed"} else None) \
        or values.get("last_result") or stored_status or ""
    value = str(raw).strip().casefold().replace("_", " ")
    if value in {"pass", "passed", "success", "successful"}:
        return "Pass"
    if value in {"fail", "failed", "failure", "error"}:
        return "Fail"
    if value in {"", "not executed", "not run", "manual", "draft", "automated", "ready", "pending"}:
        return "Not Executed"
    return str(raw).strip()


def normalize_test_case(values) -> dict:
    """Return one canonical Test Case representation."""
    row = dict(values or {})
    execution = normalize_execution(row)
    row["execution"] = execution
    row["status"] = normalize_status(row)
    return row


def workbook_bytes(rows=(), *, columns=COLUMNS) -> bytes:
    book = Workbook()
    sheet = book.active
    sheet.title = SHEET_NAME
    sheet.append([label for _, label in columns])
    for row in rows:
        row = normalize_test_case(row)
        sheet.append([row.get(field, "") for field, _ in columns])
    sheet.freeze_panes = "A2"
    sheet.auto_filter.ref = sheet.dimensions
    stream = BytesIO()
    book.save(stream)
    return stream.getvalue()


def stage_workbook(rows, destination: str | Path) -> Path:
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(
        prefix=f".{destination.stem}-", suffix=".tmp.xlsx", dir=destination.parent
    )
    os.close(descriptor)
    staged = Path(name)
    staged.write_bytes(workbook_bytes(rows))
    # Prove that the staged file is readable before it can replace anything.
    load_workbook(staged, read_only=True, data_only=False).close()
    return staged


def validate_import(data: bytes, filename: str) -> list[dict]:
    if Path(filename or "").suffix.lower() != ".xlsx":
        raise ValueError("External Test Case Import requires an .xlsx file.")
    try:
        book = load_workbook(BytesIO(data), read_only=True, data_only=False)
    except Exception as error:
        raise ValueError(f"Workbook cannot be opened: {error}") from error
    try:
        if SHEET_NAME not in book.sheetnames:
            raise ValueError(f"Required sheet '{SHEET_NAME}' was not found.")
        sheet = book[SHEET_NAME]
        raw_headers = [str(cell.value or "").strip() for cell in next(sheet.iter_rows(min_row=1, max_row=1))]
        normalized = [value.casefold() for value in raw_headers]
        mapped = [HEADER_TO_FIELD.get(value, value) for value in normalized if value]
        duplicates = sorted({value for value in mapped if mapped.count(value) > 1})
        if duplicates:
            raise ValueError("Duplicate column header(s): " + ", ".join(duplicates) + ".")
        present_fields = {HEADER_TO_FIELD[value] for value in normalized if value in HEADER_TO_FIELD}
        missing = [label for field, label in COLUMNS if field not in present_fields]
        if missing:
            raise ValueError("Missing required canonical column(s): " + ", ".join(missing) + ".")
        index = {HEADER_TO_FIELD[value]: position for position, value in enumerate(normalized) if value in HEADER_TO_FIELD}
        seen_ids, rows, errors = set(), [], []
        for excel_row, cells in enumerate(sheet.iter_rows(min_row=2, values_only=True), start=2):
            if not any(value not in (None, "") for value in cells):
                continue
            row = {field: cells[position] if position < len(cells) else "" for field, position in index.items()}
            row = {key: str(value or "").strip() for key, value in row.items()}
            for field in REQUIRED_FIELDS:
                if not row.get(field):
                    errors.append(f"{dict(COLUMNS)[field]} is missing in row {excel_row}.")
            row = normalize_test_case(row)
            tc_number = row.get("tc_number", "")
            if tc_number:
                if not re.fullmatch(r"TC(?:\d+|_[A-Z0-9_]+)", tc_number.upper()):
                    errors.append(
                        f"Row {excel_row}, Test Case ID: use a canonical TC number or repository TC identifier."
                    )
                elif tc_number.upper() in seen_ids:
                    errors.append(f"Row {excel_row}, Test Case ID: duplicate '{tc_number}'.")
                seen_ids.add(tc_number.upper())
            if row.get("execution") not in EXECUTION_VALUES:
                errors.append(
                    f"Invalid Execution Type value '{row.get('execution')}' in row {excel_row}. "
                    f"Allowed values: {', '.join(EXECUTION_VALUES)}."
                )
            if row.get("status") not in STATUS_VALUES:
                errors.append(
                    f"Invalid Status value '{row.get('status')}' in row {excel_row}. "
                    "Allowed values are Not Executed, Pass, or Fail."
                )
            if row.get("importance") not in {"High", "Medium", "Low"}:
                errors.append(f"Row {excel_row}, Importance: use High, Medium, or Low.")
            row["test_types"] = [value.strip() for value in row.get("test_type", "").split(",") if value.strip()]
            if not any(message.startswith(f"Row {excel_row},") for message in errors):
                rows.append(row)
        if errors:
            raise ValueError("\n".join(errors[:50]))
        if not rows:
            raise ValueError("Workbook contains no Test Case rows.")
        return rows
    finally:
        book.close()
