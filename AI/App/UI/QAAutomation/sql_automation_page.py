# Create: App/UI/QAAutomation/sql_automation_page.py

"""
==========================================================
QA AI Studio

SQL Automation

Version: 1.0

New for QA-AUTOMATION-FINAL-ARCHITECTURE-04 — one of the 3 required
primary QA Automation tabs (Playwright Automation / API Automation /
SQL Automation), replacing "SQL" as a silently-unsupported entry in
Test Execution Automation's per-row Automation Type combo (see
test_execution_page.py's AUTOMATION_TYPES comment). This is the
Desktop-side mirror of the Web port's SQL Automation tab
(AI-Web/Frontend/index.html's #auto-sql panel) — same Select ->
Grid -> Action -> Log layout, same grid columns, same real backend
(Core/sql_automation_manager.py, ported from the Web port's
Core/automation_web_repository.py -> SqlAutomationWeb).

SQL Automation is deliberately NOT unrestricted database access:
- Read-only by design. Only a single SELECT/WITH statement is ever
  allowed to run — enforced server-side by
  Core/sql_automation_runner.py's validate_readonly_sql(), re-checked
  independently at Save-time review, Validate, Set Active, AND right
  before Execute (never trusts a stale flag alone).
- AI-generated SQL is never trusted blindly: Core/sql_generator.py's
  SQLGenerator refuses to invent table/column names when no real
  database schema is available, and a generated draft can never
  become "Active" without passing the same safety gate as a
  hand-written query.
- The target database connection profile (Test Environment Setting)
  is separate from, and never confused with, this application's own
  metadata database (Core/sqlite_manager.py) or the Playwright/API
  Test Environment Settings.

No "Record" column here — Manual Recording (Playwright's own
recorder) has no meaning for a SQL query.

No persisted run-history grid here — see
Core/sql_automation_manager.py's module docstring for why (Desktop
has no run-history table for ANY automation type today, not just
SQL); this page's "Execution Log" is the same kind of in-session,
read-only text log the Playwright/API tabs already use.
==========================================================
"""

import json

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QGridLayout,
    QFormLayout,
    QLabel,
    QPushButton,
    QComboBox,
    QCheckBox,
    QGroupBox,
    QTextEdit,
    QPlainTextEdit,
    QLineEdit,
    QMessageBox,
    QScrollArea,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QHeaderView,
    QDialog,
    QDialogButtonBox,
)

from Core.metadata_manager import MetadataManager

#from Core.clickup_client import ClickUpClient
from Core.sql_automation_manager import SqlAutomationManager
from Core.sql_environment_config import SqlEnvironmentConfig, SUPPORTED_DB_TYPES

# TEMPORARY: ClickUp integration disabled for Desktop startup.
class ClickUpConfig:
    def load(self):
        return {
            "clickup_api_token": "",
            "clickup_list_id": "",
        }

    def save(self, **kwargs):
        return None

TC_ID_ROLE = Qt.UserRole


# ==========================================================
# Small dialog: view / edit a test case's SQL + assertion, generate
# (AI), validate, set active. Mirrors test_execution_page.py's
# ViewScriptDialog but for the SQL script envelope (sql/assertion_*)
# instead of a Playwright/API script string.
# ==========================================================

class SqlScriptDialog(QDialog):

    def __init__(self, test_case, manager, parent=None):

        super().__init__(parent)

        self.test_case = test_case

        self.manager = manager

        self.setWindowTitle(f"{test_case['tc_number']} — SQL")

        self.resize(720, 640)

        layout = QVBoxLayout(self)

        case_label = QLabel(
            (test_case.get("test_case") or test_case.get("scenario") or "")[:400]
        )

        case_label.setWordWrap(True)

        layout.addWidget(case_label)

        layout.addWidget(QLabel("SQL Query (SELECT / WITH only — read-only)"))

        self.sql_edit = QPlainTextEdit()

        self.sql_edit.setPlainText(self._script().get("sql") or "")

        self.sql_edit.setMinimumHeight(220)

        layout.addWidget(self.sql_edit)

        assertion_form = QFormLayout()

        self.assertion_type = QComboBox()

        self.assertion_type.addItems([
            "row_exists", "no_rows", "row_count_equals",
            "scalar_equals", "value_equals",
        ])

        current_type = self._script().get("assertion_type") or "row_exists"

        idx = self.assertion_type.findText(current_type)

        self.assertion_type.setCurrentIndex(max(idx, 0))

        self.assertion_column = QLineEdit(self._script().get("assertion_column") or "")

        self.assertion_column.setPlaceholderText("e.g. status (for 'value_equals')")

        self.assertion_value = QLineEdit(str(self._script().get("assertion_value") or ""))

        self.assertion_value.setPlaceholderText("e.g. Approved")

        assertion_form.addRow("Assertion", self.assertion_type)

        assertion_form.addRow("Column", self.assertion_column)

        assertion_form.addRow("Expected value", self.assertion_value)

        layout.addLayout(assertion_form)

        self.status_label = QLabel("")

        self.status_label.setWordWrap(True)

        layout.addWidget(self.status_label)

        active_row = QHBoxLayout()

        self.active_checkbox = QCheckBox("Active (executable)")

        self.active_checkbox.setChecked(
            (test_case.get("status") or "") == "Automated"
        )

        self.active_checkbox.toggled.connect(self._on_active_toggled)

        active_row.addWidget(self.active_checkbox)

        active_row.addStretch()

        layout.addLayout(active_row)

        buttons_row = QHBoxLayout()

        self.generate_btn = QPushButton("AI Auto Generate SQL")

        self.generate_btn.clicked.connect(self._on_generate)

        self.validate_btn = QPushButton("Validate")

        self.validate_btn.clicked.connect(self._on_validate)

        self.save_btn = QPushButton("Save")

        self.save_btn.clicked.connect(self._on_save)

        buttons_row.addWidget(self.generate_btn)

        buttons_row.addWidget(self.validate_btn)

        buttons_row.addStretch()

        buttons_row.addWidget(self.save_btn)

        layout.addLayout(buttons_row)

        close_buttons = QDialogButtonBox(QDialogButtonBox.Close)

        close_buttons.rejected.connect(self.reject)

        close_buttons.accepted.connect(self.accept)

        layout.addWidget(close_buttons)

    def _script(self):

        return self.manager._unpack_script(self.test_case.get("automation_script"))

    def _on_generate(self):

        self.generate_btn.setEnabled(False)

        try:

            result = self.manager.generate_sql(self.test_case["id"])

            self.sql_edit.setPlainText(result.get("sql") or "")

            self.test_case = result["test_case"]

            if result.get("needs_review"):

                self.status_label.setText(
                    "AI could not produce safe, executable SQL without a "
                    "known database schema — review/complete it manually: "
                    + (result.get("validation_error") or "")
                )

            else:

                self.status_label.setText(
                    "SQL draft generated (AI) — review, then Save + "
                    "Validate before it can be activated."
                )

        except Exception as ex:

            QMessageBox.critical(self, "QA AI Studio", str(ex))

        finally:

            self.generate_btn.setEnabled(True)

    def _on_save(self):

        try:

            self.test_case = self.manager.save_script(
                self.test_case["id"],
                self.sql_edit.toPlainText(),
                self.assertion_type.currentText(),
                self.assertion_value.text() or None,
                self.assertion_column.text() or None,
            )

            self.status_label.setText("Saved.")

        except Exception as ex:

            QMessageBox.critical(self, "QA AI Studio", str(ex))

    def _on_validate(self):

        try:

            result = self.manager.validate_sql(self.test_case["id"])

            if result["valid"]:

                self.status_label.setText("Safe to execute.")

            else:

                self.status_label.setText(result["error"])

        except Exception as ex:

            QMessageBox.critical(self, "QA AI Studio", str(ex))

    def _on_active_toggled(self, checked):

        if not checked:

            # Deactivating never needs validation — only ever a
            # gate on turning IT ON.
            return

        try:

            self.test_case = self.manager.set_active(self.test_case["id"], True)

            self.status_label.setText("Active — this query will run on Execute.")

        except Exception as ex:

            self.active_checkbox.blockSignals(True)

            self.active_checkbox.setChecked(False)

            self.active_checkbox.blockSignals(False)

            QMessageBox.warning(self, "Cannot Activate", str(ex))


# ==========================================================
# Small dialog: SQL Test Environment Setting (target database
# connection profile) — separate from the Playwright/API dialog in
# test_execution_page.py, since a DB connection profile has nothing
# in common with a browser/HTTP environment.
# ==========================================================

class SqlEnvironmentDialog(QDialog):

    def __init__(self, parent=None):

        super().__init__(parent)

        self.config = SqlEnvironmentConfig()

        self.setWindowTitle("SQL Test Environment Setting")

        self.resize(480, 420)

        layout = QVBoxLayout(self)

        note = QLabel(
            "The target database SQL Automation connects to — never "
            "this application's own database. Use a read-only test/"
            "UAT account."
        )

        note.setWordWrap(True)

        layout.addWidget(note)

        form = QFormLayout()

        self.db_type = QComboBox()

        self.db_type.addItems(list(SUPPORTED_DB_TYPES))

        self.db_type.currentTextChanged.connect(self._toggle_fields)

        self.sqlite_path = QLineEdit()

        self.host = QLineEdit()

        self.port = QLineEdit()

        self.database = QLineEdit()

        self.username = QLineEdit()

        self.password = QLineEdit()

        self.password.setEchoMode(QLineEdit.Password)

        self.timeout_seconds = QLineEdit()

        self.max_rows = QLineEdit()

        form.addRow("Database type", self.db_type)

        form.addRow("SQLite file path", self.sqlite_path)

        form.addRow("Host", self.host)

        form.addRow("Port", self.port)

        form.addRow("Database", self.database)

        form.addRow("Username", self.username)

        form.addRow("Password", self.password)

        form.addRow("Query timeout (s)", self.timeout_seconds)

        form.addRow("Max rows returned", self.max_rows)

        layout.addLayout(form)

        self.test_result_label = QLabel("")

        self.test_result_label.setWordWrap(True)

        layout.addWidget(self.test_result_label)

        button_row = QHBoxLayout()

        self.test_btn = QPushButton("Test Connection")

        self.test_btn.clicked.connect(self._on_test)

        button_row.addWidget(self.test_btn)

        button_row.addStretch()

        layout.addLayout(button_row)

        buttons = QDialogButtonBox(
            QDialogButtonBox.Save | QDialogButtonBox.Cancel
        )

        buttons.accepted.connect(self._on_save)

        buttons.rejected.connect(self.reject)

        layout.addWidget(buttons)

        self._load()

    def _load(self):

        data = self.config.load()

        idx = self.db_type.findText(data.get("sql_db_type") or "sqlite")

        self.db_type.setCurrentIndex(max(idx, 0))

        self.sqlite_path.setText(data.get("sql_sqlite_path") or "")

        self.host.setText(data.get("sql_host") or "")

        self.port.setText(data.get("sql_port") or "")

        self.database.setText(data.get("sql_database") or "")

        self.username.setText(data.get("sql_username") or "")

        self.password.setText(data.get("sql_password") or "")

        self.timeout_seconds.setText(str(data.get("sql_timeout_seconds") or "30"))

        self.max_rows.setText(str(data.get("sql_max_rows") or "200"))

        self._toggle_fields(self.db_type.currentText())

    def _toggle_fields(self, db_type):

        is_sqlite = (db_type == "sqlite")

        self.sqlite_path.setVisible(is_sqlite)

        for field in (self.host, self.port, self.database, self.username, self.password):

            field.setVisible(not is_sqlite)

    def _on_test(self):

        self.test_result_label.setText("Testing…")

        # Save first so "Test Connection" always reflects exactly what
        # "Save" would persist, not whatever was last saved before this
        # dialog was opened.
        saved_profile = self.config.save(**self._collect())

        from Core.sql_automation_runner import SqlAutomationRunner

        outcome = SqlAutomationRunner().test_connection(saved_profile)

        if outcome.get("success"):

            self.test_result_label.setText(outcome.get("message") or "Connection succeeded.")

        else:

            self.test_result_label.setText(outcome.get("error") or "Connection failed.")

    def _collect(self):

        return {
            "sql_db_type": self.db_type.currentText(),
            "sql_sqlite_path": self.sqlite_path.text().strip(),
            "sql_host": self.host.text().strip(),
            "sql_port": self.port.text().strip(),
            "sql_database": self.database.text().strip(),
            "sql_username": self.username.text().strip(),
            "sql_password": self.password.text(),
            "sql_timeout_seconds": self.timeout_seconds.text().strip() or "30",
            "sql_max_rows": self.max_rows.text().strip() or "200",
        }

    def _on_save(self):

        self.config.save(**self._collect())

        self.accept()


# ==========================================================
# Main page
# ==========================================================

class SqlAutomationPage(QWidget):

    def __init__(self):

        super().__init__()

        self.metadata = MetadataManager()

        self.manager = SqlAutomationManager()

        self.clickup_config = ClickUpConfig()

        self.test_cases = []

        self.build_ui()

        self.load_domains()

    # ------------------------------------------------------
    # UI
    # ------------------------------------------------------

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

        title = QLabel("SQL Automation")

        title.setObjectName("SectionTitle")

        layout.addWidget(title)

        note = QLabel(
            "Read-only by design — only a single SELECT/WITH query is "
            "ever allowed to run, enforced on the server regardless of "
            "what the configured database account itself permits."
        )

        note.setWordWrap(True)

        layout.addWidget(note)

        # ---- Select ----

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

        # ---- Table ----

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

        self.table = QTableWidget(0, 9)

        self.table.setHorizontalHeaderLabels([
            "",
            "TC #",
            "Test Case",
            "Status",
            "Result",
            "SQL",
            "Active Script",
            "AI Auto Generate SQL",
            "ClickUp",
        ])

        # QA-AUTOMATION-FINAL-ARCHITECTURE-04 hidden-bug fix: without
        # this, QTableWidget gives every column the same fixed default
        # width regardless of its header text length, so longer
        # headers ("AI Auto Generate SQL", "Active Script") were
        # rendered clipped/unreadable (confirmed via an actual
        # on-screen screenshot, not just code review) no matter how
        # wide the window is. resizeColumnsToContents() alone fixes
        # the clipping but sizes each column to the bare minimum (no
        # header padding, headers visually touching) and shrinks the
        # blank-header checkbox column (0) to near-invisible — also
        # confirmed via screenshot, so it is not used alone: pad every
        # non-stretch column afterward, and floor the checkbox
        # column's width explicitly.
        self.table.resizeColumnsToContents()

        for col in range(self.table.columnCount()):
            if col == 2:
                continue
            self.table.setColumnWidth(
                col, self.table.columnWidth(col) + 18
            )

        self.table.setColumnWidth(
            0, max(self.table.columnWidth(0), 36)
        )

        self.table.horizontalHeader().setSectionResizeMode(
            2, QHeaderView.Stretch
        )

        self.table.setMinimumHeight(300)

        self.table.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

        table_layout.addWidget(self.table)

        layout.addWidget(table_group)

        # ---- Actions ----

        actions_group = QGroupBox("Actions")

        actions_layout = QHBoxLayout(actions_group)

        actions_layout.setContentsMargins(15, 20, 15, 15)

        self.execute_selected_btn = QPushButton("Execute Selected")

        self.execute_all_btn = QPushButton("Execute All")

        self.environment_settings_btn = QPushButton("Test Environment Setting")

        actions_layout.addWidget(self.execute_selected_btn)

        actions_layout.addWidget(self.execute_all_btn)

        actions_layout.addStretch()

        actions_layout.addWidget(self.environment_settings_btn)

        layout.addWidget(actions_group)

        # ---- Log ----

        log_group = QGroupBox("Execution Log")

        log_layout = QVBoxLayout(log_group)

        log_layout.setContentsMargins(15, 20, 15, 15)

        self.log = QTextEdit()

        self.log.setReadOnly(True)

        self.log.setMinimumHeight(160)

        self.log.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

        log_layout.addWidget(self.log)

        layout.addWidget(log_group)

        # ---- Events ----

        self.domain.currentTextChanged.connect(self.on_domain_changed)

        self.module.currentTextChanged.connect(self.on_module_changed)

        self.load_btn.clicked.connect(self.load_test_cases)

        self.select_all_btn.clicked.connect(lambda: self.set_all_checked(True))

        self.clear_all_btn.clicked.connect(lambda: self.set_all_checked(False))

        self.execute_selected_btn.clicked.connect(lambda: self.execute(only_selected=True))

        self.execute_all_btn.clicked.connect(lambda: self.execute(only_selected=False))

        self.environment_settings_btn.clicked.connect(self.open_environment_settings)

    def open_environment_settings(self):

        dialog = SqlEnvironmentDialog(self)

        if dialog.exec() == QDialog.Accepted:

            self.log.append("SQL Test Environment Setting saved.")

    # ------------------------------------------------------
    # Domain / Module / Knowledge Name cascade
    # ------------------------------------------------------

    def load_domains(self):

        self.domain.blockSignals(True)

        self.domain.clear()

        self.domain.addItems(self.metadata.list_domains())

        self.domain.blockSignals(False)

        self.on_domain_changed(self.domain.currentText())

    def on_domain_changed(self, domain_name):

        self.module.blockSignals(True)

        self.module.clear()

        if domain_name:

            self.module.addItems(self.metadata.list_modules(domain_name))

        self.module.blockSignals(False)

        self.on_module_changed(self.module.currentText())

    def on_module_changed(self, module_name):

        self.knowledge_name.clear()

        domain_name = self.domain.currentText()

        if domain_name and module_name:

            self.knowledge_name.addItems(
                self.metadata.get_knowledge_names(domain_name, module_name)
            )

    # ------------------------------------------------------
    # Load / populate
    # ------------------------------------------------------

    def load_test_cases(self):

        domain = self.domain.currentText().strip()

        module = self.module.currentText().strip()

        knowledge_name = self.knowledge_name.currentText().strip()

        if not (domain and module and knowledge_name):

            QMessageBox.warning(
                self, "QA AI Studio",
                "Select a Domain, Module and Knowledge Name first."
            )

            return

        # Same scoped pool every other tab reads from (QA Engineering's
        # persisted test cases) — filtered to this tab's own type, same
        # rule as the Web port and the Playwright/API tabs.
        all_cases = self.manager.repository.list_test_cases(
            domain, module, knowledge_name
        )

        sql_tool = self.manager.repository.execution_tool_for_automation_type("SQL")
        self.test_cases = [
            tc for tc in all_cases
            if tc.get("execution_type") == "Automatable"
            and tc.get("execution_tool") == sql_tool
        ]

        self.populate_table()

        if not self.test_cases:

            self.log.append(
                f"No SQL test cases found for {domain} / {module} / "
                f"{knowledge_name}."
            )

        else:

            self.log.append(f"Loaded {len(self.test_cases)} SQL test case(s).")

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

        status_item = QTableWidgetItem(test_case.get("status", "Manual"))

        status_item.setFlags(status_item.flags() & ~Qt.ItemIsEditable)

        self.table.setItem(row, 3, status_item)

        result_item = QTableWidgetItem(test_case.get("last_result", "Not Run"))

        result_item.setFlags(result_item.flags() & ~Qt.ItemIsEditable)

        self.table.setItem(row, 4, result_item)

        script_btn = QPushButton("View SQL")

        script = self.manager._unpack_script(test_case.get("automation_script"))

        script_btn.setEnabled(bool(script.get("sql")))

        script_btn.clicked.connect(
            lambda _, tc_id=test_case["id"]: self.view_script(tc_id)
        )

        self.table.setCellWidget(row, 5, script_btn)

        active_item = QTableWidgetItem(
            "Active" if (test_case.get("status") == "Automated") else "Draft"
        )

        active_item.setFlags(active_item.flags() & ~Qt.ItemIsEditable)

        self.table.setItem(row, 6, active_item)

        generate_btn = QPushButton("AI Generate")

        generate_btn.clicked.connect(
            lambda _, tc_id=test_case["id"]: self.view_script(tc_id, open_generate=True)
        )

        self.table.setCellWidget(row, 7, generate_btn)

        clickup_btn = QPushButton("Create Bug")

        clickup_btn.clicked.connect(
            lambda _, tc_id=test_case["id"]: self.create_clickup_bug_for_row(tc_id)
        )

        self.table.setCellWidget(row, 8, clickup_btn)

        self._update_clickup_button(clickup_btn, test_case.get("last_result"))

    # ------------------------------------------------------
    # Selection helpers
    # ------------------------------------------------------

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

    def refresh_row(self, row, test_case):
        """
        Re-reads a single row's Status/Result/SQL-enabled/Active/
        ClickUp cells from a freshly-fetched test case, without
        rebuilding the whole grid (used right after Save/Generate/
        Validate/Execute for that one row).
        """

        self.table.item(row, 3).setText(test_case.get("status", "Manual"))

        self.table.item(row, 4).setText(test_case.get("last_result", "Not Run"))

        script = self.manager._unpack_script(test_case.get("automation_script"))

        self.table.cellWidget(row, 5).setEnabled(bool(script.get("sql")))

        self.table.item(row, 6).setText(
            "Active" if (test_case.get("status") == "Automated") else "Draft"
        )

        self._update_clickup_button(
            self.table.cellWidget(row, 8), test_case.get("last_result")
        )

        for idx, tc in enumerate(self.test_cases):

            if tc["id"] == test_case["id"]:

                self.test_cases[idx] = test_case

                break

    # ------------------------------------------------------
    # View / edit / generate / validate SQL
    # ------------------------------------------------------

    def view_script(self, test_case_id, open_generate=False):

        test_case = self.manager.repository.get_test_case(test_case_id)

        if not test_case:

            QMessageBox.critical(self, "QA AI Studio", "Test case could not be loaded.")

            return

        dialog = SqlScriptDialog(test_case, self.manager, self)

        if open_generate:

            dialog._on_generate()

        dialog.exec()

        refreshed = self.manager.repository.get_test_case(test_case_id)

        for row in range(self.table.rowCount()):

            if self.row_tc_id(row) == test_case_id:

                self.refresh_row(row, refreshed)

                break

    # ------------------------------------------------------
    # Execute
    # ------------------------------------------------------

    def execute(self, only_selected=True):

        rows = self.get_checked_rows() if only_selected else list(range(self.table.rowCount()))

        if not rows:

            QMessageBox.warning(self, "QA AI Studio", "Select at least one test case first.")

            return

        skipped = 0

        for row in rows:

            test_case_id = self.row_tc_id(row)

            tc_number = self.row_tc_number(row)

            try:

                result = self.manager.execute_sql(test_case_id)

            except Exception as ex:

                self.log.append(f"{tc_number}: skipped — {ex}")

                skipped += 1

                continue

            self.log.append(
                f"{tc_number}: {result['outcome']} — "
                f"{result.get('assertion_message') or result.get('error') or ''}"
            )

            refreshed = self.manager.repository.get_test_case(test_case_id)

            self.refresh_row(row, refreshed)

        self.log.append(
            f"Finished executing {len(rows) - skipped} of {len(rows)}."
            + (f" Skipped {skipped}." if skipped else "")
        )

    # ------------------------------------------------------
    # ClickUp — contextual "create bug for a failed test" action only.
    # ------------------------------------------------------

    def _update_clickup_button(self, button, last_result):

        is_fail = (last_result == "Fail")

        button.setEnabled(is_fail)

        button.setVisible(is_fail)

    def create_clickup_bug_for_row(self, test_case_id):

        test_case = self.manager.repository.get_test_case(test_case_id)

        if not test_case:

            QMessageBox.critical(self, "QA AI Studio", "Test case could not be loaded.")

            return

        client = ClickUpClient(self.clickup_config.load())

        result = client.create_bug_task(test_case)

        if not result.get("success"):

            QMessageBox.warning(
                self, "ClickUp", result.get("error", "ClickUp task creation failed.")
            )

            return

        self.log.append(
            f"{test_case.get('tc_number')}: ClickUp bug created — "
            f"{result.get('task_url') or result.get('task_id')}"
        )

        QMessageBox.information(
            self, "ClickUp",
            f"Bug created: {result.get('task_url') or result.get('task_id')}"
        )
