"""
QA AI Studio - Unified Smart Upload Discovery Worker
Location: UI/KnowledgeHub/smart_upload_worker.py

Executes Playwright Session Initialization, Authentication, and
(now) an on-demand, multi-step guided discovery capture — all
sequentially inside a single QThread, because Playwright's sync API
is bound to whichever OS thread created it; calling it from a
second thread raises greenlet/thread-switch errors. That is also
why this class does not spin up a fresh worker for every "Capture
This Screen" click — instead, after authenticating, it parks on a
blocking command queue *inside its own thread* and waits for the
GUI thread to ask it to capture the current screen or finish, one
command at a time, without ever touching Playwright from anywhere
but this one thread.

Guided capture flow (new):
    1. run() authenticates, exactly as before.
    2. Once authenticated, it emits flow_ready_signal and blocks on
       an internal queue.Queue — GUI thread work continues normally
       while this thread waits; nothing here is polled or busy-waited.
    3. The GUI thread calls request_capture() after the operator has
       manually driven the (still open, visible) browser to the next
       step of the real business process. That just pushes a command
       onto the queue — thread-safe, no Playwright call happens on
       the GUI thread.
    4. This thread wakes up, scans the CURRENT screen (no navigation,
       no clicking — see URLDiscoveryEngine.scan_current_view()) and
       emits step_ready_signal with the result for the confirmation
       dialog to review.
    5. Repeat 3-4 for as many steps as the real business flow needs.
    6. The GUI thread calls request_finish() once the operator marks
       a step as the final one (or gives up early); this thread
       closes the browser (unless told to keep it open) and emits
       finished_signal.

The single-shot "authenticate then discover() once" behaviour some
older/dead call sites in smart_upload_page.py still reference is
intentionally not preserved here — see the developer notes shipped
alongside this change for why: it only ever discovered whatever
was on screen immediately after login, so "Pages discovered: 0" /
"Forms discovered: 0" was the normal outcome, not a bug specific to
one run.
"""

from __future__ import annotations

import logging
import queue
from PySide6.QtCore import QThread, Signal

from Core.url_authenticated_session import URLAuthenticatedSession
from Core.url_discovery_engine import URLDiscoveryEngine


class SmartUploadWorker(QThread):
    """
    Unified worker that keeps Playwright alive across Auth and
    however many guided-capture steps the operator needs.
    """

    log_signal = Signal(str)
    progress_signal = Signal(int, str)
    finished_signal = Signal(dict)

    # New: guided multi-step capture signals.
    flow_ready_signal = Signal(dict)
    step_ready_signal = Signal(dict)
    state_signal = Signal(str)

    CMD_CAPTURE = "capture"
    CMD_FINISH = "finish"
    CMD_PAUSE = "pause"
    CMD_RESUME = "resume"

    def __init__(
        self,
        target_url: str,
        analysis_data: dict,
        credentials: dict,
        db_conn=None,
        parent=None,
    ):
        super().__init__(parent)
        self.target_url = target_url
        self.analysis_data = analysis_data
        self.credentials = credentials
        self.db_conn = db_conn
        self.logger = logging.getLogger(__name__)

        self._commands: "queue.Queue" = queue.Queue()
        self._session = None

    # ------------------------------------------------------------
    # Called from the GUI thread — thread-safe (queue.Queue handles
    # its own locking). These never touch Playwright directly.
    # ------------------------------------------------------------

    def request_capture(self, step_label: str = ""):
        self._commands.put((self.CMD_CAPTURE, step_label))

    def request_finish(self, keep_browser_open: bool = False):
        self._commands.put((self.CMD_FINISH, keep_browser_open))

    def request_pause(self):
        self._commands.put((self.CMD_PAUSE, None))

    def request_resume(self):
        self._commands.put((self.CMD_RESUME, None))

    # ------------------------------------------------------------
    # Runs entirely inside this thread.
    # ------------------------------------------------------------

    def run(self):
        """Sequential single-threaded execution: authenticate, then
        idle on the command queue for as many capture steps as the
        operator needs."""

        self.log_signal.emit("Initializing authenticated Playwright environment...")
        self.progress_signal.emit(10, "Starting Playwright Engine...")

        session = URLAuthenticatedSession(headless=False)
        self._session = session

        try:
            # 1. Authenticate
            self.log_signal.emit(f"Authenticating access for: {self.target_url}")
            self.progress_signal.emit(30, "Logging into application...")

            auth_res = session.authenticate(
                url=self.target_url,
                analysis=self.analysis_data,
                credentials=self.credentials,
            )

            if not auth_res.get("success"):
                error_msg = auth_res.get("error", "Authentication failed.")
                self.log_signal.emit(f"Authentication Failed: {error_msg}")
                self.finished_signal.emit({"success": False, "error": error_msg})
                return

            self.log_signal.emit(
                "Authenticated successfully! Waiting for post-login dashboard to load..."
            )
            self.progress_signal.emit(50, "Waiting for page rendering...")

            # 2. Extract active context/page from the live session
            if hasattr(session, "get_authenticated_context"):
                context = session.get_authenticated_context()
            else:
                context = getattr(session, "context", None)

            if context is None:
                raise ValueError("Could not obtain active Playwright context from session.")

            for p in context.pages:
                p.set_default_navigation_timeout(30000)
                p.set_default_timeout(30000)

            self.progress_signal.emit(70, "Ready for guided capture.")

            engine = URLDiscoveryEngine(context=context, db_conn=self.db_conn)
            paused = False

            self.log_signal.emit(
                "Authenticated. Drive the business process in the open browser "
                "window, then click “Capture This Screen” after each step."
            )

            self.flow_ready_signal.emit(
                {
                    "success": True,
                    "url": self._current_url(context, fallback=self.target_url),
                }
            )

            # 3. Idle on the command queue — every Playwright call
            #    from here on still happens on THIS thread, no matter
            #    how many times the operator clicks "Capture This
            #    Screen" from the GUI thread.
            while True:

                command, payload = self._commands.get()  # blocks, no polling

                if command == self.CMD_CAPTURE:

                    if paused:
                        self.step_ready_signal.emit({
                            "success": False,
                            "error": "Guided capture is paused.",
                            "label": payload or "",
                        })
                        continue

                    step_label = payload or ""

                    self.log_signal.emit(f"Capturing current screen ({step_label or 'step'})...")

                    try:
                        scan = engine.scan_current_view(label=step_label)
                    except Exception as scan_ex:
                        self.logger.exception("scan_current_view failed.")
                        scan = {"success": False, "error": str(scan_ex), "label": step_label}

                    if scan.get("success"):
                        self.log_signal.emit(
                            "Captured: "
                            f"{len(scan.get('fields', []))} field(s), "
                            f"{len(scan.get('buttons', []))} button(s), "
                            f"{len(scan.get('links', []))} link(s), "
                            f"{len(scan.get('tabs', []))} tab(s)."
                        )
                    else:
                        self.log_signal.emit(f"Capture failed: {scan.get('error')}")

                    self.step_ready_signal.emit(scan)

                elif command == self.CMD_FINISH:

                    keep_open = bool(payload)

                    self.log_signal.emit("Finishing business flow capture.")

                    if not keep_open:
                        try:
                            session.close()
                        except Exception:
                            pass

                    self.finished_signal.emit({"success": True})
                    break

                elif command == self.CMD_PAUSE:
                    paused = True
                    self.state_signal.emit("paused")
                    self.log_signal.emit("Guided capture paused; the browser session remains open.")

                elif command == self.CMD_RESUME:
                    paused = False
                    self.state_signal.emit("ready")
                    self.log_signal.emit("Guided capture resumed.")

                else:
                    self.logger.warning(f"Unknown worker command ignored: {command}")

        except Exception as ex:
            self.logger.exception("Unified Smart Upload Worker encountered an error.")
            self.log_signal.emit(f"Execution Error: {str(ex)}")
            self.log_signal.emit("Closing the failed Playwright discovery session.")
            # Deliberately not closing the session here — same reasoning as
            # the original single-shot worker: if something went wrong,
            # leaving the visible browser open lets the operator see why,
            # instead of it vanishing along with the error.
            self.finished_signal.emit({"success": False, "error": str(ex)})
        finally:
            try:
                session.close()
            except Exception:
                self.logger.exception("Failed to close the Playwright discovery session.")

    @staticmethod
    def _current_url(context, fallback: str) -> str:
        try:
            pages = [p for p in context.pages if not p.is_closed()]
            if pages:
                return pages[-1].url or fallback
        except Exception:
            pass
        return fallback
