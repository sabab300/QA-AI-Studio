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

        self.build_ui()


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
            "Knowledge Information"
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
            "Source Files"
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
            "AI Classification Result"
        )


        result_layout = QVBoxLayout(
            result_group
        )


        self.result_table = QTableWidget(
            0,
            2
        )


        self.result_table.setHorizontalHeaderLabels(
            [
                "Field",
                "Value"
            ]
        )


        self.result_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.Stretch
        )


        result_layout.addWidget(
            self.result_table
        )


        self.log = QTextEdit()

        self.log.setReadOnly(
            True
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


        self.analyze_btn.clicked.connect(
            self.analyze_files
        )


        self.upload_btn.clicked.connect(
            self.upload_files
        )


        self.upload_btn.setEnabled(
            False
        )


    # ======================================================
    # File Selection
    # ======================================================

    def select_files(self):

        files, _ = QFileDialog.getOpenFileNames(
            self,
            "Select Knowledge Files"
        )


        if files:

            self.files.extend(files)

            self.refresh_files()



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


        self.result_table.setRowCount(
            0
        )


        self.analysis_results = {}


        for file in self.files:

            try:

                result = self.service.smart_upload(
                    file
                )


                self.analysis_results[file] = result


                self.show_analysis(
                    result
                )


                self.log.append(
                    f"Analyzed: {Path(file).name}"
                )


            except Exception as ex:

                self.log.append(
                    str(ex)
                )


        self.upload_btn.setEnabled(
            True
        )



    def show_analysis(self, data):

        row = self.result_table.rowCount()

        self.result_table.insertRow(
            row
        )


        for key, value in data.items():

            row = self.result_table.rowCount()

            self.result_table.insertRow(
                row
            )


            self.result_table.setItem(
                row,
                0,
                QTableWidgetItem(
                    str(key)
                )
            )


            self.result_table.setItem(
                row,
                1,
                QTableWidgetItem(
                    str(value)
                )
            )



    # ======================================================
    # Upload
    # ======================================================

    def upload_files(self):

        if not self.files:

            return


        if not self.knowledge_name.text().strip():

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