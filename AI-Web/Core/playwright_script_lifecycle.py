# Create: AI-Web/Core/playwright_script_lifecycle.py

"""
QA AI Studio
Playwright script lifecycle: revisions, rollback, replacement tracking and
dependency-aware deletion.

Task 1 / Phase 7.

An additive layer over TestCaseRepository's stable Playwright assets
(playwright_automation_assets, keyed by the immutable Knowledge source id +
Test Case ID).  It does not change the Domain -> Module -> Knowledge ->
Document Type -> Version hierarchy and does not migrate any data.

* Every content change of a script slot (AUTO / UPLOADED / MANUAL) is recorded
  as an immutable revision INSIDE the same database transaction that saves the
  script, so DB, files and history cannot disagree.
* The first time a slot that already holds a script is touched, that existing
  script is captured as a BASELINE revision, so even pre-existing scripts can
  be rolled back to.
* Rollback never edits history: it saves the chosen revision's text as a NEW
  revision (reason ROLLBACK) through the normal save path, so the validated
  hash is reset and the existing Validate -> Set Active lifecycle applies.
* Script removal is a save of empty text (reason DELETE) and is therefore
  reversible via rollback.  Removing a script that is active, or that has run
  history, requires explicit acknowledgement; a queued/running execution
  blocks removal.
* Deleting a Test Case is checked the same way.  Scripts live in the stable
  asset and are never purged by a Test Case row deletion.
"""

import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime

SOURCES = ("AUTO", "UPLOADED", "MANUAL")
_DEFAULT_REASON = {"AUTO": "GENERATED", "UPLOADED": "UPLOAD", "MANUAL": "RECORDED"}
_FIELD = {"AUTO": "automation_script", "UPLOADED": "uploaded_script", "MANUAL": "recorded_script"}
_ACTIVE_RUN_STATES = ("Queued", "Running")

_context = threading.local()


class LifecycleError(ValueError):
    """Base class: message is safe to show to the operator."""


class NotFound(LifecycleError):
    pass


class Blocked(LifecycleError):
    """The operation can never proceed right now (e.g. a run is in progress)."""

    def __init__(self, message, impact=None):
        super().__init__(message)
        self.impact = impact


class NeedsAcknowledgement(LifecycleError):
    """The operation is allowed but has dependants the caller must accept."""

    def __init__(self, message, impact):
        super().__init__(message)
        self.impact = impact


# ---------------------------------------------------------------- schema / hook

def ensure_schema(cursor):
    cursor.execute(
        """CREATE TABLE IF NOT EXISTS playwright_script_revisions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            stable_scope_key TEXT NOT NULL,
            tc_id TEXT NOT NULL,
            source TEXT NOT NULL,
            revision_number INTEGER NOT NULL,
            script_text TEXT NOT NULL DEFAULT '',
            script_fingerprint TEXT NOT NULL DEFAULT '',
            size_chars INTEGER NOT NULL DEFAULT 0,
            reason TEXT NOT NULL,
            actor TEXT,
            note TEXT,
            restored_from_revision INTEGER,
            created_date TEXT NOT NULL,
            UNIQUE(stable_scope_key, tc_id, source, revision_number)
        )"""
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_pw_script_revisions_scope "
        "ON playwright_script_revisions(stable_scope_key, tc_id, source)"
    )


@contextmanager
def revision_context(reason=None, actor=None, note=None, restored_from=None):
    """Attach metadata to every revision recorded on this thread inside the block."""

    previous = getattr(_context, "value", None)
    _context.value = {"reason": reason, "actor": actor, "note": note, "restored_from": restored_from}
    try:
        yield
    finally:
        _context.value = previous


def _fingerprint(repo, text):
    try:
        return repo.script_fingerprint(text)
    except Exception:
        import hashlib
        return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def _next_number(conn, scope_key, tc_id, source):
    row = conn.execute(
        "SELECT COALESCE(MAX(revision_number),0) FROM playwright_script_revisions "
        "WHERE stable_scope_key=? AND tc_id=? AND source=?",
        (scope_key, tc_id, source),
    ).fetchone()
    return int(row[0] if not isinstance(row, dict) else list(row.values())[0]) + 1


def _insert(repo, conn, scope_key, tc_id, source, text, reason, actor=None, note=None, restored_from=None):
    number = _next_number(conn, scope_key, tc_id, source)
    conn.execute(
        """INSERT INTO playwright_script_revisions
           (stable_scope_key,tc_id,source,revision_number,script_text,script_fingerprint,size_chars,
            reason,actor,note,restored_from_revision,created_date)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
        (scope_key, tc_id, source, number, text or "", _fingerprint(repo, text or ""), len(text or ""),
         reason, actor, note, restored_from, datetime.now().isoformat()),
    )
    return number


def capture(repo, conn, scope_key, tc_id, source, previous_script, new_script, changed):
    """Hook called by TestCaseRepository._update_stable_playwright_script().

    Runs inside the save transaction.  Never raises into the save path for a
    missing history table (an older database that has not been migrated yet
    simply keeps working without history).
    """

    try:
        context = getattr(_context, "value", None) or {}
        exists = conn.execute(
            "SELECT 1 FROM playwright_script_revisions WHERE stable_scope_key=? AND tc_id=? AND source=? LIMIT 1",
            (scope_key, tc_id, source),
        ).fetchone()
        if not exists and (previous_script or "").strip():
            _insert(repo, conn, scope_key, tc_id, source, previous_script, "BASELINE",
                    note="Script that existed before revision history was available.")
        if changed or not exists:
            if not changed and not (new_script or "").strip():
                return
            _insert(repo, conn, scope_key, tc_id, source, new_script,
                    context.get("reason") or _DEFAULT_REASON.get(source, "SAVE"),
                    actor=context.get("actor"), note=context.get("note"),
                    restored_from=context.get("restored_from"))
    except sqlite3.OperationalError:
        return


# ---------------------------------------------------------------- service

class PlaywrightScriptLifecycle:
    """Operations over one TestCaseRepository."""

    def __init__(self, repository):
        self.repo = repository

    # -- helpers --------------------------------------------------------
    def _connect(self):
        conn = self.repo.db.get_connection()
        conn.row_factory = self.repo._dict_factory
        return conn

    def _identity(self, conn, test_case_id):
        row = conn.execute("SELECT * FROM test_cases WHERE id=?", (int(test_case_id),)).fetchone()
        if not row:
            raise NotFound(f"Test case {test_case_id} not found.")
        identity = self.repo._playwright_identity(conn.cursor(), row)
        return row, identity

    @staticmethod
    def _check_source(source):
        source = str(source or "").upper()
        if source not in SOURCES:
            raise LifecycleError("Script source must be AUTO, UPLOADED or MANUAL.")
        return source

    def _sharing_rows(self, conn, identity):
        scope_key, source_id, tc_id = identity
        return conn.execute(
            """SELECT tc.id AS test_case_id, v.id AS version_id, v.version_number AS version_number
               FROM test_cases tc JOIN test_case_versions v ON v.id=tc.test_case_version_id
               WHERE v.source_knowledge_id=? AND tc.tc_id=? ORDER BY v.version_number, tc.id""",
            (source_id, tc_id),
        ).fetchall()

    @staticmethod
    def _run_stats(conn, test_case_ids):
        if not test_case_ids:
            return {"total": 0, "active": 0, "last_started": None, "last_status": None}
        marks = ",".join("?" for _ in test_case_ids)
        try:
            total = conn.execute(f"SELECT COUNT(*) AS n FROM automation_runs WHERE test_case_id IN ({marks})",
                                 test_case_ids).fetchone()["n"]
            active = conn.execute(
                f"SELECT COUNT(*) AS n FROM automation_runs WHERE test_case_id IN ({marks}) "
                f"AND status IN ({','.join('?' for _ in _ACTIVE_RUN_STATES)})",
                [*test_case_ids, *_ACTIVE_RUN_STATES]).fetchone()["n"]
            last = conn.execute(
                f"SELECT started_at, status FROM automation_runs WHERE test_case_id IN ({marks}) "
                "ORDER BY id DESC LIMIT 1", test_case_ids).fetchone()
        except sqlite3.OperationalError:
            return {"total": 0, "active": 0, "last_started": None, "last_status": None}
        return {"total": total, "active": active,
                "last_started": (last or {}).get("started_at"), "last_status": (last or {}).get("status")}

    # -- revisions -------------------------------------------------------
    def list_revisions(self, test_case_id, source=None):
        conn = self._connect()
        try:
            row, identity = self._identity(conn, test_case_id)
            if not identity:
                return {"supported": False, "revisions": [],
                        "reason": "This test case has no stable Knowledge source identity, so no script history is kept."}
            scope_key, _, tc_id = identity
            params, clause = [scope_key, tc_id], ""
            if source:
                clause = " AND source=?"
                params.append(self._check_source(source))
            rows = conn.execute(
                "SELECT id,source,revision_number,script_fingerprint,size_chars,reason,actor,note,"
                "restored_from_revision,created_date FROM playwright_script_revisions "
                f"WHERE stable_scope_key=? AND tc_id=?{clause} ORDER BY id DESC", params).fetchall()
            asset = conn.execute(
                "SELECT * FROM playwright_automation_assets WHERE stable_scope_key=? AND tc_id=?",
                (scope_key, tc_id)).fetchone() or {}
            current = {s: _fingerprint(self.repo, asset.get(_FIELD[s]) or "") for s in SOURCES}
            for item in rows:
                item["is_current"] = item["script_fingerprint"] == current.get(item["source"]) and bool(
                    (asset.get(_FIELD[item["source"]]) or "").strip() or item["size_chars"] == 0)
            return {"supported": True, "tc_id": tc_id, "revisions": rows}
        except sqlite3.OperationalError:
            return {"supported": False, "revisions": [], "reason": "Script history is not available yet."}
        finally:
            conn.close()

    def get_revision(self, test_case_id, revision_id):
        conn = self._connect()
        try:
            row, identity = self._identity(conn, test_case_id)
            if not identity:
                raise NotFound("This test case has no script history.")
            rev = conn.execute(
                "SELECT * FROM playwright_script_revisions WHERE id=? AND stable_scope_key=? AND tc_id=?",
                (int(revision_id), identity[0], identity[2])).fetchone()
            if not rev:
                raise NotFound("Script revision not found for this test case.")
            return rev
        finally:
            conn.close()

    def rollback(self, test_case_id, revision_id, actor=None):
        rev = self.get_revision(test_case_id, revision_id)
        if self.script_impact(test_case_id, rev["source"])["running_runs"]:
            raise Blocked("A run of this test case is in progress. Wait for it to finish before rolling back.")
        with revision_context("ROLLBACK", actor,
                              f"Restored revision {rev['revision_number']} of {rev['source']}",
                              restored_from=rev["id"]):
            changed = self.repo._update_stable_playwright_script(test_case_id, rev["script_text"], source=rev["source"])
        if changed is None:
            raise NotFound("This test case has no stable Playwright asset to restore into.")
        return {"changed": bool(changed), "source": rev["source"], "restored_revision": rev["revision_number"],
                "requires_validation": bool(changed) and bool((rev["script_text"] or "").strip())}

    # -- impact / deletion ------------------------------------------------
    def script_impact(self, test_case_id, source):
        source = self._check_source(source)
        conn = self._connect()
        try:
            row, identity = self._identity(conn, test_case_id)
            if not identity:
                raise NotFound("This test case has no stable Playwright asset.")
            scope_key, _, tc_id = identity
            asset = conn.execute(
                "SELECT * FROM playwright_automation_assets WHERE stable_scope_key=? AND tc_id=?",
                (scope_key, tc_id)).fetchone() or {}
            sharing = self._sharing_rows(conn, identity)
            stats = self._run_stats(conn, [r["test_case_id"] for r in sharing])
            try:
                revisions = conn.execute(
                    "SELECT COUNT(*) AS n FROM playwright_script_revisions WHERE stable_scope_key=? AND tc_id=? AND source=?",
                    (scope_key, tc_id, source)).fetchone()["n"]
            except sqlite3.OperationalError:
                revisions = 0
            present = bool((asset.get(_FIELD[source]) or "").strip())
            active = (asset.get("active_script_source") or "AUTO").upper()
            other_slots = [s for s in SOURCES if s != source and (asset.get(_FIELD[s]) or "").strip()]
            return {
                "test_case_id": int(test_case_id), "tc_id": tc_id, "source": source,
                "script_present": present, "is_active_source": active == source,
                "lifecycle_status": asset.get("lifecycle_status"),
                "other_scripts_present": other_slots,
                "shared_versions": [{"version_id": r["version_id"], "version_number": r["version_number"],
                                     "test_case_id": r["test_case_id"]} for r in sharing],
                "run_count": stats["total"], "running_runs": stats["active"],
                "last_run_status": stats["last_status"], "revision_count": revisions,
                "recoverable": True,
                "notes": self._notes(present, active == source, stats, len(sharing)),
            }
        finally:
            conn.close()

    @staticmethod
    def _notes(present, is_active, stats, sharing):
        notes = []
        if is_active and present:
            notes.append("This is the ACTIVE script: removing it leaves the test case with nothing to execute until another script is validated and set active.")
        if stats["total"]:
            notes.append(f"{stats['total']} execution run(s) refer to this test case; run history is kept.")
        if sharing > 1:
            notes.append(f"The script is shared by {sharing} test case version(s) of the same Knowledge source; all of them are affected.")
        notes.append("Every removal is recorded as a revision and can be restored with Rollback.")
        return notes

    def delete_script(self, test_case_id, source, actor=None, acknowledge=False):
        impact = self.script_impact(test_case_id, source)
        if not impact["script_present"]:
            raise LifecycleError(f"There is no {impact['source']} script to remove.")
        if impact["running_runs"]:
            raise Blocked("A run of this test case is in progress. Wait for it to finish or cancel it first.", impact)
        if (impact["is_active_source"] or impact["run_count"] or len(impact["shared_versions"]) > 1) and not acknowledge:
            raise NeedsAcknowledgement(
                "This script has dependants. Review the impact and confirm to remove it.", impact)
        with revision_context("DELETE", actor, "Script removed"):
            self.repo._update_stable_playwright_script(test_case_id, "", source=impact["source"])
        after = self.repo.get_test_case(test_case_id) or {}
        active_after = (after.get("active_script_source") or "AUTO").upper()
        active_changed = impact["is_active_source"] and active_after != impact["source"]
        note = ""
        if impact["is_active_source"]:
            remaining = [name for name in SOURCES if name != impact["source"]
                         and (after.get(_FIELD[name]) or "").strip()]
            if not remaining:
                note = "The removed script was the active one and no other script exists. Record or upload a script, then Validate and Set Active."
            elif (after.get("automation_lifecycle_status") or after.get("status")) == "Automated":
                note = f"The removed script was the active one; the validated {active_after} script is now active."
            else:
                note = (f"The removed script was the active one. Nothing is ready to execute now: validate the "
                        f"{active_after} script and Set Active before running.")
        return {"removed": True, "source": impact["source"], "recoverable": True, "impact": impact,
                "active_source_after": active_after, "active_source_changed": bool(active_changed),
                "active_note": note}

    def test_case_deletion_impact(self, test_case_id):
        conn = self._connect()
        try:
            row, identity = self._identity(conn, test_case_id)
            sharing = self._sharing_rows(conn, identity) if identity else []
            ids = [r["test_case_id"] for r in sharing] or [int(test_case_id)]
            stats = self._run_stats(conn, [int(test_case_id)])
            asset = {}
            if identity:
                asset = conn.execute(
                    "SELECT * FROM playwright_automation_assets WHERE stable_scope_key=? AND tc_id=?",
                    (identity[0], identity[2])).fetchone() or {}
            slots = [s for s in SOURCES if (asset.get(_FIELD[s]) or "").strip()]
            others = [r for r in sharing if r["test_case_id"] != int(test_case_id)]
            notes = []
            if stats["total"]:
                notes.append(f"{stats['total']} execution run(s) reference this test case; the history rows are kept.")
            if slots:
                notes.append("Its Playwright scripts are kept in the stable script store (not purged) so the "
                             "scripts survive a re-import of the same Test Case ID.")
            if others:
                notes.append(f"{len(others)} other version(s) of this test case share those scripts and are unaffected.")
            return {"test_case_id": int(test_case_id), "run_count": stats["total"], "running_runs": stats["active"],
                    "scripts_present": slots, "other_versions": [dict(r) for r in others], "notes": notes,
                    "has_dependants": bool(stats["total"] or slots)}
        finally:
            conn.close()

    def delete_test_case(self, test_case_id, acknowledge=False):
        impact = self.test_case_deletion_impact(test_case_id)
        if impact["running_runs"]:
            raise Blocked("A run of this test case is in progress. Wait for it to finish or cancel it first.", impact)
        if impact["has_dependants"] and not acknowledge:
            raise NeedsAcknowledgement("This test case has execution history or scripts. Confirm to delete it.", impact)
        return self.repo.delete_test_case(int(test_case_id)), impact
