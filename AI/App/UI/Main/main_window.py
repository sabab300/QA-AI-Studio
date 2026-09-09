"""
QA AI Studio
Main Window

Version: 3.0
"""

from pathlib import Path
from UI.QAEngineering.qa_engineering_hub_page import QAEngineeringHubPage
from UI.QAAutomation.qa_automation_hub_page import QAAutomationHubPage
from UI.UserManagement.user_management_page import UserManagementPage



from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QStackedWidget,
    QStatusBar,
    QVBoxLayout,
    QWidget,
)

from UI.Main.navigation import NavigationPanel
from UI.Components.top_bar import TopBar
from UI.Components.status_bar import StatusBarWidget

from UI.Dashboard.dashboard_page import DashboardPage
from UI.KnowledgeHub.knowledge_hub_page import KnowledgeHubPage


class MainWindow(QMainWindow):

    def __init__(self):

        super().__init__()

        self.setWindowTitle("QA AI Studio")

        self.resize(1600, 900)

        self.setMinimumSize(1300, 800)

        self._load_stylesheet()

        self._build_ui()

    # --------------------------------------------------

    def _load_stylesheet(self):

        style = (
            Path(__file__)
            .resolve()
            .parents[1]
            / "Resources"
            / "style.qss"
        )

        if style.exists():

            with open(style, "r", encoding="utf-8") as f:

                QApplication.instance().setStyleSheet(
                    f.read()
                )

    # --------------------------------------------------

    def _build_ui(self):

        central = QWidget()

        central.setObjectName("CentralWidget")

        self.setCentralWidget(central)

        root = QVBoxLayout(central)

        root.setContentsMargins(0, 0, 0, 0)

        root.setSpacing(0)

        # ------------------------------------------------

        self.topbar = TopBar()

        root.addWidget(self.topbar)

        # ------------------------------------------------

        body = QHBoxLayout()

        body.setContentsMargins(0, 0, 0, 0)

        body.setSpacing(0)

        root.addLayout(body)

        # ------------------------------------------------

        self.navigation = NavigationPanel()

        body.addWidget(self.navigation)

        # ------------------------------------------------

        content = QFrame()

        content.setObjectName("ContentArea")

        body.addWidget(content, 1)

        content_layout = QVBoxLayout(content)

        content_layout.setContentsMargins(
            25,
            20,
            25,
            20
        )

        content_layout.setSpacing(15)

        # ------------------------------------------------

        self.page_title = QLabel("Dashboard")

        self.page_title.setObjectName("PageTitle")

        content_layout.addWidget(self.page_title)

        # ------------------------------------------------

        self.pages = QStackedWidget()

        content_layout.addWidget(self.pages)

        self._create_pages()

        # ------------------------------------------------

        self.status = QStatusBar()

        self.setStatusBar(self.status)

        self.status_widget = StatusBarWidget()

        self.status.addPermanentWidget(
            self.status_widget
        )

        # ------------------------------------------------

        self.navigation.page_changed.connect(
            self.change_page
        )

    # --------------------------------------------------

    def _create_pages(self):

        # ------------------------------------------------
        # Dashboard
        # ------------------------------------------------

        self.dashboard_page = DashboardPage()

        self.pages.addWidget(
            self.dashboard_page
        )

        # ------------------------------------------------
        # Knowledge Hub
        # (Owns Upload Manual / AI Smart Upload /
        #  Manage Knowledge internally)
        # ------------------------------------------------

        self.knowledge_page = KnowledgeHubPage()

        self.pages.addWidget(
            self.knowledge_page
        )

        # ------------------------------------------------
        # QA Engineering
        # ------------------------------------------------

        self.qa_page = QAEngineeringHubPage()
 
        self.pages.addWidget(
            self.qa_page
        )

        # ------------------------------------------------
        # QA Automation
        # ------------------------------------------------

        self.automation_page = QAAutomationHubPage()
 
        self.pages.addWidget(
            self.automation_page
        )

        # ------------------------------------------------
        # AI Assistant
        # ------------------------------------------------

        self.ai_page = self._placeholder(
            "AI Assistant"
        )

        self.pages.addWidget(
            self.ai_page
        )

        # ------------------------------------------------
        # User Management
        # ------------------------------------------------

        self.user_management_page = UserManagementPage()

        self.pages.addWidget(
            self.user_management_page
        )

        # ------------------------------------------------
        # Settings
        # ------------------------------------------------

        self.settings_page = self._placeholder(
            "Settings"
        )

        self.pages.addWidget(
            self.settings_page
        )

    # --------------------------------------------------

    def _placeholder(self, title):

        page = QWidget()

        layout = QVBoxLayout(page)

        layout.setAlignment(Qt.AlignCenter)

        label = QLabel(title)

        label.setObjectName("SectionTitle")

        layout.addWidget(label)

        return page

    # --------------------------------------------------

    def change_page(self, index, title):

        self.pages.setCurrentIndex(index)

        self.page_title.setText(title)

        self.status.showMessage(

            f"Opened : {title}",

            3000

        )