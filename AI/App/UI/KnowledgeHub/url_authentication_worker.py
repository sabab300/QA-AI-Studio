"""
QA AI Studio
Authenticated Playwright Worker
Development #2C

Credentials are memory-only.
No username/password is persisted.
"""

from PySide6.QtCore import QObject, Signal

from Core.url_authenticated_session import URLAuthenticatedSession


class URLAuthenticationWorker(QObject):

    finished = Signal(dict)
    error = Signal(str)
    progress = Signal(str)

    def __init__(
        self,
        url,
        analysis,
        credentials,
        headless=False,
    ):
        super().__init__()

        self.url = url
        self.analysis = analysis or {}
        self.credentials = credentials or {}
        self.headless = headless

    def run(self):

        try:

            self.progress.emit(
                "Starting authenticated Playwright session..."
            )

            session = URLAuthenticatedSession(
                headless=self.headless
            )

            result = session.authenticate(
                url=self.url,
                analysis=self.analysis,
                credentials=self.credentials,
            )

            self.finished.emit(result)

        except Exception as ex:

            self.error.emit(str(ex))