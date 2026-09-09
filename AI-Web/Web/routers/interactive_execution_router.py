# Create: AI-Web/Web/routers/interactive_execution_router.py

"""
QA AI Studio — Web
Interactive Locator Repair — Execute-time WebSocket endpoint

Version: 1.0

Web port of Desktop's Interactive Locator Repair feature (see this
project's 2026-08-18 delivery notes) — a real, human-drivable browser
window pauses on a failed Playwright step, lets the operator fix the
Locator/value (or the whole line, in Advanced mode) or ask a local-AI
suggestion grounded in the live page, retries, and — once the run
finishes successfully — offers to persist any fixes into the stored
script. See Core/web_interactive_execution.py's module docstring for
the full architecture and the 2026-09-08 scope decision (applies to
every Playwright Execute, single or as part of Execute Selected/
Execute All, matching the CURRENT Desktop behavior — not the narrower
scope recorded in this project's original 2026-08-18 delivery notes).

Kept as a WebSocket, same reasoning as Web/routers/recorder_router.py:
the operator needs a live, low-latency channel to be asked a question
mid-run and to answer it — a request/response REST cycle can't pause a
run and wait.

Auth: same query-param token pattern as recorder_router.py (a
WebSocket handshake can't carry a Bearer header) — reuses that file's
_authorize() rather than duplicating the exact same check.

Protocol (JSON text frames):
  Client -> Server:
    {"type": "decision", "decision": {"action": "retry", "locator": "...", "value": "..."}}
    {"type": "decision", "decision": {"action": "retry_code", "code": "..."}}
    {"type": "decision", "decision": {"action": "verify_locator", "locator": "..."}}
                                                                -- live-page existence/visibility check
                                                                   ONLY (no click/fill attempted, no
                                                                   repair round consumed); the run stays
                                                                   paused on the SAME step_failed and a
                                                                   "locator_verified" event comes back --
                                                                   send as many of these as needed, then
                                                                   send a normal retry/retry_code/cancel
                                                                   decision to actually resume the run
    {"type": "decision", "decision": {"action": "cancel"}}    -- from INSIDE the repair dialog
    {"type": "ask_ai"}                                         -- grounded in whichever step_failed is outstanding
    {"type": "cancel_run"}                                     -- standalone Cancel Execution, any time
    {"type": "save_repairs", "repairs": [...]}                 -- after "finished", operator confirmed Yes
    {"type": "discard_repairs"}                                -- after "finished", operator confirmed No
  Server -> Client:
    {"type": "started", "run_uuid": "..."}
    {"type": "progress", "text": "..."}
    {"type": "step_failed", "event": {...}}
    {"type": "locator_verified", "event": {"locator": "...", "found": bool, "count": int, "visible": bool, "error": "..."}}
    {"type": "step_repaired", "repair": {...}}
    {"type": "ai_suggestion", "result": {...}}
    {"type": "finished", "run_uuid": "...", "result": {...}}
    {"type": "repairs_saved"}
    {"type": "error", "message": "..."}
"""

import asyncio
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from Core.web_interactive_execution import WebInteractiveExecutionSession
from Web.routers.recorder_router import _authorize

router = APIRouter(prefix="/api/automation", tags=["automation-interactive-execution"])

_logger = logging.getLogger("QA_AI_STUDIO")


@router.websocket("/test-cases/{test_case_id}/execute/interactive/ws")
async def execute_interactive_ws(websocket: WebSocket, test_case_id: int):

    token = websocket.query_params.get("token")

    user = _authorize(token)

    if user is None:
        await websocket.close(code=4401)
        return

    if not WebInteractiveExecutionSession.try_acquire(test_case_id):
        # Another recording OR another interactive execution already
        # has the one real browser window this server can drive — see
        # Core/web_interactive_execution.py's module docstring.
        await websocket.close(code=4409)  # Conflict.
        return

    session = WebInteractiveExecutionSession(
        test_case_id,
        executed_by_user_id=user["id"],
        executed_by_username=user["username"],
    )

    await websocket.accept()

    loop = asyncio.get_event_loop()

    async def send_json(payload):
        try:
            await websocket.send_json(payload)
        except Exception:
            _logger.exception("[web-interactive-execute] send_json failed")

    pump_task = None

    async def pump_events():
        # Relays WebInteractiveExecutionSession's outbound queue
        # (filled from the background execution thread) to the
        # browser, one blocking get() at a time via an executor so it
        # never blocks this connection's own asyncio event loop.
        # Stops right after relaying "finished" — that is always the
        # very last event the session ever pushes (see
        # WebInteractiveExecutionSession._run()).
        while True:
            event = await loop.run_in_executor(None, session.next_event)
            await send_json(event)
            if event.get("type") == "finished":
                break

    try:

        try:
            run = session.start()
        except Exception as ex:
            await send_json({"type": "error", "message": str(ex)})
            await websocket.close(code=1011)
            WebInteractiveExecutionSession.release()
            return

        await send_json({"type": "started", "run_uuid": run["run_uuid"]})

        pump_task = asyncio.ensure_future(pump_events())

        while True:

            message = await websocket.receive_json()
            msg_type = message.get("type")

            if msg_type == "decision":

                session.submit_decision(
                    message.get("decision") or {"action": "cancel"}
                )

            elif msg_type == "ask_ai":

                result = await loop.run_in_executor(None, session.ask_ai)
                await send_json({"type": "ai_suggestion", "result": result})

            elif msg_type == "cancel_run":

                session.cancel_run()

            elif msg_type == "save_repairs":

                await loop.run_in_executor(
                    None, session.apply_repairs, message.get("repairs") or [],
                )
                await send_json({"type": "repairs_saved"})
                break

            elif msg_type == "discard_repairs":

                break

    except WebSocketDisconnect:

        # Same reasoning as recorder_router.py's WebSocketDisconnect
        # handler: the real browser window is a separate OS process
        # that would otherwise keep running (or sit blocked waiting
        # for an operator who's no longer there) — never leave that
        # behind. Harmless no-op if the run had already finished.
        session.abandon()

    except Exception as ex:

        _logger.exception("[web-interactive-execute] main handler crashed")
        await send_json({"type": "error", "message": str(ex)})
        session.abandon()

    finally:

        if pump_task is not None:
            # Give the final "finished" event (and "repairs_saved", if
            # the operator answered before disconnecting) a moment to
            # actually reach the client, but never hang forever if the
            # run itself is stuck.
            try:
                await asyncio.wait_for(pump_task, timeout=5)
            except Exception:
                pump_task.cancel()

        WebInteractiveExecutionSession.release()

        try:
            await websocket.close()
        except Exception:
            pass
