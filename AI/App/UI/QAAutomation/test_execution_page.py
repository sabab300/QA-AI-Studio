# Create: App/UI/QAAutomation/test_execution_page.py

"""
==========================================================
QA AI Studio

QA Automation

Test Execution Automation

Version : 1.0 (Phase 1)

Flow:
    Select Domain / Module / Knowledge Name -> Load Test Cases
        |
        v
    Table: TC001, TC002, ... with per-row Status
    (Manual/Automated) and Automation Type
    (None/Playwright/Selenium/API/SQL)
        |
        v
    Add Automation / Update Automation
        -> generates a script per selected row (background thread)
    Execute Selected / Execute All
        -> Manual rows: opens a Record Result dialog (Pass/Fail/Blocked)
        -> Automated rows: opens the generated script for manual
           review/run (no auto-exec of AI-generated code — see notes
           in Core/test_execution_manager.py)

Not in this phase (tracked separately):
    ClickUp Automation, Test Manager Automation, Git Automation,
    real Playwright/Selenium/API/SQL execution runners.
==========================================================
"""

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
    QTableWidget,
    QTableWidgetItem,
    QHeaderView,
    QDialog,
    QDialogButtonBox,
    QPlainTextEdit,
    QLineEdit,
)

from Core.metadata_manager import MetadataManager
from Core.test_environment_config import TestEnvironmentConfig

from Core.test_execution_manager import TestExecutionManager

from UI.QAAutomation.test_execution_worker import (
    AutomationGenerationWorker,
    AutomationSuggestionWorker,
    PlaywrightExecutionWorker,
    ManualRecordingWorker,
)


AUTOMATION_TYPES = [
    "None",
    "Playwright",
    "Selenium",
    "API",
    "SQL",
]

RESULT_OPTIONS = [
    "Pass",
    "Fail",
    "Blocked",
]

TC_ID_ROLE = Qt.UserRole


# ==========================================================
# Small dialog: record a manual result
# ==========================================================

class RecordResultDialog(QDialog):

    def __init__(self, tc_number, test_case_text, parent=None):

        super().__init__(parent)

        self.setWindowTitle(f"Record Result — {tc_number}")

        layout = QVBoxLayout(self)

        layout.addWidget(
            QLabel(test_case_text[:300])
        )

        self.result_combo = QComboBox()

        self.result_combo.addItems(RESULT_OPTIONS)

        layout.addWidget(
            QLabel("Result")
        )

        layout.addWidget(self.result_combo)

        buttons = QDialogButtonBox(
            QDialogButtonBox.Save | QDialogButtonBox.Cancel
        )

        buttons.accepted.connect(self.accept)

        buttons.rejected.connect(self.reject)

        layout.addWidget(buttons)

    def selected_result(self):

        return self.result_combo.currentText()


# ==========================================================
# Small dialog: AI-suggested automation type, editable before
# generating the actual script
# ==========================================================

class AutomationSuggestionDialog(QDialog):

    def __init__(self, tc_number, test_case_text, suggestion, parent=None):

        super().__init__(parent)

        self.setWindowTitle(
            f"Automate — {tc_number}"
        )

        self.resize(480, 320)

        layout = QVBoxLayout(self)

        case_label = QLabel(test_case_text[:300])

        case_label.setWordWrap(True)

        layout.addWidget(case_label)


        layout.addWidget(QLabel("AI Suggestion"))

        suggestion_label = QLabel(
            f"{suggestion['suggested_type']} — "
            f"{suggestion['reason']}"
        )

        suggestion_label.setWordWrap(True)

        suggestion_label.setStyleSheet(
            "color:#005B96; font-weight:bold;"
        )

        layout.addWidget(suggestion_label)


        layout.addWidget(QLabel("Automation Type (edit if needed)"))

        self.type_combo = QComboBox()

        self.type_combo.addItems(
            ["Playwright", "Selenium", "API", "SQL"]
        )

        self.type_combo.setCurrentText(
            suggestion["suggested_type"]
        )

        layout.addWidget(self.type_combo)


        buttons = QDialogButtonBox(
            QDialogButtonBox.Cancel
        )

        self.generate_btn = buttons.addButton(
            "Generate Automation", QDialogButtonBox.AcceptRole
        )

        buttons.accepted.connect(self.accept)

        buttons.rejected.connect(self.reject)

        layout.addWidget(buttons)

    def selected_type(self):

        return self.type_combo.currentText()


# ==========================================================
# Small dialog: real test environment values, used in generation
# so the AI stops guessing URLs/logins
# ==========================================================

class EnvironmentSettingsDialog(QDialog):

    def __init__(self, config_manager, parent=None):

        super().__init__(parent)

        self.config_manager = config_manager

        self.setWindowTitle("Test Environment Settings")

        self.resize(450, 260)

        layout = QVBoxLayout(self)

        note = QLabel(
            "Set your REAL test/UAT environment details here. New "
            "Playwright scripts will use these instead of the AI "
            "guessing a URL or login. Use a TEST account, not a "
            "production one — these values get written directly "
            "into generated scripts."
        )

        note.setWordWrap(True)

        layout.addWidget(note)

        grid = QGridLayout()

        grid.setColumnStretch(1, 1)


        self.base_url = QLineEdit()

        self.base_url.setPlaceholderText(
            "https://uat.psw.gov.pk/..."
        )

        self.username = QLineEdit()

        self.password = QLineEdit()

        self.password.setEchoMode(QLineEdit.Password)

        self.notes = QLineEdit()

        self.notes.setPlaceholderText(
            "optional, e.g. 'use EFS license holder test account'"
        )


        grid.addWidget(QLabel("Base URL"), 0, 0)

        grid.addWidget(self.base_url, 0, 1)

        grid.addWidget(QLabel("Username"), 1, 0)

        grid.addWidget(self.username, 1, 1)

        grid.addWidget(QLabel("Password"), 2, 0)

        grid.addWidget(self.password, 2, 1)

        grid.addWidget(QLabel("Notes"), 3, 0)

        grid.addWidget(self.notes, 3, 1)


        layout.addLayout(grid)


        data = self.config_manager.load()

        self.base_url.setText(data.get("base_url", ""))

        self.username.setText(data.get("username", ""))

        self.password.setText(data.get("password", ""))

        self.notes.setText(data.get("notes", ""))


        buttons = QDialogButtonBox(
            QDialogButtonBox.Save | QDialogButtonBox.Cancel
        )

        buttons.accepted.connect(self.save)

        buttons.rejected.connect(self.reject)

        layout.addWidget(buttons)

    def save(self):

        self.config_manager.save(
            base_url=self.base_url.text().strip(),
            username=self.username.text().strip(),
            password=self.password.text(),
            notes=self.notes.text().strip(),
        )

        self.accept()

# ==========================================================
# Small dialog: start a manual recording
# ==========================================================

class ManualRecordingStartDialog(QDialog):

    def __init__(self, tc_number, default_url, parent=None):

        super().__init__(parent)

        self.setWindowTitle(f"Record Manually — {tc_number}")

        self.resize(480, 260)

        layout = QVBoxLayout(self)

        note = QLabel(
            "This opens a real, visible browser using Playwright's "
            "own recorder — the same engine Playwright's official "
            "codegen tool uses. Perform this test case's steps by "
            "hand, exactly as a user would: click, type, navigate. "
            "Every action is captured automatically as an editable "
            "Playwright script, with real, tested locators — not "
            "AI-guessed ones.\n\n"
            "When you're done, close the recorder's browser window "
            "(or click 'Cancel Recording' back in QA AI Studio to "
            "stop without saving)."
        )

        note.setWordWrap(True)

        layout.addWidget(note)

        layout.addWidget(QLabel("Starting URL:"))

        self.url_field = QLineEdit(default_url or "")

        self.url_field.setPlaceholderText(
            "https://qa.psw.gov.pk/... "
            "(defaults to Test Environment Settings)"
        )

        layout.addWidget(self.url_field)

        buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel
        )

        buttons.button(QDialogButtonBox.Ok).setText("Start Recording")

        buttons.accepted.connect(self.accept)

        buttons.rejected.connect(self.reject)

        layout.addWidget(buttons)

    def selected_url(self):

        return self.url_field.text().strip()


# ==========================================================
# Small dialog: view / edit an automation script
#
# A test case can now have TWO scripts — the AI-generated one
# (automation_script) and a hand-recorded one (recorded_script, via
# Record Manually). This dialog lets you view/edit either one and
# choose which is "active" (the one Execute actually runs) — see
# TestExecutionManager.get_active_script()/set_active_script().
# ==========================================================

class ViewScriptDialog(QDialog):

    def __init__(self, test_case, manager, parent=None):

        super().__init__(parent)

        self.test_case_id = test_case["id"]

        self.automation_type = test_case.get("automation_type") or "Playwright"

        self.manager = manager

        self.test_case = dict(test_case)

        self.setWindowTitle(
            f"{test_case['tc_number']} — {self.automation_type} Script"
        )

        self.resize(720, 600)

        layout = QVBoxLayout(self)

        note = QLabel(
            "Edit the script below — fix the URL, test login, or "
            "any step — then click Save. This script is still not "
            "auto-run without you clicking Execute."
        )

        note.setWordWrap(True)

        layout.addWidget(note)

        source_row = QHBoxLayout()

        source_row.addWidget(QLabel("Viewing:"))

        self.source_combo = QComboBox()

        self.source_combo.addItem("Auto-Generated (AI)", "AUTO")

        self.source_combo.addItem("Manually Recorded", "MANUAL")

        active_source = (
            test_case.get("active_script_source") or "AUTO"
        ).upper()

        self.source_combo.setCurrentIndex(
            1 if active_source == "MANUAL" else 0
        )

        self.source_combo.currentIndexChanged.connect(
            self._on_source_changed
        )

        source_row.addWidget(self.source_combo)

        source_row.addStretch()

        self.active_label = QLabel()

        self.active_label.setStyleSheet("color: #16A34A; font-weight: bold;")

        source_row.addWidget(self.active_label)

        self.set_active_btn = QPushButton("Set as Active for Execution")

        self.set_active_btn.clicked.connect(self._set_active)

        source_row.addWidget(self.set_active_btn)

        layout.addLayout(source_row)

        self.editor = QPlainTextEdit()

        self.editor.setPlaceholderText(
            "Nothing here yet — use Add Automation (AI-generated) "
            "or Record Manually to create a script for this source."
        )

        font = self.editor.font()

        font.setFamily("Consolas")

        self.editor.setFont(font)

        layout.addWidget(self.editor)

        buttons = QDialogButtonBox(QDialogButtonBox.Close)

        self.save_btn = buttons.addButton(
            "Save", QDialogButtonBox.AcceptRole
        )

        buttons.rejected.connect(self.reject)

        buttons.accepted.connect(self.save)

        layout.addWidget(buttons)

        self._load_current_source()

    def _current_source(self):

        return self.source_combo.currentData()

    def _script_for(self, source):

        if source == "MANUAL":

            return self.test_case.get("recorded_script") or ""

        return self.test_case.get("automation_script") or ""

    def _load_current_source(self):

        source = self._current_source()

        self.editor.setPlainText(self._script_for(source))

        active_source = (
            self.test_case.get("active_script_source") or "AUTO"
        ).upper()

        if source == active_source:

            self.active_label.setText("✓ Active for Execution")

            self.set_active_btn.setEnabled(False)

        else:

            self.active_label.setText("")

            self.set_active_btn.setEnabled(True)

    def _on_source_changed(self):

        self._load_current_source()

    def _set_active(self):

        source = self._current_source()

        try:

            self.manager.set_active_script(self.test_case_id, source)

            self.test_case["active_script_source"] = source

            self._load_current_source()

        except Exception as ex:

            QMessageBox.critical(self, "Could Not Set Active", str(ex))

    def save(self):

        new_script = self.editor.toPlainText()

        source = self._current_source()

        if self.automation_type == "Playwright":

            error = self.manager.check_script_syntax(new_script)

            if error:

                proceed = QMessageBox.warning(
                    self,
                    "Syntax Issue",
                    f"This script has a Python syntax problem:\n\n"
                    f"{error}\n\n"
                    f"You can still save it and fix it later, but "
                    f"it won't run as-is until it's corrected.\n\n"
                    f"Save anyway?",
                    QMessageBox.Yes | QMessageBox.No,
                )

                if proceed != QMessageBox.Yes:

                    return

        try:

            if source == "MANUAL":

                self.manager.update_recorded_script(
                    self.test_case_id, new_script
                )

                self.test_case["recorded_script"] = new_script

            else:

                self.manager.update_script(
                    self.test_case_id,
                    self.automation_type,
                    new_script,
                )

                self.test_case["automation_script"] = new_script

            self.accept()

        except Exception as ex:

            QMessageBox.critical(
                self, "Save Failed", str(ex)
            )


# ==========================================================
# Main Page
# ==========================================================

class TestExecutionPage(QWidget):

    def __init__(self):

        super().__init__()

        self.metadata = MetadataManager()

        self.environment_config = TestEnvironmentConfig()

        self.manager = TestExecutionManager()

        self.test_cases = []

        self.generation_thread = None

        self.generation_worker = None

        self.suggestion_thread = None

        self.suggestion_worker = None

        self.execution_thread = None

        self.execution_worker = None

        self.execution_queue = []

        self.recording_thread = None

        self.recording_worker = None

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


        title = QLabel("Test Execution Automation")

        title.setObjectName("SectionTitle")

        layout.addWidget(title)


        # ==================================================
        # Selection
        # ==================================================

        select_group = QGroupBox("Select")

        select_layout = QGridLayout(select_group)

        select_layout.setContentsMargins(15, 20, 15, 15)

        select_layout.setHorizontalSpacing(12)

        select_layout.setVerticalSpacing(10)

        select_layout.setColumnStretch(1, 1)

        select_layout.setColumnStretch(3, 1)


        self.domain = QComboBox()

        self.module = QComboBox()

        self.knowledge_name = QComboBox()

        self.load_btn = QPushButton("Load Test Cases")


        select_layout.addWidget(QLabel("Domain"), 0, 0)

        select_layout.addWidget(self.domain, 0, 1)

        select_layout.addWidget(QLabel("Module"), 0, 2)

        select_layout.addWidget(self.module, 0, 3)

        select_layout.addWidget(QLabel("Knowledge Name"), 1, 0)

        select_layout.addWidget(self.knowledge_name, 1, 1)

        select_layout.addWidget(self.load_btn, 1, 3)


        layout.addWidget(select_group)


        # ==================================================
        # Table
        # ==================================================

        table_group = QGroupBox("Test Cases")

        table_layout = QVBoxLayout(table_group)

        table_layout.setContentsMargins(15, 20, 15, 15)


        selection_row = QHBoxLayout()

        self.select_all_btn = QPushButton("Select All")

        self.clear_all_btn = QPushButton("Clear All")

        selection_row.addWidget(self.select_all_btn)

        selection_row.addWidget(self.clear_all_btn)

        selection_row.addStretch()

        table_layout.addLayout(selection_row)


        self.table = QTableWidget(0, 10)

        self.table.setHorizontalHeaderLabels([
            "",
            "TC #",
            "Test Case",
            "Status",
            "Automation Type",
            "Last Result",
            "Script",
            "Record",
            "Active Script",
            "Automate",
        ])

        self.table.horizontalHeader().setSectionResizeMode(
            2, QHeaderView.Stretch
        )

        self.table.setMinimumHeight(300)

        self.table.setSizePolicy(
            QSizePolicy.Expanding,
            QSizePolicy.Fixed
        )

        table_layout.addWidget(self.table)


        layout.addWidget(table_group)


        # ==================================================
        # Actions
        # ==================================================

        actions_group = QGroupBox("Actions")

        actions_layout = QHBoxLayout(actions_group)

        actions_layout.setContentsMargins(15, 20, 15, 15)

        actions_layout.setSpacing(10)


        self.execute_selected_btn = QPushButton("Execute Selected")

        self.execute_all_btn = QPushButton("Execute All")

        self.add_automation_btn = QPushButton("Add Automation")

        self.update_automation_btn = QPushButton("Update Automation")

        self.environment_settings_btn = QPushButton(
            "Test Environment Settings"
        )

        self.cancel_recording_btn = QPushButton("Cancel Recording")

        self.cancel_recording_btn.setVisible(False)

        self.cancel_recording_btn.setStyleSheet(
            "background-color: #DC2626; color: white;"
        )


        actions_layout.addWidget(self.execute_selected_btn)

        actions_layout.addWidget(self.execute_all_btn)

        actions_layout.addWidget(self.add_automation_btn)

        actions_layout.addWidget(self.update_automation_btn)

        actions_layout.addWidget(self.cancel_recording_btn)

        actions_layout.addStretch()

        actions_layout.addWidget(self.environment_settings_btn)

        layout.addWidget(actions_group)


        # ==================================================
        # Log
        # ==================================================

        log_group = QGroupBox("Log")

        log_layout = QVBoxLayout(log_group)

        log_layout.setContentsMargins(15, 20, 15, 15)


        self.log = QTextEdit()

        self.log.setReadOnly(True)

        self.log.setMinimumHeight(120)

        self.log.setSizePolicy(
            QSizePolicy.Expanding,
            QSizePolicy.Fixed
        )

        log_layout.addWidget(self.log)


        layout.addWidget(log_group)


        # ==================================================
        # Events
        # ==================================================

        self.domain.currentTextChanged.connect(self.on_domain_changed)

        self.module.currentTextChanged.connect(self.on_module_changed)

        self.load_btn.clicked.connect(self.load_test_cases)

        self.select_all_btn.clicked.connect(
            lambda: self.set_all_checked(True)
        )

        self.clear_all_btn.clicked.connect(
            lambda: self.set_all_checked(False)
        )

        self.execute_selected_btn.clicked.connect(
            lambda: self.execute(only_selected=True)
        )

        self.execute_all_btn.clicked.connect(
            lambda: self.execute(only_selected=False)
        )

        self.add_automation_btn.clicked.connect(
            self.generate_automation_for_selected
        )

        self.update_automation_btn.clicked.connect(
            self.generate_automation_for_selected
        )

        self.environment_settings_btn.clicked.connect(
            self.open_environment_settings
        )

        self.cancel_recording_btn.clicked.connect(
            self.cancel_current_recording
        )


    def open_environment_settings(self):

        dialog = EnvironmentSettingsDialog(
            self.environment_config, self
        )

        if dialog.exec() == QDialog.Accepted:

            self.log.append(
                "Test environment settings saved. New Playwright "
                "scripts will use these values."
            )


    # ======================================================
    # Domain / Module / Knowledge Name cascade
    # ======================================================

    def load_domains(self):

        self.domain.blockSignals(True)

        self.domain.clear()

        domains = self.metadata.list_domains()

        self.domain.addItems(domains)

        self.domain.blockSignals(False)

        self.on_domain_changed(self.domain.currentText())


    def on_domain_changed(self, domain_name):

        self.module.blockSignals(True)

        self.module.clear()

        if domain_name:

            self.module.addItems(
                self.metadata.list_modules(domain_name)
            )

        self.module.blockSignals(False)

        self.on_module_changed(self.module.currentText())


    def on_module_changed(self, module_name):

        self.knowledge_name.clear()

        domain_name = self.domain.currentText()

        if domain_name and module_name:

            self.knowledge_name.addItems(
                self.metadata.get_knowledge_names(
                    domain_name,
                    module_name
                )
            )


    # ======================================================
    # Load Test Cases
    # ======================================================

    def load_test_cases(self):

        domain = self.domain.currentText().strip()

        module = self.module.currentText().strip()

        knowledge_name = self.knowledge_name.currentText().strip()

        if not (domain and module and knowledge_name):

            QMessageBox.warning(
                self,
                "QA AI Studio",
                "Select a Domain, Module and Knowledge Name first."
            )

            return

        self.test_cases = self.manager.list_test_cases(
            domain,
            module,
            knowledge_name
        )

        self.populate_table()

        if not self.test_cases:

            self.log.append(
                f"No test cases found for {domain} / {module} / "
                f"{knowledge_name}. Generate some first in QA "
                f"Engineering."
            )

        else:

            self.log.append(
                f"Loaded {len(self.test_cases)} test case(s)."
            )


    def populate_table(self):

        self.table.setRowCount(0)

        for test_case in self.test_cases:

            self.add_row(test_case)


    def add_row(self, test_case):

        row = self.table.rowCount()

        self.table.insertRow(row)


        checkbox = QCheckBox()

        checkbox.setProperty("tc_id", test_case["id"])

        checkbox_container = QWidget()

        checkbox_layout = QHBoxLayout(checkbox_container)

        checkbox_layout.addWidget(checkbox)

        checkbox_layout.setAlignment(Qt.AlignCenter)

        checkbox_layout.setContentsMargins(0, 0, 0, 0)

        self.table.setCellWidget(row, 0, checkbox_container)


        tc_item = QTableWidgetItem(test_case["tc_number"])

        tc_item.setData(TC_ID_ROLE, test_case["id"])

        tc_item.setFlags(tc_item.flags() & ~Qt.ItemIsEditable)

        self.table.setItem(row, 1, tc_item)


        case_item = QTableWidgetItem(test_case.get("test_case", ""))

        case_item.setFlags(case_item.flags() & ~Qt.ItemIsEditable)

        case_item.setToolTip(test_case.get("test_case", ""))

        self.table.setItem(row, 2, case_item)


        status_combo = QComboBox()

        status_combo.addItems(["Manual", "Automated"])

        status_combo.setCurrentText(
            test_case.get("status", "Manual")
        )

        status_combo.currentTextChanged.connect(
            lambda value, tc_id=test_case["id"]: self.on_status_changed(
                tc_id, value
            )
        )

        self.table.setCellWidget(row, 3, status_combo)


        automation_combo = QComboBox()

        automation_combo.addItems(AUTOMATION_TYPES)

        automation_combo.setCurrentText(
            test_case.get("automation_type") or "None"
        )

        self.table.setCellWidget(row, 4, automation_combo)


        result_item = QTableWidgetItem(
            test_case.get("last_result", "Not Run")
        )

        result_item.setFlags(result_item.flags() & ~Qt.ItemIsEditable)

        self.table.setItem(row, 5, result_item)


        script_btn = QPushButton("View Script")

        script_btn.setEnabled(
            bool(test_case.get("automation_script"))
            or bool(test_case.get("recorded_script"))
        )

        script_btn.clicked.connect(
            lambda _, tc_id=test_case["id"]: self.view_script(tc_id)
        )

        self.table.setCellWidget(row, 6, script_btn)


        record_btn = QPushButton("Record Manually")

        record_btn.setToolTip(
            "Opens a real browser (Playwright's own recorder). "
            "Perform this test case by hand — clicking, typing, "
            "navigating — and it's captured as an editable script, "
            "stored separately from the AI-generated one."
        )

        record_btn.clicked.connect(
            lambda _, tc_id=test_case["id"]: self.start_manual_recording(
                tc_id
            )
        )

        self.table.setCellWidget(row, 7, record_btn)

        active_combo = QComboBox()

        active_combo.addItem("Auto-Generated (AI)", "AUTO")

        active_combo.addItem("Manually Recorded", "MANUAL")

        active_source = (
            test_case.get("active_script_source") or "AUTO"
        ).upper()

        active_combo.setCurrentIndex(
            1 if active_source == "MANUAL" else 0
        )

        active_combo.currentIndexChanged.connect(
            lambda _, tc_id=test_case["id"], combo=active_combo:
                self.on_active_script_changed(tc_id, combo)
        )

        self.table.setCellWidget(row, 8, active_combo)


        automate_btn = QPushButton("Automate")

        automate_btn.setToolTip(
            "AI suggests an automation type, you confirm, "
            "then it generates the script."
        )

        automate_btn.clicked.connect(
            lambda _, tc_id=test_case["id"]: self.open_automation_suggestion(
                tc_id
            )
        )

        self.table.setCellWidget(row, 9, automate_btn)

    # ======================================================
    # Selection helpers
    # ======================================================

    def set_all_checked(self, checked):

        for row in range(self.table.rowCount()):

            container = self.table.cellWidget(row, 0)

            checkbox = container.findChild(QCheckBox)

            checkbox.setChecked(checked)


    def get_checked_rows(self):

        rows = []

        for row in range(self.table.rowCount()):

            container = self.table.cellWidget(row, 0)

            checkbox = container.findChild(QCheckBox)

            if checkbox.isChecked():

                rows.append(row)

        return rows


    def row_tc_id(self, row):

        return self.table.item(row, 1).data(TC_ID_ROLE)


    def row_tc_number(self, row):

        return self.table.item(row, 1).text()


    # ======================================================
    # Status change (Manual <-> Automated)
    # ======================================================

    def on_status_changed(self, test_case_id, status):

        try:

            self.manager.set_status(test_case_id, status)

            self.log.append(
                f"Status updated to '{status}' for test case "
                f"#{test_case_id}."
            )

        except Exception as ex:

            QMessageBox.critical(self, "QA AI Studio", str(ex))


    # ======================================================
    # Add / Update Automation (background thread)
    # ======================================================

    def generate_automation_for_selected(self):

        if self.generation_thread is not None:

            QMessageBox.information(
                self,
                "QA AI Studio",
                "Please wait for the current automation generation "
                "to finish."
            )

            return

        rows = self.get_checked_rows()

        if not rows:

            QMessageBox.warning(
                self,
                "QA AI Studio",
                "Select at least one test case first."
            )

            return

        items = []

        for row in rows:

            automation_combo = self.table.cellWidget(row, 4)

            automation_type = automation_combo.currentText()

            if automation_type == "None":

                continue

            items.append(
                (self.row_tc_id(row), automation_type)
            )

        if not items:

            QMessageBox.warning(
                self,
                "QA AI Studio",
                "Choose an Automation Type (Playwright / Selenium / "
                "API / SQL) for the selected test case(s) first."
            )

            return

        self.start_automation_generation(items)


    def start_automation_generation(self, items):
        """
        items: list of (test_case_id, automation_type) tuples.
        Shared by the bulk 'Add Automation'/'Update Automation'
        buttons and the single-item 'Automate' popup flow.
        """

        if self.generation_thread is not None:

            QMessageBox.information(
                self,
                "QA AI Studio",
                "Please wait for the current automation generation "
                "to finish."
            )

            return

        domain = self.domain.currentText().strip()

        module = self.module.currentText().strip()

        knowledge_name = self.knowledge_name.currentText().strip()


        self.add_automation_btn.setEnabled(False)

        self.update_automation_btn.setEnabled(False)

        self.log.append(
            f"Generating automation for {len(items)} test case(s)..."
        )

        self.generation_thread = QThread()

        self.generation_worker = AutomationGenerationWorker(
            items=items,
            domain=domain,
            module=module,
            knowledge_name=knowledge_name,
        )

        self.generation_worker.moveToThread(self.generation_thread)

        self.generation_thread.started.connect(
            self.generation_worker.run
        )

        self.generation_worker.progress.connect(self.log.append)

        self.generation_worker.case_done.connect(
            self.on_automation_case_done
        )

        self.generation_worker.case_failed.connect(
            self.on_automation_case_failed
        )

        self.generation_worker.finished.connect(
            self.on_automation_generation_finished
        )

        self.generation_worker.error.connect(
            self.on_automation_generation_error
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


    # ======================================================
    # Automate button: AI suggests a type, you confirm, then
    # generate (background thread for the suggestion step too —
    # it's an LLM call)
    # ======================================================

    def open_automation_suggestion(self, test_case_id):

        if self.suggestion_thread is not None:

            QMessageBox.information(
                self,
                "QA AI Studio",
                "Please wait — already analyzing a test case."
            )

            return

        if self.generation_thread is not None:

            QMessageBox.information(
                self,
                "QA AI Studio",
                "Please wait for the current automation generation "
                "to finish."
            )

            return

        self.log.append(
            f"Analyzing test case #{test_case_id} with AI..."
        )

        self.suggestion_thread = QThread()

        self.suggestion_worker = AutomationSuggestionWorker(
            test_case_id
        )

        self.suggestion_worker.moveToThread(self.suggestion_thread)

        self.suggestion_thread.started.connect(
            self.suggestion_worker.run
        )

        self.suggestion_worker.progress.connect(self.log.append)

        self.suggestion_worker.finished.connect(
            self.on_suggestion_ready
        )

        self.suggestion_worker.error.connect(
            self.on_suggestion_error
        )

        self.suggestion_worker.finished.connect(
            self.suggestion_thread.quit
        )

        self.suggestion_worker.error.connect(
            self.suggestion_thread.quit
        )

        self.suggestion_thread.finished.connect(
            self.cleanup_suggestion_thread
        )

        self.suggestion_thread.start()


    def on_suggestion_ready(self, test_case_id, suggestion):

        self.log.append(
            f"AI suggests: {suggestion['suggested_type']} — "
            f"{suggestion['reason']}"
        )

        tc_number = ""

        test_case_text = ""

        for row in range(self.table.rowCount()):

            if self.row_tc_id(row) == test_case_id:

                tc_number = self.row_tc_number(row)

                test_case_text = self.table.item(row, 2).text()

                break

        dialog = AutomationSuggestionDialog(
            tc_number, test_case_text, suggestion, self
        )

        if dialog.exec() == QDialog.Accepted:

            chosen_type = dialog.selected_type()

            self.start_automation_generation(
                [(test_case_id, chosen_type)]
            )


    def on_suggestion_error(self, message):

        self.log.append(
            f"AI suggestion failed: {message}"
        )

        QMessageBox.critical(
            self, "Automation Suggestion Failed", message
        )


    def cleanup_suggestion_thread(self):

        if self.suggestion_thread:

            self.suggestion_thread.deleteLater()

        self.suggestion_thread = None

        self.suggestion_worker = None


    def on_automation_case_done(self, test_case_id, script):

        for row in range(self.table.rowCount()):

            if self.row_tc_id(row) == test_case_id:

                self.table.cellWidget(row, 3).setCurrentText(
                    "Automated"
                )

                self.table.cellWidget(row, 6).setEnabled(True)

                break

        self.log.append(
            f"Automation script generated for test case #{test_case_id}."
        )


    def on_automation_case_failed(self, test_case_id, message):

        self.log.append(
            f"Automation generation failed for test case "
            f"#{test_case_id}: {message}"
        )


    def on_automation_generation_finished(self):

        self.add_automation_btn.setEnabled(True)

        self.update_automation_btn.setEnabled(True)

        self.log.append("Automation generation batch complete.")


    def on_automation_generation_error(self, message):

        self.add_automation_btn.setEnabled(True)

        self.update_automation_btn.setEnabled(True)

        self.log.append(f"Automation generation error: {message}")

        QMessageBox.critical(
            self, "Automation Generation Failed", message
        )


    def cleanup_generation_thread(self):

        if self.generation_thread:

            self.generation_thread.deleteLater()

        self.generation_thread = None

        self.generation_worker = None


    # ======================================================
    # View Script
    # ======================================================

    def view_script(self, test_case_id):

        test_case = self.manager.repository.get_test_case(test_case_id)

        if not test_case:

            return

        dialog = ViewScriptDialog(
            test_case,
            self.manager,
            self,
        )

        if dialog.exec() == QDialog.Accepted:

            self.log.append(
                f"{test_case['tc_number']}: script updated manually."
            )

            self.load_test_cases()


    # ======================================================
    # Active Script (Auto-Generated vs Manually Recorded)
    # ======================================================

    def on_active_script_changed(self, test_case_id, combo):

        source = combo.currentData()

        test_case = self.manager.repository.get_test_case(test_case_id)

        if source == "MANUAL" and not (test_case or {}).get(
            "recorded_script"
        ):

            QMessageBox.warning(
                self,
                "Nothing Recorded Yet",
                "This test case doesn't have a manually recorded "
                "script yet — use 'Record Manually' first. Staying "
                "on the AI-generated script for now."
            )

            combo.blockSignals(True)

            combo.setCurrentIndex(0)

            combo.blockSignals(False)

            return

        try:

            self.manager.set_active_script(test_case_id, source)

            self.log.append(
                f"{self.tc_number_for_id(test_case_id)}: active "
                f"script set to "
                + (
                    "manually recorded"
                    if source == "MANUAL"
                    else "AI-generated"
                )
                + "."
            )

        except Exception as ex:

            QMessageBox.critical(self, "QA AI Studio", str(ex))


    # ======================================================
    # Manual Recording
    #
    # Alternative to Add/Update Automation's AI-generated script —
    # opens a real browser (Playwright's own codegen recorder) and
    # captures your own clicks/typing/navigation as an editable
    # script, stored separately (recorded_script) so it never
    # overwrites the AI-generated one.
    # ======================================================

    def set_actions_enabled(self, enabled):
        """
        Disabled while a recording is in progress — only one
        Playwright recorder session makes sense at a time, and the
        rest of the page's actions (generation, execution, loading a
        different test case list) would otherwise race against it.
        """

        self.execute_selected_btn.setEnabled(enabled)

        self.execute_all_btn.setEnabled(enabled)

        self.add_automation_btn.setEnabled(enabled)

        self.update_automation_btn.setEnabled(enabled)

        self.load_btn.setEnabled(enabled)

        for row in range(self.table.rowCount()):

            record_widget = self.table.cellWidget(row, 7)

            if record_widget:

                record_widget.setEnabled(enabled)

            automate_widget = self.table.cellWidget(row, 9)

            if automate_widget:

                automate_widget.setEnabled(enabled)


    def start_manual_recording(self, test_case_id):

        if self.recording_thread is not None:

            QMessageBox.information(
                self,
                "Recording In Progress",
                "Only one recording can run at a time. Finish or "
                "cancel the current recording first."
            )

            return

        test_case = self.manager.repository.get_test_case(test_case_id)

        if not test_case:

            return

        environment = self.manager.environment_config.load()

        dialog = ManualRecordingStartDialog(
            test_case["tc_number"],
            environment.get("base_url", ""),
            self,
        )

        if dialog.exec() != QDialog.Accepted:

            return

        start_url = dialog.selected_url()

        self.set_actions_enabled(False)

        self.cancel_recording_btn.setVisible(True)

        self.log.append(
            f"{test_case['tc_number']}: recorder browser opening — "
            f"perform the test steps by hand, then close the "
            f"recorder window when done."
        )

        self.recording_thread = QThread()

        self.recording_worker = ManualRecordingWorker(
            test_case_id, start_url
        )

        self.recording_worker.moveToThread(self.recording_thread)

        self.recording_thread.started.connect(
            self.recording_worker.run
        )

        self.recording_worker.progress.connect(self.log.append)

        self.recording_worker.finished.connect(
            self.on_recording_finished
        )

        self.recording_worker.cancelled.connect(
            self.on_recording_cancelled
        )

        self.recording_worker.error.connect(self.on_recording_error)

        self.recording_worker.finished.connect(
            self.recording_thread.quit
        )

        self.recording_worker.cancelled.connect(
            self.recording_thread.quit
        )

        self.recording_worker.error.connect(
            self.recording_thread.quit
        )

        self.recording_thread.finished.connect(
            self.cleanup_recording_thread
        )

        self.recording_thread.start()


    def cancel_current_recording(self):

        if self.recording_worker is not None:

            self.recording_worker.cancel()

            self.log.append("Cancelling recording...")


    def on_recording_finished(self, test_case_id, script):

        tc_number = self.tc_number_for_id(test_case_id)

        self.log.append(
            f"{tc_number}: manual recording saved "
            f"({len(script.splitlines())} line(s))."
        )

        make_active = QMessageBox.question(
            self,
            "Recording Saved",
            f"{tc_number}'s manual recording was saved.\n\n"
            f"Set it as the ACTIVE script for Execute (instead of "
            f"whichever script is active now)?",
            QMessageBox.Yes | QMessageBox.No,
        )

        if make_active == QMessageBox.Yes:

            try:

                self.manager.set_active_script(test_case_id, "MANUAL")

            except Exception as ex:

                QMessageBox.critical(self, "QA AI Studio", str(ex))

        self.set_actions_enabled(True)

        self.cancel_recording_btn.setVisible(False)

        self.load_test_cases()


    def on_recording_cancelled(self, test_case_id):

        tc_number = self.tc_number_for_id(test_case_id)

        self.log.append(
            f"{tc_number}: recording cancelled, nothing was saved."
        )

        self.set_actions_enabled(True)

        self.cancel_recording_btn.setVisible(False)


    def on_recording_error(self, message):

        self.log.append(f"Manual recording failed: {message}")

        QMessageBox.critical(self, "Recording Failed", message)

        self.set_actions_enabled(True)

        self.cancel_recording_btn.setVisible(False)


    def cleanup_recording_thread(self):

        if self.recording_thread:

            self.recording_thread.deleteLater()

        self.recording_thread = None

        self.recording_worker = None


    # ======================================================
    # Execute Selected / Execute All
    # ======================================================

    def execute(self, only_selected=True):

        if only_selected:

            rows = self.get_checked_rows()

            if not rows:

                QMessageBox.warning(
                    self,
                    "QA AI Studio",
                    "Select at least one test case first."
                )

                return

        else:

            rows = list(range(self.table.rowCount()))

        if not rows:

            QMessageBox.information(
                self, "QA AI Studio", "No test cases to execute."
            )

            return

        manual_rows = []

        automated_rows = []

        for row in rows:

            status = self.table.cellWidget(row, 3).currentText()

            if status == "Automated":

                automated_rows.append(row)

            else:

                manual_rows.append(row)

        for row in manual_rows:

            self.execute_manual_row(row)

        if automated_rows:

            self.handle_automated_rows(automated_rows)


    def execute_manual_row(self, row):

        tc_id = self.row_tc_id(row)

        tc_number = self.row_tc_number(row)

        test_case_text = self.table.item(row, 2).text()

        dialog = RecordResultDialog(
            tc_number,
            test_case_text,
            self
        )

        if dialog.exec() == QDialog.Accepted:

            result = dialog.selected_result()

            try:

                self.manager.record_manual_result(tc_id, result)

                self.table.item(row, 5).setText(result)

                self.log.append(
                    f"{tc_number}: recorded result '{result}'."
                )

            except Exception as ex:

                QMessageBox.critical(self, "QA AI Studio", str(ex))


    def handle_automated_rows(self, rows):

        without_script = []

        playwright_ready = []

        other_with_script = []

        for row in rows:

            has_script = self.table.cellWidget(row, 6).isEnabled()

            automation_type = self.table.cellWidget(row, 4).currentText()

            if not has_script:

                without_script.append(row)

            elif automation_type == "Playwright":

                playwright_ready.append(row)

            else:

                other_with_script.append(row)

        if without_script:

            numbers = ", ".join(
                self.row_tc_number(r) for r in without_script
            )

            self.log.append(
                f"Skipped (no automation script yet): {numbers}. "
                f"Use 'Add Automation' first."
            )

        if other_with_script:

            numbers = ", ".join(
                self.row_tc_number(r) for r in other_with_script
            )

            QMessageBox.information(
                self,
                "Manual Review Required",
                f"{len(other_with_script)} automated test case(s) "
                f"use Selenium/API/SQL, which don't have an "
                f"automatic runner yet.\n\nTest cases: {numbers}\n\n"
                f"Use 'View Script' on each row to review and run "
                f"it in your own test environment."
            )

        if playwright_ready:

            self.confirm_and_run_playwright(playwright_ready)


    def confirm_and_run_playwright(self, rows):

        numbers = ", ".join(
            self.row_tc_number(r) for r in rows
        )

        confirm = QMessageBox.question(
            self,
            "Run Playwright Tests",
            f"This will open a real browser and run {len(rows)} "
            f"AI-generated Playwright script(s):\n\n{numbers}\n\n"
            f"Make sure you've reviewed them with 'View Script' "
            f"first, especially the target URL — this runs against "
            f"whatever environment the script points to.\n\n"
            f"Continue?",
            QMessageBox.Yes | QMessageBox.No,
        )

        if confirm != QMessageBox.Yes:

            self.log.append(
                "Playwright execution cancelled."
            )

            return

        test_case_ids = [
            self.row_tc_id(row) for row in rows
        ]

        self.execution_queue = test_case_ids

        self.execute_selected_btn.setEnabled(False)

        self.execute_all_btn.setEnabled(False)

        self.run_next_execution()


    def run_next_execution(self):

        if not self.execution_queue:

            self.execute_selected_btn.setEnabled(True)

            self.execute_all_btn.setEnabled(True)

            self.log.append(
                "Playwright execution queue complete."
            )

            return

        if self.execution_thread is not None:

            return

        test_case_id = self.execution_queue.pop(0)

        tc_number = self.tc_number_for_id(test_case_id)

        self.log.append(
            f"Running {tc_number} in a real browser..."
        )

        self.execution_thread = QThread()

        self.execution_worker = PlaywrightExecutionWorker(
            test_case_id
        )

        self.execution_worker.moveToThread(self.execution_thread)

        self.execution_thread.started.connect(
            self.execution_worker.run
        )

        self.execution_worker.progress.connect(self.log.append)

        self.execution_worker.finished.connect(
            self.on_execution_finished
        )

        self.execution_worker.error.connect(
            self.on_execution_error
        )

        self.execution_worker.finished.connect(
            self.execution_thread.quit
        )

        self.execution_worker.error.connect(
            self.execution_thread.quit
        )

        self.execution_thread.finished.connect(
            self.cleanup_execution_thread
        )

        self.execution_thread.start()


    def on_execution_finished(self, test_case_id, result):

        tc_number = self.tc_number_for_id(test_case_id)

        if "outcome" in result:

            outcome = result["outcome"]

            duration = result.get("duration", 0)

            self.set_row_last_result(test_case_id, outcome)

            self.log.append(
                f"{tc_number}: {outcome} ({duration:.1f}s)"
            )

            if not result["success"] and result.get("stderr"):

                self.log.append(
                    f"{tc_number} error output:\n{result['stderr'][:500]}"
                )

        else:

            # Didn't actually run — e.g. Playwright not installed.
            self.log.append(
                f"{tc_number}: could not run — "
                f"{result.get('error', 'unknown error')}"
            )

            QMessageBox.warning(
                self, "QA AI Studio", result.get("error", "")
            )


    def on_execution_error(self, message):

        self.log.append(
            f"Execution error: {message}"
        )

        QMessageBox.critical(
            self, "Playwright Execution Failed", message
        )


    def cleanup_execution_thread(self):

        if self.execution_thread:

            self.execution_thread.deleteLater()

        self.execution_thread = None

        self.execution_worker = None

        self.run_next_execution()


    def tc_number_for_id(self, test_case_id):

        for row in range(self.table.rowCount()):

            if self.row_tc_id(row) == test_case_id:

                return self.row_tc_number(row)

        return f"#{test_case_id}"


    def set_row_last_result(self, test_case_id, text):

        for row in range(self.table.rowCount()):

            if self.row_tc_id(row) == test_case_id:

                self.table.item(row, 5).setText(text)

                return