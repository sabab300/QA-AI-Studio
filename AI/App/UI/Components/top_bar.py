"""
QA AI Studio
Top Bar

Version: 1.0
"""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QVBoxLayout,
    QWidget
)


class TopBar(QFrame):

    # --------------------------------------------------

    def __init__(self):

        super().__init__()

        self.setObjectName("TopBar")

        self._build_ui()

    # --------------------------------------------------

    def _build_ui(self):

        layout = QHBoxLayout(self)

        layout.setContentsMargins(20, 10, 20, 10)

        layout.setSpacing(15)

        # -----------------------------------------

        title_layout = QVBoxLayout()

        title_layout.setSpacing(0)

        self.title = QLabel("QA AI Studio")

        self.title.setObjectName("AppTitle")

        self.subtitle = QLabel(
            "Enterprise QA Engineering Platform"
        )

        self.subtitle.setObjectName("AppSubtitle")

        title_layout.addWidget(self.title)

        title_layout.addWidget(self.subtitle)

        layout.addLayout(title_layout)

        # -----------------------------------------

        spacer = QWidget()

        spacer.setSizePolicy(
            QSizePolicy.Expanding,
            QSizePolicy.Preferred
        )

        layout.addWidget(spacer)

        # -----------------------------------------

        self.mode = QLabel("Mode : LOCAL AI")

        self.mode.setStyleSheet(
            "color:white;font-weight:bold;"
        )

        layout.addWidget(self.mode)

        # -----------------------------------------

        self.user = QLabel("User : Administrator")

        self.user.setStyleSheet(
            "color:white;"
        )

        layout.addWidget(self.user)

        layout.setAlignment(Qt.AlignVCenter)