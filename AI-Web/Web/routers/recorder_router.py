# Create: AI-Web/Web/routers/recorder_router.py

"""
QA AI Studio — Web
Live Playwright Recording — WebSocket endpoint

Version: 1.1

See Core/web_recorder.py's module docstring for the full architecture,
including why the Web recorder opens a REAL, VISIBLE Playwright
browser window (headless=False, item 1 of TASK:
QA-AUTOMATION-PLAYWRIGHT-COMPLETE-WEB-DESKTOP-FIX) rather than either
(a) a headless browser streamed into the QA AI Studio modal (the
prior version of this file — that made the recording look
embedded/iframe-like, which item 1 explicitly disallows as the final
mechanism), or (b) a literal port of Desktop's `playwright codegen`
subprocess (Core/test_execution_manager.py's still-available
record_manual_script()) — kept as a WebSocket rather than converted
to plain REST because the operator still needs a live, low-latency
channel for the Runtime Activity log (item 10) and for the server to
tell the client the instant the real window is up, without polling.

Since the browser window is now real and visible instead of streamed,
this protocol is simpler than the version it replaces: there is
nothing to relay pointer/keyboard input into (the operator drives the
real window directly with their own mouse and keyboard) and no video
frames to pump — only lifecycle control (stop/cancel) and one-way
activity/status notifications.

Auth note: browsers cannot set an Authorization header on a WebSocket
handshake, so the session token travels as a query parameter
(?token=...) instead of the usual Bearer header. It is verified with
the exact same Core.security.verify_token() + permission check every
other 'automation'/'edit' endpoint uses (see Web/deps.py's
require_permission) — just done by hand here, since FastAPI's
HTTPBearer dependency doesn't apply to WebSocket routes the same way.

Protocol (JSON text frames):
  Client -> Server:
    {"type": "stop"}     -> finalize, save (state -> Stopping -> Captured), close
    {"type": "cancel"}   -> discard (state -> Idle), close
    {"type": "show_window"} -> raise the recorder's browser window; answered with {"type": "window", "report": {...}}
  Server -> Client:
    {"type": "started", "target": {...}}          state: Recording
    {"type": "activity", "text": "..."}           Runtime Activity line (item 10)
    {"type": "stopped", "script": "..."}          state: Captured (complete recording, saved)
    {"type": "incomplete", ...}                   state: Incomplete — NOT saved; a draft was kept
                                                  and must be confirmed via the recording-drafts API
    {"type": "cancelled"}                         state: Idle
    {"type": "error", "message": "..."}           state: Failed

Recording target (query params on the WebSocket URL):
    target_id   "configured" (default, Test Environment Settings Base URL) or a saved target id
    start_url   an explicit http(s) URL; it must belong to the configured / a saved target,
                or the caller must also pass authorized=1 to state they may test it
    authorized  1 | true — acknowledgement for a custom start_url

Incomplete recordings (timeout, crashed recorder page, dropped client connection, or only the
initial navigation captured) are never written to the test case automatically: see
Core/recording_drafts.py and the /recording-drafts endpoints below.
"""

import asyncio
import logging

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel

from Core.security import verify_token
from Core.user_repository import UserRepository
from Core.test_case_repository import TestCaseRepository
from Core.web_recorder import WebRecordingSession, MAX_RECORDING_SECONDS
from Core import recording_drafts
from Core import recorder_window
from Core import recording_targets
from Core.recording_targets import RecordingTargetError
from Web.deps import require_permission

router = APIRouter(prefix="/api/automation", tags=["automation-recording"])

_logger = logging.getLogger("QA_AI_STUDIO")


def _authorize(token):
    """
    Returns the user dict if `token` is valid, active, and has
    'automation'/'edit' permission — else None. Mirrors Web/deps.py's
    require_permission("automation", "edit") exactly (that dependency
    can't be reused as-is on a WebSocket route in this FastAPI
    version, since it expects an HTTPAuthorizationCredentials header
    dependency, not a query param).
    """

    if not token:
        return None

    payload = verify_token(token)

    if payload is None:
        return None

    repository = UserRepository()

    user = repository.get_user_by_id(payload["user_id"])

    if user is None or not user["is_active"]:
        return None

    permissions = repository.get_permissions_for_role(user["role_id"])

    if "edit" not in permissions.get("automation", set()):
        return None

    return user


def _terminal_message(session, script):
    """The one accurate terminal message for how a recording ended."""

    outcome = session.outcome or {}

    if outcome.get("status") == "incomplete":
        return {
            "type": "incomplete",
            "saved": False,
            "reason_code": outcome.get("reason_code"),
            "reasons": outcome.get("reasons") or [],
            "meaningful_actions": outcome.get("meaningful_actions", 0),
            "total_actions": outcome.get("total_actions", 0),
            "draft_id": outcome.get("draft_id"),
            "steps": outcome.get("steps") or [],
        }

    return {
        "type": "stopped", "status": "complete", "saved": True, "script": script,
        # Task-1 closure: user actions the recorder could not locate are NOT in the script;
        # carry that into the terminal message so the UI can warn after the dialog closes.
        "skipped_actions": outcome.get("skipped_actions", 0),
        "warnings": outcome.get("warnings") or [],
        # Task-1 reliability fix: a recording with skipped actions is saved as an INCOMPLETE Draft that
        # cannot be validated, activated or executed until it is corrected (see Core/recording_completeness.py).
        "recording_incomplete": bool(outcome.get("recording_incomplete")),
    }


# ----------------------------------------------------------------
# Recording targets
# ----------------------------------------------------------------

class RecordingTargetRequest(BaseModel):
    name: Optional[str] = ""
    url: str
    authorized: bool = False


@router.get("/recording-targets")
def list_recording_targets(current_user=Depends(require_permission("automation", "view"))):

    return {"targets": recording_targets.list_targets()}


@router.post("/recording-targets")
def save_recording_target(
    payload: RecordingTargetRequest,
    current_user=Depends(require_permission("automation", "edit")),
):

    try:
        item = recording_targets.save_target(payload.name, payload.url, payload.authorized)
    except RecordingTargetError as ex:
        raise HTTPException(status_code=400, detail=str(ex))

    UserRepository().write_audit_log(
        user_id=current_user["id"], username=current_user["username"],
        action="SAVE_RECORDING_TARGET", resource="automation",
        detail=f"Saved recording target {recording_targets.origin_of(item['url'])}",
    )
    return item


@router.delete("/recording-targets/{target_id}")
def delete_recording_target(
    target_id: str,
    current_user=Depends(require_permission("automation", "edit")),
):

    if not recording_targets.delete_target(target_id):
        raise HTTPException(status_code=404, detail="Recording target not found")
    return {"deleted": True}


# ----------------------------------------------------------------
# Incomplete recording drafts
# ----------------------------------------------------------------

class ConfirmDraftRequest(BaseModel):
    confirm_incomplete: bool = False


# ---------------------------------------------------------------------------
# Recorder window diagnostics — see Core/recorder_window.py
# ---------------------------------------------------------------------------

class WindowProbeFeedback(BaseModel):
    seen: bool
    note: Optional[str] = None


@router.get("/recorder/diagnostics")
def recorder_diagnostics(current_user=Depends(require_permission("automation", "view"))):
    """Can a visible recorder window be shown on this machine? (no browser is launched)"""
    return recorder_window.environment_report(recording_active=WebRecordingSession.is_active())


@router.post("/recorder/test-window")
async def recorder_test_window(hold_seconds: float = 8.0, current_user=Depends(require_permission("automation", "edit"))):
    """Open a labelled test window using the recorder's own launch options and report what the OS sees."""
    hold = max(2.0, min(float(hold_seconds), 30.0))
    report = await WebRecordingSession.run_window_probe(hold)
    if report is None:
        raise HTTPException(status_code=409, detail="A recording is in progress; finish it before testing the recorder window.")
    report["environment"] = recorder_window.environment_report()
    return report


@router.post("/recorder/test-window/feedback")
def recorder_test_window_feedback(body: WindowProbeFeedback, current_user=Depends(require_permission("automation", "edit"))):
    """The operator's answer to 'did you see the test window?' — kept in the audit log for support."""
    try:
        UserRepository().write_audit_log(
            user_id=current_user["id"], username=current_user["username"], action="RECORDER_WINDOW_TEST",
            resource="automation",
            detail=f"Operator {'saw' if body.seen else 'did NOT see'} the recorder test window" + (f": {body.note[:200]}" if body.note else ""),
        )
    except Exception:
        _logger.exception("[web-recorder] could not write the window-test audit entry (non-fatal)")
    return {"recorded": True}


@router.get("/test-cases/{test_case_id}/recording-drafts")
def list_recording_drafts(
    test_case_id: int,
    current_user=Depends(require_permission("automation", "view")),
):

    return {"drafts": recording_drafts.list_drafts(test_case_id)}


@router.post("/test-cases/{test_case_id}/recording-drafts/{draft_id}/confirm")
def confirm_recording_draft(
    test_case_id: int,
    draft_id: str,
    payload: ConfirmDraftRequest,
    current_user=Depends(require_permission("automation", "edit")),
):

    draft = recording_drafts.get_draft(draft_id)

    if not draft or int(draft.get("test_case_id", -1)) != test_case_id:
        raise HTTPException(status_code=404, detail="Recording draft not found")

    if not payload.confirm_incomplete:
        raise HTTPException(
            status_code=409,
            detail="This recording is incomplete. Set confirm_incomplete=true to save it anyway.",
        )

    script = draft.get("script") or ""

    if not script.strip():
        raise HTTPException(status_code=400, detail="This draft has no generated script to save.")

    import ast

    try:
        ast.parse(script)
    except SyntaxError as ex:
        raise HTTPException(status_code=400, detail=f"The draft script is not valid Python: {ex.msg}")

    from Core.test_execution_manager import TestExecutionManager

    unsafe = [
        item for item in TestExecutionManager.assess_playwright_script_quality(script)
        if item.get("classification") == "Invalid" or not item.get("semantic_valid", True)
        or not item.get("parent_valid", True) or not item.get("action_valid", True)
    ]
    if unsafe:
        raise HTTPException(
            status_code=400,
            detail=f"The draft contains {len(unsafe)} unsafe executable step(s) and cannot be saved.",
        )

    marked = recording_drafts.mark_incomplete_script(
        script, draft.get("reasons") or [], saved_by=current_user.get("username", ""),
    )
    TestCaseRepository().update_recorded_script(test_case_id, marked)
    recording_drafts.delete_draft(draft_id)

    UserRepository().write_audit_log(
        user_id=current_user["id"], username=current_user["username"],
        action="SAVE_INCOMPLETE_RECORDING", resource="automation",
        detail=f"Test case {test_case_id}: saved incomplete recording ({draft.get('reason_code')}, "
               f"{draft.get('meaningful_actions')} interaction(s)) after explicit confirmation",
    )
    return {"saved": True, "incomplete": True, "test_case_id": test_case_id}


@router.delete("/test-cases/{test_case_id}/recording-drafts/{draft_id}")
def discard_recording_draft(
    test_case_id: int,
    draft_id: str,
    current_user=Depends(require_permission("automation", "edit")),
):

    draft = recording_drafts.get_draft(draft_id)

    if not draft or int(draft.get("test_case_id", -1)) != test_case_id:
        raise HTTPException(status_code=404, detail="Recording draft not found")

    recording_drafts.delete_draft(draft_id)
    return {"discarded": True}


# ----------------------------------------------------------------
# Skipped-action (incomplete recording) control - Task-1 reliability fix.
# See Core/recording_completeness.py.
# ----------------------------------------------------------------

class ConfirmCorrectionRequest(BaseModel):
    confirm: bool = False
    note: str = ""
    source: str = "MANUAL"


@router.get("/test-cases/{test_case_id}/recording-completeness")
def get_recording_completeness(
    test_case_id: int,
    source: str = "MANUAL",
    current_user=Depends(require_permission("automation", "view")),
):
    from Core.recording_completeness import RecordingCompleteness, CompletenessError

    try:
        return RecordingCompleteness().get_state(test_case_id, source)
    except CompletenessError as ex:
        raise HTTPException(status_code=404, detail=str(ex))


@router.post("/test-cases/{test_case_id}/recording-completeness/confirm-correction")
def confirm_recording_correction(
    test_case_id: int,
    payload: ConfirmCorrectionRequest,
    current_user=Depends(require_permission("automation", "edit")),
):
    """Controlled correction: the missing steps must really be restored in the script, the operator must
    explicitly confirm (with a note), and the script must then be validated again.  A bare confirmation is
    refused (and the refusal is recorded)."""

    from Core.recording_completeness import RecordingCompleteness, CompletenessError

    audit = UserRepository()
    try:
        state = RecordingCompleteness().confirm_correction(
            test_case_id, current_user.get("username", ""), payload.note, payload.confirm, payload.source,
        )
    except CompletenessError as ex:
        try:
            audit.write_audit_log(
                user_id=current_user["id"], username=current_user["username"],
                action="RECORDING_CORRECTION_REFUSED", resource="automation",
                detail=f"Test case {test_case_id}: {ex}",
            )
        except Exception:
            _logger.exception("could not write the correction-refused audit entry (non-fatal)")
        raise HTTPException(status_code=400, detail=str(ex))

    audit.write_audit_log(
        user_id=current_user["id"], username=current_user["username"],
        action="RECORDING_CORRECTION_CONFIRMED", resource="automation",
        detail=f"Test case {test_case_id}: skipped-action correction confirmed - {payload.note.strip()}",
    )
    return state


@router.websocket("/test-cases/{test_case_id}/record/ws")
async def record_ws(websocket: WebSocket, test_case_id: int):

    token = websocket.query_params.get("token")

    user = _authorize(token)

    if user is None:
        await websocket.close(code=4401)
        return

    test_case = TestCaseRepository().get_test_case(test_case_id)

    if not test_case:
        await websocket.close(code=4404)
        return

    params = websocket.query_params

    try:
        target = recording_targets.resolve_target(
            target_id=params.get("target_id"),
            url=params.get("start_url"),
            authorized=str(params.get("authorized") or "").lower() in ("1", "true", "yes"),
        )
    except RecordingTargetError as ex:
        await websocket.accept()
        await websocket.send_json({"type": "error", "message": str(ex)})
        await websocket.close(code=4400)  # Bad request — invalid or unauthorised target.
        return

    if not WebRecordingSession.try_acquire(test_case_id):
        await websocket.close(code=4409)  # Conflict — another recording is active.
        return

    await websocket.accept()

    try:
        UserRepository().write_audit_log(
            user_id=user["id"], username=user["username"], action="START_RECORDING",
            resource="automation",
            detail=f"Test case {test_case_id}: recording target {recording_targets.origin_of(target['url'])} ({target['source']})",
        )
    except Exception:
        _logger.exception("[web-recorder] could not write the recording-target audit entry (non-fatal)")

    # An explicit start URL can be sent as a query param
    # (?start_url=...); otherwise WebRecordingSession.start() falls
    # back to Test Environment Settings' configured Base URL, exactly
    # like Desktop's record_manual_script().
    #
    # domain/module/knowledge are the operator's currently-selected
    # Domain / Module / Knowledge Name scope in the grid (see item
    # 29: "prefer known stable locators from Knowledge Hub discovery
    # data when a captured element matches"). Optional — a recording
    # started with no scope selected simply gets no Knowledge Hub
    # locator preference, same as before this feature existed.
    session = WebRecordingSession(
        test_case_id,
        target["url"],
        domain=websocket.query_params.get("domain") or "",
        module=websocket.query_params.get("module") or "",
        knowledge_name=websocket.query_params.get("knowledge") or "",
    )
    # Who recorded it: kept in the skipped-action audit record (Task-1 reliability fix).
    session.recorded_by = user.get("username") or ""

    watchdog_task = None
    terminal_sent = False
    connection_open = True

    async def send_json(payload):
        nonlocal connection_open
        if not connection_open:
            return False
        try:
            await websocket.send_json(payload)
            return True
        except WebSocketDisconnect:
            connection_open = False
            _logger.info("[web-recorder] client connection closed")
            return False
        except Exception:
            connection_open = False
            _logger.exception("[web-recorder] send_json failed")
            return False

    async def watchdog():
        # Hard ceiling so an operator who closes their laptop mid
        # recording can never leave the real Chromium window running
        # forever on the server machine (see item 25's cleanup
        # requirement).
        await asyncio.sleep(MAX_RECORDING_SECONDS)
        await send_json({
            "type": "activity",
            "text": "Recording auto-stopped after 15 minutes.",
        })
        script = await session.finish(save=True, reason="timeout")
        await send_json(_terminal_message(session, script))
        await websocket.close(code=4408)

    try:

        def on_activity(text):
            asyncio.ensure_future(send_json({"type": "activity", "text": text}))

        try:
            await session.start(on_activity=on_activity)
        except Exception as ex:
            await send_json({"type": "error", "message": str(ex)})
            await websocket.close(code=1011)
            WebRecordingSession.release()
            return

        await send_json({"type": "started", "target": {
            "label": target["label"], "source": target["source"], "origin": recording_targets.origin_of(target["url"]),
        }, "window": session.window_report})

        watchdog_task = asyncio.ensure_future(watchdog())

        while True:

            message = await websocket.receive_json()
            msg_type = message.get("type")

            if msg_type == "stop":
                await send_json({"type": "activity", "text": "Stopping recorder..."})
                script = await session.finish(save=True, reason="user")
                terminal_sent = await send_json(_terminal_message(session, script))
                break
            elif msg_type == "cancel":
                await session.finish(save=False)
                terminal_sent = await send_json({"type": "cancelled"})
                break
            elif msg_type == "show_window":
                report = await session.show_window()
                await send_json({"type": "window", "report": report})

    except WebSocketDisconnect:

        connection_open = False

        # Operator's QA AI Studio tab closed / lost connection
        # mid-recording. The real recorder browser window is a
        # separate OS process from this WebSocket connection, so it
        # would otherwise keep running unattended with no way left to
        # tell it to stop — never leave that running, but there is
        # also nobody left to review a script before it's saved, so
        # this discards rather than silently persisting a partial one.
        # Known limitation: a flaky network connection (not just the
        # operator closing the tab on purpose) hits this same path and
        # discards whatever was captured — there is no reconnect-to-
        # an-in-progress-recording mechanism. Matches item 25's
        # cleanup requirement; the reconnect gap is called out in the
        # final report's Remaining Gaps section.
        #
        # Incomplete-recording protection: captured interactions are not
        # silently lost any more — they are kept as a recoverable draft
        # (never saved to the test case) that the operator can review and
        # confirm from the recording-drafts list when they reconnect.
        if not terminal_sent:
            await session.finish(save=False, reason="client_disconnected", preserve_draft=True)

    except Exception as ex:

        _logger.exception("[web-recorder] main handler crashed")
        await send_json({"type": "error", "message": str(ex)})
        await session.finish(save=False, reason="error", preserve_draft=True)

    finally:

        if watchdog_task is not None:
            watchdog_task.cancel()

        WebRecordingSession.release()

        try:
            await websocket.close()
        except Exception:
            pass
