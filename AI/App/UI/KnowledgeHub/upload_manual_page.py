# Replace/Create: App/UI/KnowledgeHub/upload_manual_page.py
# Part 1/2

"""
==========================================================
QA AI Studio
Knowledge Hub
Enterprise Knowledge Upload Studio

Version : 3.0

Features:
    • Enterprise knowledge metadata capture
    • Multiple source selection
    • File / Folder upload
    • Image/document support
    • Future source connector support
    • Existing UploadWorker compatibility
==========================================================
"""

import os

from PySide6.QtCore import QThread

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
    QInputDialog
)

from UI.KnowledgeHub.upload_worker import UploadWorker


class UploadManualPage(QWidget):

    def __init__(self):

        super().__init__()

        self.selected_files = []

        self.thread = None

        self.worker = None

        self.build_ui()


    # ======================================================
    # Build UI
    # ======================================================

    def build_ui(self):

        root = QVBoxLayout(self)

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


        self.domain = QComboBox()

        self.domain.addItems(
            [
                "PSW Core",
                "WeBOC",
                "PCS",
                "ACS",
                "Other"
            ]
        )


        self.add_domain_btn = QPushButton(
            "+"
        )


        self.module = QComboBox()

        self.module.setEditable(
            True
        )


        self.add_module_btn = QPushButton(
            "+"
        )


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


        self.table = QTableWidget(
            0,
            4
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
    # Part 2/2

        # ==================================================
        # Progress Section
        # ==================================================

        progress_group = QGroupBox(
            "Upload Progress"
        )

        progress_layout = QVBoxLayout(
            progress_group
        )


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


    # ======================================================
    # Custom Add
    # ======================================================

    def add_domain(self):

        value, ok = QInputDialog.getText(
            self,
            "Add Domain",
            "Domain Name"
        )

        if ok and value.strip():

            self.domain.addItem(
                value.strip()
            )


    def add_module(self):

        value, ok = QInputDialog.getText(
            self,
            "Add Module",
            "Module Name"
        )

        if ok and value.strip():

            self.module.addItem(
                value.strip()
            )

            self.module.setCurrentText(
                value.strip()
            )


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


        self.worker = UploadWorker(

            self.domain.currentText(),

            self.module.currentText(),

            self.knowledge_name.text(),

            self.version.text(),

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


        self.log.append(
            "Knowledge upload completed."
        )


        QMessageBox.information(
            self,
            "QA AI Studio",
            str(result)
        )



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