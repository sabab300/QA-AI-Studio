# Create: App/UI/QAAutomation/qa_automation_hub_page.py

"""
==========================================================
QA AI Studio

QA Automation Hub

Version : 1.0

Mirrors Knowledge Hub's layout exactly: a left sidebar of
sub-sections, and a stacked panel on the right.

Sub-sections (per spec):
    Test Execution Automation  -> built (Phase 1)
    Git Automation             -> built today
    ClickUp Automation         -> placeholder, waiting on your
                                   API token + workspace/list ID
    Test Manager Automation    -> placeholder, waiting on API
                                   details for testmanager.psw.gov.pk

Note (v1.1): "API Upload" used to be its own sub-section here. It
now lives in Knowledge Hub -> Upload New Knowledge (Source Type:
"API Collection") instead, alongside every other knowledge source,
with imported endpoints viewable/editable from Manage Knowledge —
one upload entry point instead of two. See api_upload_page.py's
module docstring for the old standalone page (kept in the tree for
reference; no longer wired in anywhere).
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

from UI.QAAutomation.test_execution_page import TestExecutionPage

from UI.QAAutomation.git_automation_page import GitAutomationPage

class PlaceholderPage(QWidget):

    def __init__(self, title, note=""):

        super().__init__()

        layout = QVBoxLayout(self)

        layout.addStretch()

        label = QLabel(title)

        label.setObjectName("SectionTitle")

        layout.addWidget(label)

        if note:

            note_label = QLabel(note)

            note_label.setWordWrap(True)

            layout.addWidget(note_label)

        layout.addStretch()


class QAAutomationHubPage(QWidget):

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

        title = QLabel("QA Automation")

        title.setObjectName("SectionTitle")

        menu_layout.addWidget(title)

        # --------------------------------------------------

        self.btn_execution = QPushButton(
            "Test Execution Automation"
        )

        self.btn_git = QPushButton(
            "Git Automation"
        )

        self.btn_clickup = QPushButton(
            "ClickUp Automation"
        )

        self.btn_testmanager = QPushButton(
            "Test Manager Automation"
        )

        for btn in (
            self.btn_execution,
            self.btn_git,
            self.btn_clickup,
            self.btn_testmanager,
        ):

            btn.setMinimumHeight(42)

            menu_layout.addWidget(btn)

        menu_layout.addStretch()

        root.addWidget(menu)

        # ==================================================
        # RIGHT PANEL
        # ==================================================

        self.pages = QStackedWidget()

        root.addWidget(self.pages, 1)

        self.execution_page = TestExecutionPage()

        self.pages.addWidget(self.execution_page)

        self.git_page = GitAutomationPage()

        self.pages.addWidget(self.git_page)

        self.clickup_page = PlaceholderPage(
            "ClickUp Automation",
            "Coming soon — send your ClickUp API token and "
            "Workspace/List ID and this will be built next."
        )

        self.pages.addWidget(self.clickup_page)

        self.testmanager_page = PlaceholderPage(
            "Test Manager Automation",
            "Coming soon — send the API details for "
            "testmanager.psw.gov.pk and this will be built next."
        )

        self.pages.addWidget(self.testmanager_page)

        # ==================================================
        # Events
        # ==================================================

        self.btn_execution.clicked.connect(
            lambda: self.show_page(0)
        )

        self.btn_git.clicked.connect(
            lambda: self.show_page(1)
        )

        self.btn_clickup.clicked.connect(
            lambda: self.show_page(2)
        )

        self.btn_testmanager.clicked.connect(
            lambda: self.show_page(3)
        )

        self.show_page(0)

    # -----------------------------------------------------

    def show_page(self, index):

        self.pages.setCurrentIndex(index)

        self.update_menu(index)

    # -----------------------------------------------------

    def update_menu(self, active_index):

        buttons = [
            self.btn_execution,
            self.btn_git,
            self.btn_clickup,
            self.btn_testmanager,
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