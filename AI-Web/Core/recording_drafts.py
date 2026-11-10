# Create: AI-Web/Core/recording_drafts.py

"""
QA AI Studio
Recording Drafts

Version: 1.0

An incomplete recording (it timed out, the recorder page crashed, the
operator's connection dropped, or nothing but the initial navigation was
captured) must never be written into the test case as if it were a
finished recording.  The recorder instead preserves whatever it captured
here, as a draft file, and the operator confirms explicitly whether that
draft may be saved.  Nothing in this module touches a test case: saving
a confirmed draft is done by the caller through
TestCaseRepository.update_recorded_script().
"""

import json
import re
import threading
import uuid
from datetime import datetime
from pathlib import Path

DRAFT_DIR = Path(__file__).resolve().parent.parent / "Output" / "RecordingDrafts"

INCOMPLETE_MARKER = "# QA_RECORDING_STATUS: INCOMPLETE"

_LOCK = threading.Lock()
_ID_RE = re.compile(r"^[A-Za-z0-9_-]{6,64}$")


def _path(draft_id):
    if not _ID_RE.match(draft_id or ""):
        return None
    return DRAFT_DIR / f"{draft_id}.json"


def save_draft(test_case_id, script, steps, reasons, reason_code, meaningful_actions,
               total_actions, script_issues=None):
    draft_id = f"rd_{test_case_id}_{datetime.now().strftime('%Y%m%d%H%M%S')}_{uuid.uuid4().hex[:6]}"
    record = {
        "draft_id": draft_id,
        "test_case_id": int(test_case_id),
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "status": "incomplete",
        "reason_code": reason_code,
        "reasons": list(reasons),
        "meaningful_actions": int(meaningful_actions),
        "total_actions": int(total_actions),
        "steps": list(steps)[:200],
        "script": script or "",
        "script_issues": list(script_issues or []),
    }
    with _LOCK:
        DRAFT_DIR.mkdir(parents=True, exist_ok=True)
        _path(draft_id).write_text(json.dumps(record, indent=2), encoding="utf-8")
    return record


def get_draft(draft_id):
    path = _path(draft_id)
    if path is None or not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def list_drafts(test_case_id, include_script=False):
    items = []
    if not DRAFT_DIR.exists():
        return items
    for path in sorted(DRAFT_DIR.glob(f"rd_{int(test_case_id)}_*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if int(data.get("test_case_id", -1)) != int(test_case_id):
            continue
        if not include_script:
            data = {key: value for key, value in data.items() if key != "script"}
        items.append(data)
    return items


def delete_draft(draft_id):
    path = _path(draft_id)
    if path is None or not path.exists():
        return False
    with _LOCK:
        path.unlink()
    return True


def mark_incomplete_script(script, reasons, saved_by=""):
    """Prefix a confirmed-incomplete script with a visible status marker."""

    text = script or ""
    if text.lstrip().startswith(INCOMPLETE_MARKER):
        return text
    reason_text = "; ".join(reasons) or "incomplete recording"
    who = f" by {saved_by}" if saved_by else ""
    stamp = datetime.now().isoformat(timespec="seconds")
    header = (
        f"{INCOMPLETE_MARKER} ({reason_text})\n"
        f"# Saved{who} on {stamp} after explicit confirmation. Review and complete "
        "this script before activating it.\n"
    )
    return header + text


def is_marked_incomplete(script):
    return (script or "").lstrip().startswith(INCOMPLETE_MARKER)
