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

    # emitted with {"suggested_type": ..., "reason": ...}
    finished = Signal(dict)

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

            self.finished.emit(suggestion)

        except Exception as ex:

            self.error.emit(str(ex))