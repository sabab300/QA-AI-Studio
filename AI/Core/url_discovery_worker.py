# AI/Core/url_discovery_worker.py

import logging
from typing import Dict, Any, List, Optional
from AI.Core.url_authenticated_session import UrlAuthenticatedSession
from AI.Core.url_discovery_engine import URLDiscoveryEngine

logger = logging.getLogger(__name__)


async def run_authenticated_discovery(
    login_url: str,
    target_url: str,
    credentials: Dict[str, str],
    detected_fields: Optional[List[Dict[str, Any]]] = None,
    signals: Optional[Any] = None
) -> Optional[Dict[str, Any]]:
    """
    Orchestrates authentication and link discovery over a live Playwright session.
    """
    if detected_fields is None:
        detected_fields = []

    auth_session = UrlAuthenticatedSession()

    try:
        # Step 1: Authenticate and maintain active session
        success = await auth_session.authenticate_and_hold_session(
            target_url=login_url or target_url,
            credentials=credentials,
            detected_fields=detected_fields
        )

        live_page = auth_session.get_page()

        # Step 2: Validate live session
        if not success or live_page is None:
            error_msg = "Authentication completed but no live authenticated Playwright session was returned."
            logger.error(error_msg)
            if signals and hasattr(signals, 'error'):
                signals.error.emit(error_msg)
            return None

        # Step 3: Execute link discovery using active page
        logger.info("Starting authenticated URL discovery...")
        discovery_engine = URLDiscoveryEngine(page=live_page)
        discovered_urls = await discovery_engine.discover(url=target_url)

        if signals and hasattr(signals, 'finished'):
            signals.finished.emit(discovered_urls)

        return discovered_urls

    except Exception as e:
        logger.exception(f"Error during authenticated discovery: {str(e)}")
        if signals and hasattr(signals, 'error'):
            signals.error.emit(str(e))
        return None

    finally:
        # Step 4: Cleanup browser resources
        await auth_session.close()


class URLDiscoveryWorker:
    """Worker wrapper for Qt thread signal management."""

    def __init__(self, signals=None):
        self.signals = signals

    async def run(self, login_url: str, target_url: str, credentials: Dict[str, str], detected_fields: Optional[List[Dict[str, Any]]] = None):
        return await run_authenticated_discovery(
            login_url=login_url,
            target_url=target_url,
            credentials=credentials,
            detected_fields=detected_fields,
            signals=self.signals
        )