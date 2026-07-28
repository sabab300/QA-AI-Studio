# Create: App/UI/KnowledgeHub/smart_upload_worker.py

"""
==========================================================
QA AI Studio

AI Smart Upload Worker

Version : 1.0

Production Background Analysis Executor

Responsibilities:
    • Run KnowledgeService.smart_upload() for each selected file
    • Keep the UI responsive during text extraction + analysis
    • Emit per-file progress so the log can show live status
    • Return the full set of per-file results in one dict
    • Never let one bad/unreadable file abort the whole batch
==========================================================
"""

from pathlib import Path

from PySide6.QtCore import QObject, Signal

from Core.knowledge_service import KnowledgeService


class SmartUploadWorker(QObject):

    # lifecycle signals
    started = Signal()

    # emitted once per file as it completes: (file_path, result_dict)
    file_analyzed = Signal(str, dict)

    # emitted once per file that fails: (file_path, error_message)
    file_failed = Signal(str, str)

    progress = Signal(str)

    # emitted at the very end with { file_path: result_dict, ... }
    finished = Signal(dict)

    error = Signal(str)

    def __init__(self, files):

        super().__init__()

        self.files = files

        self.service = KnowledgeService()

    # -------------------------------------------------
    # Worker execution
    # -------------------------------------------------

    def run(self):

        try:

            self.started.emit()

            if not self.files:

                raise ValueError(
                    "No files selected for analysis"
                )

            results = {}

            total = len(self.files)

            for index, file_path in enumerate(self.files, start=1):

                name = Path(file_path).name

                self.progress.emit(
                    f"Analyzing ({index}/{total}): {name}"
                )

                try:

                    result = self.service.smart_upload(
                        source_file=file_path
                    )

                    results[file_path] = result

                    self.file_analyzed.emit(
                        file_path,
                        result
                    )

                except Exception as file_ex:

                    message = str(file_ex)

                    self.file_failed.emit(
                        file_path,
                        message
                    )

                    self.progress.emit(
                        f"Failed: {name} — {message}"
                    )

            self.progress.emit(
                "AI analysis completed."
            )

            self.finished.emit(results)

        except Exception as ex:

            self.error.emit(
                str(ex)
            )