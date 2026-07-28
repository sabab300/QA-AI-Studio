"""
QA AI Studio

Upload Summary Dialog

Production Version 1.0
"""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QTextBrowser,
    QPushButton,
    QHBoxLayout
)


class UploadSummaryDialog(QDialog):

    def __init__(self, html, parent=None):

        super().__init__(parent)

        self.setWindowTitle("Knowledge Upload Result")

        self.resize(900, 650)

        self.setMinimumSize(850, 600)

        layout = QVBoxLayout(self)

        self.viewer = QTextBrowser()

        self.viewer.setOpenExternalLinks(True)

        self.viewer.setReadOnly(True)

        self.viewer.setHtml(html)

        layout.addWidget(self.viewer)

        buttons = QHBoxLayout()

        buttons.addStretch()

        close_btn = QPushButton("Close")

        close_btn.setMinimumWidth(120)

        close_btn.clicked.connect(self.accept)

        buttons.addWidget(close_btn)

        layout.addLayout(buttons)