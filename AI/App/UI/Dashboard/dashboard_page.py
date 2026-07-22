"""
QA AI Studio
Dashboard Page

Version: 1.0
"""

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QWidget,
    QLabel,
    QVBoxLayout,
    QGridLayout,
    QFrame,
)

from UI.Components.info_card import InfoCard

from Config import settings
from Core.metadata_manager import MetadataManager


class DashboardPage(QWidget):

    def __init__(self):

        super().__init__()

        self.metadata = MetadataManager()

        self.build_ui()

        self.load_dashboard()

    # --------------------------------------------------

    def build_ui(self):

        layout = QVBoxLayout(self)

        layout.setContentsMargins(20, 20, 20, 20)

        layout.setSpacing(20)

        # ----------------------------------------------

        title = QLabel("QA AI Studio Dashboard")

        title.setObjectName("PageTitle")

        layout.addWidget(title)

        # ----------------------------------------------

        self.grid = QGridLayout()

        self.grid.setHorizontalSpacing(20)

        self.grid.setVerticalSpacing(20)

        layout.addLayout(self.grid)

        # ----------------------------------------------

        self.card_knowledge = InfoCard(
            "Knowledge Items",
            0
        )

        self.card_domains = InfoCard(
            "Domains",
            0
        )

        self.card_modules = InfoCard(
            "Modules",
            0
        )

        self.card_versions = InfoCard(
            "Versions",
            0
        )

        self.card_ai = InfoCard(
            "AI Mode",
            settings.AI_MODE.upper()
        )

        self.card_model = InfoCard(
            "LLM Model",
            settings.LLM_MODEL
        )

        self.grid.addWidget(
            self.card_knowledge,
            0,
            0
        )

        self.grid.addWidget(
            self.card_domains,
            0,
            1
        )

        self.grid.addWidget(
            self.card_modules,
            0,
            2
        )

        self.grid.addWidget(
            self.card_versions,
            1,
            0
        )

        self.grid.addWidget(
            self.card_ai,
            1,
            1
        )

        self.grid.addWidget(
            self.card_model,
            1,
            2
        )

        # ----------------------------------------------

        info = QFrame()

        info.setObjectName("Card")

        info_layout = QVBoxLayout(info)

        info_layout.setContentsMargins(20, 20, 20, 20)

        info_layout.setSpacing(10)

        lbl = QLabel(
            "Enterprise QA AI Studio\n"
            "Pakistan Single Window"
        )

        lbl.setAlignment(Qt.AlignCenter)

        lbl.setStyleSheet(
            "font-size:18px;font-weight:bold;"
        )

        info_layout.addWidget(lbl)

        layout.addWidget(info)

    # --------------------------------------------------

    def load_dashboard(self):

        try:

            items = self.metadata.list_all()

        except Exception:

            items = []

        knowledge = len(items)

        domains = len({
            i[1]
            for i in items
        }) if items else 0

        modules = len({
            i[2]
            for i in items
        }) if items else 0

        versions = len({
            i[4]
            for i in items
        }) if items else 0

        self.card_knowledge.set_value(
            knowledge
        )

        self.card_domains.set_value(
            domains
        )

        self.card_modules.set_value(
            modules
        )

        self.card_versions.set_value(
            versions
        )