"""
QA AI Studio - Unified Smart Upload Discovery Worker
Location: UI/KnowledgeHub/smart_upload_worker.py

Executes Playwright Session Initialization, Authentication, and URL Discovery
sequentially inside a single QThread context to prevent Playwright thread-switch errors.
"""

from __future__ import annotations

import logging
import time
from PySide6.QtCore import QThread, Signal

from Core.url_authenticated_session import URLAuthenticatedSession
from AI.Core.url_discovery_engine import URLDiscoveryEngine


class SmartUploadWorker(QThread):
    """
    Unified worker that keeps Playwright alive across both Auth and Discovery stages.
    """
    log_signal = Signal(str)
    progress_signal = Signal(int, str)
    finished_signal = Signal(dict)

    def __init__(
        self,
        target_url: str,
        analysis_data: dict,
        credentials: dict,
        db_conn=None,
        parent=None,
    ):
        super().__init__(parent)
        self.target_url = target_url
        self.analysis_data = analysis_data
        self.credentials = credentials
        self.db_conn = db_conn
        self.logger = logging.getLogger(__name__)

    def run(self):
        """Sequential single-threaded execution."""
        self.log_signal.emit("Initializing authenticated Playwright environment...")
        self.progress_signal.emit(10, "Starting Playwright Engine...")

        # 1. Initialize Session INSIDE this thread
        session = URLAuthenticatedSession(headless=False)

        try:
            # 2. Authenticate
            self.log_signal.emit(f"Authenticating access for: {self.target_url}")
            self.progress_signal.emit(30, "Logging into application...")

            auth_res = session.authenticate(
                url=self.target_url,
                analysis=self.analysis_data,
                credentials=self.credentials,
            )

            if not auth_res.get("success"):
                error_msg = auth_res.get("error", "Authentication failed.")
                self.log_signal.emit(f"Authentication Failed: {error_msg}")
                self.finished_signal.emit({"success": False, "error": error_msg})
                return

            self.log_signal.emit("Authenticated successfully! Waiting for post-login dashboard to load...")
            self.progress_signal.emit(50, "Waiting for page rendering...")

            time.sleep(3)

            # ... Authenticated session steps ...

            # 3. Extract active context/page from the live session
            if hasattr(session, "get_authenticated_context"):
                context = session.get_authenticated_context()
            else:
                context = getattr(session, "context", None)

            if context is None:
                raise ValueError("Could not obtain active Playwright context from session.")

            # Apply global timeouts across all open context pages
            for p in context.pages:
                p.set_default_navigation_timeout(30000)
                p.set_default_timeout(30000)

            self.log_signal.emit("Transitioning to URL discovery engine...")
            self.progress_signal.emit(70, "Discovering UI elements...")

            # 4. Instantiate URLDiscoveryEngine
            discovery_engine = URLDiscoveryEngine(context=context, db_conn=self.db_conn)

            # 5. Execute discover()
            discovery_result = discovery_engine.discover(self.target_url)

            self.progress_signal.emit(100, "Discovery Complete.")
            self.log_signal.emit("URL discovery completed successfully!")

            # 6. Normalize discovery output structure
            payload = {"success": True}

            if isinstance(discovery_result, dict):
                raw_data = discovery_result.get("data", discovery_result) if "data" in discovery_result else discovery_result
                if isinstance(raw_data, dict):
                    payload.update(raw_data)
                else:
                    payload["data"] = raw_data
            else:
                payload["data"] = discovery_result

            for key in ["pages", "forms", "fields", "buttons", "links", "tabs", "navigation_targets"]:
                if key not in payload or not isinstance(payload[key], list):
                    payload[key] = payload.get(key, [])

            self.finished_signal.emit(payload)

        except Exception as ex:
            self.logger.exception("Unified Smart Upload Worker encountered an error.")
            self.log_signal.emit(f"Execution Error: {str(ex)}")
            self.finished_signal.emit({"success": False, "error": str(ex)})

        finally:
            self.log_signal.emit("Discovery finished. Playwright browser kept open.")