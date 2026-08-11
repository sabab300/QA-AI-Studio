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

    def _dismiss_blocking_modals(self, page):
        """Dismiss known blocking modal/overlay dialogs before discovery."""

        selectors = [
            "button:has-text('Close')",
            "button:has-text('OK')",
            "button:has-text('Got it')",
            "[aria-label='Close']",
            "[aria-label='close']",
            ".modal button.close",
            ".modal .btn-close",
            ".SecurityAwarenessModal button",
            ".SecurityAwarenessModal_close",
        ]

        for selector in selectors:
            try:
                locator = page.locator(selector)

                count = locator.count()

                if count == 0:
                    continue

                for index in range(count):
                    button = locator.nth(index)

                    if button.is_visible(timeout=1000):
                        button.click(
                            timeout=3000,
                            force=True,
                        )

                        page.wait_for_timeout(500)

            except Exception:
                continue

        # Generic visible modal/backdrop cleanup
        try:
            page.keyboard.press("Escape")
            page.wait_for_timeout(500)
        except Exception:
            pass