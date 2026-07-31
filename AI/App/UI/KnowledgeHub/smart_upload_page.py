# Replace: App/UI/KnowledgeHub/smart_upload_page.py

"""
==========================================================
QA AI Studio

Knowledge Hub

AI Smart Upload

Version : 3.0  (Backend-Wired Fix)

Production AI Smart Upload Workflow

Flow:
    Select Files / Folder
        |
        v
    Analyze With AI  (background thread)
        |
        v
    Review + Edit Suggested Domain / Module /
    Knowledge Name / Version / Document Type
        |
        v
    Confirm Upload  (background thread, ALL files)
        |
        v
    Metadata + VectorStore

Fix Notes (v3.0):
    • KnowledgeService.smart_upload() now exists in the backend —
      wired it in via a new background QThread worker
      (SmartUploadWorker) instead of calling it directly on the
      UI thread.
    • Analyzing multiple files no longer overwrites itself —
      every file's suggestion is appended to the AI Analysis
      Summary as it completes; the first *successful* file's
      suggestion is used to auto-fill the editable fields.
    • Domain field is now a real editable QComboBox pre-populated
      with the known PSW domains, so a wrong AI guess can be
      corrected instead of being stuck.
    • Confirm Upload now sends ALL selected files (previously it
      silently uploaded only the first file), and runs on a
      background thread via the existing UploadWorker so the UI
      doesn't freeze during embedding.
    • Folder selection now filters to file types the extractor
      can actually read, instead of walking every file on disk.
    • Wrapped page content in a QScrollArea to prevent the same
      overlap issue fixed earlier in Manual Upload.
    • Removed dead/duplicate upload_files() method and wired the
      four placeholder connector buttons to a "coming soon" note
      instead of doing nothing.
==========================================================
"""

from pathlib import Path

from PySide6.QtCore import QThread

from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QGridLayout,
    QLabel,
    QPushButton,
    QListWidget,
    QFileDialog,
    QTextEdit,
    QMessageBox,
    QLineEdit,
    QComboBox,
    QGroupBox,
    QScrollArea,
    QSizePolicy,
    QInputDialog,
)

from Core.metadata_manager import MetadataManager

from UI.KnowledgeHub.smart_upload_worker import SmartUploadWorker

from UI.KnowledgeHub.upload_worker import UploadWorker


# Extensions the backend TextExtractor can actually read.
# Keeps folder scans from dragging in images/binaries that
# would just fail analysis and clutter the log.
SUPPORTED_EXTENSIONS = {
    ".pdf",
    ".docx",
    ".doc",
    ".xlsx",
    ".xls",
    ".txt",
}

KNOWN_DOMAINS = [
    "PSW Core",
    "WeBOC",
    "WeBOC 2.0",
    "PCS",
    "ACS",
    "Other",
]

DOCUMENT_TYPES = [
    "General",
    "SRS",
    "CRF",
    "Test Case",
    "API",
    "SOP",
    "Release Notes",
    "Technical Document",
    "Other",
]


class SmartUploadPage(QWidget):

    def __init__(self):

        super().__init__()

        self.files = []

        self.analysis_results = {}

        self.analysis_thread = None

        self.analysis_worker = None

        self.upload_thread = None

        self.upload_worker = None

        self.metadata = MetadataManager()

        self.build_ui()

        self.set_fields_enabled(False)


    # ======================================================
    # UI
    # ======================================================

    def build_ui(self):

        outer = QVBoxLayout(self)

        outer.setContentsMargins(0, 0, 0, 0)

        outer.setSpacing(0)

        scroll = QScrollArea()

        scroll.setWidgetResizable(True)

        scroll.setFrameShape(QScrollArea.NoFrame)

        outer.addWidget(scroll)

        content = QWidget()

        scroll.setWidget(content)

        layout = QVBoxLayout(content)

        layout.setContentsMargins(20, 20, 20, 20)

        layout.setSpacing(15)


        title = QLabel(
            "AI Smart Upload"
        )

        title.setObjectName(
            "SectionTitle"
        )

        layout.addWidget(title)


        subtitle = QLabel(
            "AI analyzes enterprise knowledge and prepares metadata automatically."
        )

        layout.addWidget(subtitle)


        # ==================================================
        # Knowledge Information (AI-suggested, editable)
        # ==================================================

        info_group = QGroupBox(
            "Knowledge Information — AI Suggested"
        )

        info_layout = QGridLayout(
            info_group
        )

        info_layout.setContentsMargins(15, 20, 15, 15)

        info_layout.setHorizontalSpacing(12)

        info_layout.setVerticalSpacing(10)

        info_layout.setColumnStretch(1, 1)

        info_layout.setColumnStretch(3, 1)


        self.domain = QComboBox()

        self.domain.setEditable(True)

        self.domain.addItems(
            KNOWN_DOMAINS
        )

        self.domain.setCurrentIndex(-1)

        self.domain.lineEdit().setPlaceholderText(
            "Select or type domain"
        )


        self.module = QComboBox()

        self.module.setEditable(True)

        self.module.lineEdit().setPlaceholderText(
            "Select or type module"
        )

        self.add_module_btn = QPushButton("+")

        self.add_module_btn.setFixedWidth(30)

        self.add_module_btn.setToolTip(
            "Add a new module under the selected domain"
        )

        module_row = QHBoxLayout()

        module_row.setContentsMargins(0, 0, 0, 0)

        module_row.addWidget(self.module)

        module_row.addWidget(self.add_module_btn)


        self.knowledge_name = QLineEdit()

        self.knowledge_name.setPlaceholderText(
            "Knowledge Name"
        )


        self.version = QLineEdit(
            "1.0"
        )


        self.document_type = QComboBox()

        self.document_type.addItems(
            DOCUMENT_TYPES
        )


        info_layout.addWidget(
            QLabel("Domain"), 0, 0
        )

        info_layout.addWidget(
            self.domain, 0, 1
        )

        info_layout.addWidget(
            QLabel("Module"), 0, 2
        )

        info_layout.addLayout(
            module_row, 0, 3
        )


        info_layout.addWidget(
            QLabel("Knowledge Name"), 1, 0
        )

        info_layout.addWidget(
            self.knowledge_name, 1, 1
        )

        info_layout.addWidget(
            QLabel("Version"), 1, 2
        )

        info_layout.addWidget(
            self.version, 1, 3
        )


        info_layout.addWidget(
            QLabel("Document Type"), 2, 0
        )

        info_layout.addWidget(
            self.document_type, 2, 1
        )


        layout.addWidget(
            info_group
        )


        # ==================================================
        # File Selection
        # ==================================================

        file_group = QGroupBox(
            "Upload Documents"
        )

        file_layout = QVBoxLayout(
            file_group
        )

        file_layout.setContentsMargins(15, 20, 15, 15)

        file_layout.setSpacing(10)


        buttons = QHBoxLayout()

        buttons.setSpacing(8)


        self.file_btn = QPushButton("Select Files")

        self.folder_btn = QPushButton("Select Folder")

        self.url_btn = QPushButton("Add URL")

        self.api_btn = QPushButton("Add API")

        self.sql_btn = QPushButton("Add SQL")

        self.image_btn = QPushButton("Add Images")

        self.analyze_btn = QPushButton("Analyze With AI")

        self.upload_btn = QPushButton("Confirm Upload")
        
        self.remove_btn = QPushButton("Remove Selected")

        self.clear_btn = QPushButton("Clear All")

        self.remove_btn.clicked.connect(
            self.remove_selected
        )

        self.clear_btn.clicked.connect(
            self.clear_files
        )

        buttons.addWidget(self.file_btn)

        buttons.addWidget(self.folder_btn)

        buttons.addWidget(self.url_btn)

        buttons.addWidget(self.api_btn)

        buttons.addWidget(self.sql_btn)

        buttons.addWidget(self.image_btn)
    
        buttons.addWidget(self.remove_btn)

        buttons.addWidget(self.clear_btn)
        
        buttons.addStretch()

        buttons.addWidget(self.analyze_btn)

        buttons.addWidget(self.upload_btn)


        file_layout.addLayout(
            buttons
        )


        self.file_list = QListWidget()

        self.file_list.setMinimumHeight(120)

        self.file_list.setSizePolicy(
            QSizePolicy.Expanding,
            QSizePolicy.Fixed
        )

        file_layout.addWidget(
            self.file_list
        )


        layout.addWidget(
            file_group
        )


        # ==================================================
        # AI Result
        # ==================================================

        result_group = QGroupBox(
            "AI Analysis Summary"
        )

        result_layout = QVBoxLayout(
            result_group
        )

        result_layout.setContentsMargins(15, 20, 15, 15)

        result_layout.setSpacing(10)


        self.summary = QTextEdit()

        self.summary.setReadOnly(True)

        self.summary.setMinimumHeight(220)

        self.summary.setSizePolicy(
            QSizePolicy.Expanding,
            QSizePolicy.Fixed
        )

        result_layout.addWidget(
            self.summary
        )


        self.log = QTextEdit()

        self.log.setReadOnly(True)

        self.log.setMinimumHeight(100)

        self.log.setSizePolicy(
            QSizePolicy.Expanding,
            QSizePolicy.Fixed
        )

        result_layout.addWidget(
            self.log
        )


        layout.addWidget(
            result_group
        )


        # ==================================================
        # Events
        # ==================================================

        self.file_btn.clicked.connect(
            self.select_files
        )

        self.folder_btn.clicked.connect(
            self.select_folder
        )

        self.url_btn.clicked.connect(
            lambda: self.connector_coming_soon("URL")
        )

        self.api_btn.clicked.connect(
            lambda: self.connector_coming_soon("API Collection")
        )

        self.sql_btn.clicked.connect(
            lambda: self.connector_coming_soon("SQL Script")
        )

        self.image_btn.clicked.connect(
            lambda: self.connector_coming_soon("Images")
        )

        self.analyze_btn.clicked.connect(
            self.analyze_files
        )

        self.upload_btn.clicked.connect(
            self.confirm_upload
        )

        self.domain.currentTextChanged.connect(
            self.on_domain_changed
        )

        self.add_module_btn.clicked.connect(
            self.add_module
        )

        self.upload_btn.setEnabled(False)


    # ======================================================
    # Domain -> Module cascade
    # ======================================================

    def on_domain_changed(self, domain_name):

        self.module.clear()

        domain_name = (domain_name or "").strip()

        if not domain_name:

            return

        try:

            existing_modules = self.metadata.list_modules(
                domain_name
            )

            self.module.addItems(existing_modules)

            self.module.setCurrentIndex(-1)

        except Exception:

            # Domain isn't registered yet (e.g. brand new AI
            # suggestion) — that's fine, just start with an empty,
            # freely-typed module field.
            pass


    def add_module(self):

        domain_name = self.domain.currentText().strip()

        if not domain_name:

            QMessageBox.warning(
                self,
                "QA AI Studio",
                "Select or type a Domain first."
            )

            return

        value, ok = QInputDialog.getText(
            self, "Add Module", "Module Name"
        )

        if not (ok and value.strip()):

            return

        module_name = value.strip()

        try:

            existing_modules = self.metadata.list_modules(
                domain_name
            )

        except Exception:

            existing_modules = []

        if module_name in existing_modules:

            QMessageBox.information(
                self,
                "QA AI Studio",
                f"'{module_name}' already exists under "
                f"'{domain_name}'. Selecting it instead."
            )

            self.module.setCurrentText(module_name)

            return

        try:

            if self.module.findText(module_name) == -1:
                self.module.addItem(module_name)

            self.module.setCurrentText(module_name)

        except Exception as ex:

            QMessageBox.critical(
                self, "QA AI Studio", str(ex)
            )

            return

        self.module.addItem(module_name)

        self.module.setCurrentText(module_name)


    # ======================================================
    # Helpers
    # ======================================================

    def connector_coming_soon(self, source_name):

        QMessageBox.information(
            self,
            "Connector",
            f"{source_name} connector will be enabled in a future backend phase."
        )


    def set_fields_enabled(self, enabled):

        self.domain.setEnabled(enabled)

        self.module.setEnabled(enabled)

        self.knowledge_name.setEnabled(enabled)

        self.version.setEnabled(enabled)

        self.document_type.setEnabled(enabled)


    def reset_analysis_state(self):

        self.analysis_results = {}

        self.summary.clear()

        self.log.clear()

        self.set_fields_enabled(False)

        self.upload_btn.setEnabled(False)

        self.domain.setCurrentIndex(-1)

        self.module.clear()

        self.knowledge_name.clear()

        self.version.setText("1.0")

        self.document_type.setCurrentIndex(0)


    # ======================================================
    # File Selection
    # ======================================================

    def select_files(self):

        files, _ = QFileDialog.getOpenFileNames(
            self,
            "Select Knowledge Files"
        )

        if not files:

            return

        for file in files:

            if file not in self.files:

                self.files.append(file)

        self.refresh_files()

        self.reset_analysis_state()


    def select_folder(self):

        folder = QFileDialog.getExistingDirectory(
            self,
            "Select Folder"
        )

        if not folder:

            return

        added = 0

        for file in Path(folder).rglob("*.*"):

            if file.suffix.lower() not in SUPPORTED_EXTENSIONS:

                continue

            path_str = str(file)

            if path_str not in self.files:

                self.files.append(path_str)

                added += 1

        self.refresh_files()

        self.reset_analysis_state()

        if added == 0:

            QMessageBox.information(
                self,
                "QA AI Studio",
                "No supported documents (PDF, Word, Excel, Text) "
                "were found in that folder."
            )


    def refresh_files(self):

        self.file_list.clear()

        self.file_list.addItems(
            self.files
        )

    def remove_selected(self):

        item = self.file_list.currentItem()

        if item is None:
            return

        path = item.text()

        if path in self.files:
            self.files.remove(path)

        self.refresh_files()

        self.reset_analysis_state()

    def load_modules(self):

        domain = self.domain.currentText().strip()

        self.module.clear()

        if not domain:
            return

        try:

            modules = self.manager.get_modules(domain)

            self.module.addItems(modules)

        except Exception:

            pass


    # ======================================================
    # AI Analyze (background thread)
    # ======================================================

    def analyze_files(self):

        if not self.files:

            QMessageBox.warning(
                self,
                "QA AI Studio",
                "Select files first."
            )

            return

        if self.analysis_thread is not None:

            # Already running — ignore double-click.
            return

        if self.upload_thread is not None:

            QMessageBox.information(
                self,
                "QA AI Studio",
                "Please wait for the current upload to finish."
            )

            return

        self.reset_analysis_state()

        self.analyze_btn.setEnabled(False)

        self.log.append(
            "Starting AI analysis..."
        )

        self.analysis_thread = QThread()

        self.analysis_worker = SmartUploadWorker(
            list(self.files)
        )

        self.analysis_worker.moveToThread(
            self.analysis_thread
        )

        self.analysis_thread.started.connect(
            self.analysis_worker.run
        )

        self.analysis_worker.progress.connect(
            self.log.append
        )

        self.analysis_worker.file_analyzed.connect(
            self.handle_file_analyzed
        )

        self.analysis_worker.file_failed.connect(
            self.handle_file_failed
        )

        self.analysis_worker.finished.connect(
            self.analysis_finished
        )

        self.analysis_worker.error.connect(
            self.analysis_error
        )

        self.analysis_worker.finished.connect(
            self.analysis_thread.quit
        )

        self.analysis_worker.error.connect(
            self.analysis_thread.quit
        )

        self.analysis_thread.finished.connect(
            self.cleanup_analysis_thread
        )

        self.analysis_thread.start()


    def handle_file_analyzed(self, file_path, result):

        self.analysis_results[file_path] = result

        name = Path(file_path).name

        domain = result.get("domain", "Unknown")

        module = result.get("module", "") or "Unknown"

        document_type = result.get("document_type", "General")

        confidence = result.get("confidence", 0)

        tags = result.get("tags", [])

        summary_text = result.get("summary", "").strip() or "No summary available."


        block = (
            f"File: {name}\n"
            f"Suggested Domain: {domain}\n"
            f"Suggested Module: {module}\n"
            f"Document Type: {document_type}\n"
            f"Confidence: {confidence}\n"
            f"Tags: {', '.join(tags) if tags else '—'}\n"
            f"Summary: {summary_text}\n"
            + ("-" * 50)
        )

        self.summary.append(block)


    def handle_file_failed(self, file_path, message):

        name = Path(file_path).name

        self.summary.append(
            f"File: {name}\nAnalysis failed: {message}\n"
            + ("-" * 50)
        )


    def analysis_finished(self, results):

        self.analyze_btn.setEnabled(True)

        if not results:

            QMessageBox.warning(
                self,
                "QA AI Studio",
                "AI analysis did not succeed for any of the selected files. "
                "You can still fill in the details manually and upload."
            )

            self.set_fields_enabled(True)

            self.upload_btn.setEnabled(True)

            return

        # Use the first successfully analyzed file, in the order the
        # user picked them, as the primary suggestion for the batch.
        primary_result = None

        for file_path in self.files:

            if file_path in results:

                primary_result = results[file_path]

                break

        if primary_result:

            domain = primary_result.get("domain", "")
            module = primary_result.get("module", "")

            if domain and self.domain.findText(domain) == -1:

                self.domain.addItem(domain)

            self.domain.setCurrentText(domain)

            self.load_modules()

            if module:

                if self.module.findText(module) == -1:

                    self.module.addItem(module)

                self.module.setCurrentText(module)


            if not self.knowledge_name.text().strip():

                first_file = next(iter(results.keys()))

                self.knowledge_name.setText(
                    Path(first_file).stem.replace("_", " ").replace("-", " ").strip()
                )


            self.version.setText(
                primary_result.get("version", "1.0")
            )


            doc_type = primary_result.get("document_type", "General")

            index = self.document_type.findText(doc_type)

            if index == -1:

                index = 0

            self.document_type.setCurrentIndex(index)


        self.set_fields_enabled(True)

        self.upload_btn.setEnabled(True)

        self.log.append(
            f"Analysis complete: {len(results)}/{len(self.files)} file(s) succeeded."
        )


    def analysis_error(self, message):

        self.analyze_btn.setEnabled(True)

        self.log.append(
            f"Analysis error: {message}"
        )

        QMessageBox.critical(
            self,
            "AI Analysis Failed",
            message
        )


    def cleanup_analysis_thread(self):

        if self.analysis_thread:

            self.analysis_thread.deleteLater()

        self.analysis_thread = None

        self.analysis_worker = None


    # ======================================================
    # Confirm Upload (background thread, ALL files)
    # ======================================================

    def confirm_upload(self):

        if not self.files:

            QMessageBox.warning(
                self,
                "QA AI Studio",
                "Select files first."
            )

            return

        if not self.analysis_results:

            QMessageBox.warning(
                self,
                "QA AI Studio",
                "Please analyze the selected files with AI first."
            )

            return

        if self.upload_thread is not None:

            # Already running — ignore double-click.
            return

        domain = self.domain.currentText().strip()

        module = self.module.currentText().strip()

        knowledge_name = self.knowledge_name.text().strip()

        version = self.version.text().strip() or "1.0"

        document_type = self.document_type.currentText()


        if not domain:

            QMessageBox.warning(
                self, "Validation", "Domain is required."
            )

            return

        if not module:

            QMessageBox.warning(
                self, "Validation", "Module is required."
            )

            return

        if not knowledge_name:

            QMessageBox.warning(
                self, "Validation", "Knowledge Name is required."
            )

            return


        self.upload_btn.setEnabled(False)

        self.analyze_btn.setEnabled(False)

        self.log.append(
            f"Uploading {len(self.files)} file(s)..."
        )

        self.upload_thread = QThread()

        self.upload_worker = UploadWorker(
            domain,
            module,
            knowledge_name,
            version,
            document_type,
            list(self.files)
        )

        self.upload_worker.moveToThread(
            self.upload_thread
        )

        self.upload_thread.started.connect(
            self.upload_worker.run
        )

        self.upload_worker.progress.connect(
            self.log.append
        )

        self.upload_worker.finished.connect(
            self.upload_finished
        )

        self.upload_worker.error.connect(
            self.upload_error
        )

        self.upload_worker.finished.connect(
            self.upload_thread.quit
        )

        self.upload_worker.error.connect(
            self.upload_thread.quit
        )

        self.upload_thread.finished.connect(
            self.cleanup_upload_thread
        )

        self.upload_thread.start()


    def upload_finished(self, result):

        self.log.append(
            "Knowledge uploaded successfully."
        )

        QMessageBox.information(
            self,
            "QA AI Studio",
            f"Upload completed.\n\nFiles processed: {len(self.files)}"
        )

        # Ready for the next batch.
        self.files = []

        self.file_list.clear()

        self.reset_analysis_state()

        self.analyze_btn.setEnabled(True)


    def upload_error(self, message):

        self.upload_btn.setEnabled(True)

        self.analyze_btn.setEnabled(True)

        self.log.append(
            f"Upload failed: {message}"
        )

        QMessageBox.critical(
            self,
            "Upload Failed",
            message
        )


    def cleanup_upload_thread(self):

        if self.upload_thread:

            self.upload_thread.deleteLater()

        self.upload_thread = None

        self.upload_worker = None


    def clear_files(self):

        self.files.clear()

        self.refresh_files()

        self.reset_analysis_state()