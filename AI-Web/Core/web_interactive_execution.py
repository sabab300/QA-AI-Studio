# Create: AI-Web/Core/web_interactive_execution.py

"""
QA AI Studio — Web
Interactive Locator Repair — Execute-time session

Version: 1.0

Web port of Desktop's Interactive Locator Repair feature (this
project's 2026-08-18 delivery notes: "Playwright gets stuck on a
Locator/element/value with no way to correct it and keep going" — a
real, human-drivable browser window pauses on a failed step instead of
just failing the run, lets the operator fix it or ask a local-AI
suggestion grounded in the live page, retries, and — once the run
finishes successfully — offers to persist any fixes into the stored
script).

Every piece of the underlying engine already exists in Core, shared
with and unchanged by Desktop:
    TestExecutionManager.execute_playwright_interactive()
    TestExecutionManager.apply_script_repairs()
    TestExecutionManager.suggest_locator_fix()
    TestExecutionManager.cancel_interactive_run()
    PlaywrightRunner.run_script_interactive()
      (INTERACTIVE_HARNESS_TEMPLATE — the subprocess-side step runner
      that sends "step_failed"/"step_repaired" JSON events on stdout
      and blocks reading a JSON command back on stdin)
None of those change here. What was missing for Web (see
Core/automation_web_repository.py's own module docstring, which
called this out by name as deliberately deferred) was a live,
bidirectional channel to actually DRIVE that engine from a browser tab
instead of a desktop Qt dialog — this module plus
Web/routers/interactive_execution_router.py are that channel, built on
the exact same real-browser-window architecture already proven for
the Web recorder (Core/web_recorder.py).

This is the Web-port counterpart of
App/UI/QAAutomation/test_execution_worker.py's
PlaywrightInteractiveWorker + AiLocatorSuggestionWorker. Those two Qt
workers hand a step_failed event to the UI thread via a Qt
cross-thread signal and block on a plain queue.Queue() until
submit_decision() is called back from the dialog. This does the exact
same handoff — same queue.Queue() primitive — just consumed by an
async WebSocket router (which pumps events out to the browser and
decisions back in) instead of Qt's event loop.

Shares WebRecordingSession's single "one real, visible,
human-drivable browser window at a time" lock (Core/web_recorder.py)
on purpose: recording and interactive execution both need the
operator's full attention on one real window on this one machine, so
they can never run concurrently with each other either.

SCOPE (confirmed with the user 2026-09-08): applies to every
Playwright Execute — single, or one at a time as part of Execute
Selected/Execute All — matching the CURRENT Desktop behavior in
App/UI/QAAutomation/test_execution_page.py's
confirm_and_run_playwright() ("Interactive locator repair is now
offered for every test case in the queue, batch or not"). This is
newer than, and supersedes, the narrower "single-test Execute only"
scope recorded in this project's original 2026-08-18 delivery notes.
The Web frontend runs a batch strictly one test case at a time (see
Frontend/index.html's executeManyTestCases()), exactly mirroring
Desktop's run_next_execution() queue, so only one interactive run —
and therefore only one real browser window — is ever in flight.

Unlike the plain background Execute job
(Core/automation_web_repository.py's TestCasesWeb._run_job(),
force_headless=True — nobody is watching a server-side headless run),
interactive execution ALWAYS opens a real, visible window: neither
PlaywrightRunner._build_interactive_script() nor
_build_generic_interactive_script() ever apply the headless-forcing
shim, by design — the operator has to be able to see and interact
with the page to fix a broken locator. See those methods' own
comments in playwright_runner.py.
"""

import queue
import threading

from Core.automation_execution_repository import AutomationExecutionRepository
from Core.logger import Logger
from Core.test_execution_manager import TestExecutionManager
from Core.web_recorder import WebRecordingSession

logger = Logger.get_logger()


class WebInteractiveExecutionSession:
    """
    One instance per WebSocket connection / per Execute click — never
    reused across runs. Created fresh by
    Web/routers/interactive_execution_router.py's endpoint handler for
    every request.
    """

    def __init__(
        self, test_case_id, executed_by_user_id=None, executed_by_username=None,
    ):

        self.test_case_id = test_case_id

        self.executed_by_user_id = executed_by_user_id

        self.executed_by_username = executed_by_username

        self.runs = AutomationExecutionRepository()

        # Deliberately NOT force_headless — see module docstring:
        # interactive execution always needs a real, visible window,
        # regardless of how this manager instance is configured (the
        # interactive script builders never honor force_headless
        # anyway — see playwright_runner.py — but not passing it here
        # keeps this class's own intent honest to read).
        self.manager = TestExecutionManager()

        self.run_uuid = None

        # Events destined for the browser (progress / step_failed /
        # step_repaired / finished) — consumed by the router's
        # pump_events() via next_event(), one blocking get() at a
        # time. Thread-safe on its own; no lock needed.
        self._out_queue = queue.Queue()

        # The operator's decision for whichever step_failed event is
        # currently outstanding. submit_decision() is the ONLY way
        # this ever gets a value; _on_step_failed() blocks reading it,
        # exactly like Desktop's PlaywrightInteractiveWorker does with
        # its own queue.Queue().
        self._decision_queue = queue.Queue()

        # So ask_ai() can ground its prompt in whichever failure is
        # CURRENTLY on screen without the browser having to resend the
        # whole event over the wire.
        self._pending_failure_event = None

        self._thread = None

    # ------------------------------------------------------------
    # Single global "one real browser window at a time" lock — shared
    # with WebRecordingSession on purpose (see module docstring).
    # ------------------------------------------------------------

    @staticmethod
    def try_acquire(test_case_id):

        return WebRecordingSession.try_acquire(test_case_id)

    @staticmethod
    def release():

        WebRecordingSession.release()

    # ------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------

    def start(self):
        """
        Validates the test case is actually eligible, creates the
        run-history row, and starts the background thread. Raises
        ValueError (caller turns this into a WebSocket error frame)
        for anything that means this Execute click was never going to
        work at all — the same checks TestCasesWeb.can_auto_execute()
        applies to the plain (non-interactive) Execute button, so a
        test case that can't be run one way can't be run the other
        way either.
        """

        test_case = self.manager.repository.get_test_case(self.test_case_id)

        if not test_case:

            raise ValueError(f"Test case {self.test_case_id} not found.")

        if test_case.get("automation_type") != "Playwright":

            raise ValueError(
                "Interactive execution is only available for "
                "Playwright test cases."
            )

        if not TestExecutionManager.get_active_script(test_case):

            raise ValueError(
                "This test case has no Playwright automation script "
                "available yet — use Add Automation (AI-generated) "
                "or Record Manually first."
            )

        run = self.runs.create_run(
            test_case, self.executed_by_user_id, self.executed_by_username,
        )

        self.run_uuid = run["run_uuid"]

        self._thread = threading.Thread(target=self._run, daemon=True)

        self._thread.start()

        return run

    def _run(self):

        self.runs.mark_running(self.run_uuid)

        self._out_queue.put({
            "type": "progress",
            "text": "Launching browser and running the script...",
        })

        try:

            result = self.manager.execute_playwright_interactive(
                self.test_case_id,
                on_step_failed=self._on_step_failed,
                on_repaired=self._on_repaired,
                on_locator_verified=self._on_locator_verified,
                max_repair_rounds=3,
            )

            if not result.get("interactive_supported"):

                # This script's structure matches neither the flat
                # AI-generated shape nor the standard Manually
                # Recorded (Playwright-codegen) shape interactive
                # repair supports — the exact same "isn't available
                # for this script's structure" case Desktop's
                # on_execution_finished() falls back from. Reuses the
                # SAME run row (rather than creating a second one) so
                # Execution History/Run Details show one continuous
                # run, not a phantom "unsupported" attempt plus a
                # real one — no browser was actually opened for this
                # first attempt, so nothing here needs undoing.
                self._out_queue.put({
                    "type": "progress",
                    "text": (
                        "Interactive step-by-step repair isn't "
                        "available for this script's structure — "
                        "running it normally instead."
                    ),
                })

                fallback_manager = TestExecutionManager(force_headless=True)

                result = fallback_manager.execute_playwright(self.test_case_id)

        except Exception as ex:

            logger.exception("[web-interactive-execute] run crashed")

            result = {"error": str(ex)}

        self.runs.mark_finished(self.run_uuid, result)

        self._out_queue.put({
            "type": "finished",
            "run_uuid": self.run_uuid,
            "result": result,
        })

    def _on_step_failed(self, event):
        """
        Called SYNCHRONOUSLY, on this session's background thread, by
        TestExecutionManager.execute_playwright_interactive() (via
        PlaywrightRunner.run_script_interactive()) — BLOCKS until
        submit_decision() is called from the WebSocket router after
        the operator answers, in the repair dialog, on the browser
        tab. Exactly the handoff Desktop's PlaywrightInteractiveWorker
        does with its own queue.Queue(); this one is just consumed by
        an async router instead of a Qt signal handler.
        """

        self._pending_failure_event = event

        self._out_queue.put({"type": "step_failed", "event": event})

        return self._decision_queue.get()

    def _on_repaired(self, repair):

        self._out_queue.put({"type": "step_repaired", "repair": repair})

    def _on_locator_verified(self, event):
        """
        Called SYNCHRONOUSLY on the background thread, same handoff as
        _on_step_failed(), but for a "verify_locator" round the
        harness just completed for the SAME still-open step_failed
        pause (see PlaywrightRunner.run_script_interactive()'s
        docstring). Deliberately does NOT touch
        self._pending_failure_event — it's left pointing at the
        ORIGINAL step_failed event so Ask AI still works while the
        operator is verifying a candidate locator, and submit_decision()
        below only clears it once a terminal decision (retry /
        retry_code / cancel) actually arrives.
        """

        self._out_queue.put({"type": "locator_verified", "event": event})

        return self._decision_queue.get()

    def next_event(self):
        """
        Blocking — the router calls this via an executor
        (loop.run_in_executor), never directly on the asyncio event
        loop thread.
        """

        return self._out_queue.get()

    def submit_decision(self, decision):
        # "verify_locator" is a non-terminal, repeatable check (see
        # _on_locator_verified() above) -- it must NOT clear the
        # pending failure event, or a subsequent Ask AI click would
        # wrongly report "No failure is currently awaiting a fix."
        # while the operator is still mid-verification on the exact
        # same paused step.
        if (decision or {}).get("action") != "verify_locator":

            self._pending_failure_event = None

        self._decision_queue.put(decision)

    def ask_ai(self):
        """
        Blocking (a real local-Ollama round-trip can take several
        seconds) — the router calls this via an executor too, same as
        next_event(). Grounded in whichever failure is CURRENTLY
        outstanding; returns an explicit {"success": False, ...} dict
        (never raises) if called with nothing pending — e.g. a stale
        double-click after the operator already retried the step
        successfully.
        """

        if self._pending_failure_event is None:

            return {
                "success": False,
                "error": "No failure is currently awaiting a fix.",
            }

        test_case = self.manager.repository.get_test_case(self.test_case_id)

        return self.manager.suggest_locator_fix(
            self._pending_failure_event, test_case,
        )

    def cancel_run(self):
        """
        Terminates the running subprocess directly — works whether or
        not a step_failed prompt is currently outstanding. This is
        Desktop's standalone Cancel Execution button; distinct from
        the repair dialog's own Cancel Test Run button, which instead
        submits {"action": "cancel"} as a normal decision through
        submit_decision() so the harness itself exits cleanly.
        """

        self.manager.cancel_interactive_run()

    def apply_repairs(self, repairs):
        """
        Only meaningful after the run has already finished
        successfully — mirrors Desktop's offer_to_save_repairs(),
        called once the operator confirms via the "Save these
        fixes?" prompt.
        """

        return self.manager.apply_script_repairs(self.test_case_id, repairs)

    def abandon(self):
        """
        Called when the WebSocket drops (tab closed, connection lost)
        while this run might still be in progress — unblocks the
        background thread with a cancel decision (covers "paused on
        the repair dialog") AND terminates the subprocess directly
        (covers "mid-step, nothing currently paused"), so a dropped
        connection never leaves either a blocked background thread or
        an unattended real browser window behind. Mirrors
        recorder_router.py's WebSocketDisconnect handling. Safe to
        call even after the run has already finished — both calls are
        harmless no-ops at that point (nothing blocked, nothing left
        to terminate).
        """

        self._decision_queue.put({"action": "cancel"})

        self.cancel_run()
