"""Automatic format detection (user can always override)."""
from __future__ import annotations

import json
import re

from Core.script_import.models import (
    FORMAT_QA_JSON, FORMAT_CHROME_RECORDER, FORMAT_PYTHON, FORMAT_JAVASCRIPT,
    FORMAT_TYPESCRIPT, FORMAT_UNKNOWN,
)

_ID_KEYS = {"testcaseid", "tcid", "testcase", "tcnumber", "tc"}
_SCRIPT_KEYS = {"script", "playwrightscript"}
_CHROME_STEP_TYPES = {
    "navigate", "click", "doubleClick", "change", "keyDown", "keyUp", "hover",
    "scroll", "setViewport", "waitForElement", "waitForExpression",
    "emulateNetworkConditions", "customStep", "close",
}


def decode_bytes(data: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-16", "cp1252"):
        try:
            return data.decode(encoding)
        except (UnicodeDecodeError, UnicodeError):
            continue
    return data.decode("utf-8", errors="replace")


def _norm(key) -> str:
    return re.sub(r"[^a-z0-9]", "", str(key).lower())


def _looks_like_chrome_recording(obj) -> bool:
    if not isinstance(obj, dict):
        return False
    steps = obj.get("steps")
    if not isinstance(steps, list) or not steps:
        return False
    typed = [s for s in steps if isinstance(s, dict) and s.get("type") in _CHROME_STEP_TYPES]
    return len(typed) >= max(1, len(steps) // 2)


def _looks_like_qa_rows(obj) -> bool:
    rows = obj if isinstance(obj, list) else obj.get("tests") if isinstance(obj, dict) else None
    if not isinstance(rows, list) or not rows or not all(isinstance(r, dict) for r in rows):
        return False
    first = {_norm(k) for k in rows[0]}
    return bool(first & _ID_KEYS) and bool(first & _SCRIPT_KEYS)


def detect_format(filename: str, text: str) -> dict:
    """Return {format, confidence, reasons, alternatives}."""
    name = (filename or "").lower()
    ext = name.rsplit(".", 1)[-1] if "." in name else ""
    reasons = []
    stripped = (text or "").lstrip()

    if ext == "json" or stripped[:1] in "{[":
        try:
            obj = json.loads(text)
        except (ValueError, TypeError):
            obj = None
            reasons.append("Content looks like JSON but could not be parsed.")
        if obj is not None:
            if _looks_like_chrome_recording(obj):
                return {"format": FORMAT_CHROME_RECORDER, "confidence": "high",
                        "reasons": ["JSON with a 'steps' array of Chrome Recorder step types."],
                        "alternatives": []}
            if isinstance(obj, list) and obj and all(_looks_like_chrome_recording(o) for o in obj):
                return {"format": FORMAT_CHROME_RECORDER, "confidence": "high",
                        "reasons": [f"JSON array of {len(obj)} Chrome Recorder recordings."],
                        "alternatives": []}
            if _looks_like_qa_rows(obj):
                return {"format": FORMAT_QA_JSON, "confidence": "high",
                        "reasons": ["JSON rows with a Test Case ID and a Script column."],
                        "alternatives": []}
            return {"format": FORMAT_UNKNOWN, "confidence": "none",
                    "reasons": ["Valid JSON, but neither a Chrome Recorder recording nor Test Case ID/Script rows."],
                    "alternatives": [FORMAT_CHROME_RECORDER, FORMAT_QA_JSON]}
        return {"format": FORMAT_UNKNOWN, "confidence": "none", "reasons": reasons,
                "alternatives": [FORMAT_CHROME_RECORDER, FORMAT_QA_JSON]}

    py_markers = bool(re.search(r"^\s*(from\s+playwright|import\s+playwright)", text, re.M)) or \
        "sync_playwright" in text or "async_playwright" in text
    js_markers = bool(re.search(r"@playwright/test|require\(['\"]playwright|from ['\"]playwright", text))
    ts_markers = bool(re.search(r":\s*(Page|Locator|Browser|BrowserContext)\b|\bas\s+const\b|\binterface\s+\w+|:\s*(string|number|boolean)\b", text))

    if ext == "py":
        return {"format": FORMAT_PYTHON, "confidence": "high" if py_markers else "medium",
                "reasons": [".py extension" + (" with Playwright imports." if py_markers else " but no Playwright import found.")],
                "alternatives": []}
    if ext in ("ts", "mts", "cts"):
        return {"format": FORMAT_TYPESCRIPT, "confidence": "high" if js_markers else "medium",
                "reasons": [f".{ext} extension" + (" with @playwright/test import." if js_markers else ".")],
                "alternatives": [FORMAT_JAVASCRIPT]}
    if ext in ("js", "mjs", "cjs"):
        return {"format": FORMAT_JAVASCRIPT, "confidence": "high" if js_markers else "medium",
                "reasons": [f".{ext} extension" + (" with @playwright/test import." if js_markers else ".")],
                "alternatives": [FORMAT_TYPESCRIPT] if ts_markers else []}

    # Unknown / missing extension: sniff.
    if py_markers and not js_markers:
        return {"format": FORMAT_PYTHON, "confidence": "medium", "reasons": ["Python Playwright imports detected."], "alternatives": []}
    if js_markers:
        fmt = FORMAT_TYPESCRIPT if ts_markers else FORMAT_JAVASCRIPT
        return {"format": fmt, "confidence": "medium", "reasons": ["@playwright/test usage detected."],
                "alternatives": [FORMAT_JAVASCRIPT if fmt == FORMAT_TYPESCRIPT else FORMAT_TYPESCRIPT]}
    return {"format": FORMAT_UNKNOWN, "confidence": "none",
            "reasons": ["Could not recognise the file as a supported Playwright script format."],
            "alternatives": [FORMAT_PYTHON, FORMAT_JAVASCRIPT, FORMAT_TYPESCRIPT, FORMAT_CHROME_RECORDER, FORMAT_QA_JSON]}
