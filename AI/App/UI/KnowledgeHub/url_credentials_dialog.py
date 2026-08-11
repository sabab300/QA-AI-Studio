from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLineEdit,
)


class URLCredentialsDialog(QDialog):

    def __init__(self, credential_fields=None, parent=None):

        super().__init__(parent)

        self.setWindowTitle("URL Authentication Required")
        self.resize(450, 250)

        self.fields = {}

        layout = QFormLayout(self)

        credential_fields = credential_fields or [
            "Login ID",
            "Password",
        ]

        for field in credential_fields:

            field_name = str(field).strip()

            if not field_name:
                continue

            editor = QLineEdit()

            editor.setPlaceholderText(
                f"Enter {field_name}"
            )

            if "password" in field_name.lower():
                editor.setEchoMode(
                    QLineEdit.Password
                )

            self.fields[field_name] = editor

            layout.addRow(
                f"{field_name}:",
                editor
            )

        buttons = QDialogButtonBox(
            QDialogButtonBox.Ok |
            QDialogButtonBox.Cancel
        )

        buttons.accepted.connect(
            self.accept
        )

        buttons.rejected.connect(
            self.reject
        )

        layout.addRow(buttons)

    def get_credentials(self):

        credentials = {}

        for field_name, editor in self.fields.items():

            value = editor.text().strip()

            if value:
                credentials[field_name] = value

        return credentials