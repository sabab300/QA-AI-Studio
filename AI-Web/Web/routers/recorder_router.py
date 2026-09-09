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
  Server -> Client:
    {"type": "started"}                          state: Recording
    {"type": "activity", "text": "..."}           Runtime Activity line (item 10)
    {"type": "stopped", "script": "..."}          state: Captured
    {"type": "cancelled"}                         state: Idle
    {"type": "error", "message": "..."}           state: Failed
"""

import asyncio
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from Core.security import verify_token
from Core.user_repository import UserRepository
from Core.test_case_repository import TestCaseRepository
from Core.web_recorder import WebRecordingSession, MAX_RECORDING_SECONDS

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

    if not WebRecordingSession.try_acquire(test_case_id):
        await websocket.close(code=4409)  # Conflict — another recording is active.
        return

    await websocket.accept()

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
        websocket.query_params.get("start_url") or "",
        domain=websocket.query_params.get("domain") or "",
        module=websocket.query_params.get("module") or "",
        knowledge_name=websocket.query_params.get("knowledge") or "",
    )

    watchdog_task = None

    async def send_json(payload):
        try:
            await websocket.send_json(payload)
        except Exception:
            _logger.exception("[web-recorder] send_json failed")

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
        script = await session.finish(save=True)
        await send_json({"type": "stopped", "script": script, "reason": "timeout"})
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

        await send_json({"type": "started"})

        watchdog_task = asyncio.ensure_future(watchdog())

        while True:

            message = await websocket.receive_json()
            msg_type = message.get("type")

            if msg_type == "stop":
                await send_json({"type": "activity", "text": "Stopping recorder..."})
                script = await session.finish(save=True)
                await send_json({"type": "stopped", "script": script})
                break
            elif msg_type == "cancel":
                await session.finish(save=False)
                await send_json({"type": "cancelled"})
                break

    except WebSocketDisconnect:

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
        await session.finish(save=False)

    except Exception as ex:

        _logger.exception("[web-recorder] main handler crashed")
        await send_json({"type": "error", "message": str(ex)})
        await session.finish(save=False)

    finally:

        if watchdog_task is not None:
            watchdog_task.cancel()

        WebRecordingSession.release()

        try:
            await websocket.close()
        except Exception:
            pass
