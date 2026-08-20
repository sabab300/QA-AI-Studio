# Create: App/UI/QAAutomation/test_execution_worker.py

"""
==========================================================
QA AI Studio

Test Execution Worker

Version : 1.0

Runs automation-script generation (LLM call, can be slow) off the
UI thread for one or more selected test cases. Listing / status /
manual-result updates are plain local SQLite calls and stay
synchronous on the UI thread — they're fast enough not to need this.
==========================================================
"""
import queue

from PySide6.QtCore import QObject, Signal

from Core.test_execution_manager import TestExecutionManager


class AutomationGenerationWorker(QObject):

    started = Signal()

    progress = Signal(str)

    # emitted once per test case: (test_case_id, script)
    case_done = Signal(int, str)

    # emitted once per test case that fails: (test_case_id, message)
    case_failed = Signal(int, str)

    finished = Signal()

    error = Signal(str)

    def __init__(
        self,
        items,
        domain,
        module,
        knowledge_name,
        version=None,
    ):
        """
        items: list of (test_case_id, automation_type) tuples — each
        row can request a different automation type.
        """

        super().__init__()

        self.items = items

        self.domain = domain

        self.module = module

        self.knowledge_name = knowledge_name

        self.version = version

        self.manager = TestExecutionManager()

    def run(self):

        try:

            self.started.emit()

            total = len(self.items)

            for index, (test_case_id, automation_type) in enumerate(
                self.items, start=1
            ):

                self.progress.emit(
                    f"Generating {automation_type} automation "
                    f"({index}/{total})..."
                )

                try:

                    script = self.manager.generate_automation(
                        test_case_id=test_case_id,
                        automation_type=automation_type,
                        domain=self.domain,
                        module=self.module,
                        knowledge_name=self.knowledge_name,
                        version=self.version,
                    )

                    self.case_done.emit(
                        test_case_id,
                        script
                    )

                except Exception as case_ex:

                    self.case_failed.emit(
                        test_case_id,
                        str(case_ex)
                    )

            self.finished.emit()

        except Exception as ex:

            self.error.emit(str(ex))


class AutomationSuggestionWorker(QObject):
    """
    Runs the AI automation-type suggestion (LLM call) off the UI
    thread for a single test case, for the "Automate" popup.
    """

    started = Signal()

    progress = Signal(str)

    # emitted with (test_case_id, {"suggested_type": ..., "reason": ...})
    finished = Signal(int, dict)

    error = Signal(str)

    def __init__(self, test_case_id):

        super().__init__()

        self.test_case_id = test_case_id

        self.manager = TestExecutionManager()

    def run(self):

        try:

            self.started.emit()

            self.progress.emit(
                "Analyzing test case with AI..."
            )

            suggestion = self.manager.suggest_automation_type(
                self.test_case_id
            )

            self.finished.emit(self.test_case_id, suggestion)

        except Exception as ex:

            self.error.emit(str(ex))


class ManualRecordingWorker(QObject):
    """
    Launches Playwright's own codegen recorder for a single test
    case and BLOCKS (on this background thread, never the UI thread)
    until the operator closes the recorder browser — a real,
    hand-driven browser session can legitimately run for any length
    of time, so there is no timeout here, only Cancel Recording.
    """

    started = Signal()

    progress = Signal(str)

    # emitted with (test_case_id, recorded_script)
    finished = Signal(int, str)

    # emitted with (test_case_id,) when Cancel Recording was used
    cancelled = Signal(int)

    error = Signal(str)

    def __init__(self, test_case_id, start_url=None):

        super().__init__()

        self.test_case_id = test_case_id

        self.start_url = start_url

        self.manager = TestExecutionManager()

        self._process = None

        self._cancel_requested = False

    def run(self):

        try:

            self.started.emit()

            self.progress.emit(
                "Opening the recorder browser — perform your test "
                "steps by hand (click, type, navigate), then close "
                "the recorder browser window when finished, or use "
                "Cancel Recording to stop without saving."
            )

            script = self.manager.record_manual_script(
                self.test_case_id,
                self.start_url,
                on_process_started=self._on_process_started,
            )

            self.finished.emit(self.test_case_id, script)

        except Exception as ex:

            if self._cancel_requested:

                self.cancelled.emit(self.test_case_id)

            else:

                self.error.emit(str(ex))

    def _on_process_started(self, process):

        self._process = process

    def cancel(self):
        """
        Safe to call from the UI thread while run() is executing on
        the worker thread — terminating a subprocess.Popen doesn't
        require being on the thread that started it.
        """

        self._cancel_requested = True

        if self._process is not None and self._process.poll() is None:

            self._process.terminate()


class PlaywrightExecutionWorker(QObject):
    """
    Actually runs a generated Playwright script in a real browser,
    off the UI thread — a script launching a browser and waiting on
    page elements can easily take 10-60+ seconds.
    """

    started = Signal()

    progress = Signal(str)

    # emitted with (test_case_id, result_dict)
    finished = Signal(int, dict)

    error = Signal(str)

    def __init__(self, test_case_id, timeout_seconds=None):

        super().__init__()

        self.test_case_id = test_case_id

        self.timeout_seconds = timeout_seconds

        self.manager = TestExecutionManager()

    def run(self):

        try:

            self.started.emit()

            self.progress.emit(
                "Launching browser and running the script..."
            )

            result = self.manager.execute_playwright(
                self.test_case_id,
                timeout_seconds=self.timeout_seconds,
            )

            self.finished.emit(self.test_case_id, result)

        except Exception as ex:

            self.error.emit(str(ex))


class PlaywrightInteractiveWorker(QObject):
    """
    Interactive counterpart to PlaywrightExecutionWorker — used ONLY
    when exactly one test case is being run (see
    test_execution_page.py's run_next_execution(), which decides
    which worker to use). When a step's locator/value fails, this
    pauses the SCRIPT (not the UI) and asks the operator what to do,
    instead of just failing the whole run — the direct fix for
    "Playwright gets stuck on a Locator/element/value with no way to
    correct it and keep going."

    step_failed is emitted FROM this background thread and delivered
    to the UI thread via Qt's normal cross-thread queued connection.
    The background thread then blocks (inside _on_step_failed(),
    which TestExecutionManager.execute_playwright_interactive() is
    calling synchronously) on a plain queue.Queue until the UI
    thread — after showing the repair dialog and, if needed, running
    the AI-suggestion flow — calls submit_decision() with the
    operator's answer. No Qt-specific synchronization primitives are
    needed for that hand-off; queue.Queue is thread-safe on its own.
    """

    started = Signal()

    progress = Signal(str)

    # Emitted when a step fails and needs an operator decision.
    step_failed = Signal(dict)

    # Emitted right after a step is successfully repaired — purely
    # informational, for a live log line; the run keeps going either
    # way once this fires.
    step_repaired = Signal(dict)

    # emitted with (test_case_id, result_dict)
    finished = Signal(int, dict)

    error = Signal(str)

    def __init__(self, test_case_id, max_repair_rounds=3):

        super().__init__()

        self.test_case_id = test_case_id

        self.max_repair_rounds = max_repair_rounds

        self.manager = TestExecutionManager()

        self._decision_queue = queue.Queue()

    def submit_decision(self, decision):

        self._decision_queue.put(decision)

    def cancel(self):

        self.manager.cancel_interactive_run()

    def run(self):

        try:

            self.started.emit()

            self.progress.emit(
                "Launching browser and running the script..."
            )

            result = self.manager.execute_playwright_interactive(
                self.test_case_id,
                on_step_failed=self._on_step_failed,
                on_repaired=self._on_repaired,
                max_repair_rounds=self.max_repair_rounds,
            )

            self.finished.emit(self.test_case_id, result)

        except Exception as ex:

            self.error.emit(str(ex))

    def _on_step_failed(self, event):

        self.step_failed.emit(event)

        return self._decision_queue.get()

    def _on_repaired(self, repair):

        self.step_repaired.emit(repair)


class AiLocatorSuggestionWorker(QObject):
    """
    Runs TestExecutionManager.suggest_locator_fix() off the UI
    thread — a local Ollama round-trip can take several seconds even
    for a short prompt, and the repair dialog stays open/responsive
    (Cancel still works) while this is in progress.
    """

    finished = Signal(dict)

    error = Signal(str)

    def __init__(self, manager, failure_event, test_case):

        super().__init__()

        self.manager = manager

        # NOT named "self.event" — QObject already has a virtual
        # event() method, and shadowing it with an instance
        # attribute of the same name breaks Qt's internal event
        # dispatch with a native crash (segfault) the moment this
        # QObject is moved to a thread and Qt tries to deliver it an
        # event. Cost real debugging time to track down once
        # already (see LocatorRepairDialog) — never call anything on
        # a QObject/QWidget subclass "event".
        self.failure_event = failure_event

        self.test_case = test_case

    def run(self):

        try:

            result = self.manager.suggest_locator_fix(
                self.failure_event, self.test_case
            )

            self.finished.emit(result)

        except Exception as ex:

            self.error.emit(str(ex))