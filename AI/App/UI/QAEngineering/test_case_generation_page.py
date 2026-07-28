# Create: App/UI/QAEngineering/test_case_generation_page.py

"""
==========================================================
QA AI Studio

QA Engineering

Generate Test Cases

Version : 1.0

Flow:
    Select Domain / Module / Knowledge Name
        |
        v
    Choose Test Types (checkboxes)
        |
        v
    Choose Output Format(s) — Excel / Word / PDF
        |
        v
    Generate Test Cases  (background thread)
        |
        v
    Review result + open output folder

Future buttons (disabled placeholders, per spec):
    • Upload to Test Manager (https://testmanager.psw.gov.pk/)
    • Commit to Git
==========================================================
"""

import os
import platform
import subprocess

from pathlib import Path

from PySide6.QtCore import Qt, QThread

from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QGridLayout,
    QLabel,
    QPushButton,
    QComboBox,
    QCheckBox,
    QGroupBox,
    QTextEdit,
    QMessageBox,
    QScrollArea,
    QSizePolicy,
)

from Core.metadata_manager import MetadataManager

from UI.QAEngineering.test_case_generation_worker import TestCaseGenerationWorker


TEST_TYPES = [
    "Functional",
    "Positive",
    "Negative",
    "Boundary",
    "Validation",
    "Integration",
    "API",
    "UI",
    "Database",
    "Security",
    "Performance",
    "Compatibility",
    "Regression",
    "Impact Analysis",
]

OUTPUT_FORMATS = [
    "Excel",
    "Word",
    "PDF",
]

TEST_MANAGER_URL = "https://testmanager.psw.gov.pk/"


class TestCaseGenerationPage(QWidget):

    def __init__(self):

        super().__init__()

        self.metadata = MetadataManager()

        self.generation_thread = None

        self.generation_worker = None

        self.last_result = None

        self.build_ui()

        self.load_domains()


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


        title = QLabel("Generate Test Cases")

        title.setObjectName("SectionTitle")

        layout.addWidget(title)


        subtitle = QLabel(
            "Select a Domain, Module and Knowledge Name to generate "
            "AI-powered test cases from your uploaded knowledge base."
        )

        layout.addWidget(subtitle)


        # ==================================================
        # Selection
        # ==================================================

        select_group = QGroupBox(
            "Select"
        )

        select_layout = QGridLayout(
            select_group
        )

        select_layout.setContentsMargins(15, 20, 15, 15)

        select_layout.setHorizontalSpacing(12)

        select_layout.setVerticalSpacing(10)

        select_layout.setColumnStretch(1, 1)

        select_layout.setColumnStretch(3, 1)


        self.domain = QComboBox()

        self.module = QComboBox()

        self.knowledge_name = QComboBox()

        self.refresh_btn = QPushButton("Refresh")


        select_layout.addWidget(QLabel("Domain"), 0, 0)

        select_layout.addWidget(self.domain, 0, 1)

        select_layout.addWidget(QLabel("Module"), 0, 2)

        select_layout.addWidget(self.module, 0, 3)

        select_layout.addWidget(QLabel("Knowledge Name"), 1, 0)

        select_layout.addWidget(self.knowledge_name, 1, 1)

        select_layout.addWidget(self.refresh_btn, 1, 3)


        layout.addWidget(select_group)


        # ==================================================
        # Test Types
        # ==================================================

        types_group = QGroupBox(
            "Choose Test Types"
        )

        types_outer = QVBoxLayout(
            types_group
        )

        types_outer.setContentsMargins(15, 20, 15, 15)

        types_grid = QGridLayout()

        types_grid.setHorizontalSpacing(20)

        types_grid.setVerticalSpacing(8)


        self.test_type_checkboxes = {}

        columns = 4

        for index, test_type in enumerate(TEST_TYPES):

            checkbox = QCheckBox(test_type)

            checkbox.setChecked(True)

            self.test_type_checkboxes[test_type] = checkbox

            row = index // columns

            col = index % columns

            types_grid.addWidget(checkbox, row, col)


        types_outer.addLayout(types_grid)


        type_buttons = QHBoxLayout()

        self.select_all_types_btn = QPushButton("Select All")

        self.clear_all_types_btn = QPushButton("Clear All")

        type_buttons.addWidget(self.select_all_types_btn)

        type_buttons.addWidget(self.clear_all_types_btn)

        type_buttons.addStretch()

        types_outer.addLayout(type_buttons)


        layout.addWidget(types_group)


        # ==================================================
        # Output Format
        # ==================================================

        output_group = QGroupBox(
            "Output"
        )

        output_layout = QHBoxLayout(
            output_group
        )

        output_layout.setContentsMargins(15, 20, 15, 15)


        self.output_checkboxes = {}

        for output_format in OUTPUT_FORMATS:

            checkbox = QCheckBox(output_format)

            checkbox.setChecked(output_format == "Excel")

            self.output_checkboxes[output_format] = checkbox

            output_layout.addWidget(checkbox)

        output_layout.addStretch()


        layout.addWidget(output_group)


        # ==================================================
        # Actions
        # ==================================================

        actions = QHBoxLayout()

        actions.setSpacing(10)


        self.generate_btn = QPushButton("Generate Test Cases")

        self.generate_btn.setMinimumHeight(40)

        self.generate_btn.setMinimumWidth(220)


        self.upload_test_manager_btn = QPushButton(
            "Upload to Test Manager"
        )

        self.commit_git_btn = QPushButton(
            "Commit to Git"
        )


        actions.addWidget(self.generate_btn)

        actions.addStretch()

        actions.addWidget(self.upload_test_manager_btn)

        actions.addWidget(self.commit_git_btn)


        layout.addLayout(actions)


        # ==================================================
        # Result / Log
        # ==================================================

        result_group = QGroupBox(
            "Generation Log"
        )

        result_layout = QVBoxLayout(
            result_group
        )

        result_layout.setContentsMargins(15, 20, 15, 15)


        self.log = QTextEdit()

        self.log.setReadOnly(True)

        self.log.setMinimumHeight(160)

        self.log.setSizePolicy(
            QSizePolicy.Expanding,
            QSizePolicy.Fixed
        )

        result_layout.addWidget(self.log)


        self.open_folder_btn = QPushButton(
            "Open Output Folder"
        )

        self.open_folder_btn.setEnabled(False)

        result_layout.addWidget(
            self.open_folder_btn,
            alignment=Qt.AlignLeft
        )


        layout.addWidget(result_group)


        # ==================================================
        # Events
        # ==================================================

        self.domain.currentTextChanged.connect(
            self.on_domain_changed
        )

        self.module.currentTextChanged.connect(
            self.on_module_changed
        )

        self.refresh_btn.clicked.connect(
            self.load_domains
        )

        self.select_all_types_btn.clicked.connect(
            lambda: self.set_all_test_types(True)
        )

        self.clear_all_types_btn.clicked.connect(
            lambda: self.set_all_test_types(False)
        )

        self.generate_btn.clicked.connect(
            self.generate_test_cases
        )

        self.upload_test_manager_btn.clicked.connect(
            self.upload_to_test_manager
        )

        self.commit_git_btn.clicked.connect(
            self.commit_to_git
        )

        self.open_folder_btn.clicked.connect(
            self.open_output_folder
        )


    # ======================================================
    # Domain / Module / Knowledge Name cascade
    # ======================================================

    def load_domains(self):

        self.domain.blockSignals(True)

        self.domain.clear()

        domains = self.metadata.list_domains()

        if not domains:

            self.log.append(
                "No domains found yet. Upload some knowledge first "
                "in Knowledge Hub."
            )

        else:

            self.domain.addItems(domains)

        self.domain.blockSignals(False)

        self.on_domain_changed(
            self.domain.currentText()
        )


    def on_domain_changed(self, domain_name):

        self.module.blockSignals(True)

        self.module.clear()

        if domain_name:

            modules = self.metadata.list_modules(domain_name)

            self.module.addItems(modules)

        self.module.blockSignals(False)

        self.on_module_changed(
            self.module.currentText()
        )


    def on_module_changed(self, module_name):

        self.knowledge_name.clear()

        domain_name = self.domain.currentText()

        if domain_name and module_name:

            names = self.metadata.get_knowledge_names(
                domain_name,
                module_name
            )

            self.knowledge_name.addItems(names)


    # ======================================================
    # Test Type Helpers
    # ======================================================

    def set_all_test_types(self, checked):

        for checkbox in self.test_type_checkboxes.values():

            checkbox.setChecked(checked)


    def selected_test_types(self):

        return [
            name
            for name, checkbox in self.test_type_checkboxes.items()
            if checkbox.isChecked()
        ]


    def selected_output_formats(self):

        return [
            name
            for name, checkbox in self.output_checkboxes.items()
            if checkbox.isChecked()
        ]


    # ======================================================
    # Generate (background thread)
    # ======================================================

    def generate_test_cases(self):

        if self.generation_thread is not None:

            # Already running — ignore double-click.
            return

        domain = self.domain.currentText().strip()

        module = self.module.currentText().strip()

        knowledge_name = self.knowledge_name.currentText().strip()


        if not domain:

            QMessageBox.warning(
                self, "Validation", "Please select a Domain."
            )

            return

        if not module:

            QMessageBox.warning(
                self, "Validation", "Please select a Module."
            )

            return

        if not knowledge_name:

            QMessageBox.warning(
                self,
                "Validation",
                "Please select a Knowledge Name.\n\n"
                "If none are listed, upload knowledge for this "
                "Domain/Module first in Knowledge Hub."
            )

            return

        test_types = self.selected_test_types()

        if not test_types:

            QMessageBox.warning(
                self, "Validation", "Select at least one test type."
            )

            return

        output_formats = self.selected_output_formats()

        if not output_formats:

            QMessageBox.warning(
                self, "Validation", "Select at least one output format."
            )

            return

        requirement = (
            f"Generate comprehensive QA test cases for "
            f"'{knowledge_name}' under the {module} module of the "
            f"{domain} domain."
        )

        self.generate_btn.setEnabled(False)

        self.open_folder_btn.setEnabled(False)

        self.log.append(
            f"Generating test cases for {domain} / {module} / "
            f"{knowledge_name}..."
        )

        self.generation_thread = QThread()

        self.generation_worker = TestCaseGenerationWorker(
            requirement=requirement,
            domain=domain,
            module=module,
            knowledge_name=knowledge_name,
            version=None,
            test_types=test_types,
            output_formats=output_formats,
        )

        self.generation_worker.moveToThread(
            self.generation_thread
        )

        self.generation_thread.started.connect(
            self.generation_worker.run
        )

        self.generation_worker.progress.connect(
            self.log.append
        )

        self.generation_worker.finished.connect(
            self.generation_finished
        )

        self.generation_worker.error.connect(
            self.generation_error
        )

        self.generation_worker.finished.connect(
            self.generation_thread.quit
        )

        self.generation_worker.error.connect(
            self.generation_thread.quit
        )

        self.generation_thread.finished.connect(
            self.cleanup_generation_thread
        )

        self.generation_thread.start()


    def generation_finished(self, result):

        self.generate_btn.setEnabled(True)

        self.last_result = result

        case_count = result.get("case_count", 0)

        self.log.append(
            f"Done. {case_count} test case(s) generated."
        )

        for key, label in (
            ("excel_file", "Excel"),
            ("word_file", "Word"),
            ("pdf_file", "PDF"),
        ):

            if result.get(key):

                self.log.append(
                    f"{label}: {result[key]}"
                )

        self.open_folder_btn.setEnabled(
            bool(
                result.get("excel_file")
                or result.get("word_file")
                or result.get("pdf_file")
            )
        )

        QMessageBox.information(
            self,
            "QA AI Studio",
            f"Test case generation completed.\n\n"
            f"{case_count} test case(s) generated."
        )


    def generation_error(self, message):

        self.generate_btn.setEnabled(True)

        self.log.append(
            f"Generation failed: {message}"
        )

        QMessageBox.critical(
            self,
            "Test Case Generation Failed",
            message
        )


    def cleanup_generation_thread(self):

        if self.generation_thread:

            self.generation_thread.deleteLater()

        self.generation_thread = None

        self.generation_worker = None


    # ======================================================
    # Output Folder
    # ======================================================

    def open_output_folder(self):

        if not self.last_result:

            return

        for key in ("excel_file", "word_file", "pdf_file"):

            path = self.last_result.get(key)

            if path:

                folder = str(Path(path).parent)

                self._open_folder(folder)

                return


    def _open_folder(self, folder):

        try:

            system = platform.system()

            if system == "Windows":

                os.startfile(folder)

            elif system == "Darwin":

                subprocess.Popen(["open", folder])

            else:

                subprocess.Popen(["xdg-open", folder])

        except Exception as ex:

            QMessageBox.warning(
                self,
                "QA AI Studio",
                f"Could not open folder automatically.\n\n{folder}\n\n{ex}"
            )


    # ======================================================
    # Future Buttons (placeholders per spec)
    # ======================================================

    def upload_to_test_manager(self):

        QMessageBox.information(
            self,
            "Coming Soon",
            "Upload to Test Manager integration will connect to:\n\n"
            f"{TEST_MANAGER_URL}\n\n"
            "This will be enabled in a future backend phase."
        )


    def commit_to_git(self):

        QMessageBox.information(
            self,
            "Coming Soon",
            "Commit to Git integration will be enabled in a future "
            "backend phase (Git Automation module)."
        )