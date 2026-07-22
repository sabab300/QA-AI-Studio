"""
QA AI Studio
Information Card

Version: 1.0
"""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFrame,
    QLabel,
    QVBoxLayout
)


class InfoCard(QFrame):

    def __init__(
        self,
        title="Title",
        value="0"
    ):

        super().__init__()

        self.setObjectName("Card")

        self.setMinimumHeight(120)

        self.build_ui(
            title,
            value
        )

    # --------------------------------------------------

    def build_ui(
        self,
        title,
        value
    ):

        layout = QVBoxLayout(self)

        layout.setContentsMargins(
            20,
            18,
            20,
            18
        )

        layout.setSpacing(8)

        self.title = QLabel(title)

        self.title.setAlignment(
            Qt.AlignLeft
        )

        self.title.setObjectName(
            "SectionTitle"
        )

        self.value = QLabel(str(value))

        self.value.setAlignment(
            Qt.AlignCenter
        )

        self.value.setStyleSheet("""

            font-size:32px;

            font-weight:bold;

            color:#0C4DA2;

        """)

        layout.addWidget(self.title)

        layout.addStretch()

        layout.addWidget(self.value)

    # --------------------------------------------------

    def set_value(
        self,
        value
    ):

        self.value.setText(
            str(value)
        )

    # --------------------------------------------------

    def set_title(
        self,
        title
    ):

        self.title.setText(title)