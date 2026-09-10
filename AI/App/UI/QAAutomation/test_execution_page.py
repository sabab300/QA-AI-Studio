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

UPDATE (QA-AUTOMATION-FINAL-ARCHITECTURE-04): the note above is
historical/stale (Playwright and API execution are both real now —
see confirm_and_run_playwright()/handle_api_rows()). Selenium is not
required for this product and was removed as an Automation Type
choice (never had a real runner anyway). SQL Automation is real now
too, but as its OWN dedicated tab/page (see sql_automation_page.py)
with its own read-only-by-design runner — not a type choice in this
grid. This class is now instantiated twice from
qa_automation_hub_page.py — once per Domain/Module/Knowledge-scoped,
type-filtered tab (Playwright-only, API-only) — via the additive
`automation_type_filter` constructor parameter; see its docstring.
==========================================================
"""

import os

from PySide6.QtCore import Qt, QThread

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
    QFileDialog,
    QApplication,
)

from Core.metadata_manager import MetadataManager
from Core.test_environment_config import TestEnvironmentConfig
from Core.playwright_runner import (
    DEFAULT_SLOW_MO_MS,
    DEFAULT_ACTION_TIMEOUT_MS,
)

from Core.test_execution_manager import TestExecutionManager

# ==========================================================
# TEMPORARY: ClickUp integration disabled for Desktop startup.
# Restore real imports when ClickUp backend modules are available.
# ==========================================================

# from Core.clickup_config import ClickUpConfig
# from Core.clickup_client import ClickUpClient

class ClickUpConfig:
    def load(self):
        return {
            "clickup_api_token": "",
            "clickup_list_id": "",
        }

    def save(self, **kwargs):
        return None


class ClickUpClient:
    def __init__(self, config=None):
        self.config = config or {}

    def create_bug_task(self, test_case):
        return {
            "success": False,
            "error": "ClickUp integration is temporarily disabled."
        }




from UI.QAAutomation.test_execution_worker import (
    AutomationGenerationWorker,
    AutomationSuggestionWorker,
    PlaywrightExecutionWorker,
    PlaywrightInteractiveWorker,
    AiLocatorSuggestionWorker,
    ManualRecordingWorker,
)

AUTOMATION_TYPES = [
    "None",
    "Playwright",
    "API",
]
# QA-AUTOMATION-FINAL-ARCHITECTURE-04: Selenium is not required for
# this product and is removed as an option entirely (it never had a
# real runner anyway — see handle_automated_rows()'s old
# "unsupported" branch below). SQL is no longer offered from this
# grid's per-row Automation Type combo either — SQL Automation is now
# its own dedicated tab/page (see sql_automation_page.py), with its
# own grid, safety-gated runner and Test Environment Setting, instead
# of a silently-unsupported entry mixed into this one. Any test case
# a prior version already saved with automation_type 'Selenium' or
# 'SQL' still loads and displays correctly here (see add_row()) — this
# only removes them as a choice for NEW automation going forward.

RESULT_OPTIONS = [
    "Pass",
    "Fail",
    "Blocked",
]

TC_ID_ROLE = Qt.UserRole

DEFAULT_SLOW_MO_DISPLAY = f"{DEFAULT_SLOW_MO_MS}ms"

DEFAULT_TIMEOUT_DISPLAY = f"{DEFAULT_ACTION_TIMEOUT_MS}ms"


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

    def __init__(self, tc_number, test_case_text, suggestion, parent=None, allowed_types=None):

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

        # QA-AUTOMATION-FINAL-ARCHITECTURE-04: Selenium/SQL removed as
        # choices here too (see AUTOMATION_TYPES' own comment above).
        # `allowed_types` lets a filtered (Playwright-only / API-only)
        # tab restrict this to just its own type, so accepting the AI's
        # suggestion here can never generate the OTHER tab's type of
        # script. Falls back to both real types when unset.
        combo_items = list(allowed_types) if allowed_types else ["Playwright", "API"]

        suggested = suggestion["suggested_type"]

        if suggested not in combo_items:

            combo_items = combo_items + [suggested]

        self.type_combo.addItems(combo_items)

        self.type_combo.setCurrentText(suggested)

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

API_AUTH_TYPES = [
    "None", "Bearer Token", "API Key Header", "Basic Auth",
]


class EnvironmentSettingsDialog(QDialog):

    def __init__(self, config_manager, parent=None):

        super().__init__(parent)

        self.config_manager = config_manager

        self.setWindowTitle("Test Environment Settings")

        self.resize(560, 720)

        outer_layout = QVBoxLayout(self)

        scroll = QScrollArea()

        scroll.setWidgetResizable(True)

        outer_layout.addWidget(scroll)

        scroll_body = QWidget()

        scroll.setWidget(scroll_body)

        layout = QVBoxLayout(scroll_body)

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

        self.slow_mo_ms = QLineEdit()

        self.slow_mo_ms.setPlaceholderText(
            f"default {DEFAULT_SLOW_MO_DISPLAY} — higher = slower "
            f"but more reliable"
        )

        self.default_timeout_ms = QLineEdit()

        self.default_timeout_ms.setPlaceholderText(
            f"default {DEFAULT_TIMEOUT_DISPLAY} — how long to wait "
            f"for an element before giving up"
        )


        grid.addWidget(QLabel("Base URL"), 0, 0)

        grid.addWidget(self.base_url, 0, 1)

        grid.addWidget(QLabel("Username"), 1, 0)

        grid.addWidget(self.username, 1, 1)

        grid.addWidget(QLabel("Password"), 2, 0)

        grid.addWidget(self.password, 2, 1)

        grid.addWidget(QLabel("Notes"), 3, 0)

        grid.addWidget(self.notes, 3, 1)

        grid.addWidget(QLabel("Playback Speed (ms)"), 4, 0)

        grid.addWidget(self.slow_mo_ms, 4, 1)

        grid.addWidget(QLabel("Default Timeout (ms)"), 5, 0)

        grid.addWidget(self.default_timeout_ms, 5, 1)


        layout.addLayout(grid)

        speed_note = QLabel(
            "If Execute keeps failing because the real application "
            "is slower than Playwright expects, raise these instead "
            "of blaming the script: Playback Speed pauses after "
            "every click/type/navigate (try 500-1000ms), and Default "
            "Timeout is how long Playwright waits for something to "
            "appear before giving up (try 60000ms). Leave both blank "
            f"to use the defaults ({DEFAULT_SLOW_MO_DISPLAY} / "
            f"{DEFAULT_TIMEOUT_DISPLAY}). Applies to every script — "
            "AI-generated or manually recorded — the next time it "
            "runs, with no need to regenerate or re-record anything."
        )

        speed_note.setWordWrap(True)

        speed_note.setStyleSheet("color: #64748B;")

        layout.addWidget(speed_note)

        # --------------------------------------------------
        # API Automation ("Execute Against Real Server") settings —
        # everything ApiAutomationRunner needs to actually send a
        # real request instead of the app only ever producing an
        # Ollama-generated script text nobody runs. See
        # Core/api_automation_runner.py.
        # --------------------------------------------------

        api_group = QGroupBox("API Automation — Execute Against Real Server")

        api_layout = QVBoxLayout(api_group)

        api_note = QLabel(
            "Used only when you choose 'Execute Against Real "
            "Server' for an API-type test case or endpoint — this "
            "actually sends the request, so use a TEST/UAT server "
            "and a TEST auth token/account here, not production."
        )

        api_note.setWordWrap(True)

        api_layout.addWidget(api_note)

        api_grid = QGridLayout()

        api_grid.setColumnStretch(1, 1)

        self.api_base_url_override = QLineEdit()

        self.api_base_url_override.setPlaceholderText(
            "optional — overrides just the host, e.g. "
            "https://uat.psw.gov.pk (leave blank to call the "
            "endpoint's own imported URL as-is)"
        )

        self.api_auth_type = QComboBox()

        self.api_auth_type.addItems(API_AUTH_TYPES)

        self.api_auth_token = QLineEdit()

        self.api_auth_token.setEchoMode(QLineEdit.Password)

        self.api_auth_header_name = QLineEdit()

        self.api_auth_header_name.setPlaceholderText("e.g. X-API-Key")

        self.api_auth_header_value = QLineEdit()

        self.api_auth_header_value.setEchoMode(QLineEdit.Password)

        self.api_username = QLineEdit()

        self.api_password = QLineEdit()

        self.api_password.setEchoMode(QLineEdit.Password)

        self.api_timeout_seconds = QLineEdit()

        self.api_timeout_seconds.setPlaceholderText("default 30")

        self.api_verify_ssl = QCheckBox(
            "Verify SSL certificates (turn off only for a test "
            "server with a self-signed certificate)"
        )

        api_grid.addWidget(QLabel("Base URL Override"), 0, 0)

        api_grid.addWidget(self.api_base_url_override, 0, 1)

        api_grid.addWidget(QLabel("Auth Type"), 1, 0)

        api_grid.addWidget(self.api_auth_type, 1, 1)

        bearer_token_label = QLabel("Bearer Token")

        api_grid.addWidget(bearer_token_label, 2, 0)

        api_grid.addWidget(self.api_auth_token, 2, 1)

        api_key_name_label = QLabel("API Key Header Name")

        api_grid.addWidget(api_key_name_label, 3, 0)

        api_grid.addWidget(self.api_auth_header_name, 3, 1)

        api_key_value_label = QLabel("API Key Header Value")

        api_grid.addWidget(api_key_value_label, 4, 0)

        api_grid.addWidget(self.api_auth_header_value, 4, 1)

        basic_username_label = QLabel("Basic Auth Username")

        api_grid.addWidget(basic_username_label, 5, 0)

        api_grid.addWidget(self.api_username, 5, 1)

        basic_password_label = QLabel("Basic Auth Password")

        api_grid.addWidget(basic_password_label, 6, 0)

        api_grid.addWidget(self.api_password, 6, 1)

        # Kept keyed by the SAME field this label belongs next to, so
        # _update_api_auth_field_visibility() can toggle each
        # label/field pair together — a visible field with no label
        # (or vice versa) would look broken.
        self._api_auth_field_labels = {
            id(self.api_auth_token): bearer_token_label,
            id(self.api_auth_header_name): api_key_name_label,
            id(self.api_auth_header_value): api_key_value_label,
            id(self.api_username): basic_username_label,
            id(self.api_password): basic_password_label,
        }

        api_grid.addWidget(QLabel("Timeout (seconds)"), 7, 0)

        api_grid.addWidget(self.api_timeout_seconds, 7, 1)

        api_layout.addLayout(api_grid)

        api_layout.addWidget(self.api_verify_ssl)

        self._api_auth_rows = {
            "Bearer Token": [2],
            "API Key Header": [3, 4],
            "Basic Auth": [5, 6],
        }

        self.api_auth_type.currentTextChanged.connect(
            self._update_api_auth_field_visibility
        )

        api_layout.addWidget(QLabel("Extra Headers (sent on every request)"))

        self.api_headers_table = QTableWidget(0, 2)

        self.api_headers_table.setHorizontalHeaderLabels(["Key", "Value"])

        self.api_headers_table.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.Stretch
        )

        self.api_headers_table.setMinimumHeight(100)

        api_layout.addWidget(self.api_headers_table)

        api_header_buttons = QHBoxLayout()

        add_api_header_btn = QPushButton("Add Header")

        add_api_header_btn.clicked.connect(
            lambda: self._add_api_header_row()
        )

        remove_api_header_btn = QPushButton("Remove Selected")

        remove_api_header_btn.clicked.connect(
            self._remove_api_header_row
        )

        api_header_buttons.addWidget(add_api_header_btn)

        api_header_buttons.addWidget(remove_api_header_btn)

        api_header_buttons.addStretch()

        api_layout.addLayout(api_header_buttons)

        api_layout.addWidget(
            QLabel(
                "Remembered {{variable}} Values (same idea as a "
                "Postman Environment — view, edit, add, or remove "
                "individual values here; a real API run only asks "
                "for one the first time it's needed)"
            )
        )

        self.api_variables_table = QTableWidget(0, 2)

        self.api_variables_table.setHorizontalHeaderLabels(
            ["Variable Name", "Value"]
        )

        self.api_variables_table.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.Stretch
        )

        self.api_variables_table.setMinimumHeight(100)

        api_layout.addWidget(self.api_variables_table)

        api_variable_buttons = QHBoxLayout()

        add_api_variable_btn = QPushButton("Add Variable")

        add_api_variable_btn.clicked.connect(
            lambda: self._add_api_variable_row()
        )

        remove_api_variable_btn = QPushButton("Remove Selected")

        remove_api_variable_btn.clicked.connect(
            self._remove_api_variable_row
        )

        clear_variables_btn = QPushButton("Remove All")

        clear_variables_btn.clicked.connect(
            self._clear_remembered_variables
        )

        api_variable_buttons.addWidget(add_api_variable_btn)

        api_variable_buttons.addWidget(remove_api_variable_btn)

        api_variable_buttons.addWidget(clear_variables_btn)

        api_variable_buttons.addStretch()

        api_layout.addLayout(api_variable_buttons)

        layout.addWidget(api_group)


        data = self.config_manager.load()

        self.base_url.setText(data.get("base_url", ""))

        self.username.setText(data.get("username", ""))

        self.password.setText(data.get("password", ""))

        self.notes.setText(data.get("notes", ""))

        self.slow_mo_ms.setText(data.get("slow_mo_ms", ""))

        self.default_timeout_ms.setText(
            data.get("default_timeout_ms", "")
        )

        self.api_base_url_override.setText(
            data.get("api_base_url_override", "")
        )

        auth_index = self.api_auth_type.findText(
            data.get("api_auth_type") or "None"
        )

        self.api_auth_type.setCurrentIndex(max(auth_index, 0))

        self.api_auth_token.setText(data.get("api_auth_token", ""))

        self.api_auth_header_name.setText(
            data.get("api_auth_header_name", "")
        )

        self.api_auth_header_value.setText(
            data.get("api_auth_header_value", "")
        )

        self.api_username.setText(data.get("api_username", ""))

        self.api_password.setText(data.get("api_password", ""))

        self.api_timeout_seconds.setText(
            str(data.get("api_timeout_seconds", "") or "")
        )

        self.api_verify_ssl.setChecked(
            data.get("api_verify_ssl", True)
        )

        for key, value in (data.get("api_extra_headers") or {}).items():

            self._add_api_header_row(key, value)

        self._update_api_auth_field_visibility(
            self.api_auth_type.currentText()
        )

        self._load_api_variables_table(data.get("api_variables") or {})

        # ClickUp is a single global integration (not per Domain/
        # Module), used only for the contextual "Create Bug" action on
        # FAILED rows across all 3 QA Automation tabs — configured
        # once, here, rather than duplicated per tab. Mirrors the Web
        # port's ClickUp Integration card in its Test Environment
        # Setting modal.
        clickup_group = QGroupBox("ClickUp Integration")

        clickup_layout = QFormLayout(clickup_group)

        self.clickup_config = ClickUpConfig()

        self.clickup_api_token = QLineEdit()

        self.clickup_api_token.setEchoMode(QLineEdit.Password)

        self.clickup_list_id = QLineEdit()

        clickup_layout.addRow("API token", self.clickup_api_token)

        clickup_layout.addRow("List ID", self.clickup_list_id)

        layout.addWidget(clickup_group)

        clickup_data = self.clickup_config.load()

        self.clickup_api_token.setText(clickup_data.get("clickup_api_token", ""))

        self.clickup_list_id.setText(clickup_data.get("clickup_list_id", ""))


        buttons = QDialogButtonBox(
            QDialogButtonBox.Save | QDialogButtonBox.Cancel
        )

        buttons.accepted.connect(self.save)

        buttons.rejected.connect(self.reject)

        layout.addWidget(buttons)

    def _update_api_auth_field_visibility(self, auth_type):

        active_rows = set(self._api_auth_rows.get(auth_type, []))

        # Toggling row visibility on a QGridLayout means hiding each
        # widget individually — there's no single "hide row" call.
        # Each field's label is hidden/shown right alongside it, so
        # there's never a visible label with no field (or vice
        # versa) next to it.
        for field, row in (
            (self.api_auth_token, 2),
            (self.api_auth_header_name, 3),
            (self.api_auth_header_value, 4),
            (self.api_username, 5),
            (self.api_password, 6),
        ):

            visible = row in active_rows

            field.setVisible(visible)

            self._api_auth_field_labels[id(field)].setVisible(visible)

    def _add_api_header_row(self, key="", value=""):

        row = self.api_headers_table.rowCount()

        self.api_headers_table.insertRow(row)

        self.api_headers_table.setItem(row, 0, QTableWidgetItem(key))

        self.api_headers_table.setItem(row, 1, QTableWidgetItem(value))

    def _remove_api_header_row(self):

        rows = sorted(
            {
                index.row()
                for index in self.api_headers_table.selectedIndexes()
            },
            reverse=True,
        )

        for row in rows:

            self.api_headers_table.removeRow(row)

    def _collect_api_headers(self):

        headers = {}

        for row in range(self.api_headers_table.rowCount()):

            key_item = self.api_headers_table.item(row, 0)

            value_item = self.api_headers_table.item(row, 1)

            key = key_item.text().strip() if key_item else ""

            if not key:

                continue

            headers[key] = value_item.text() if value_item else ""

        return headers

    def _load_api_variables_table(self, variables):

        self.api_variables_table.setRowCount(0)

        for name, value in sorted(variables.items()):

            self._add_api_variable_row(name, value)

    def _add_api_variable_row(self, name="", value=""):

        row = self.api_variables_table.rowCount()

        self.api_variables_table.insertRow(row)

        self.api_variables_table.setItem(row, 0, QTableWidgetItem(name))

        self.api_variables_table.setItem(row, 1, QTableWidgetItem(value))

    def _remove_api_variable_row(self):

        rows = sorted(
            {
                index.row()
                for index in self.api_variables_table.selectedIndexes()
            },
            reverse=True,
        )

        for row in rows:

            self.api_variables_table.removeRow(row)

    def _collect_api_variables(self):

        variables = {}

        for row in range(self.api_variables_table.rowCount()):

            name_item = self.api_variables_table.item(row, 0)

            value_item = self.api_variables_table.item(row, 1)

            name = name_item.text().strip() if name_item else ""

            if not name:

                continue

            variables[name] = value_item.text() if value_item else ""

        return variables

    def _clear_remembered_variables(self):

        confirm = QMessageBox.question(
            self,
            "Remove All Remembered Values?",
            "This clears every {{variable}} value remembered from "
            "past API runs — you'll be asked again the next time "
            "each one is needed. Continue?",
            QMessageBox.Yes | QMessageBox.No,
        )

        if confirm != QMessageBox.Yes:

            return

        self.api_variables_table.setRowCount(0)

    def save(self):

        slow_mo_text = self.slow_mo_ms.text().strip()

        timeout_text = self.default_timeout_ms.text().strip()

        for label, value in (
            ("Playback Speed", slow_mo_text),
            ("Default Timeout", timeout_text),
        ):

            if value and (not value.isdigit() or int(value) < 0):

                QMessageBox.warning(
                    self,
                    "Invalid Value",
                    f"{label} must be a whole number of "
                    f"milliseconds (0 or higher), or left blank to "
                    f"use the default. Got: '{value}'"
                )

                return

        api_timeout_text = self.api_timeout_seconds.text().strip()

        if api_timeout_text:

            try:

                if float(api_timeout_text) <= 0:

                    raise ValueError

            except ValueError:

                QMessageBox.warning(
                    self,
                    "Invalid Value",
                    f"API Timeout must be a positive number of "
                    f"seconds, or left blank to use the default "
                    f"(30s). Got: '{api_timeout_text}'"
                )

                return

        self.config_manager.save(
            base_url=self.base_url.text().strip(),
            username=self.username.text().strip(),
            password=self.password.text(),
            notes=self.notes.text().strip(),
            slow_mo_ms=slow_mo_text,
            default_timeout_ms=timeout_text,
            api_auth_type=self.api_auth_type.currentText(),
            api_auth_token=self.api_auth_token.text(),
            api_auth_header_name=self.api_auth_header_name.text().strip(),
            api_auth_header_value=self.api_auth_header_value.text(),
            api_username=self.api_username.text().strip(),
            api_password=self.api_password.text(),
            api_extra_headers=self._collect_api_headers(),
            api_timeout_seconds=api_timeout_text,
            api_verify_ssl=self.api_verify_ssl.isChecked(),
            api_base_url_override=self.api_base_url_override.text().strip(),
            api_variables=self._collect_api_variables(),
        )

        self.clickup_config.save(
            clickup_api_token=self.clickup_api_token.text(),
            clickup_list_id=self.clickup_list_id.text().strip(),
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

        if self.automation_type == "API":

            api_note = QLabel(
                "IMPORTANT: this script is for reference / manual "
                "review only. \"Execute Against Real Server\" does "
                "NOT run this Python code and does NOT read any edit "
                "you make here — it always rebuilds the request "
                "fresh from the ORIGINAL imported endpoint (method, "
                "URL, headers, body), exactly as captured when the "
                "API Collection was imported. To change what "
                "actually gets sent (e.g. a signature, a header, or "
                "a body value), edit the endpoint itself: Knowledge "
                "Hub -> Manage Knowledge -> find it under its API "
                "Collection -> Edit. A value you'll update often "
                "(like a signature) can be set to {{variableName}} "
                "there, then updated anytime via Settings -> Test "
                "Environment Settings -> Remembered {{variable}} "
                "Values, without re-editing the endpoint each time."
            )

            api_note.setWordWrap(True)

            api_note.setStyleSheet(
                "color: #92400E; font-weight: bold; "
                "background-color: #FEF3C7; padding: 8px; "
                "border-radius: 4px;"
            )

            layout.addWidget(api_note)

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

        buttons = QDialogButtonBox()

        self.validate_btn = buttons.addButton(
            "Validate", QDialogButtonBox.ActionRole
        )
        self.save_btn = buttons.addButton(
            "Save Script", QDialogButtonBox.ActionRole
        )

        self.validate_btn.clicked.connect(self.validate_current_script)
        self.save_btn.clicked.connect(self.save)

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

        # QA-AUTOMATION-FINAL-ARCHITECTURE-04 hidden-bug fix: this
        # method — the actual gate that flips a script to "Active for
        # Execution" — previously never validated the script at all.
        # save() only *warns* on a syntax error and still lets you
        # save it (by design — Draft scripts may be invalid), but
        # nothing stopped that same broken script from then being set
        # Active here, straight from whatever was already persisted.
        # Confirmed this was a real, live gap by finding genuinely
        # broken persisted scripts on disk (AI/App/Output/AutomationRuns
        # /TC003_*.py — "unterminated string literal"; TC001_*
        # _interactive.py — "unexpected indent"). Per this task's
        # explicit rule ("invalid scripts may stay Draft but must
        # NEVER become Active"), block Active here on a real syntax
        # error instead of only warning.

        script_text = self._script_for(source)

        if script_text.strip():

            error = self.manager.check_script_syntax(script_text)

            if error:

                QMessageBox.critical(
                    self,
                    "Cannot Set Active",
                    f"This script has a Python syntax problem and "
                    f"cannot be made Active for Execution:\n\n"
                    f"{error}\n\n"
                    f"Fix it and save first, then try again.",
                )

                return

        try:

            self.manager.set_active_script(self.test_case_id, source)

            self.test_case["active_script_source"] = source

            self._load_current_source()

        except Exception as ex:

            QMessageBox.critical(self, "Could Not Set Active", str(ex))

    def validate_current_script(self):

        script_text = self.editor.toPlainText()

        if not script_text.strip():

            QMessageBox.warning(
                self,
                "Validation Failed",
                "The selected script is empty.",
            )

            return

        if self.automation_type == "Playwright":

            error = self.manager.check_script_syntax(script_text)

            if error:

                QMessageBox.critical(
                    self,
                    "Validation Failed",
                    f"Script validation failed:\n\n{error}",
                )

                return

        QMessageBox.information(
            self,
            "Validation Passed",
            "The selected script passed validation.\n\n"
            "Use 'Set as Active for Execution' when you want this "
            "saved source to be the one that Execute runs.",
        )

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

            QMessageBox.information(
                self,
                "Script Saved",
                "The selected script was saved successfully.",
            )
            self._load_current_source()

        except Exception as ex:

            QMessageBox.critical(
                self, "Save Failed", str(ex)
            )


class LocatorRepairDialog(QDialog):
    """
    Shown when a Playwright step can't find its Locator/element
    during an interactive Execute run — the direct fix for
    "Playwright gets stuck with no way to correct it and keep
    going." Lets the operator correct the Locator and/or its value
    directly, drop into an Advanced full-code edit for fixes a
    locator alone can't cover (e.g. switching a failed .fill() /
    .click() to .select_option() for a dropdown, or to a role-based
    click for a calendar day), ask the local AI for a suggestion
    grounded in the real page right now, or cancel the whole run.

    self.decision is read by the caller (TestExecutionPage.
    on_step_failed()) after exec() returns — it defaults to
    {"action": "cancel"} so closing the dialog any way other than a
    real button (the X button, Escape) never leaves the background
    thread blocked waiting forever.
    """

    def __init__(self, failure_event, manager, test_case, parent=None):

        super().__init__(parent)

        self.failure_event = failure_event

        self.manager = manager

        self.test_case = test_case

        self.decision = {"action": "cancel"}

        self.ai_thread = None

        self.ai_worker = None

        self._last_ai_code = ""

        self.setWindowTitle(
            f"Step {failure_event.get('step')} Failed — Element Not Found"
        )

        self.resize(560, 520)

        self.setModal(True)

        layout = QVBoxLayout(self)

        attempt = failure_event.get("attempt", 1)

        title = QLabel(
            f"Step {failure_event.get('step')} couldn't find its element"
            + (f" — attempt {attempt}" if attempt > 1 else "")
        )

        title.setStyleSheet("font-weight: bold; font-size: 13px;")

        layout.addWidget(title)

        layout.addWidget(QLabel("Failing step:"))

        code_view = QPlainTextEdit(failure_event.get("code", ""))

        code_view.setReadOnly(True)

        code_view.setMaximumHeight(50)

        layout.addWidget(code_view)

        error_label = QLabel(f"Error: {failure_event.get('error', '')}")

        error_label.setWordWrap(True)

        error_label.setStyleSheet("color: #B91C1C;")

        layout.addWidget(error_label)

        context_label = QLabel(
            f"Page: {failure_event.get('title', '') or '(unknown)'}  "
            f"({failure_event.get('url', '')})"
        )

        context_label.setWordWrap(True)

        context_label.setStyleSheet("color: #64748B;")

        layout.addWidget(context_label)

        form = QFormLayout()

        self.locator_field = QLineEdit(failure_event.get("locator", ""))

        self.value_field = QLineEdit(failure_event.get("value", ""))

        # Tracked explicitly rather than read back via
        # self.value_field.isVisible() later — a widget only reports
        # itself as visible once the dialog is actually on screen,
        # which makes isVisible() an unreliable/untestable proxy for
        # "does this step have a value field" at the moment a button
        # handler runs.
        self.has_value_field = bool(failure_event.get("value"))

        form.addRow("Locator:", self.locator_field)

        if self.has_value_field:

            form.addRow("Value:", self.value_field)

        else:

            self.value_field.setVisible(False)

        layout.addLayout(form)

        self.advanced_checkbox = QCheckBox(
            "Advanced: edit this step's full code directly (needed "
            "for e.g. a dropdown that needs select_option() instead "
            "of fill()/click(), or a calendar day that needs a "
            "role-based click)"
        )

        self.advanced_checkbox.toggled.connect(self._toggle_advanced)

        layout.addWidget(self.advanced_checkbox)

        self.code_edit = QPlainTextEdit(failure_event.get("code", ""))

        self.code_edit.setMaximumHeight(60)

        self.code_edit.setVisible(False)

        layout.addWidget(self.code_edit)

        # -------- AI suggestion area (hidden until requested) -----

        self.ai_status_label = QLabel("")

        self.ai_status_label.setWordWrap(True)

        self.ai_status_label.setVisible(False)

        layout.addWidget(self.ai_status_label)

        self.ai_suggestion_view = QPlainTextEdit()

        self.ai_suggestion_view.setReadOnly(True)

        self.ai_suggestion_view.setMaximumHeight(60)

        self.ai_suggestion_view.setVisible(False)

        layout.addWidget(self.ai_suggestion_view)

        self.ai_explanation_label = QLabel("")

        self.ai_explanation_label.setWordWrap(True)

        self.ai_explanation_label.setStyleSheet("color: #64748B;")

        self.ai_explanation_label.setVisible(False)

        layout.addWidget(self.ai_explanation_label)

        # -------- buttons -------------------------------------

        button_row = QHBoxLayout()

        self.retry_btn = QPushButton("Retry With This Fix")

        self.retry_btn.clicked.connect(self._on_retry)

        self.ai_btn = QPushButton("Ask AI for a Suggestion")

        self.ai_btn.clicked.connect(self._on_ask_ai)

        self.apply_ai_btn = QPushButton("Apply AI Suggestion")

        self.apply_ai_btn.setVisible(False)

        self.apply_ai_btn.clicked.connect(self._on_apply_ai_suggestion)

        self.cancel_btn = QPushButton("Cancel Test Run")

        self.cancel_btn.setStyleSheet(
            "background-color: #DC2626; color: white;"
        )

        self.cancel_btn.clicked.connect(self._on_cancel)

        button_row.addWidget(self.retry_btn)

        button_row.addWidget(self.ai_btn)

        button_row.addWidget(self.apply_ai_btn)

        button_row.addStretch()

        button_row.addWidget(self.cancel_btn)

        layout.addLayout(button_row)

    def _toggle_advanced(self, checked):

        self.code_edit.setVisible(checked)

        self.locator_field.setEnabled(not checked)

        self.value_field.setEnabled(not checked)

    def _on_retry(self):

        if self.advanced_checkbox.isChecked():

            self.decision = {
                "action": "retry_code",
                "code": self.code_edit.toPlainText().strip(),
            }

        else:

            self.decision = {
                "action": "retry",
                "locator": self.locator_field.text(),
                "value": (
                    self.value_field.text()
                    if self.has_value_field
                    else self.failure_event.get("value", "")
                ),
            }

        self.accept()

    def _on_cancel(self):

        self.decision = {"action": "cancel"}

        self.reject()

    def _on_ask_ai(self):
        """
        Only ever reachable by the operator clicking this button —
        the AI is never called automatically on the first failure,
        only if/when the operator decides a manual fix isn't coming
        quickly (matching "if not found so AI Analysis..." — human
        attempt first, AI only after).
        """

        self.ai_btn.setEnabled(False)

        self.ai_status_label.setText(
            "Asking the local AI model for a suggestion — this can "
            "take a few seconds..."
        )

        self.ai_status_label.setVisible(True)

        self.ai_thread = QThread()

        self.ai_worker = AiLocatorSuggestionWorker(
            self.manager, self.failure_event, self.test_case
        )

        self.ai_worker.moveToThread(self.ai_thread)

        self.ai_thread.started.connect(self.ai_worker.run)

        self.ai_worker.finished.connect(self._on_ai_suggestion_ready)

        self.ai_worker.error.connect(self._on_ai_suggestion_error)

        self.ai_worker.finished.connect(self.ai_thread.quit)

        self.ai_worker.error.connect(self.ai_thread.quit)

        self.ai_thread.finished.connect(self._cleanup_ai_thread)

        self.ai_thread.start()

    def _on_ai_suggestion_ready(self, result):

        self.ai_btn.setEnabled(True)

        if not result.get("success"):

            self.ai_status_label.setText(
                f"AI suggestion failed: "
                f"{result.get('error', 'unknown error')}. You can "
                f"try again or fix it manually above."
            )

            return

        self.ai_status_label.setText(
            "AI suggestion (grounded in the real page right now):"
        )

        self._last_ai_code = result.get("corrected_code", "")

        self.ai_suggestion_view.setPlainText(self._last_ai_code)

        self.ai_suggestion_view.setVisible(True)

        self.ai_explanation_label.setText(
            result.get("explanation", "")
        )

        self.ai_explanation_label.setVisible(True)

        self.apply_ai_btn.setVisible(True)

    def _on_ai_suggestion_error(self, message):

        self.ai_btn.setEnabled(True)

        self.ai_status_label.setText(f"AI suggestion failed: {message}")

    def _on_apply_ai_suggestion(self):

        # Never execute an AI suggestion blindly. Load it into the
        # Advanced editor so the operator can inspect/adjust the exact
        # Playwright statement, then explicitly press Retry With This Fix.
        self.advanced_checkbox.setChecked(True)
        self.code_edit.setPlainText(self._last_ai_code)
        self.code_edit.setVisible(True)
        self.locator_field.setEnabled(False)
        self.value_field.setEnabled(False)
        self.ai_status_label.setText(
            "AI suggestion loaded into Advanced edit. Review the scoped "
            "locator/code, then press Retry With This Fix to continue the "
            "same paused run."
        )
        self.retry_btn.setFocus()

    def _cleanup_ai_thread(self):

        if self.ai_thread:

            self.ai_thread.deleteLater()

        self.ai_thread = None

        self.ai_worker = None


# ==========================================================
# Main Page
# ==========================================================

class TestExecutionPage(QWidget):

    def __init__(self, automation_type_filter=None):
        """
        `automation_type_filter`: QA-AUTOMATION-FINAL-ARCHITECTURE-04
        — additive, backward-compatible (default None = every prior
        caller/behavior unchanged). When set to "Playwright" or "API",
        this instance only loads/shows/generates/executes test cases
        of that type (a blank/"None" automation_type counts as
        Playwright-eligible, same rule the Web port uses), and its
        per-row Automation Type combo only offers that type — never a
        different one — so qa_automation_hub_page.py can host one
        Playwright-only and one API-only instance of this same page as
        two of the 3 required tabs, without duplicating this file's
        ~3700 lines of grid/generation/execution business logic, and
        without a tab silently mixing in another type's test cases or
        scripts.
        """

        super().__init__()

        self.automation_type_filter = automation_type_filter

        # QA-AUTOMATION-FINAL-ARCHITECTURE-04: ClickUp is a contextual,
        # per-row action on FAILED test cases only (never a standalone
        # tab) — see the "ClickUp" grid column in add_row() and
        # create_clickup_bug_for_row() below. One global config, same
        # as the Web port's ClickUpConfig.
        self.clickup_config = ClickUpConfig()

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

                # Whether the run.next_execution() is about to start offers
        # interactive locator repair — recomputed fresh for EVERY
        # test case in run_next_execution() (see
        # _non_interactive_test_case_ids below), never just set once
        # for the whole batch. Defaults to False here only because
        # no run is in progress yet.
        self._interactive_eligible = False

        # Test case ids that already proved, THIS run, that their
        # script's structure doesn't support interactive step-by-step
        # repair (see on_execution_finished()'s
        # "interactive_supported is False" branch) — those specific
        # test cases fall back to a plain run, but every OTHER queued
        # test case still gets interactive repair regardless of how
        # many are queued. Reset at the start of every new batch by
        # confirm_and_run_playwright().
        self._non_interactive_test_case_ids = set()

        # Accumulates {"step", "original", "corrected"} entries for
        # the CURRENT interactive run, so on_execution_finished() can
        # offer to save them into the stored script once the run
        # completes successfully.
        self.pending_repairs = []
        self.repair_persistence_errors = []

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


        # QA-AUTOMATION-FINAL-ARCHITECTURE-04: this class now backs two
        # distinct tabs (Playwright-only, API-only) via
        # automation_type_filter — show a title matching whichever one
        # is actually active instead of a generic label on both.
        if self.automation_type_filter == "Playwright":
            title_text = "Playwright Automation"
        elif self.automation_type_filter == "API":
            title_text = "API Automation"
        else:
            title_text = "Test Execution Automation"

        title = QLabel(title_text)

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


        self.table = QTableWidget(0, 11)

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
            "ClickUp",
        ])

        # QA-AUTOMATION-FINAL-ARCHITECTURE-04 hidden-bug fix: without
        # this, QTableWidget gives every column the same fixed default
        # width regardless of its header text length, so longer
        # headers ("Automation Type", "Active Script") were rendered
        # clipped/unreadable (confirmed via an actual on-screen
        # screenshot, not just code review) no matter how wide the
        # window is. Pre-existing on this grid since before this task;
        # same fix applied to sql_automation_page.py's new grid for
        # consistency across all 3 tabs. resizeColumnsToContents()
        # alone fixes the clipping but sizes each column to the bare
        # minimum (no header padding, headers visually touching) and
        # shrinks the blank-header checkbox column (0) to
        # near-invisible — also confirmed via screenshot, so it is not
        # used alone: pad every non-stretch column afterward, and
        # floor the checkbox column's width explicitly.
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

        # QA Automation used to only ever see test cases that came
        # out of AI generation, purely because that was the only
        # thing that ever wrote a row into test_cases — these two
        # give it two more ways in: a hand-written Excel sheet of
        # test cases, or an imported API collection with no test
        # case at all yet (see TestExecutionManager.
        # import_test_cases_from_excel() /
        # generate_automation_from_collection()).
        self.import_test_cases_btn = QPushButton(
            "Import Test Cases (Excel)"
        )

        self.generate_from_collection_btn = QPushButton(
            "Generate Automation from API Collection"
        )

        self.environment_settings_btn = QPushButton(
            "Test Environment Settings"
        )

        self.cancel_recording_btn = QPushButton("Cancel Recording")

        self.cancel_recording_btn.setVisible(False)

        self.cancel_recording_btn.setStyleSheet(
            "background-color: #DC2626; color: white;"
        )

        # Shown/enabled whenever the CURRENT test case in the queue
        # is running interactively — see confirm_and_run_playwright()
        # and run_next_execution(). Every test case in a run gets
        # interactive repair by default now, batch or not; this only
        # hides itself for the rare test case whose script structure
        # doesn't support interactive repair (see
        # on_execution_finished()'s fallback).
        self.cancel_execution_btn = QPushButton("Cancel Execution")

        self.cancel_execution_btn.setVisible(False)

        self.cancel_execution_btn.setStyleSheet(
            "background-color: #DC2626; color: white;"
        )


        actions_layout.addWidget(self.execute_selected_btn)

        actions_layout.addWidget(self.execute_all_btn)

        actions_layout.addWidget(self.add_automation_btn)

        actions_layout.addWidget(self.update_automation_btn)

        actions_layout.addWidget(self.import_test_cases_btn)

        actions_layout.addWidget(self.generate_from_collection_btn)

        # "Generate Automation from API Collection" only makes sense
        # on the API-only tab (it auto-creates API test cases straight
        # from imported endpoints) — hidden, not just disabled, on the
        # Playwright-only tab so that tab's action row isn't cluttered
        # with a button that can never apply to it. Left visible on an
        # unfiltered instance (backward-compatible default).
        if self.automation_type_filter == "Playwright":

            self.generate_from_collection_btn.setVisible(False)

        actions_layout.addWidget(self.cancel_recording_btn)

        actions_layout.addWidget(self.cancel_execution_btn)

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

        self.cancel_execution_btn.clicked.connect(
            self.cancel_interactive_execution
        )

        self.add_automation_btn.clicked.connect(
            self.generate_automation_for_selected
        )

        self.update_automation_btn.clicked.connect(
            self.generate_automation_for_selected
        )

        self.import_test_cases_btn.clicked.connect(
            self.import_test_cases_from_excel
        )

        self.generate_from_collection_btn.clicked.connect(
            self.generate_automation_from_collection
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

        # QA-AUTOMATION-FINAL-ARCHITECTURE-04: filter to this tab's own
        # automation type — the scoped /test-cases-style call above
        # returns every type together (Playwright/API/SQL/None all
        # share one pool of test cases from QA Engineering), same as
        # the Web port; a Playwright-type row must never appear in (or
        # be executable from) the API tab, and vice versa.
        if self.automation_type_filter:

            def _matches(tc):
                expected_tool = self.manager.repository.execution_tool_for_automation_type(
                    self.automation_type_filter
                )
                return (
                    tc.get("execution_type") == "Automatable"
                    and tc.get("execution_tool") == expected_tool
                )

            self.test_cases = [
                tc for tc in self.test_cases if _matches(tc)
            ]

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


    # ======================================================
    # Import Test Cases (Excel) — Issue: QA Automation was only
    # ever populated by AI-generated test cases; this lets someone
    # bring in hand-written ones directly, with no AI/RAG step.
    # ======================================================

    def import_test_cases_from_excel(self):

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

        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Select Test Case Excel File",
            "",
            "Excel Files (*.xlsx *.xlsm);;All Files (*.*)",
        )

        if not file_path:

            return

        QApplication.setOverrideCursor(Qt.WaitCursor)

        try:

            result = self.manager.import_test_cases_from_excel(
                file_path, domain, module, knowledge_name
            )

        finally:

            QApplication.restoreOverrideCursor()

        if not result.get("success"):

            QMessageBox.critical(
                self,
                "Import Failed",
                result.get("error", "Unknown error.")
            )

            return

        self.log.append(
            f"Imported {result['imported']} test case(s) from "
            f"'{os.path.basename(file_path)}'."
        )

        if result.get("skipped"):

            self.log.append(
                f"Skipped {result['skipped']} row(s) with no Test "
                f"Case text."
            )

        self.load_test_cases()

    # ======================================================
    # Generate Automation from API Collection — no test case
    # required first. Auto-creates a lightweight test case per
    # endpoint (see TestExecutionManager.
    # generate_automation_from_collection()) so results/scripts hang
    # off the same test_cases plumbing as everything else.
    # ======================================================

    def generate_automation_from_collection(self):

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

        confirm = QMessageBox.question(
            self,
            "Generate Automation from API Collection",
            f"Generate API automation scripts directly from every "
            f"endpoint imported under {domain} / {module} / "
            f"{knowledge_name} — including endpoints with no test "
            f"case yet?\n\nA lightweight test case is auto-created "
            f"per endpoint so scripts, results and Execute all work "
            f"exactly like any other automated test case.",
        )

        if confirm != QMessageBox.Yes:

            return

        QApplication.setOverrideCursor(Qt.WaitCursor)

        try:

            result = self.manager.generate_automation_from_collection(
                domain, module, knowledge_name
            )

        finally:

            QApplication.restoreOverrideCursor()

        if not result.get("success"):

            QMessageBox.critical(
                self,
                "Generation Failed",
                result.get("error", "Unknown error.")
            )

            return

        self.log.append(
            f"Generated automation for {result['generated']} "
            f"endpoint(s) from the imported API collection."
        )

        if result.get("skipped"):

            self.log.append(
                f"{result['skipped']} endpoint(s) failed — see below:"
            )

            for item in result.get("results", []):

                if not item.get("success"):

                    self.log.append(
                        f"  - {item.get('endpoint_name', '')}: "
                        f"{item.get('error', '')}"
                    )

        self.load_test_cases()

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

        # QA-AUTOMATION-FINAL-ARCHITECTURE-04: on a filtered tab
        # (Playwright-only / API-only instance — see
        # self.automation_type_filter, set by qa_automation_hub_page.py)
        # only offer "None" plus this tab's own type, so generating
        # automation from THIS tab can never silently create/overwrite
        # a different automation type's script. An unfiltered instance
        # (kept as a backward-compatible default) still offers every
        # currently-supported type.
        offered_types = (
            ["None", self.automation_type_filter]
            if self.automation_type_filter
            else list(AUTOMATION_TYPES)
        )

        existing_type = test_case.get("automation_type") or "None"

        # A test case saved under a now-removed/out-of-scope type
        # (legacy 'Selenium'/'SQL' from before this task, or simply a
        # type outside this tab's own filter) must still show its REAL
        # persisted value rather than silently snapping to something
        # else — appended here so it stays visible for review, never
        # fabricated away.
        if existing_type not in offered_types:

            offered_types = offered_types + [existing_type]

        automation_combo.addItems(offered_types)

        automation_combo.setCurrentText(existing_type)

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

        # Manual Recording (Playwright's own recorder) has no meaning
        # for an API test case — on the API-only tab this column is
        # simply disabled/blank rather than offering a control that
        # would confuse the operator or record nothing useful. Left
        # enabled everywhere else (Playwright tab, and any legacy
        # unfiltered instance) exactly as before.
        if self.automation_type_filter == "API":

            record_btn.setEnabled(False)

            record_btn.setText("—")

            record_btn.setToolTip("Not applicable for API test cases.")

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

        clickup_btn = QPushButton("Create Bug")

        clickup_btn.setToolTip(
            "Creates a real ClickUp task summarizing this FAILED "
            "test case (requires ClickUp to be configured in Test "
            "Environment Settings)."
        )

        clickup_btn.clicked.connect(
            lambda _, tc_id=test_case["id"]: self.create_clickup_bug_for_row(
                tc_id
            )
        )

        self.table.setCellWidget(row, 10, clickup_btn)

        self._update_clickup_button(clickup_btn, test_case.get("last_result"))

    # ======================================================
    # ClickUp — contextual "create bug for a failed test" action only
    # (see this task's product decision: no standalone ClickUp tab).
    # A PASS/Not Run row never gets an enabled/encouraged bug button.
    # ======================================================

    def _update_clickup_button(self, button, last_result):

        is_fail = (last_result == "Fail")

        button.setEnabled(is_fail)

        button.setVisible(is_fail)

    def refresh_clickup_button(self, row):

        button = self.table.cellWidget(row, 10)

        if button is None:

            return

        result_item = self.table.item(row, 5)

        self._update_clickup_button(
            button, result_item.text() if result_item else None
        )

    def create_clickup_bug_for_row(self, test_case_id):

        test_case = self.manager.repository.get_test_case(test_case_id)

        if not test_case:

            QMessageBox.critical(
                self, "QA AI Studio", "Test case could not be loaded."
            )

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
                "Choose an Automation Type (Playwright / API) for "
                "the selected test case(s) first."
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
            tc_number, test_case_text, suggestion, self,
            allowed_types=(
                [self.automation_type_filter]
                if self.automation_type_filter else None
            ),
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

        # QA-AUTOMATION-FINAL-ARCHITECTURE-04 hidden-bug fix: this is
        # the actual "Active Script" grid-column control most users
        # will use, and it never validated the script it was about to
        # activate — same gap as ViewScriptDialog._set_active() (see
        # its comment for the confirmed real broken-script evidence).
        # Block Active here too on a real syntax error rather than
        # silently activating a script that can never successfully
        # run, and reset the combo back to reflect what's actually
        # active so the UI doesn't show a state that was rejected.

        script_text = (
            (test_case or {}).get("recorded_script")
            if source == "MANUAL"
            else (test_case or {}).get("automation_script")
        ) or ""

        if script_text.strip():

            error = self.manager.check_script_syntax(script_text)

            if error:

                QMessageBox.critical(
                    self,
                    "Cannot Set Active",
                    f"This script has a Python syntax problem and "
                    f"cannot be made Active for Execution:\n\n"
                    f"{error}\n\n"
                    f"Open the script (Script column) to fix and "
                    f"save it first, then try again.",
                )

                combo.blockSignals(True)

                previous_source = (
                    (test_case or {}).get("active_script_source")
                    or "AUTO"
                ).upper()

                combo.setCurrentIndex(
                    1 if previous_source == "MANUAL" else 0
                )

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

        # Playwright's own recorder picked these locators while the
        # operator drove the browser — flag anything that looks
        # dynamically generated (an id/name that will likely change
        # next time the page reloads) BEFORE the first replay,
        # instead of only discovering it when a replay fails.
        try:

            flagged = self.manager.scan_script_for_dynamic_locators(
                script
            )

        except Exception as ex:

            # BUGFIX (task spec section 33 — Desktop Remaining
            # Defects): this used to swallow ANY exception from the
            # scan and silently fall back to flagged=[], which reads
            # identically to "scanned it, found nothing" — the
            # operator would never know the dynamic-locator check
            # didn't actually run for this recording. Surface it in
            # the log instead (never fabricate a false "all clear"),
            # while still letting the recording save proceed — a
            # failed scan is a real, separate problem from whether
            # the recording itself is good.
            flagged = []

            self.log.append(
                f"{tc_number}: could not scan the recorded script for "
                f"dynamic locators ({ex}) — this recording was saved "
                f"anyway, but the dynamic-locator check above did not "
                f"actually run for it."
            )

        if flagged:

            lines = "\n".join(
                f"  Line {f['line_number']}: {f['code']}"
                for f in flagged
            )

            self.log.append(
                f"{tc_number}: {len(flagged)} recorded locator(s) "
                f"look dynamically generated and may stop matching "
                f"on the next run:\n{lines}"
            )

            QMessageBox.warning(
                self,
                "Dynamic Locator(s) Found",
                f"{tc_number}'s recording used {len(flagged)} "
                f"locator(s) built from an id/name that looks "
                f"auto-generated (e.g. a value that changes every "
                f"time the page loads):\n\n{lines}\n\n"
                f"These may work today and stop matching on the "
                f"next run. You can fix them now via 'View Script' "
                f"(an XPath built on a stable part of the value, or "
                f"a different attribute, usually survives a reload "
                f"better) — or leave them as-is and fix them via the "
                f"Locator Repair popup the first time one actually "
                f"fails during Execute.",
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

                self.refresh_clickup_button(row)

                self.log.append(
                    f"{tc_number}: recorded result '{result}'."
                )

            except Exception as ex:

                QMessageBox.critical(self, "QA AI Studio", str(ex))


    def handle_automated_rows(self, rows):

        without_script = []

        playwright_ready = []

        api_ready = []

        unsupported = []

        for row in rows:

            has_script = self.table.cellWidget(row, 6).isEnabled()

            automation_type = self.table.cellWidget(row, 4).currentText()

            if not has_script:

                without_script.append(row)

            elif automation_type == "Playwright":

                playwright_ready.append(row)

            elif automation_type == "API":

                api_ready.append(row)

            else:

                unsupported.append(row)

        if without_script:

            numbers = ", ".join(
                self.row_tc_number(r) for r in without_script
            )

            self.log.append(
                f"Skipped (no automation script yet): {numbers}. "
                f"Use 'Add Automation' first."
            )

        if unsupported:

            numbers = ", ".join(
                self.row_tc_number(r) for r in unsupported
            )

            QMessageBox.information(
                self,
                "Manual Review Required",
                f"{len(unsupported)} test case(s) are saved under a "
                f"legacy automation type (e.g. Selenium) that this "
                f"tab no longer offers a runner for. SQL test cases "
                f"belong on the SQL Automation tab instead, which has "
                f"its own real, read-only runner.\n\n"
                f"Test cases: {numbers}\n\n"
                f"Use 'View Script' on each row to review it."
            )

        if api_ready:

            self.handle_api_rows(api_ready)

        if playwright_ready:

            self.confirm_and_run_playwright(playwright_ready)


    def handle_api_rows(self, rows):
        """
        API-type test cases get a choice the other unsupported
        automation types (Selenium/SQL) don't: generate/keep an
        Ollama-produced script for manual review (today's existing
        behaviour, unchanged), OR actually send the real HTTP
        request right now and report back what happened. See
        Core/api_automation_runner.py and
        App/UI/QAAutomation/api_run_dialogs.py.
        """

        from UI.QAAutomation.api_run_dialogs import ApiRunModeDialog

        mode_dialog = ApiRunModeDialog(len(rows), self)

        if mode_dialog.exec() != QDialog.Accepted:

            self.log.append(
                f"{len(rows)} API test case(s): cancelled — no "
                f"action taken."
            )

            return

        if mode_dialog.chosen_mode != "execute":

            numbers = ", ".join(self.row_tc_number(r) for r in rows)

            QMessageBox.information(
                self,
                "Manual Review Required",
                f"{len(rows)} API test case(s) — use 'View Script' "
                f"on each row to review the generated script and "
                f"run it in your own test environment.\n\n"
                f"Test cases: {numbers}"
            )

            return

        self.run_api_automation_rows(rows)


    def run_api_automation_rows(self, rows):
        """
        The "Execute Against Real Server" path chosen in
        handle_api_rows() — finds the REAL, imported endpoint each
        test case actually matches (never the AI-generated script
        text — see
        TestExecutionManager.get_relevant_endpoints_for_test_case())
        and runs it through the same real-request flow Manage
        Knowledge's "Run Now" uses
        (UI.QAAutomation.api_run_dialogs.run_api_endpoint_interactive()
        — shared by both entry points so they behave identically).
        """

        from UI.QAAutomation.api_run_dialogs import (
            run_api_endpoint_interactive,
        )

        for row in rows:

            test_case_id = self.row_tc_id(row)

            tc_number = self.row_tc_number(row)

            test_case = self.manager.repository.get_test_case(
                test_case_id
            )

            if not test_case:

                self.log.append(
                    f"{tc_number}: test case could not be loaded — "
                    f"skipped."
                )

                continue

            matched = self.manager.get_relevant_endpoints_for_test_case(
                test_case
            )

            if not matched:

                self.log.append(
                    f"{tc_number}: no imported API Collection "
                    f"endpoint found for this Domain / Module / "
                    f"Knowledge Name — import one via Upload New "
                    f"Knowledge -> API Collection first. Skipped."
                )

                continue

            endpoint = matched[0]

            self.log.append(
                f"{tc_number}: sending "
                f"{endpoint.get('method')} "
                f"{endpoint.get('url_resolved') or endpoint.get('url_raw')} "
                f"..."
            )

            outcome = run_api_endpoint_interactive(
                self, tc_number, endpoint
            )

            if outcome in ("Pass", "Fail"):

                self.manager.repository.update_result(
                    test_case_id, outcome
                )

                self.set_row_last_result(test_case_id, outcome)

                self.log.append(f"{tc_number}: {outcome}.")

            else:

                self.log.append(f"{tc_number}: cancelled.")

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
            f"If a step's locator or value can't be found, you'll "
            f"be asked for a fix before that test case continues — "
            f"the same as a single-test run, regardless of how many "
            f"are queued here. Stay nearby while this batch runs.\n\n"
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

        # Interactive locator repair is now offered for every test
        # case in the queue, batch or not — the operator asked for
        # this explicitly: a multi-test run no longer just fails
        # silently on the first broken locator with nobody able to
        # respond, because the operator running it IS there watching
        # and wants the same pause-and-ask popups a single-test run
        # gets. run_next_execution() re-derives the actual per-test-
        # case eligibility from _non_interactive_test_case_ids below
        # (reset here for this new batch) rather than trusting a
        # single flag for the whole queue — a script structure that
        # doesn't support interactive repair only falls back to a
        # plain run for THAT test case, not every one after it.
        self._non_interactive_test_case_ids = set()

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

        self.pending_repairs = []
        self.repair_persistence_errors = []

        self.execution_thread = QThread()

        # Re-derived per test case (not a single flag for the whole
        # batch) — a script structure that turned out not to support
        # interactive repair only opts THAT test case out; every
        # other one in the queue still gets asked about locator/value
        # problems instead of failing silently.
        self._interactive_eligible = (
            test_case_id not in self._non_interactive_test_case_ids
        )

        if self._interactive_eligible:

            self.log.append(
                f"Running {tc_number} in a real browser — if a "
                f"step's locator can't be found, you'll be asked "
                f"for a fix instead of the run just failing."
            )

            self.execution_worker = PlaywrightInteractiveWorker(
                test_case_id
            )

            self.execution_worker.step_failed.connect(
                self.on_step_failed
            )

            self.execution_worker.step_repaired.connect(
                self.on_step_repaired
            )

            self.cancel_execution_btn.setVisible(True)

            self.cancel_execution_btn.setEnabled(True)

        else:

            self.log.append(
                f"Running {tc_number} in a real browser..."
            )

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


    def cancel_interactive_execution(self):

        if self.execution_worker and self._interactive_eligible:

            self.execution_worker.cancel()

            self.log.append(
                "Cancelling execution — this may take a moment "
                "while the current step finishes."
            )

            self.cancel_execution_btn.setEnabled(False)


    def on_step_failed(self, event):
        """
        Runs on the UI thread (queued-connection delivery from
        PlaywrightInteractiveWorker's background thread). Shows the
        repair dialog and sends the operator's decision back — the
        background thread is blocked waiting for exactly this.
        """

        manager = self.execution_worker.manager

        test_case = manager.repository.get_test_case(
            self.execution_worker.test_case_id
        )

        dialog = LocatorRepairDialog(event, manager, test_case, self)

        dialog.exec()

        self.execution_worker.submit_decision(dialog.decision)

        if dialog.decision.get("action") == "cancel":

            self.log.append(
                f"Step {event.get('step')}: operator cancelled the "
                f"run."
            )

        else:

            self.log.append(
                f"Step {event.get('step')}: retrying with a "
                f"corrected step (attempt {event.get('attempt')})..."
            )


    def on_step_repaired(self, repair):

        self.pending_repairs.append(repair)

        # The corrected statement has just executed successfully in
        # the CURRENT visible browser session. Persist it immediately
        # before replay moves on, so a later failed/cancelled step can
        # never make the operator repeat an already-proven locator fix.
        # preserve_execution_state=True keeps an already-Active script
        # executable; ordinary editor changes still follow their normal
        # lifecycle. The running subprocess is independent of this DB
        # write and continues directly with the next statement.
        try:
            self.execution_worker.manager.apply_script_repairs(
                self.execution_worker.test_case_id,
                [repair],
                preserve_execution_state=True,
            )
            self.log.append(
                f"Step {repair.get('step')} fixed and saved; replay "
                f"continues from the next step:\n"
                f"  was: {repair.get('original')}\n"
                f"  now: {repair.get('corrected')}"
            )
        except Exception as ex:
            self.repair_persistence_errors.append(str(ex))
            self.log.append(
                f"Step {repair.get('step')} fixed for this live run, "
                f"but the stored script could not be updated yet: {ex}"
            )

    def on_execution_finished(self, test_case_id, result):

        tc_number = self.tc_number_for_id(test_case_id)

        if result.get("interactive_supported") is False:

            # This test case's script matches neither the flat,
            # AI-generated shape nor the standard Playwright-codegen
            # (Manually Recorded) shape interactive repair supports
            # — most likely a script that's been hand-edited into
            # something unusual. Fall back to a normal run for this
            # one test case transparently, rather than surfacing an
            # internal limitation as an error.
            self.log.append(
                f"{tc_number}: interactive step-by-step repair isn't "
                f"available for this script's structure — running "
                f"it normally instead."
            )

            # Only THIS test case opts out — every other one still
            # queued gets interactive repair, see run_next_execution().
            self._non_interactive_test_case_ids.add(test_case_id)

            self.execution_queue.insert(0, test_case_id)

            return

        if result.get("cancelled"):

            self.log.append(
                f"{tc_number}: execution cancelled by operator."
            )

            return

        if "outcome" in result:

            outcome = result["outcome"]

            duration = result.get("duration", 0)

            self.set_row_last_result(test_case_id, outcome)

            speed_note = ""

            if "slow_mo_ms" in result:

                speed_note = (
                    f" [slow_mo={result['slow_mo_ms']}ms, "
                    f"timeout={result.get('timeout_ms')}ms]"
                )

            self.log.append(
                f"{tc_number}: {outcome} ({duration:.1f}s)"
                f"{speed_note}"
            )

            if not result["success"] and result.get("stderr"):

                # Python tracebacks put the actual exception TYPE
                # and MESSAGE at the very END, after every "File
                # ..., line ..., in ..." frame — for Playwright
                # specifically, those frames are dominated by long
                # site-packages paths, so the first 500 characters
                # used to be nothing but that boilerplate, cutting
                # off before the real reason (e.g. "TimeoutError:
                # Locator.click: Timeout 45000ms exceeded...")
                # every single time. Keep the END, not the start.
                stderr_text = result["stderr"].strip()

                max_len = 4000

                if len(stderr_text) > max_len:

                    stderr_text = (
                        "...(earlier frames truncated)...\n"
                        + stderr_text[-max_len:]
                    )

                self.log.append(
                    f"{tc_number} error output:\n{stderr_text}"
                )

            if self.pending_repairs:

                if self.repair_persistence_errors:
                    # Immediate persistence normally makes a second
                    # Save prompt unnecessary. Keep the existing prompt
                    # only as a recovery path if one of those writes
                    # actually failed.
                    self.offer_to_save_repairs(
                        test_case_id, tc_number, self.pending_repairs
                    )
                else:
                    self.log.append(
                        f"{tc_number}: {len(self.pending_repairs)} "
                        f"repaired step(s) are already stored in the "
                        f"active script; no second Save is required."
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


    def offer_to_save_repairs(self, test_case_id, tc_number, repairs):

        details = "\n".join(
            f"Step {r.get('step')}:\n"
            f"  was: {r.get('original')}\n"
            f"  now: {r.get('corrected')}"
            for r in repairs
        )

        confirm = QMessageBox.question(
            self,
            "Save Locator Fix?",
            f"{tc_number} needed {len(repairs)} locator/value "
            f"fix(es) to pass this run:\n\n{details}\n\n"
            f"Save these into the stored script so future runs "
            f"don't need to repair them again?",
            QMessageBox.Yes | QMessageBox.No,
        )

        if confirm != QMessageBox.Yes:

            self.log.append(
                f"{tc_number}: fix(es) NOT saved — this run's "
                f"corrections were used once and discarded."
            )

            return

        manager = TestExecutionManager()

        manager.apply_script_repairs(test_case_id, repairs)

        self.log.append(
            f"{tc_number}: script updated with {len(repairs)} "
            f"locator/value fix(es)."
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

        self.cancel_execution_btn.setVisible(False)

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

                self.refresh_clickup_button(row)

                return
