"""
QA AI Studio
Navigation Panel

Version: 1.0
"""

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QButtonGroup,
    QFrame,
    QPushButton,
    QVBoxLayout,
    QSizePolicy
)


class NavigationPanel(QFrame):

    page_changed = Signal(int, str)

    # --------------------------------------------------

    def __init__(self):

        super().__init__()

        self.setObjectName("SideMenu")

        self.buttons = []

        self.group = QButtonGroup(self)

        self.group.setExclusive(True)

        self._build_ui()

    # --------------------------------------------------

    def _build_ui(self):

        layout = QVBoxLayout(self)

        layout.setContentsMargins(10, 15, 10, 15)

        layout.setSpacing(8)

        pages = [

            ("🏠  Dashboard", 0),

            ("📚  Knowledge Hub", 1),

            ("🧪  QA Engineering", 2),

            ("🤖  QA Automation", 3),

            ("🧠  AI Assistant", 4),

            ("⚙️  Settings", 5)

        ]

        for text, index in pages:

            button = QPushButton(text)

            button.setObjectName("MenuButton")

            button.setCheckable(True)

            button.setCursor(Qt.PointingHandCursor)

            button.setMinimumHeight(42)

            button.setSizePolicy(
                QSizePolicy.Expanding,
                QSizePolicy.Fixed
            )

            button.clicked.connect(

                lambda checked, i=index,
                t=text.replace("🏠  ", "")
                     .replace("📚  ", "")
                     .replace("🧪  ", "")
                     .replace("🤖  ", "")
                     .replace("🧠  ", "")
                     .replace("⚙️  ", ""):

                self.page_changed.emit(i, t)

            )

            self.group.addButton(button)

            self.buttons.append(button)

            layout.addWidget(button)

        layout.addStretch()

        self.buttons[0].setChecked(True)
        