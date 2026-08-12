"""
QA AI Studio
Authenticated URL Discovery Worker

Development #2D-FIX
"""

from PySide6.QtCore import QObject, Signal

from Core.url_discovery_engine import URLDiscoveryEngine


class URLDiscoveryWorker(QObject):
    """
    Executes authenticated Playwright URL discovery.

    The worker receives an already authenticated
    Playwright browser context.

    Credentials are NOT handled here.
    """

    finished = Signal(dict)
    error = Signal(str)

    def __init__(
        self,
        context,
        url: str,
        authentication_type: str = "NONE",
        headless: bool = False,
    ):
        super().__init__()

        if context is None:
            raise ValueError(
                "Authenticated Playwright context is required."
            )

        self.context = context
        self.url = url
        self.authentication_type = (
            authentication_type or "NONE"
        )
        self.headless = headless

    def run(self):

        try:

            engine = URLDiscoveryEngine(
                context=self.context,
                headless=self.headless,
            )

            result = engine.discover(
                url=self.url,
                authentication_type=(
                    self.authentication_type
                ),
            )

            if not isinstance(result, dict):

                result = {
                    "success": False,
                    "error": (
                        "URL discovery engine returned "
                        "an invalid result."
                    ),
                }

            self.finished.emit(result)

        except Exception as ex:

            self.error.emit(str(ex))