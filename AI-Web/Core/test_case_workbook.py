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
    ("execution", "Execution"),
    ("status", "Status"),
    ("bug_id", "Bug ID"),
    ("comments", "Comments"),
)
HEADERS = tuple(label for _, label in COLUMNS)
HEADER_TO_FIELD = {label.casefold(): field for field, label in COLUMNS}
REQUIRED_FIELDS = (
    "tc_number", "scenario", "test_type", "pre_conditions", "test_case", "steps",
    "expected_result", "importance", "execution",
)
EXECUTION_VALUES = ("Manual", "Playwright", "API", "SQL")
STATUS_VALUES = ("Pass", "Fail")


def normalize_execution(values) -> str:
    """Map the legacy type/tool pair to the one canonical Execution field."""
    value = str(values.get("execution") or "").strip()
    aliases = {"API Automation": "API", "SQL Automation": "SQL",
               "Manual Execution": "Manual", "manual": "Manual",
               "playwright": "Playwright", "api": "API", "sql": "SQL"}
    value = aliases.get(value, value)
    return aliases.get(value, value or "Manual")


def normalize_test_case(values) -> dict:
    """Return one canonical Test Case representation."""
    row = dict(values or {})
    execution = normalize_execution(row)
    row["execution"] = execution
    status = str(row.get("status") or "").strip()
    row["status"] = "" if status in {"Manual", "Not Run"} else status
    return row


def workbook_bytes(rows=()) -> bytes:
    book = Workbook()
    sheet = book.active
    sheet.title = SHEET_NAME
    sheet.append(list(HEADERS))
    for row in rows:
        row = normalize_test_case(row)
        sheet.append([row.get(field, "") for field, _ in COLUMNS])
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
        duplicates = sorted({value for value in normalized if value and normalized.count(value) > 1})
        if duplicates:
            raise ValueError("Duplicate column header(s): " + ", ".join(duplicates) + ".")
        missing = [label for _, label in COLUMNS if label.casefold() not in normalized]
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
                    f"Invalid Execution value '{row.get('execution')}' in row {excel_row}. "
                    f"Allowed values: {', '.join(EXECUTION_VALUES)}."
                )
            if row.get("status") and row.get("status") not in STATUS_VALUES:
                errors.append(
                    f"Invalid Status value '{row.get('status')}' in row {excel_row}. "
                    "Allowed values are Pass, Fail, or blank."
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
