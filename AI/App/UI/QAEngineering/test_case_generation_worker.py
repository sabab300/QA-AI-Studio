# Create: App/UI/QAEngineering/test_case_generation_worker.py

"""
==========================================================
QA AI Studio

Test Case Generation Worker

Version : 1.0

Production Background Test Case Generation Executor

Responsibilities:
    • Run TestCaseGenerator.generate() off the UI thread
      (RAG retrieval + LLM generation can take a while)
    • Emit progress updates for the log
    • Return the full result dict (file paths, case count, etc.)
    • Report failures without crashing the UI
==========================================================
"""

from PySide6.QtCore import QObject, Signal

from Core.test_case_generator import TestCaseGenerator


class TestCaseGenerationWorker(QObject):

    started = Signal()

    progress = Signal(str)

    finished = Signal(dict)

    error = Signal(str)

    def __init__(
        self,
        requirement,
        domain,
        module,
        knowledge_name,
        version,
        test_types,
        output_formats,
    ):

        super().__init__()

        self.requirement = requirement

        self.domain = domain

        self.module = module

        self.knowledge_name = knowledge_name

        self.version = version

        self.test_types = test_types

        self.output_formats = output_formats

        self.generator = TestCaseGenerator()

    # -------------------------------------------------
    # Worker execution
    # -------------------------------------------------

    def run(self):

        try:

            self.started.emit()

            if not self.domain:

                raise ValueError("Domain is required")

            if not self.module:

                raise ValueError("Module is required")

            if not self.knowledge_name:

                raise ValueError("Knowledge Name is required")

            if not self.test_types:

                raise ValueError("Select at least one test type")

            if not self.output_formats:

                raise ValueError("Select at least one output format")

            self.progress.emit(
                "Retrieving knowledge context..."
            )

            self.progress.emit(
                "Generating test cases with AI "
                "(this can take a little while)..."
            )

            result = self.generator.generate(

                requirement=self.requirement,

                domain=self.domain,

                module=self.module,

                knowledge_name=self.knowledge_name,

                version=self.version,

                test_types=self.test_types,

                output_formats=self.output_formats,

            )

            if not result.get("success"):

                self.error.emit(
                    result.get("error", "Test case generation failed.")
                )

                return

            self.progress.emit(
                f"Generated {result.get('case_count', 0)} test case(s)."
            )

            self.finished.emit(result)

        except Exception as ex:

            self.error.emit(
                str(ex)
            )