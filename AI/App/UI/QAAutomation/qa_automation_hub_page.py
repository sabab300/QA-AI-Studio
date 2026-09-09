# Create: App/UI/QAAutomation/qa_automation_hub_page.py

"""
==========================================================
QA AI Studio

QA Automation Hub

Version : 3.0

REDESIGN (QA-AUTOMATION-FINAL-ARCHITECTURE-04): PRODUCT DECISION —
QA Automation now has exactly 3 primary tabs, matching the Web port
exactly (AI-Web/Frontend/index.html's auto-playwright / auto-api /
auto-sql panels):

    Playwright Automation  -> TestExecutionPage(automation_type_filter="Playwright")
    API Automation          -> TestExecutionPage(automation_type_filter="API")
    SQL Automation          -> SqlAutomationPage() (new — see its own
                                module docstring)

Git Automation, ClickUp Automation and Test Manager Automation are
NO LONGER separate tabs here:

    Git Automation          -> unwired from this page's navigation
                                only. git_automation_page.py /
                                git_automation_worker.py are left
                                completely untouched in the tree —
                                nothing was deleted — for a possible
                                future "QA/test documentation
                                repository" use, per this task's
                                explicit instruction to preserve
                                reusable Core/UI code rather than
                                delete it unnecessarily.
    ClickUp Automation       -> no longer a standalone tab at all.
                                ClickUp is now a real, working
                                CONTEXTUAL action — a "Create Bug"
                                button shown only on FAILED rows in
                                the Playwright/API tabs' grids (see
                                test_execution_page.py's ClickUp
                                column) and the SQL tab's grid (see
                                sql_automation_page.py) — configured
                                once from either tab's Test
                                Environment Setting (Core/
                                clickup_config.py / clickup_client.py).
    Test Manager Automation  -> fully deferred, no UI at all (not even
                                a placeholder tab) — still needs real
                                API details for testmanager.psw.gov.pk
                                from the product owner before ANY of
                                it can be built for real, per this
                                project's "no fake completion" rule.

Selenium is not required for this product and was removed as an
Automation Type option in test_execution_page.py.

This file only changes how you switch between the 3 primary
sub-sections — it does not duplicate any of their business logic
(see TestExecutionPage's own `automation_type_filter` docstring for
how one ~3700-line class safely backs two of these three tabs without
being copy-pasted).

Note (v1.1, unchanged): "API Upload" used to be its own sub-section
here. It now lives in Knowledge Hub -> Upload New Knowledge (Source
Type: "API Collection") instead, alongside every other knowledge
source, with imported endpoints viewable/editable from Manage
Knowledge — one upload entry point instead of two. See
api_upload_page.py's module docstring for the old standalone page
(kept in the tree for reference; no longer wired in anywhere).
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
    QSizePolicy,
)

from UI.QAAutomation.test_execution_page import TestExecutionPage

# TEMP: SQL Automation disabled for Desktop startup.
# Missing Core/sql_environment_config.py and related SQL backend modules.
# Re-enable when SQL Automation backend files are restored.
# from UI.QAAutomation.sql_automation_page import SqlAutomationPage

from Core.test_case_repository import TestCaseRepository


class QAAutomationHubPage(QWidget):

    TAB_LABELS = (
        "Playwright Automation",
        "API Automation",
        "SQL Automation",
    )

    def __init__(self):

        super().__init__()

        self.build_ui()

    # -----------------------------------------------------

    def build_ui(self):

        registered_tools = TestCaseRepository().list_execution_tools()
        tool_labels = tuple(item["label"] for item in registered_tools)
        labels = tool_labels if len(tool_labels) == 3 else self.TAB_LABELS

        root = QVBoxLayout(self)

        root.setContentsMargins(0, 0, 0, 0)

        root.setSpacing(0)

        # ==================================================
        # HORIZONTAL TAB STRIP
        # ==================================================
        # A single row instead of a permanent side column — the
        # active sub-section's own page now gets the full width of
        # this widget, not (full width - 260px).

        tabstrip = QFrame()

        tabstrip.setObjectName("QaAutomationTabStrip")

        tabstrip_layout = QHBoxLayout(tabstrip)

        tabstrip_layout.setContentsMargins(12, 0, 12, 0)

        tabstrip_layout.setSpacing(18)

        self.btn_playwright = QPushButton(labels[0])

        self.btn_api = QPushButton(labels[1])

        self.btn_sql = QPushButton(labels[2])

        self.tab_buttons = (
            self.btn_playwright,
            self.btn_api,
            self.btn_sql,
        )

        for btn in self.tab_buttons:

            btn.setObjectName("DesktopTabButton")
            btn.setCheckable(True)
            btn.setMinimumHeight(30)

            # QA-AUTOMATION-FINAL-ARCHITECTURE-04 hidden-bug fix: this
            # used to call btn.setCursor(0) — passing a plain int
            # instead of a real Qt.CursorShape/QCursor. This PySide6
            # version rejects that with a genuine runtime TypeError
            # ("called with wrong argument types") the very first time
            # QA Automation was opened, caught by actually launching
            # the app rather than just compiling it. ArrowCursor is
            # already every QWidget's default, so the fix is simply to
            # not call setCursor() at all here.

            btn.setSizePolicy(
                QSizePolicy.Preferred, QSizePolicy.Fixed
            )

            tabstrip_layout.addWidget(btn)

        tabstrip_layout.addStretch(1)

        root.addWidget(tabstrip)

        # ==================================================
        # ACTIVE TAB WORKSPACE — gets all remaining width/height
        # ==================================================

        self.pages = QStackedWidget()

        root.addWidget(self.pages, 1)

        self.playwright_page = TestExecutionPage(
            automation_type_filter="Playwright"
        )

        self.pages.addWidget(self.playwright_page)

        self.api_page = TestExecutionPage(
            automation_type_filter="API"
        )

        self.pages.addWidget(self.api_page)

        # TEMP: SQL Automation disabled for Desktop startup.
        # self.sql_page = SqlAutomationPage()
        # self.pages.addWidget(self.sql_page)
        self.sql_page = None

        # ==================================================
        # Events
        # ==================================================

        self.btn_playwright.clicked.connect(lambda: self.show_page(0))

        self.btn_api.clicked.connect(lambda: self.show_page(1))

        self.btn_sql.clicked.connect(lambda: self.show_page(2))

        self.show_page(0)

    # -----------------------------------------------------

    def show_page(self, index):

        self.pages.setCurrentIndex(index)

        self.update_tabstrip(index)

    # -----------------------------------------------------

    def update_tabstrip(self, active_index):

        for index, button in enumerate(self.tab_buttons):
            button.setChecked(index == active_index)
