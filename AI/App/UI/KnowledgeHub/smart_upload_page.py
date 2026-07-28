"""
==========================================================
QA AI Studio

Knowledge Hub

AI Smart Upload

Version : 2.0

Production AI Smart Upload Workflow

Flow:
    Select Files
        |
        v
    AI Analyze
        |
        v
    Review Classification
        |
        v
    Upload Knowledge
        |
        v
    Metadata + VectorStore
==========================================================
"""

from pathlib import Path

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
    QTableWidget,
    QTableWidgetItem,
    QHeaderView
)

from Core.knowledge_service import KnowledgeService


class SmartUploadPage(QWidget):

    def __init__(self):

        super().__init__()
        self.files = []
        self.analysis_results = {}
        self.service = KnowledgeService()
        self.selected_file = ""
        self.build_ui()
        self.domain.setEnabled(False)
        self.module.setEnabled(False)
        self.knowledge_name.setEnabled(False)
        self.version.setEnabled(False)
        self.document_type.setEnabled(False)
        self.upload_btn.setEnabled(False)


    # ======================================================
    # UI
    # ======================================================

    def build_ui(self):

        layout = QVBoxLayout(self)

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
        # Knowledge Information
        # ==================================================

        info_group = QGroupBox(
            "AI Suggestions Repository"
        )


        info_layout = QGridLayout(
            info_group
        )


        self.domain = QComboBox()

        self.domain.addItem(
            "PSW Domain"
        )


        self.module = QComboBox()

        self.module.setEditable(
            True
        )


        self.knowledge_name = QLineEdit()


        self.version = QLineEdit(
            "1.0"
        )

        self.document_type = QComboBox()

        self.document_type.addItems([
            "General",
            "SRS",
            "CRF",
            "Test Case",
            "API",
            "SOP"
        ])


        self.knowledge_name.setPlaceholderText(
            "Knowledge Name"
        )


        info_layout.addWidget(
            QLabel("Domain"),
            0,
            0
        )

        info_layout.addWidget(
            self.domain,
            0,
            1
        )


        info_layout.addWidget(
            QLabel("Module"),
            0,
            2
        )

        info_layout.addWidget(
            self.module,
            0,
            3
        )


        info_layout.addWidget(
            QLabel("Knowledge Name"),
            1,
            0
        )

        info_layout.addWidget(
            self.knowledge_name,
            1,
            1
        )


        info_layout.addWidget(
            QLabel("Version"),
            1,
            2
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

        info_layout.addWidget(
            self.version,
            1,
            3
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


        buttons = QHBoxLayout()

        self.file_btn = QPushButton(
            "Select Files"
        )
        self.folder_btn = QPushButton(
            "Select Folder"
        )
        self.url_btn = QPushButton(
            "Add URL"
        )
        self.api_btn = QPushButton(
            "Add API"
        )
        self.sql_btn = QPushButton(
            "Add SQL"
        )
        self.image_btn = QPushButton(
            "Add Images"
        )
        self.analyze_btn = QPushButton(
            "Analyze With AI"
        )
        self.upload_btn = QPushButton(
            "Confirm Upload"
        )


        buttons.addWidget(
            self.file_btn
        )

        buttons.addWidget(
            self.folder_btn
        )

        buttons.addWidget(
        self.url_btn
        )

        buttons.addWidget(
            self.api_btn
        )

        buttons.addWidget(
            self.sql_btn
        )

        buttons.addWidget(
            self.image_btn
        )  


        buttons.addStretch()


        buttons.addWidget(
            self.analyze_btn
        )


        buttons.addWidget(
            self.upload_btn
        )


        file_layout.addLayout(
            buttons
        )


        self.file_list = QListWidget()


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
            "AI Suggestions Summary"
        )

        result_layout = QVBoxLayout(result_group)

        self.summary = QTextEdit()
        self.summary.setReadOnly(True)
        self.summary.setMinimumHeight(300)

        result_layout.addWidget(self.summary)

        self.log = QTextEdit()
        self.log.setReadOnly(True)

        result_layout.addWidget(self.log)

        layout.addWidget(result_group)


        # ==================================================
        # Events
        # ==================================================

        self.file_btn.clicked.connect(
            self.select_files
        )

        self.folder_btn.clicked.connect(
            self.select_folder
        )

        self.analyze_btn.clicked.connect(
            self.analyze_files
        )

        self.upload_btn.clicked.connect(
            self.confirm_upload
        )

        self.upload_btn.setEnabled(
            False
        )

        self.confirm_btn = self.upload_btn


    # ======================================================
    # File Selection
    # ======================================================

    def select_files(self):

        files, _ = QFileDialog.getOpenFileNames(
            self,
            "Select Knowledge Files"
        )

        if files:

            self.files = files

            self.selected_file = files[0]

            self.refresh_files()

            # Reset previous AI analysis
            self.analysis_results = {}

            self.summary.clear()
            self.log.clear()

            self.domain.setEnabled(False)
            self.module.setEnabled(False)
            self.knowledge_name.setEnabled(False)
            self.version.setEnabled(False)
            self.document_type.setEnabled(False)

            self.upload_btn.setEnabled(False)

            self.domain.clear()
            self.module.clear()
            self.knowledge_name.clear()
            self.version.setText("1.0")
            self.document_type.setCurrentIndex(0)

    def select_folder(self):

        folder = QFileDialog.getExistingDirectory(
            self,
            "Select Folder"
        )


        if not folder:

            return


        for file in Path(folder).rglob("*.*"):

            self.files.append(
                str(file)
            )


        self.refresh_files()

        if self.files:
            self.selected_file = self.files[0]

        # Reset previous AI analysis
        self.analysis_results = {}

        self.summary.clear()
        self.log.clear()

        self.domain.setEnabled(False)
        self.module.setEnabled(False)
        self.knowledge_name.setEnabled(False)
        self.version.setEnabled(False)
        self.document_type.setEnabled(False)

        self.upload_btn.setEnabled(False)

        self.domain.clear()
        self.module.clear()
        self.knowledge_name.clear()
        self.version.setText("1.0")
        self.document_type.setCurrentIndex(0)



    def refresh_files(self):

        self.file_list.clear()

        self.file_list.addItems(
            self.files
        )



    # ======================================================
    # AI Analyze
    # ======================================================

    def analyze_files(self):

        if not self.files:

            QMessageBox.warning(
                self,
                "QA AI Studio",
                "Select files first."
            )

            return

        self.analysis_results = {}

        self.summary.clear()
        self.log.clear()

        for file in self.files:

            try:

                result = self.service.smart_upload(
                    source_file=file
                )

                self.analysis_results[file] = result


                self.domain.setEnabled(True)
                self.module.setEnabled(True)
                self.knowledge_name.setEnabled(True)
                self.version.setEnabled(True)
                self.document_type.setEnabled(True)

                self.domain.clear()

                domain = result.get("domain", "Unknown")

                self.domain.addItem(domain)
                self.domain.setCurrentText(domain)

                self.module.clear()

                module = result.get("module", "")

                if not module:
                    module = "Unknown"

                self.module.addItem(module)

                if not self.knowledge_name.text().strip():
                    self.knowledge_name.setText(
                        Path(file).stem
                    )

                self.version.setText(
                    result.get("version", "1.0")
                )

                self.document_type.setCurrentText(
                    result.get("document_type", "General")
                )

                summary = result.get("summary", "").strip()

                if not summary:
                    summary = "No AI summary available."

                self.summary.setPlainText(summary)


                self.log.append(
                    f"Analyzed: {Path(file).name}"
                )


            except Exception as ex:

                self.log.append(
                    str(ex)
                )


        if self.analysis_results:

            self.upload_btn.setEnabled(True)

        else:

            self.upload_btn.setEnabled(False)
    
    # ======================================================
    # Upload
    # ======================================================

    def upload_files(self):

        if not self.files:

            return


        if (
            self.knowledge_name.isEnabled()
            and not self.knowledge_name.text().strip()
        ):

            QMessageBox.warning(
                self,
                "Validation",
                "Knowledge Name required."
            )

            return


        try:

            result = self.service.upload(

                domain=self.domain.currentText(),

                module=self.module.currentText(),

                knowledge_name=self.knowledge_name.text(),

                version=self.version.text(),

                files=self.files

            )


            QMessageBox.information(

                self,

                "QA AI Studio",

                f"Upload Completed\n\n"
                f"Files processed: {len(result.get('results', []))}"

            )


            self.log.append(
                "Knowledge uploaded successfully."
            )


        except Exception as ex:


            QMessageBox.critical(

                self,

                "Upload Failed",

                str(ex)

            )

    def confirm_upload(self):

        if not self.selected_file:
            QMessageBox.warning(
                self,
                "QA AI Studio",
                "Please analyze a document first."
            )
            return

        result = self.service.upload(
            domain=self.domain.currentText(),
            module=self.module.currentText(),
            knowledge_name=self.knowledge_name.text(),
            version=self.version.text(),
            document_type=self.document_type.currentText(),
            files=[self.selected_file]
        )

        if result.get("success"):

            QMessageBox.information(
                self,
                "QA AI Studio",
                "Knowledge saved successfully."
            )

        else:

            QMessageBox.critical(
                self,
                "QA AI Studio",
                "Knowledge upload failed."
            )