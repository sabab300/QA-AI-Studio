"""
QA AI Studio - Unified Smart Upload Discovery Worker
Location: UI/KnowledgeHub/smart_upload_worker.py

Executes Playwright Session Initialization, Authentication, and URL Discovery
sequentially inside a single QThread context to prevent Playwright thread-switch errors.
"""

from __future__ import annotations

import logging
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

            self.log_signal.emit("Authenticated successfully! Transitioning to discovery...")
            self.progress_signal.emit(60, "Running URL Discovery Engine...")

            # 3. Extract active context/page from the live session
            if hasattr(session, "get_authenticated_context"):
                context = session.get_authenticated_context()
            else:
                context = getattr(session, "context", None)

            if context is None:
                raise ValueError("Could not obtain active Playwright context from session.")

            # 4. Instantiate URLDiscoveryEngine
            try:
                discovery_engine = URLDiscoveryEngine(context=context, db_conn=self.db_conn)
            except TypeError:
                discovery_engine = URLDiscoveryEngine(context=context)

            # 5. Execute discover() with flexible parameter fallbacks
            try:
                # Try positional argument first
                discovery_result = discovery_engine.discover(self.target_url)
            except TypeError:
                try:
                    # Try keyword argument 'url'
                    discovery_result = discovery_engine.discover(url=self.target_url)
                except TypeError:
                    # Try zero-argument call (if engine discovers current active page)
                    discovery_result = discovery_engine.discover()

            self.progress_signal.emit(100, "Discovery Complete.")
            self.log_signal.emit("URL discovery completed successfully!")
            
            self.finished_signal.emit({
                "success": True,
                "data": discovery_result
            })

        except Exception as ex:
            self.logger.exception("Unified Smart Upload Worker encountered an error.")
            self.log_signal.emit(f"Execution Error: {str(ex)}")
            self.finished_signal.emit({"success": False, "error": str(ex)})

        finally:
            # 6. Clean up Playwright resources on thread exit
            try:
                session.close()
            except Exception:
                pass