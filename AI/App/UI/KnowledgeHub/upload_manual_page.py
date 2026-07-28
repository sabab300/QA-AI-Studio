# Replace/Create: App/UI/KnowledgeHub/upload_manual_page.py

"""
==========================================================
QA AI Studio
Knowledge Hub
Enterprise Knowledge Upload Studio

Version : 3.1  (Layout Fix)

Features:
    • Enterprise knowledge metadata capture
    • Multiple source selection
    • File / Folder upload
    • Image/document support
    • Future source connector support
    • Existing UploadWorker compatibility

Fix Notes (v3.1):
    • Root cause of overlapping UI: the page had no QScrollArea,
      so 4 stacked QGroupBox sections were force-compressed into
      the visible window height, pushing widgets below their
      stylesheet min-height and causing them to visually overlap.
    • Fix: entire page content now lives inside a QScrollArea.
      The content keeps its natural size and scrolls instead of
      being squeezed.
    • Added explicit grid spacing/margins and column stretch so
      fields line up cleanly instead of hugging the group title.
    • Added sensible minimum heights for the table and log so
      they render nicely at natural size.
==========================================================
"""

import os

from PySide6.QtCore import QThread, Qt
from Core.metadata_manager import MetadataManager
from UI.KnowledgeHub.upload_summary_dialog import UploadSummaryDialog


from PySide6.QtWidgets import (
    QWidget,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QHBoxLayout,
    QGridLayout,
    QGroupBox,
    QComboBox,
    QLineEdit,
    QFileDialog,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QProgressBar,
    QHeaderView,
    QMessageBox,
    QInputDialog,
    QScrollArea,
    QSizePolicy,
)

from UI.KnowledgeHub.upload_worker import UploadWorker


class UploadManualPage(QWidget):

    def __init__(self):

        super().__init__()

        self.selected_files = []

        self.thread = None

        self.worker = None

        self.upload_source = "-"

        self.build_ui()

    # ======================================================
    # Build UI
    # ======================================================

    def build_ui(self):

        # --------------------------------------------------
        # Outer layout for this page just holds the scroll area.
        # This is the key fix: without this, all the group boxes
        # below get squeezed into whatever height the stacked
        # widget has available, which causes overlap.
        # --------------------------------------------------

        outer = QVBoxLayout(self)

        outer.setContentsMargins(0, 0, 0, 0)

        outer.setSpacing(0)

        scroll = QScrollArea()

        scroll.setWidgetResizable(True)

        scroll.setFrameShape(QScrollArea.NoFrame)

        outer.addWidget(scroll)

        content = QWidget()

        scroll.setWidget(content)

        root = QVBoxLayout(content)

        root.setContentsMargins(20, 20, 20, 20)

        root.setSpacing(15)


        title = QLabel(
            "Enterprise Knowledge Upload Studio"
        )

        title.setObjectName(
            "SectionTitle"
        )

        root.addWidget(title)


        subtitle = QLabel(
            "Upload and manage PSW enterprise knowledge sources."
        )

        root.addWidget(subtitle)


        # ==================================================
        # Knowledge Information
        # ==================================================

        info_group = QGroupBox(
            "Knowledge Information"
        )

        info_layout = QGridLayout(
            info_group
        )

        info_layout.setContentsMargins(15, 20, 15, 15)

        info_layout.setHorizontalSpacing(12)

        info_layout.setVerticalSpacing(10)

        # Give the input columns room to grow, keep the
        # "+" button columns fixed and small.
        info_layout.setColumnStretch(1, 1)

        info_layout.setColumnStretch(4, 1)


        self.domain = QComboBox()
        
        self.add_domain_btn = QPushButton(
            "+"
        )

        self.add_domain_btn.setFixedWidth(36)


        self.module = QComboBox()

        self.module.setEditable(
            True
        )


        self.add_module_btn = QPushButton(
            "+"
        )

        self.add_module_btn.setFixedWidth(36)


        self.knowledge_name = QLineEdit()

        self.knowledge_name.setPlaceholderText(
            "Knowledge Name"
        )


        self.version = QLineEdit(
            "1.0"
        )


        self.document_type = QComboBox()

        self.document_type.addItems(
            [
                "SRS",
                "CRF",
                "Test Case",
                "SOP",
                "API Documentation",
                "Release Notes",
                "Technical Document",
                "Other"
            ]
        )


        info_layout.addWidget(
            QLabel("Domain *"),
            0,
            0
        )

        info_layout.addWidget(
            self.domain,
            0,
            1
        )

        info_layout.addWidget(
            self.add_domain_btn,
            0,
            2
        )


        info_layout.addWidget(
            QLabel("Module *"),
            0,
            3
        )

        info_layout.addWidget(
            self.module,
            0,
            4
        )

        info_layout.addWidget(
            self.add_module_btn,
            0,
            5
        )


        info_layout.addWidget(
            QLabel("Knowledge Name *"),
            1,
            0
        )

        info_layout.addWidget(
            self.knowledge_name,
            1,
            1,
            1,
            2
        )


        info_layout.addWidget(
            QLabel("Version"),
            1,
            3
        )

        info_layout.addWidget(
            self.version,
            1,
            4
        )


        info_layout.addWidget(
            QLabel("Document Type"),
            2,
            0
        )

        info_layout.addWidget(
            self.document_type,
            2,
            1
        )


        root.addWidget(
            info_group
        )


        # ==================================================
        # Source Selection
        # ==================================================

        source_group = QGroupBox(
            "Upload Sources"
        )

        source_layout = QVBoxLayout(
            source_group
        )

        source_layout.setContentsMargins(15, 20, 15, 15)

        source_layout.setSpacing(8)


        self.source_type = QComboBox()

        self.source_type.addItems(
            [
                "Files",
                "Folder",
                "Images",
                "URL",
                "API Collection",
                "SQL Script",
                "Database Metadata",
                "Git Repository",
                "Release Notes",
                "Test Cases",
                "SOP Documents"
            ]
        )


        source_layout.addWidget(
            self.source_type
        )


        self.source_info = QLabel(
            "Select source type and add knowledge."
        )

        source_layout.addWidget(
            self.source_info
        )


        root.addWidget(
            source_group
        )


        # ==================================================
        # File List
        # ==================================================

        docs_group = QGroupBox(
            "Selected Sources"
        )

        docs_layout = QVBoxLayout(
            docs_group
        )

        docs_layout.setContentsMargins(15, 20, 15, 15)

        docs_layout.setSpacing(10)


        self.table = QTableWidget(
            0,
            4
        )

        self.table.setMinimumHeight(180)

        self.table.setSizePolicy(
            QSizePolicy.Expanding,
            QSizePolicy.Fixed
        )


        self.table.setHorizontalHeaderLabels(
            [
                "Source",
                "Type",
                "Size",
                "Status"
            ]
        )


        self.table.horizontalHeader().setSectionResizeMode(
            QHeaderView.Stretch
        )


        docs_layout.addWidget(
            self.table
        )


        buttons = QHBoxLayout()

        buttons.setSpacing(10)


        self.add_source_btn = QPushButton(
            "Add Source"
        )


        self.remove_btn = QPushButton(
            "Remove Selected"
        )


        buttons.addWidget(
            self.add_source_btn
        )

        buttons.addWidget(
            self.remove_btn
        )

        buttons.addStretch()


        docs_layout.addLayout(
            buttons
        )


        root.addWidget(
            docs_group
        )

        # ==================================================
        # Progress Section
        # ==================================================

        progress_group = QGroupBox(
            "Upload Progress"
        )

        progress_layout = QVBoxLayout(
            progress_group
        )

        progress_layout.setContentsMargins(15, 20, 15, 15)

        progress_layout.setSpacing(10)


        self.progress = QProgressBar()

        self.progress.setValue(
            0
        )

        progress_layout.addWidget(
            self.progress
        )


        self.total_label = QLabel(
            "Total : 0"
        )

        self.uploaded_label = QLabel(
            "Uploaded : 0"
        )

        self.failed_label = QLabel(
            "Failed : 0"
        )


        stats = QHBoxLayout()

        stats.setSpacing(20)

        stats.addWidget(
            self.total_label
        )

        stats.addWidget(
            self.uploaded_label
        )

        stats.addWidget(
            self.failed_label
        )

        stats.addStretch()


        progress_layout.addLayout(
            stats
        )


        self.log = QTextEdit()

        self.log.setReadOnly(
            True
        )

        self.log.setMinimumHeight(120)

        self.log.setSizePolicy(
            QSizePolicy.Expanding,
            QSizePolicy.Fixed
        )

        progress_layout.addWidget(
            self.log
        )


        root.addWidget(
            progress_group
        )

        # ==================================================
        # Upload Button
        # ==================================================

        bottom = QHBoxLayout()

        bottom.addStretch()


        self.upload_btn = QPushButton(
            "Upload & Process"
        )

        self.upload_btn.setMinimumHeight(
            40
        )

        self.upload_btn.setMinimumWidth(
            220
        )


        bottom.addWidget(
            self.upload_btn
        )


        root.addLayout(
            bottom
        )

        # ==================================================
        # Events
        # ==================================================

        self.add_source_btn.clicked.connect(
            self.add_source
        )

        self.remove_btn.clicked.connect(
            self.remove_selected
        )

        self.upload_btn.clicked.connect(
            self.process_upload
        )

        self.add_domain_btn.clicked.connect(
            self.add_domain
        )

        self.add_module_btn.clicked.connect(
            self.add_module
        )

        self.metadata = MetadataManager()

        self.load_domains()

        self.domain.currentIndexChanged.connect(
            self.load_modules
        )


    # ======================================================
    # Custom Add
    # ======================================================

    def add_domain(self):

        value, ok = QInputDialog.getText(
            self,
            "Add Domain",
            "Domain Name"
        )

        if not ok:
            return

        value = value.strip()

        if not value:
            return

        try:
            self.metadata.create_domain(value)

            self.load_domains()

            self.domain.setCurrentText(value)

        except Exception as ex:
            QMessageBox.critical(
                self,
                "Error",
                str(ex)
            )


    def add_module(self):

        value, ok = QInputDialog.getText(
            self,
            "Add Module",
            "Module Name"
        )

        if not ok:
            return

        value = value.strip()

        if not value:
            return

        try:
            self.metadata.create_module(
                self.domain.currentText(),
                value
            )

            self.load_modules()

            self.module.setCurrentText(value)

        except Exception as ex:
            QMessageBox.critical(
                self,
                "Error",
                str(ex)
            )
    # ======================================================
    # Load Domains
    # ======================================================

    def load_domains(self):

        self.domain.blockSignals(True)

        self.domain.clear()

        domains = self.metadata.list_domains()

        for domain in domains:
            self.domain.addItem(domain)

        self.domain.blockSignals(False)

        self.load_modules()


    # ======================================================
    # Load Modules
    # ======================================================

    def load_modules(self):

        self.module.blockSignals(True)

        self.module.clear()

        domain = self.domain.currentText()

        if domain:

            modules = self.metadata.list_modules(domain)

            for module in modules:
                self.module.addItem(module)

        self.module.blockSignals(False)

    # ======================================================
    # Source Handling
    # ======================================================

    def add_source(self):

        source_type = self.source_type.currentText()


        if source_type in [
            "Files",
            "Images",
            "Test Cases",
            "SOP Documents",
            "Release Notes"
        ]:

            files, _ = QFileDialog.getOpenFileNames(
                self,
                "Select Files"
            )

            for file in files:

                self.add_file(
                    file
                )


        elif source_type == "Folder":

            folder = QFileDialog.getExistingDirectory(
                self,
                "Select Folder"
            )

            if folder:

                self.add_file(
                    folder
                )


        else:

            QMessageBox.information(
                self,
                "Connector",
                f"{source_type} connector will be enabled in next backend phase."
            )


    def add_file(self, path):

        if path in self.selected_files:

            return


        self.selected_files.append(
            path
        )


        row = self.table.rowCount()

        self.table.insertRow(
            row
        )


        self.table.setItem(
            row,
            0,
            QTableWidgetItem(
                os.path.basename(path)
            )
        )


        self.table.setItem(
            row,
            1,
            QTableWidgetItem(
                os.path.splitext(path)[1]
                if os.path.isfile(path)
                else "Folder"
            )
        )


        size = ""

        if os.path.isfile(path):

            size = (
                f"{round(os.path.getsize(path)/1024,2)} KB"
            )


        self.table.setItem(
            row,
            2,
            QTableWidgetItem(
                size
            )
        )


        self.table.setItem(
            row,
            3,
            QTableWidgetItem(
                "Pending"
            )
        )


        self.total_label.setText(
            f"Total : {len(self.selected_files)}"
        )


    # ======================================================
    # Remove
    # ======================================================

    def remove_selected(self):

        rows = sorted(
            {
                x.row()
                for x in self.table.selectedIndexes()
            },
            reverse=True
        )


        for row in rows:

            if row < len(self.selected_files):

                self.selected_files.pop(
                    row
                )

            self.table.removeRow(
                row
            )


        self.total_label.setText(
            f"Total : {len(self.selected_files)}"
        )


    # ======================================================
    # Validation
    # ======================================================

    def validate_input(self):

        if not self.domain.currentText():

            QMessageBox.warning(
                self,
                "Validation",
                "Domain required."
            )

            return False


        if not self.module.currentText():

            QMessageBox.warning(
                self,
                "Validation",
                "Module required."
            )

            return False


        if not self.knowledge_name.text().strip():

            QMessageBox.warning(
                self,
                "Validation",
                "Knowledge name required."
            )

            return False


        if not self.selected_files:

            QMessageBox.warning(
                self,
                "Validation",
                "Please add source files."
            )

            return False


        return True


    # ======================================================
    # Upload
    # ======================================================

    def process_upload(self):

        if not self.validate_input():

            return


        self.upload_btn.setEnabled(
            False
        )

        self.thread = QThread()

        self.upload_source = self.source_type.currentText()

        self.worker = UploadWorker(

            self.domain.currentText(),

            self.module.currentText(),

            self.knowledge_name.text(),

            self.version.text(),

            self.document_type.currentText(),

            self.selected_files

        )


        self.worker.moveToThread(
            self.thread
        )


        self.thread.started.connect(
            self.worker.run
        )


        self.worker.progress.connect(
            self.update_progress
        )


        self.worker.finished.connect(
            self.upload_finished
        )


        self.worker.error.connect(
            self.upload_error
        )


        self.worker.finished.connect(
            self.thread.quit
        )


        self.worker.error.connect(
            self.thread.quit
        )


        self.thread.finished.connect(
            self.cleanup_thread
        )


        self.thread.start()

    # ======================================================
    # Progress
    # ======================================================

    def update_progress(self, message):

        self.log.append(
            message
        )

        value = self.progress.value()

        if value < 90:

            self.progress.setValue(
                value + 10
            )



    # ======================================================
    # Complete
    # ======================================================

    def upload_finished(self, result):

        self.progress.setValue(
            100
        )

        self.upload_btn.setEnabled(
            True
        )


        self.uploaded_label.setText(
            "Uploaded : Completed"
        )

        upload_result = result.get("primary_result")

        if not upload_result:

            results = result.get("results", [])

            if results:

                upload_result = results[0]

            else:

                upload_result = {}

        self.log.append("=" * 60)
        self.log.append("Knowledge Upload Completed")
        self.log.append(f"Knowledge : {result.get('knowledge_name', '-')}")
        self.log.append(f"Domain    : {result.get('domain', '-')}")
        self.log.append(f"Module    : {result.get('module', '-')}")
        self.log.append(f"Category  : {upload_result.get('category', '-')}")
        self.log.append(f"Version   : {result.get('version', '-')}")
        self.log.append(f"Chunks    : {upload_result.get('total_chunks', 0)}")
        self.log.append(f"Vectors   : {upload_result.get('vectors_saved', 0)}")
        self.log.append("=" * 60)

        summary = upload_result.get("summary", "").strip()

        summary = summary.strip()

        if not summary:
            summary = "No AI summary available."

        elif len(summary) > 1500:
            summary = summary[:1500].rstrip() + "..."

        repository = (
            f"{result.get('domain', '-')}/<br>"
            f"└── {result.get('module', '-')}/<br>"
            f"&nbsp;&nbsp;&nbsp;&nbsp;└── {result.get('knowledge_name', '-')}/<br>"
            f"&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;└── {result.get('version', '-')}/"
        )

        message = f"""
        <b>Knowledge uploaded successfully.</b><br><br>

        ------------------------------------------------<br>

        Domain&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;: {result.get('domain', '-')}<br>
        Module&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;: {result.get('module', '-')}<br>
        Knowledge Name : {result.get('knowledge_name', '-')}<br>
        Version&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;: {result.get('version', '-')}<br>
        Document Type&nbsp;&nbsp;: {upload_result.get('document_type', '-')}<br>
        Upload Source&nbsp;&nbsp;: {self.upload_source}<br><br>

        <b>Repository</b><br>

        ------------------------------------------------<br>

        {repository}<br><br>

        <b>AI Analysis Summary</b><br>

        ------------------------------------------------<br>

        {summary}
        """

        dialog = UploadSummaryDialog(message, self)
        dialog.exec()

    # ======================================================
    # Error
    # ======================================================

    def upload_error(self, message):

        self.upload_btn.setEnabled(
            True
        )


        self.failed_label.setText(
            "Failed : 1"
        )


        self.log.append(
            message
        )


        QMessageBox.critical(
            self,
            "Upload Failed",
            message
        )


    # ======================================================
    # Cleanup
    # ======================================================

    def cleanup_thread(self):

        if self.thread:

            self.thread.deleteLater()


        self.thread = None

        self.worker = None