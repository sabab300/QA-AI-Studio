"""
QA AI Studio
URL Access Analyzer UI Dialogs
Development #2
"""

import asyncio
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLineEdit,
    QLabel,
    QVBoxLayout,
    QMessageBox,
)

from AI.Core.url_access_analyzer import AuthenticatedSessionManager
from AI.Core.url_discovery_worker import run_authenticated_discovery


class URLInputDialog(QDialog):
    """Collect only the URL before any access analysis."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Access URL")
        self.setMinimumWidth(520)

        layout = QVBoxLayout(self)

        layout.addWidget(QLabel(
            "Enter the website/application URL that QA AI Studio should access:"
        ))

        self.url_edit = QLineEdit()
        self.url_edit.setPlaceholderText(
            "https://qa.psw.gov.pk/app/SD/Export/Create"
        )
        layout.addWidget(self.url_edit)

        buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel
        )
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self.url_edit.returnPressed.connect(self._accept)

    def _accept(self):
        if not self.url_edit.text().strip():
            QMessageBox.warning(self, "QA AI Studio", "URL is required.")
            return
        self.accept()

    def url(self):
        return self.url_edit.text().strip()


class CredentialsDialog(QDialog):
    """
    Ask only for credentials detected by URLAccessAnalyzer.

    Credentials are held in memory only. This dialog never persists them.
    """

    def __init__(self, analysis, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Authentication Required")
        self.setMinimumWidth(560)

        self._fields = {}

        layout = QVBoxLayout(self)

        message = QLabel(
            "QA AI Studio detected authentication requirements for this URL.\n"
            "Provide the required information to continue. Credentials are "
            "kept in memory for this access attempt only."
        )
        message.setWordWrap(True)
        layout.addWidget(message)

        form = QFormLayout()
        layout.addLayout(form)

        detected = analysis.get("detected_fields", [])
        required_types = []

        for field in detected:
            field_type = field.get("field_type", "")
            if field_type in ("LOGIN_ID", "PASSWORD", "TOKEN", "API_KEY"):
                if field_type not in required_types:
                    required_types.append(field_type)

        auth_type = analysis.get("authentication_type", "")
        if auth_type == "SSO" and not required_types:
            required_types.append("SSO")

        if not required_types:
            required_types.append("CREDENTIAL")

        labels = {
            "LOGIN_ID": "Login ID / Username",
            "PASSWORD": "Password",
            "TOKEN": "Token",
            "API_KEY": "API Key",
            "SSO": "SSO / Identity Provider",
            "CREDENTIAL": "Credential",
        }

        for field_type in required_types:
            edit = QLineEdit()
            edit.setPlaceholderText(labels.get(field_type, field_type))
            if field_type in ("PASSWORD", "TOKEN", "API_KEY"):
                edit.setEchoMode(QLineEdit.Password)
            form.addRow(labels.get(field_type, field_type), edit)
            self._fields[field_type] = edit

        if analysis.get("login_url"):
            layout.addWidget(QLabel(
                f"Detected authentication page: {analysis['login_url']}"
            ))

        buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel
        )
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _accept(self):
        missing = [
            label for label, edit in self._fields.items()
            if not edit.text().strip()
        ]
        if missing:
            QMessageBox.warning(
                self,
                "QA AI Studio",
                "Please provide: " + ", ".join(missing)
            )
            return
        self.accept()

    def credentials(self):
        # In-memory only. Caller must not persist this dictionary.
        return {
            field_type: edit.text()
            for field_type, edit in self._fields.items()
        }


class URLAccessDialog(QDialog):
    """Dialog handler for authenticating and initiating Playwright discovery."""

    def __init__(self, target_url: str, parent=None):
        super().__init__(parent)
        self.target_url = target_url
        self.session_mgr = None

    def handle_authenticated_discovery(self, username: str, password: str):
        """Authenticates and hands off active Playwright page to discovery."""
        try:
            credentials = {"username": username, "password": password}

            # Execute the async discovery routine
            discovered_data = asyncio.run(
                run_authenticated_discovery(
                    login_url=self.target_url,
                    target_url=self.target_url,
                    credentials=credentials
                )
            )

            if not discovered_data:
                raise ValueError("Authenticated Playwright session failed or returned no results.")

            sequence_count = len(discovered_data.get("sequence", [])) if isinstance(discovered_data, dict) else 0

            QMessageBox.information(
                self, 
                "Success", 
                f"Successfully authenticated and discovered {sequence_count} workflow steps."
            )
            self.accept()

        except Exception as err:
            QMessageBox.critical(
                self, 
                "Authentication Error", 
                f"Failed during authenticated discovery: {str(err)}"
            )
        finally:
            if self.session_mgr and hasattr(self.session_mgr, "close"):
                self.session_mgr.close()