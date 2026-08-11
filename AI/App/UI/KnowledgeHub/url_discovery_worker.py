"""
QA AI Studio
Authenticated URL Discovery Worker
Development #2D
"""

from PySide6.QtCore import QObject, Signal

from Core.url_discovery_engine import URLDiscoveryEngine


class URLDiscoveryWorker(QObject):

    finished = Signal(dict)
    error = Signal(str)

    def __init__(
        self,
        url: str,
        credentials: dict | None = None,
        authentication_type: str = "NONE",
        headless: bool = True,
    ):
        super().__init__()

        self.url = url
        self.credentials = credentials or {}
        self.authentication_type = authentication_type
        self.headless = headless

    def run(self):
        try:
            engine = URLDiscoveryEngine(
                headless=self.headless
            )

            result = engine.discover(
                url=self.url,
                credentials=self.credentials,
                authentication_type=self.authentication_type,
            )

            self.finished.emit(result)

        except Exception as ex:
            self.error.emit(str(ex))