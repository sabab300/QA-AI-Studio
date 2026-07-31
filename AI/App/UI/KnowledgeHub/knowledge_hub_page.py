"""
==========================================================
QA AI Studio

Knowledge Hub

Version : 3.0
==========================================================
"""

from PySide6.QtWidgets import (
    QWidget,
    QHBoxLayout,
    QVBoxLayout,
    QPushButton,
    QLabel,
    QFrame,
    QStackedWidget,
)

from UI.KnowledgeHub.upload_manual_page import UploadManualPage
from UI.KnowledgeHub.manage_knowledge_page import ManageKnowledgePage
from UI.KnowledgeHub.smart_upload_page import SmartUploadPage

class PlaceholderPage(QWidget):

    def __init__(self, title):

        super().__init__()

        layout = QVBoxLayout(self)

        layout.addStretch()

        label = QLabel(title)

        label.setObjectName("SectionTitle")

        label.setAlignment(
            label.alignment()
        )

        layout.addWidget(label)

        layout.addStretch()


class KnowledgeHubPage(QWidget):

    def __init__(self):

        super().__init__()

        self.build_ui()

    # -----------------------------------------------------

    def build_ui(self):

        root = QHBoxLayout(self)

        root.setContentsMargins(0, 0, 0, 0)

        root.setSpacing(20)

        # ==================================================
        # LEFT MENU
        # ==================================================

        menu = QFrame()

        menu.setFixedWidth(260)

        menu_layout = QVBoxLayout(menu)

        menu_layout.setContentsMargins(15, 15, 15, 15)

        menu_layout.setSpacing(12)

        title = QLabel("Knowledge Hub")

        title.setObjectName("SectionTitle")

        menu_layout.addWidget(title)

        # --------------------------------------------------

        self.btn_upload = QPushButton(
            "Upload New Knowledge"
        )

        self.btn_smart = QPushButton(
            "AI Smart Upload"
        )

        self.btn_manage = QPushButton(
            "Manage Knowledge"
        )

        self.btn_upload.setMinimumHeight(42)

        self.btn_smart.setMinimumHeight(42)

        self.btn_manage.setMinimumHeight(42)

        menu_layout.addWidget(self.btn_upload)

        menu_layout.addWidget(self.btn_smart)

        menu_layout.addWidget(self.btn_manage)

        menu_layout.addStretch()

        root.addWidget(menu)

        # ==================================================
        # RIGHT PANEL
        # ==================================================

        self.pages = QStackedWidget()

        root.addWidget(self.pages, 1)

        # --------------------------------------------------
        # Upload Manual
        # --------------------------------------------------

        self.upload_page = UploadManualPage()

        self.pages.addWidget(
            self.upload_page
        )

        # --------------------------------------------------
        # AI Smart Upload
        # --------------------------------------------------

        self.smart_page = SmartUploadPage()

        self.pages.addWidget(
            self.smart_page
        )

        # --------------------------------------------------
        # Manage Knowledge
        # --------------------------------------------------

        self.manage_page = ManageKnowledgePage()

        self.pages.addWidget(
            self.manage_page
        )

        # ==================================================
        # Events
        # ==================================================

        self.btn_upload.clicked.connect(
            lambda: self.show_page(0)
        )

        self.btn_smart.clicked.connect(
            lambda: self.show_page(1)
        )

        self.btn_manage.clicked.connect(
            lambda: self.show_page(2)
        )

        self.show_page(0)

    # -----------------------------------------------------

    def show_page(self, index):

        self.pages.setCurrentIndex(index)

        self.update_menu(index)

    # -----------------------------------------------------

    def update_menu(self, active_index):

        buttons = [

            self.btn_upload,

            self.btn_smart,

            self.btn_manage,

        ]

        for index, button in enumerate(buttons):

            if index == active_index:

                button.setStyleSheet(
                    """
                    QPushButton{
                        background:#005B96;
                        color:white;
                        font-weight:bold;
                        border-radius:6px;
                        padding:10px;
                    }
                    """
                )

            else:

                button.setStyleSheet("")