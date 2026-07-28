"""
==========================================================
QA AI Studio

Knowledge Upload Worker

Version : 2.0

Production Background Upload Executor

Responsibilities:
    • Run KnowledgeService.upload()
    • Keep UI responsive
    • Emit progress updates
    • Return upload result
    • Handle failures safely
==========================================================
"""

from PySide6.QtCore import QObject, Signal

from Core.knowledge_service import KnowledgeService


class UploadWorker(QObject):

    # lifecycle signals
    started = Signal()

    finished = Signal(dict)

    completed = Signal(dict)

    error = Signal(str)

    progress = Signal(str)

    def __init__(
        self,
        domain,
        module,
        knowledge_name,
        version,
        document_type,
        files
    ):

        super().__init__()

        self.domain = domain

        self.module = module

        self.knowledge_name = knowledge_name

        self.version = version

        self.document_type = document_type

        self.files = files

        self.service = KnowledgeService()


    # -------------------------------------------------
    # Worker execution
    # -------------------------------------------------

    def run(self):

        try:

            self.started.emit()


            self.progress.emit(
                "Validating upload information..."
            )


            if not self.domain:

                raise ValueError(
                    "Domain is required"
                )


            if not self.module:

                raise ValueError(
                    "Module is required"
                )


            if not self.knowledge_name:

                raise ValueError(
                    "Knowledge name is required"
                )


            if not self.files:

                raise ValueError(
                    "No files selected"
                )


            self.progress.emit(
                "Preparing knowledge upload..."
            )


            self.progress.emit(
                "Extracting documents..."
            )


            result = self.service.upload(

                domain=self.domain,

                module=self.module,

                knowledge_name=self.knowledge_name,

                version=self.version,

                document_type=self.document_type,

                files=self.files

            )


            if not isinstance(result, dict):

                result = {

                    "status": "completed",

                    "message": str(result)

                }


            self.progress.emit(
                "Upload completed successfully"
            )


            self.finished.emit(result)

            self.completed.emit(result)


        except Exception as ex:


            self.error.emit(
                str(ex)
            )