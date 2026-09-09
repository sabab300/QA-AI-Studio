"""
QA AI Studio — Knowledge Hub Desktop hub.

Desktop navigation parity: Knowledge Hub is one main module and its working
sub-functions are horizontal tabs, matching the Web mental model while
preserving the established PySide6 pages and Core services.
"""

from PySide6.QtWidgets import (
    QWidget,
    QHBoxLayout,
    QVBoxLayout,
    QPushButton,
    QLabel,
    QFrame,
    QStackedWidget,
    QSizePolicy,
)

from UI.KnowledgeHub.upload_manual_page import UploadManualPage
from UI.KnowledgeHub.manage_knowledge_page import ManageKnowledgePage
from UI.KnowledgeHub.smart_upload_page import SmartUploadPage


class KnowledgeHubPage(QWidget):
    TAB_LABELS = (
        "Manage Knowledge",
        "Upload New Knowledge",
        "AI Smart Upload",
    )

    def __init__(self):
        super().__init__()
        self.build_ui()

    def build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        tabstrip = QFrame()
        tabstrip.setObjectName("DesktopTabStrip")
        tabstrip_layout = QHBoxLayout(tabstrip)
        tabstrip_layout.setContentsMargins(15, 10, 15, 10)
        tabstrip_layout.setSpacing(8)

        title = QLabel("Knowledge Hub")
        title.setObjectName("SectionTitle")
        tabstrip_layout.addWidget(title)
        tabstrip_layout.addSpacing(20)

        self.btn_manage = QPushButton(self.TAB_LABELS[0])
        self.btn_upload = QPushButton(self.TAB_LABELS[1])
        self.btn_smart = QPushButton(self.TAB_LABELS[2])
        self.tab_buttons = (self.btn_manage, self.btn_upload, self.btn_smart)

        for button in self.tab_buttons:
            button.setMinimumHeight(36)
            button.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
            tabstrip_layout.addWidget(button)

        tabstrip_layout.addStretch(1)
        root.addWidget(tabstrip)

        self.pages = QStackedWidget()
        root.addWidget(self.pages, 1)

        # Preserve the existing, working pages; only navigation changes.
        self.manage_page = ManageKnowledgePage()
        self.upload_page = UploadManualPage()
        self.smart_page = SmartUploadPage()
        self.pages.addWidget(self.manage_page)
        self.pages.addWidget(self.upload_page)
        self.pages.addWidget(self.smart_page)

        self.btn_manage.clicked.connect(lambda: self.show_page(0))
        self.btn_upload.clicked.connect(lambda: self.show_page(1))
        self.btn_smart.clicked.connect(lambda: self.show_page(2))
        self.show_page(0)

    def show_page(self, index):
        self.pages.setCurrentIndex(index)
        self.update_tabstrip(index)

        # Refresh real persisted hierarchy when returning to Manage Knowledge,
        # where the page exposes a refresh/load method.
        if index == 0:
            for method_name in ("refresh", "refresh_tree", "load_data", "load_knowledge"):
                method = getattr(self.manage_page, method_name, None)
                if callable(method):
                    try:
                        method()
                    except TypeError:
                        pass
                    break

    def update_tabstrip(self, active_index):
        for index, button in enumerate(self.tab_buttons):
            if index == active_index:
                button.setStyleSheet(
                    "QPushButton{background:#005B96;color:white;font-weight:bold;"
                    "border-radius:6px;padding:8px 14px;}"
                )
            else:
                button.setStyleSheet(
                    "QPushButton{background:transparent;border-radius:6px;padding:8px 14px;}"
                    "QPushButton:hover{background:rgba(0,91,150,0.12);}"
                )
