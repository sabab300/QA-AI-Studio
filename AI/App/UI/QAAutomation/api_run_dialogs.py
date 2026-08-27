# Create: App/UI/QAAutomation/api_run_dialogs.py

"""
QA AI Studio
API Automation — "Execute Against Real Server" dialogs

Version: 1.0

Shared UI pieces for actually running an imported API Collection
endpoint against a real server (see Core/api_automation_runner.py),
used from BOTH:
    - Manage Knowledge's API Collection tree ("Run Now" on a single
      endpoint — no test case required)
    - QA Automation's Execute ("Execute Against Real Server" for
      API-type test cases — see test_execution_page.py's
      run_api_automation_rows())

Kept here (not inside either page) so both entry points show the
operator the exact same choices/behaviour, per the standing rule
this whole feature follows: whenever QA AI Studio is about to make a
decision it can't be fully sure of on its own — which mode to run
in, a variable value it doesn't have, a response that doesn't match
what was expected, a request that failed outright — it stops and
asks the operator instead of silently guessing or failing.
"""

import json

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QFormLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QRadioButton,
    QButtonGroup,
    QTextEdit,
    QDialogButtonBox,
    QScrollArea,
    QWidget,
)


# ==========================================================
# Step 1 (QA Automation Execute only — Manage Knowledge's "Run Now"
# is inherently real-server, so it skips this choice entirely):
# which way should this API test case be handled?
# ==========================================================

class ApiRunModeDialog(QDialog):
    """
    `.chosen_mode` is "generate", "execute", or None (cancelled) —
    read it after exec() returns.
    """

    def __init__(self, count, parent=None):

        super().__init__(parent)

        self.chosen_mode = None

        self.setWindowTitle("How should this run?")

        self.resize(480, 320)

        layout = QVBoxLayout(self)

        intro = QLabel(
            f"{count} API test case(s) selected. How do you want to "
            f"handle them?"
        )

        intro.setWordWrap(True)

        layout.addWidget(intro)

        self.generate_radio = QRadioButton(
            "Generate Script (Ollama/AI)"
        )

        generate_note = QLabel(
            "Today's existing behaviour: creates/keeps a "
            "'requests'-based Python script grounded in the real "
            "imported endpoint, for you to review and run yourself "
            "via 'View Script'. Nothing is actually sent anywhere."
        )

        generate_note.setWordWrap(True)

        generate_note.setStyleSheet("color: #64748B; margin-left: 22px;")

        self.execute_radio = QRadioButton(
            "Execute Against Real Server Now"
        )

        execute_note = QLabel(
            "Sends a REAL HTTP request straight to the real, "
            "imported endpoint's URL — using Test Environment "
            "Settings for auth/headers/timeout — and reports back "
            "what actually happened. This is a real call: make sure "
            "you're pointed at a TEST/UAT server, not production."
        )

        execute_note.setWordWrap(True)

        execute_note.setStyleSheet("color: #64748B; margin-left: 22px;")

        group = QButtonGroup(self)

        group.addButton(self.generate_radio)

        group.addButton(self.execute_radio)

        self.generate_radio.setChecked(True)

        layout.addWidget(self.generate_radio)

        layout.addWidget(generate_note)

        layout.addWidget(self.execute_radio)

        layout.addWidget(execute_note)

        layout.addStretch()

        buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel
        )

        buttons.accepted.connect(self._accept)

        buttons.rejected.connect(self.reject)

        layout.addWidget(buttons)

    def _accept(self):

        self.chosen_mode = (
            "execute" if self.execute_radio.isChecked() else "generate"
        )

        self.accept()


# ==========================================================
# Step 2: fill in any {{variable}} this endpoint needs that isn't
# already remembered in Test Environment Settings.
# ==========================================================

class ApiVariablePromptDialog(QDialog):
    """
    `.values` is a {name: value} dict — populated only if the
    dialog was accepted (Cancel leaves it as {}).
    """

    def __init__(self, endpoint, missing_names, parent=None):

        super().__init__(parent)

        self.values = {}

        self.setWindowTitle("Value(s) needed before this request can run")

        self.resize(480, 320)

        layout = QVBoxLayout(self)

        method = endpoint.get("method", "")

        url = endpoint.get("url_resolved") or endpoint.get("url_raw", "")

        intro = QLabel(
            f"{method} {url}\n\n"
            f"This endpoint uses {{{{variable}}}} placeholder(s) "
            f"that couldn't be resolved from the imported collection "
            f"file alone. Enter a value for each — it will be "
            f"remembered in Test Environment Settings so you won't "
            f"be asked again."
        )

        intro.setWordWrap(True)

        layout.addWidget(intro)

        form = QFormLayout()

        self._fields = {}

        for name in missing_names:

            field = QLineEdit()

            self._fields[name] = field

            form.addRow(f"{{{{{name}}}}}", field)

        layout.addLayout(form)

        layout.addStretch()

        buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel
        )

        buttons.accepted.connect(self._accept)

        buttons.rejected.connect(self.reject)

        layout.addWidget(buttons)

    def _accept(self):

        self.values = {
            name: field.text() for name, field in self._fields.items()
        }

        self.accept()


# ==========================================================
# Step 3: here's what actually happened — mark Pass/Fail, or fix
# settings and try again.
# ==========================================================

class ApiRequestResultDialog(QDialog):
    """
    `.decision` is one of "pass", "fail", "retry", "cancel" — read it
    after exec() returns.

    If result["auto_verdict"] is already "Pass" (the real response
    matched this endpoint's stored expected status exactly), this
    still shows the full response for transparency, but the default
    action is a single OK — no judgment call needed. Everything else
    (no expected status on file, a mismatch, or an outright request
    failure) requires the operator to actually decide, per Interactive
    Locator Repair's own "pause and ask, don't guess" pattern.
    """

    def __init__(self, tc_number, endpoint, result, parent=None):

        super().__init__(parent)

        self.decision = "cancel"

        self.setWindowTitle(f"{tc_number} — API Result")

        self.resize(680, 780)

        layout = QVBoxLayout(self)

        request = result.get("request", {})

        header = QLabel(
            f"{request.get('method', '')} {request.get('url', '')}"
        )

        header.setWordWrap(True)

        header.setStyleSheet("font-weight: bold;")

        layout.addWidget(header)

        request_headers = request.get("headers") or {}

        if request_headers:

            request_headers_text = "\n".join(
                f"{k}: {v}" for k, v in request_headers.items()
            )

            request_headers_box = QTextEdit()

            request_headers_box.setReadOnly(True)

            request_headers_box.setFontFamily("Consolas, monospace")

            request_headers_box.setPlainText(request_headers_text)

            request_headers_box.setMaximumHeight(90)

            layout.addWidget(QLabel("Request Headers"))

            layout.addWidget(request_headers_box)

        request_body_text = self._decode_request_body(request.get("data"))

        if request_body_text:

            request_body_box = QTextEdit()

            request_body_box.setReadOnly(True)

            request_body_box.setFontFamily("Consolas, monospace")

            request_body_box.setPlainText(
                self._pretty_json_or_raw(request_body_text)
            )

            request_body_box.setMaximumHeight(140)

            layout.addWidget(QLabel("Request Body"))

            layout.addWidget(request_body_box)

        if result.get("error"):

            status_line = QLabel(
                f"Request failed — no response was received.\n"
                f"Error: {result['error']}"
            )

            status_line.setStyleSheet("color: #B91C1C;")

        else:

            expected = result.get("expected_status")

            expected_note = (
                f" (expected {expected}, from the original imported "
                f"example response)" if expected else " (no expected "
                f"status stored for this endpoint)"
            )

            status_line = QLabel(
                f"Status: {result.get('status_code')}{expected_note}\n"
                f"Time: {result.get('elapsed_ms')} ms"
            )

        status_line.setWordWrap(True)

        layout.addWidget(status_line)

        if result.get("auto_verdict") == "Pass":

            verdict_note = QLabel(
                "Auto-verdict: Pass — the real response matched this "
                "endpoint's stored expected status, so no judgment "
                "call is needed. You can still override it below."
            )

            verdict_note.setWordWrap(True)

            verdict_note.setStyleSheet("color: #15803D;")

            layout.addWidget(verdict_note)

        body_box = QTextEdit()

        body_box.setReadOnly(True)

        body_box.setFontFamily("Consolas, monospace")

        body_box.setPlainText(
            self._pretty_json_or_raw(result.get("response_body_text"))
            or "(no response body)"
        )

        layout.addWidget(QLabel("Response Body"))

        layout.addWidget(body_box)

        response_headers = result.get("response_headers") or {}

        if response_headers:

            headers_text = "\n".join(
                f"{k}: {v}" for k, v in response_headers.items()
            )

            headers_box = QTextEdit()

            headers_box.setReadOnly(True)

            headers_box.setPlainText(headers_text)

            headers_box.setMaximumHeight(90)

            layout.addWidget(QLabel("Response Headers"))

            layout.addWidget(headers_box)

        button_row = QHBoxLayout()

        if result.get("error"):

            fail_btn = QPushButton("Mark as Fail")

            fail_btn.clicked.connect(self._mark_fail)

            retry_btn = QPushButton("Fix Settings && Retry")

            retry_btn.clicked.connect(self._retry)

            cancel_btn = QPushButton("Cancel")

            cancel_btn.clicked.connect(self.reject)

            button_row.addWidget(fail_btn)

            button_row.addWidget(retry_btn)

            button_row.addWidget(cancel_btn)

        elif result.get("auto_verdict") == "Pass":

            ok_btn = QPushButton("OK — Keep as Pass")

            ok_btn.clicked.connect(self._mark_pass)

            override_btn = QPushButton("Override to Fail")

            override_btn.clicked.connect(self._mark_fail)

            button_row.addWidget(ok_btn)

            button_row.addWidget(override_btn)

        else:

            pass_btn = QPushButton("Mark as Pass")

            pass_btn.clicked.connect(self._mark_pass)

            fail_btn = QPushButton("Mark as Fail")

            fail_btn.clicked.connect(self._mark_fail)

            retry_btn = QPushButton("Fix Settings && Retry")

            retry_btn.clicked.connect(self._retry)

            cancel_btn = QPushButton("Cancel")

            cancel_btn.clicked.connect(self.reject)

            button_row.addWidget(pass_btn)

            button_row.addWidget(fail_btn)

            button_row.addWidget(retry_btn)

            button_row.addWidget(cancel_btn)

            layout.addLayout(button_row)

    @staticmethod
    def _decode_request_body(data):
        """
        `request["data"]` is the raw bytes ApiAutomationRunner
        actually sent on the wire (see
        Core/api_automation_runner.py's build_request()) — decode it
        back to text for display here, tolerating anything that
        isn't valid UTF-8 rather than crashing the result dialog
        over it.
        """

        if not data:

            return ""

        if isinstance(data, bytes):

            try:

                return data.decode("utf-8")

            except Exception:

                return repr(data)

        return str(data)

    @staticmethod
    def _pretty_json_or_raw(text):
        """
        Pretty-prints `text` as indented JSON when it parses as
        JSON (the common case for PSW's APIs), otherwise returns it
        completely unchanged — never guesses at reformatting
        non-JSON text, and never raises on malformed/partial JSON.
        """

        stripped = (text or "").strip()

        if stripped[:1] not in ("{", "["):

            return text

        try:

            return json.dumps(json.loads(stripped), indent=2)

        except Exception:

            return text

    def _mark_pass(self):

        self.decision = "pass"

        self.accept()

    def _mark_fail(self):

        self.decision = "fail"

        self.accept()

    def _retry(self):

        self.decision = "retry"

        self.accept()


# ==========================================================
# The actual run loop, shared by both entry points
# ==========================================================

def run_api_endpoint_interactive(parent, label, endpoint):
    """
    Resolves any {{variable}} this endpoint still needs (asking the
    operator once via ApiVariablePromptDialog, then remembering the
    answer in Test Environment Settings so it's never asked again),
    sends the real request via ApiAutomationRunner, and shows the
    result via ApiRequestResultDialog. On "Fix Settings & Retry" it
    reopens Test Environment Settings and tries again with whatever
    changed.

    `parent` is just a QWidget to own the dialogs (any page works —
    this has no dependency on which page called it). Used by BOTH
    ManageKnowledgePage.run_selected_endpoint() (Manage Knowledge's
    "Run Now") and test_execution_page.py's
    run_api_automation_rows() (QA Automation Execute's "Execute
    Against Real Server"), so both entry points behave identically
    without either page needing to import the other.

    Returns "Pass", "Fail", or "Cancelled".
    """

    from Core.test_environment_config import TestEnvironmentConfig
    from Core.api_automation_runner import ApiAutomationRunner

    # Deferred import: EnvironmentSettingsDialog lives in
    # test_execution_page.py, which may in turn import THIS module —
    # both sides only import each other lazily, inside function
    # bodies, specifically so this never becomes a circular import
    # error at module load time.
    from UI.QAAutomation.test_execution_page import (
        EnvironmentSettingsDialog,
    )

    config_manager = TestEnvironmentConfig()

    runner = ApiAutomationRunner()

    while True:

        config = config_manager.load()

        missing = runner.find_missing_variables(endpoint, config)

        if missing:

            prompt = ApiVariablePromptDialog(endpoint, missing, parent)

            if prompt.exec() != QDialog.Accepted:

                return "Cancelled"

            for name, value in prompt.values.items():

                config_manager.remember_api_variable(name, value)

            continue

        result = runner.send(endpoint, config)

        result_dialog = ApiRequestResultDialog(
            label, endpoint, result, parent
        )

        result_dialog.exec()

        if result_dialog.decision == "retry":

            settings_dialog = EnvironmentSettingsDialog(
                config_manager, parent
            )

            settings_dialog.exec()

            continue

        if result_dialog.decision == "pass":

            return "Pass"

        if result_dialog.decision == "fail":

            return "Fail"

        return "Cancelled"