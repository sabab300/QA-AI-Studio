"""
QA AI Studio - Desktop Execution Worker
Location: App/workers/execution_worker.py
"""

from PySide6.QtCore import QThread, Signal  # or PyQt6 / PyQt5

from Core.url_authenticated_session import URLAuthenticatedSession
from Core.business_process_runner import BusinessProcessRunner


class ExecutionWorker(QThread):
    # Signals to update Desktop App UI
    log_signal = Signal(str)
    finished_signal = Signal(dict)

    def __init__(self, target_url: str, analysis: dict, credentials: dict, workflow_steps: list, db_conn=None):
        super().__init__()
        self.target_url = target_url
        self.analysis = analysis
        self.credentials = credentials
        self.workflow_steps = workflow_steps
        self.db_conn = db_conn

    def run(self):
        """
        Executes Auth -> Business Process -> DB Persistence inside this single thread.
        """
        self.log_signal.emit("Starting authenticated Playwright session...")
        
        # 1. Initialize Playwright inside THIS worker thread
        session = URLAuthenticatedSession(headless=False)

        try:
            # 2. Authenticate
            auth_result = session.authenticate(
                url=self.target_url,
                analysis=self.analysis,
                credentials=self.credentials,
            )

            if not auth_result.get("success"):
                self.log_signal.emit(f"Authentication failed: {auth_result.get('error')}")
                self.finished_signal.emit({"success": False, "error": auth_result.get("error")})
                return

            self.log_signal.emit("Authenticated! Starting business process execution...")

            # 3. Instantiate Runner and execute steps on live session
            runner = BusinessProcessRunner(session=session, db_connection=self.db_conn)
            
            process_result = runner.execute_workflow(
                start_url=self.target_url,
                workflow_steps=self.workflow_steps,
                process_id="PROC_001",
            )

            self.log_signal.emit(f"Process complete. Success: {process_result['success']}")
            self.finished_signal.emit(process_result)

        except Exception as ex:
            self.log_signal.emit(f"Worker Error: {str(ex)}")
            self.finished_signal.emit({"success": False, "error": str(ex)})

        finally:
            # 4. Clean up session when thread exits
            session.close()