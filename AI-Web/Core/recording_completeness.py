# Create: AI-Web/Core/recording_completeness.py

"""
QA AI Studio
Recording completeness (skipped-action) control

Version: 1.0

Task 1 critical reliability fix.  When the recorder cannot give a user action any
locator the action is NOT in the saved script.  A script that silently omits a step
can pass Validate / Set Active / Execute and report a false PASS, so every recording
that contains skipped actions is persisted here, next to the script, as an OPEN
(incomplete) record that:

* makes Validate report ``recording_complete`` = Not Ready,
* blocks Set Active and every Execute path (plain, bulk and interactive replay),
* can be cleared only by the controlled correction workflow: the script must really
  have changed since it was recorded and must contain at least as many more
  executable steps as actions were skipped; the operator then explicitly confirms the
  correction (with a note) and must Validate again.  A bare acknowledgement is refused
  and the refusal itself is written to the history.

The table is append-only audit data: rows are never deleted, the original skipped
count is never edited, and every transition (RECORDED, CONFIRMATION_REJECTED,
CORRECTION_CONFIRMED, SUPERSEDED, REMOVED) is appended to the row's history.

Nothing here knows about any application, site or locator; it only counts skipped
user actions and executable Playwright statements.
"""

import ast
import json
import sqlite3
import threading
from datetime import datetime

OPEN = "OPEN"
CORRECTED = "CORRECTED"
SUPERSEDED = "SUPERSEDED"
REMOVED = "REMOVED"

_LOCK = threading.RLock()

# Method names that perform a user-visible step.  Counting these (not comments, not
# set-up lines) is what makes "restore the missing steps" checkable without knowing
# anything about the recorded application.
_ACTION_METHODS = {
    "goto", "click", "dblclick", "fill", "type", "press", "check", "uncheck", "set_checked",
    "select_option", "set_input_files", "hover", "tap", "drag_to", "focus", "clear",
    "dispatch_event", "press_sequentially",
}


class CompletenessError(ValueError):
    """Message is safe to show to the operator."""


def count_script_steps(script):
    """Number of executable user-action statements in a Playwright script.

    Returns None when the script does not parse (it cannot then be confirmed).
    """

    try:
        tree = ast.parse(script or "")
    except SyntaxError:
        return None
    count = 0
    for node in ast.walk(tree):
        if not isinstance(node, ast.Expr):
            continue
        call = node.value
        if isinstance(call, ast.Await):
            call = call.value
        if not isinstance(call, ast.Call):
            continue
        func = call.func
        if isinstance(func, ast.Attribute) and func.attr in _ACTION_METHODS:
            count += 1
    return count


def _now():
    return datetime.now().isoformat(timespec="seconds")


class RecordingCompleteness:
    """Persistence + rules.  One instance per call site; stateless apart from the repository."""

    def __init__(self, repository=None):
        if repository is None:
            from Core.test_case_repository import TestCaseRepository
            repository = TestCaseRepository()
        self.repo = repository

    # ------------------------------------------------------------ plumbing

    def _connect(self):
        conn = self.repo.db.get_connection()
        conn.row_factory = self.repo._dict_factory
        conn.execute(
            """CREATE TABLE IF NOT EXISTS recording_completeness (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                scope_key TEXT NOT NULL,
                tc_id TEXT NOT NULL,
                source TEXT NOT NULL DEFAULT 'MANUAL',
                status TEXT NOT NULL,
                original_skipped_count INTEGER NOT NULL,
                skipped_actions_json TEXT NOT NULL DEFAULT '[]',
                recorded_fingerprint TEXT NOT NULL DEFAULT '',
                recorded_step_count INTEGER NOT NULL DEFAULT 0,
                recorded_by TEXT,
                recorded_at TEXT NOT NULL,
                resolved_at TEXT,
                history_json TEXT NOT NULL DEFAULT '[]',
                updated_at TEXT NOT NULL
            )"""
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_recording_completeness_scope "
            "ON recording_completeness(scope_key, tc_id, source)"
        )
        return conn

    def _row_and_scope(self, conn, test_case_id):
        row = conn.execute("SELECT * FROM test_cases WHERE id=?", (int(test_case_id),)).fetchone()
        if not row:
            raise CompletenessError(f"Test case {test_case_id} not found.")
        identity = self.repo._playwright_identity(conn.cursor(), row)
        if identity:
            scope_key, source_id, tc_id = identity
        else:
            scope_key, source_id, tc_id = f"id:{int(test_case_id)}", None, str(int(test_case_id))
        return row, scope_key, source_id, tc_id

    @staticmethod
    def _latest(conn, scope_key, tc_id, source):
        return conn.execute(
            "SELECT * FROM recording_completeness WHERE scope_key=? AND tc_id=? AND source=? "
            "ORDER BY id DESC LIMIT 1",
            (scope_key, tc_id, source),
        ).fetchone()

    @staticmethod
    def _append(conn, record, event, status=None, resolved=False):
        history = json.loads(record.get("history_json") or "[]")
        history.append({"at": _now(), **event})
        conn.execute(
            "UPDATE recording_completeness SET history_json=?, status=?, resolved_at=?, updated_at=? WHERE id=?",
            (
                json.dumps(history),
                status or record["status"],
                _now() if resolved else record.get("resolved_at"),
                _now(),
                record["id"],
            ),
        )

    def _current_script(self, conn, test_case_id, source="MANUAL"):
        item = self.repo.get_test_case(test_case_id) or {}
        return {"AUTO": item.get("automation_script"), "UPLOADED": item.get("uploaded_script"),
                "MANUAL": item.get("recorded_script")}.get(source) or ""

    def _clear_validation_and_demote(self, conn, scope_key, source_id, tc_id, test_case_id):
        """Force a fresh Validate and make sure the test case cannot stay Automated on this script."""

        now = datetime.now().isoformat()
        asset = conn.execute(
            "SELECT active_script_source FROM playwright_automation_assets WHERE stable_scope_key=? AND tc_id=?",
            (scope_key, tc_id),
        ).fetchone() if source_id is not None else None
        if asset is not None:
            demote = (asset.get("active_script_source") or "AUTO").upper() == "MANUAL"
            conn.execute(
                "UPDATE playwright_automation_assets SET manual_script_validated_hash=''"
                + (",lifecycle_status='Draft'" if demote else "")
                + ",modified_date=? WHERE stable_scope_key=? AND tc_id=?",
                (now, scope_key, tc_id),
            )
            conn.execute(
                """UPDATE test_cases SET manual_script_validated_hash=''"""
                + (",status='Draft'" if demote else "")
                + """ WHERE id IN (SELECT tc.id FROM test_cases tc JOIN test_case_versions v
                       ON v.id=tc.test_case_version_id WHERE v.source_knowledge_id=? AND tc.tc_id=?)""",
                (source_id, tc_id),
            )
        else:
            row = conn.execute(
                "SELECT active_script_source FROM test_cases WHERE id=?", (int(test_case_id),)
            ).fetchone()
            demote = row and (row.get("active_script_source") or "AUTO").upper() == "MANUAL"
            conn.execute(
                "UPDATE test_cases SET manual_script_validated_hash=''"
                + (",status='Draft'" if demote else "")
                + " WHERE id=?",
                (int(test_case_id),),
            )

    # ------------------------------------------------------------ recording hooks

    def record_skipped(self, test_case_id, script_text, skipped_actions, actor="", source="MANUAL"):
        """Persist a recording that contains skipped actions as an OPEN (incomplete) record.

        Call BEFORE the script itself is saved so there is never a moment where the script
        exists without its block.  Returns the new record id.
        """

        skipped = [dict(item) for item in (skipped_actions or [])]
        if not skipped:
            raise CompletenessError("No skipped actions to record.")
        with _LOCK:
            conn = self._connect()
            try:
                conn.execute("BEGIN IMMEDIATE")
                _, scope_key, source_id, tc_id = self._row_and_scope(conn, test_case_id)
                previous = conn.execute(
                    "SELECT * FROM recording_completeness WHERE scope_key=? AND tc_id=? AND source=? AND status=?",
                    (scope_key, tc_id, source, OPEN),
                ).fetchall()
                for old in previous:
                    self._append(conn, old, {"event": "SUPERSEDED", "by": actor or "",
                                             "detail": "A newer recording replaced this one."},
                                 status=SUPERSEDED, resolved=True)
                steps = count_script_steps(script_text) or 0
                history = [{
                    "at": _now(), "event": "RECORDED", "actor": actor or "",
                    "skipped_actions": len(skipped), "recorded_steps": steps,
                    "detail": f"{len(skipped)} user action(s) could not be recorded; the script is incomplete.",
                }]
                cursor = conn.execute(
                    """INSERT INTO recording_completeness
                       (scope_key,tc_id,source,status,original_skipped_count,skipped_actions_json,
                        recorded_fingerprint,recorded_step_count,recorded_by,recorded_at,history_json,updated_at)
                       VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (scope_key, tc_id, source, OPEN, len(skipped), json.dumps(skipped),
                     self.repo.script_fingerprint(script_text), steps, actor or "", _now(),
                     json.dumps(history), _now()),
                )
                record_id = cursor.lastrowid
                self._clear_validation_and_demote(conn, scope_key, source_id, tc_id, test_case_id)
                conn.commit()
                return record_id
            except Exception:
                conn.rollback()
                raise
            finally:
                conn.close()

    def discard_record(self, record_id, reason):
        """Close a record whose script could not be saved (never leave a block for a script that is not there)."""

        with _LOCK:
            conn = self._connect()
            try:
                conn.execute("BEGIN IMMEDIATE")
                record = conn.execute("SELECT * FROM recording_completeness WHERE id=?", (int(record_id),)).fetchone()
                if record and record["status"] == OPEN:
                    self._append(conn, record, {"event": "REMOVED", "detail": reason}, status=REMOVED, resolved=True)
                conn.commit()
            except Exception:
                conn.rollback()
            finally:
                conn.close()

    def supersede_open(self, test_case_id, actor="", source="MANUAL", detail="A complete recording replaced it."):
        """A new, complete recording replaced the script: the old incomplete record no longer applies."""

        with _LOCK:
            conn = self._connect()
            try:
                conn.execute("BEGIN IMMEDIATE")
                _, scope_key, _, tc_id = self._row_and_scope(conn, test_case_id)
                record = self._latest(conn, scope_key, tc_id, source)
                if record and record["status"] == OPEN:
                    self._append(conn, record, {"event": "SUPERSEDED", "by": actor or "", "detail": detail},
                                 status=SUPERSEDED, resolved=True)
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            finally:
                conn.close()

    # ------------------------------------------------------------ state

    def _reconcile_removed(self, conn, record, script):
        """The slot was emptied (Remove Script / rollback to nothing): the record no longer applies."""

        if record and record["status"] == OPEN and not (script or "").strip():
            self._append(conn, record, {"event": "REMOVED", "detail": "The recorded script was removed."},
                         status=REMOVED, resolved=True)
            return conn.execute("SELECT * FROM recording_completeness WHERE id=?", (record["id"],)).fetchone()
        return record

    @staticmethod
    def _public(record, current_script_steps=None):
        if not record:
            return None
        skipped = json.loads(record.get("skipped_actions_json") or "[]")
        data = {
            "id": record["id"], "source": record["source"], "status": record["status"],
            "original_skipped_count": record["original_skipped_count"],
            "skipped_actions": skipped,
            "recorded_steps": record["recorded_step_count"],
            "required_steps": record["recorded_step_count"] + record["original_skipped_count"],
            "recorded_by": record.get("recorded_by") or "", "recorded_at": record["recorded_at"],
            "resolved_at": record.get("resolved_at"),
            "history": json.loads(record.get("history_json") or "[]"),
        }
        if current_script_steps is not None:
            data["current_steps"] = current_script_steps
        return data

    def get_state(self, test_case_id, source="MANUAL"):
        source = str(source or "MANUAL").upper()
        script = self._current_script(None, test_case_id, source)
        with _LOCK:
            conn = self._connect()
            try:
                conn.execute("BEGIN IMMEDIATE")
                _, scope_key, _, tc_id = self._row_and_scope(conn, test_case_id)
                latest = self._reconcile_removed(conn, self._latest(conn, scope_key, tc_id, source), script)
                rows = conn.execute(
                    "SELECT * FROM recording_completeness WHERE scope_key=? AND tc_id=? AND source=? ORDER BY id DESC",
                    (scope_key, tc_id, source),
                ).fetchall()
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            finally:
                conn.close()
        steps = count_script_steps(script) if script.strip() else None
        open_record = latest is not None and latest["status"] == OPEN
        return {
            "source": source,
            "incomplete": open_record,
            "status": ("INCOMPLETE" if open_record else (latest["status"] if latest else "COMPLETE")),
            "current": self._public(latest, steps),
            "records": [self._public(item) for item in rows],
        }

    def block_reason(self, test_case, source=None):
        """Operator-facing reason why this script source must not be validated-as-ready, activated or run.

        ``source`` None means "whichever source Execute would run" (same fallback rule as
        TestExecutionManager.get_active_script()).  Never raises.
        """

        try:
            if not test_case:
                return None
            if source is None:
                active = (test_case.get("active_script_source") or "AUTO").upper()
                if active == "MANUAL" and (test_case.get("recorded_script") or "").strip():
                    source = "MANUAL"
                else:
                    return None
            source = str(source).upper()
            if source != "MANUAL" or not (test_case.get("recorded_script") or "").strip():
                return None
            state = self.get_state(test_case["id"], "MANUAL")
        except Exception:
            return None
        if not state["incomplete"]:
            return None
        record = state["current"]
        return (
            f"This recording is INCOMPLETE: {record['original_skipped_count']} user action(s) could not be "
            "recorded and are missing from the script. Restore the missing steps in the script "
            "(at least that many), then use Confirm correction and Validate again. It cannot be "
            "validated as ready, activated or executed until then."
        )

    # ------------------------------------------------------------ correction workflow

    def confirm_correction(self, test_case_id, actor, note, confirm, source="MANUAL"):
        source = str(source or "MANUAL").upper()
        if source != "MANUAL":
            raise CompletenessError("Only the Recorded script can have a recording-completeness record.")
        script = self._current_script(None, test_case_id, source)
        with _LOCK:
            conn = self._connect()
            try:
                conn.execute("BEGIN IMMEDIATE")
                _, scope_key, source_id, tc_id = self._row_and_scope(conn, test_case_id)
                record = self._reconcile_removed(conn, self._latest(conn, scope_key, tc_id, source), script)
                if not record or record["status"] != OPEN:
                    raise CompletenessError("This recording has no open incomplete-recording record to correct.")
                problem = None
                steps = count_script_steps(script) if script.strip() else None
                fingerprint = self.repo.script_fingerprint(script)
                required = record["recorded_step_count"] + record["original_skipped_count"]
                note = (note or "").strip()
                if not confirm:
                    problem = "Tick the confirmation: you must explicitly confirm that the missing steps were restored."
                elif len(note) < 5:
                    problem = "Describe what you restored (at least a few words) so the correction can be audited."
                elif not script.strip():
                    problem = "The recorded script is empty."
                elif steps is None:
                    problem = "The recorded script has a Python syntax problem; fix it before confirming the correction."
                elif fingerprint == record["recorded_fingerprint"]:
                    problem = ("The script has not been changed since it was recorded. Add the missing steps to the "
                               "script first - an acknowledgement alone does not complete a recording.")
                elif steps < required:
                    problem = (f"The script has {steps} executable step(s); at least {required} are needed "
                               f"({record['recorded_step_count']} recorded + {record['original_skipped_count']} missing). "
                               "Restore the missing steps first.")
                if problem:
                    self._append(conn, record, {
                        "event": "CONFIRMATION_REJECTED", "actor": actor or "", "note": note,
                        "confirm": bool(confirm), "script_steps": steps, "required_steps": required,
                        "detail": problem,
                    })
                    conn.commit()
                    raise CompletenessError(problem)
                self._append(conn, record, {
                    "event": "CORRECTION_CONFIRMED", "actor": actor or "", "note": note,
                    "script_steps_before": record["recorded_step_count"], "script_steps_after": steps,
                    "fingerprint_before": record["recorded_fingerprint"], "fingerprint_after": fingerprint,
                    "detail": "Missing steps restored; Validate again before Set Active.",
                }, status=CORRECTED, resolved=True)
                self._clear_validation_and_demote(conn, scope_key, source_id, tc_id, test_case_id)
                conn.commit()
            except CompletenessError:
                raise
            except Exception:
                conn.rollback()
                raise
            finally:
                conn.close()
        return self.get_state(test_case_id, source)
