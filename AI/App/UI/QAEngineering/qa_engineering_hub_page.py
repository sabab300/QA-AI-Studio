"""
QA AI Studio — QA Engineering Desktop hub.

Provides one top-level QA Engineering module with real tabs over the existing
Test Case Studio and the shared TestCaseRepository. No duplicate QA data store.
"""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QSizePolicy,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from Core.test_case_repository import TestCaseRepository
from UI.QAEngineering.test_case_generation_page import TestCaseGenerationPage


class _RepositoryTablePage(QWidget):
    def __init__(self, readiness_only=False):
        super().__init__()
        self.repository = TestCaseRepository()
        self.readiness_only = readiness_only
        self._rows = []
        self._build_ui()
        self.refresh()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        toolbar = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search test cases…")
        self.search.textChanged.connect(self._render)
        refresh = QPushButton("Refresh")
        refresh.clicked.connect(self.refresh)
        toolbar.addWidget(self.search, 1)
        toolbar.addWidget(refresh)
        layout.addLayout(toolbar)

        columns = [
            "TC #", "Domain", "Module", "Knowledge", "Version",
            "Test Case", "Automation", "Last Result"
        ]
        if self.readiness_only:
            columns.insert(7, "Readiness")
        self.table = QTableWidget(0, len(columns))
        self.table.setHorizontalHeaderLabels(columns)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(5, QHeaderView.Stretch)
        layout.addWidget(self.table, 1)

    def refresh(self):
        data = self.repository.list_all_test_cases(limit=500, offset=0)
        self._rows = data.get("test_cases", [])
        self._render()

    def _render(self):
        query = self.search.text().strip().lower()
        rows = self._rows
        if query:
            rows = [
                row for row in rows
                if query in " ".join(str(row.get(k, "")) for k in (
                    "tc_number", "domain", "module", "knowledge_name", "test_case", "scenario"
                )).lower()
            ]

        self.table.setRowCount(0)
        for record in rows:
            row_index = self.table.rowCount()
            self.table.insertRow(row_index)
            automation_type = record.get("automation_type") or "None"
            values = [
                record.get("tc_number") or "—",
                record.get("domain") or "—",
                record.get("module") or "—",
                record.get("knowledge_name") or "—",
                record.get("version") or "—",
                record.get("test_case") or record.get("scenario") or "—",
                automation_type,
            ]
            if self.readiness_only:
                active = record.get("active_script_source") or "AUTO"
                script = record.get("recorded_script") if active == "MANUAL" else record.get("automation_script")
                if automation_type in ("None", "", None):
                    readiness = "Manual / Not Automatable"
                elif script:
                    readiness = "Script Available"
                else:
                    readiness = "Automation Required"
                values.append(readiness)
            values.append(record.get("last_result") or "Not Run")

            for col, value in enumerate(values):
                item = QTableWidgetItem(str(value))
                item.setFlags(item.flags() & ~Qt.ItemIsEditable)
                self.table.setItem(row_index, col, item)
        self.table.resizeRowsToContents()


class QAEngineeringHubPage(QWidget):
    TAB_LABELS = (
        "Test Case Studio",
        "Test Case Repository",
        "Execution Readiness",
    )

    def __init__(self):
        super().__init__()
        self._build_ui()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        tabstrip = QFrame()
        tabstrip.setObjectName("DesktopTabStrip")
        row = QHBoxLayout(tabstrip)
        row.setContentsMargins(12, 0, 12, 0)
        row.setSpacing(18)

        self.tab_buttons = [QPushButton(label) for label in self.TAB_LABELS]
        for button in self.tab_buttons:
            button.setObjectName("DesktopTabButton")
            button.setCheckable(True)
            button.setMinimumHeight(30)
            button.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
            row.addWidget(button)
        row.addStretch(1)
        root.addWidget(tabstrip)

        self.pages = QStackedWidget()
        root.addWidget(self.pages, 1)
        self.studio_page = TestCaseGenerationPage()
        self.repository_page = _RepositoryTablePage(readiness_only=False)
        self.readiness_page = _RepositoryTablePage(readiness_only=True)
        self.pages.addWidget(self.studio_page)
        self.pages.addWidget(self.repository_page)
        self.pages.addWidget(self.readiness_page)

        for index, button in enumerate(self.tab_buttons):
            button.clicked.connect(lambda _checked=False, i=index: self.show_page(i))
        self.show_page(0)

    def show_page(self, index):
        self.pages.setCurrentIndex(index)
        if index == 1:
            self.repository_page.refresh()
        elif index == 2:
            self.readiness_page.refresh()
        self._update_tabs(index)

    def _update_tabs(self, active_index):
        for index, button in enumerate(self.tab_buttons):
            button.setChecked(index == active_index)
