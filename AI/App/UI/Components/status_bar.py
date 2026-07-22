"""
QA AI Studio
Status Bar Widget

Version: 1.0
"""

from datetime import datetime

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QLabel


class StatusBarWidget(QLabel):

    # --------------------------------------------------

    def __init__(self):

        super().__init__()

        self.timer = QTimer(self)

        self.timer.timeout.connect(
            self.update_clock
        )

        self.timer.start(1000)

        self.update_clock()

    # --------------------------------------------------

    def update_clock(self):

        now = datetime.now().strftime(
            "%d-%b-%Y %I:%M:%S %p"
        )

        self.setText(
            f"Ready    |    {now}"
        )