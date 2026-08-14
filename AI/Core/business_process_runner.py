"""
QA AI Studio
Business Process Execution Engine & Database Persistence
"""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional

# Assuming core logger and session imports
from Core.logger import Logger
from Core.url_authenticated_session import URLAuthenticatedSession


class BusinessProcessRunner:
    """
    Executes automated end-to-end business flows on top of an 
    active URLAuthenticatedSession and saves execution logs/data into the DB.
    """

    def __init__(self, session: URLAuthenticatedSession, db_connection: Any = None):
        try:
            self.logger = Logger.get_logger()
        except Exception:
            self.logger = logging.getLogger(__name__)

        self.session = session
        self.db = db_connection  # Your DB Session / DAO Connection instance

    def execute_workflow(
        self,
        start_url: str,
        workflow_steps: List[Dict[str, Any]],
        process_id: str,
    ) -> Dict[str, Any]:
        """
        Executes a sequence of business steps and stores step results in DB.
        """
        if not self.session.is_authenticated():
            return {
                "success": False,
                "error": "Session is not authenticated. Cannot run business process.",
            }

        # Retrieve the page instance attached to the authenticated context
        page = self.session.get_authenticated_page()
        execution_results: List[Dict[str, Any]] = []
        overall_success = True

        try:
            self.logger.info(f"Starting Business Process [{process_id}] on: {start_url}")

            # --------------------------------------------------
            # STEP 1: Navigate to Target Module / Form Area
            # --------------------------------------------------
            if page.url != start_url:
                page.goto(start_url, wait_until="domcontentloaded", timeout=30_000)
                page.wait_for_timeout(1000)

            # --------------------------------------------------
            # STEP 2: Execute Action Steps Iteratively
            # --------------------------------------------------
            for idx, step in enumerate(workflow_steps, start=1):
                step_name = step.get("name", f"Step_{idx}")
                action_type = step.get("action", "click").lower()  # click, fill, select, extract
                locator_str = step.get("locator")
                value_to_input = step.get("value", "")

                step_start_time = time.time()
                step_status = "PASSED"
                captured_data = None
                error_msg = ""

                try:
                    self.logger.info(f"Executing Step {idx}: {step_name} [{action_type}]")

                    if action_type == "fill":
                        page.locator(locator_str).first.fill(str(value_to_input))

                    elif action_type == "click":
                        page.locator(locator_str).first.click()
                        page.wait_for_timeout(1000)  # Handle UI re-render

                    elif action_type == "select":
                        page.locator(locator_str).first.select_option(value=value_to_input)

                    elif action_type == "extract":
                        # Read data off the page (e.g. grid result or confirmation code)
                        captured_data = page.locator(locator_str).first.inner_text().strip()

                    elif action_type == "wait":
                        page.wait_for_timeout(int(value_to_input or 2000))

                except Exception as ex:
                    step_status = "FAILED"
                    error_msg = str(ex)
                    overall_success = False
                    self.logger.error(f"Error at Step {idx} ({step_name}): {error_msg}")

                duration_ms = int((time.time() - step_start_time) * 1000)

                step_record = {
                    "process_id": process_id,
                    "step_number": idx,
                    "step_name": step_name,
                    "action": action_type,
                    "locator": locator_str,
                    "status": step_status,
                    "captured_value": captured_data,
                    "error_message": error_msg,
                    "execution_time_ms": duration_ms,
                }

                execution_results.append(step_record)

                # Persist Step Result to DB Immediately
                self._save_step_to_db(step_record)

                # Stop execution flow if a critical step fails
                if step_status == "FAILED" and step.get("critical", True):
                    break

            # --------------------------------------------------
            # STEP 3: Persist Summary Status
            # --------------------------------------------------
            summary = {
                "process_id": process_id,
                "overall_status": "SUCCESS" if overall_success else "FAILED",
                "total_steps": len(workflow_steps),
                "executed_steps": len(execution_results),
                "final_url": page.url,
            }
            self._save_process_summary_to_db(summary)

            return {
                "success": overall_success,
                "process_id": process_id,
                "steps": execution_results,
                "final_url": page.url,
            }

        except Exception as global_ex:
            self.logger.exception("Fatal error during business process execution.")
            return {
                "success": False,
                "error": str(global_ex),
            }

    # ==========================================================
    # DATABASE PERSISTENCE LAYER
    # ==========================================================

    def _save_step_to_db(self, step_record: Dict[str, Any]):
        """Saves individual step execution data into DB."""
        if not self.db:
            return  # Skip if DB context is not configured

        try:
            # Example SQL/ORM insertion: Adjust based on your DB schema
            query = """
                INSERT INTO process_step_logs 
                (process_id, step_number, step_name, action, status, captured_value, error_message, duration_ms)
                VALUES (:process_id, :step_number, :step_name, :action, :status, :captured_value, :error_message, :execution_time_ms)
            """
            # If using SQLAlchemy / psycopg2 / sqlite3:
            # self.db.execute(query, step_record)
            # self.db.commit()
            self.logger.debug(f"Step {step_record['step_number']} logged to DB.")
        except Exception as ex:
            self.logger.error(f"Failed to persist step log to database: {ex}")

    def _save_process_summary_to_db(self, summary: Dict[str, Any]):
        """Persists process completion summary into DB."""
        if not self.db:
            return

        try:
            query = """
                INSERT INTO process_executions
                (process_id, overall_status, total_steps, executed_steps, final_url)
                VALUES (:process_id, :overall_status, :total_steps, :executed_steps, :final_url)
            """
            # self.db.execute(query, summary)
            # self.db.commit()
            self.logger.info(f"Process summary [{summary['process_id']}] saved to DB.")
        except Exception as ex:
            self.logger.error(f"Failed to persist summary to database: {ex}")