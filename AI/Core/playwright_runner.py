# Create: AI/Core/playwright_runner.py

"""
QA AI Studio
Playwright Runner

Version: 1.0

Actually executes a generated Playwright script in a real browser.

Runs it as a SEPARATE PROCESS (not exec() inside the app) so:
    - A hung or crashed script can't freeze QA AI Studio
    - A timeout can forcibly kill it
    - stdout/stderr are captured cleanly for the result dialog

Requires (not in requirements.txt by default — add these):
    pip install playwright
    playwright install chromium

Safety note: this runs whatever script text you pass it, in a real
browser, against whatever URL is inside that script. Review the
script (View Script) before running it, especially before pointing
it at a real/production PSW environment rather than a test/UAT one.
"""

import re
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path

from Core.logger import Logger


OUTPUT_FOLDER = Path("Output") / "AutomationRuns"

DEFAULT_TIMEOUT_SECONDS = 120


class PlaywrightRunner:

    def __init__(self):

        self.logger = Logger.get_logger()

        OUTPUT_FOLDER.mkdir(parents=True, exist_ok=True)

    # --------------------------------------------------

    def _strip_code_fences(self, text):
        """
        Same fix as TestExecutionManager._strip_code_fences() —
        duplicated here so already-stored scripts (generated before
        that fix existed) also get cleaned right before running,
        without needing to regenerate them.
        """

        if not text:

            return text

        text = text.strip()

        match = re.search(
            r"```(?:[a-zA-Z0-9]*)?\n(.*?)```",
            text,
            re.DOTALL,
        )

        if match:

            return match.group(1).strip()

        lines = text.split("\n")

        if lines and lines[0].strip().startswith("```"):

            lines = lines[1:]

        if lines and lines[-1].strip() == "```":

            lines = lines[:-1]

        return "\n".join(lines).strip()

    # --------------------------------------------------

    def _looks_like_pytest_style(self, script_text):
        """
        Old scripts (generated before the standalone-format prompt
        fix) commonly use @pytest.fixture / class-based tests and
        have no if __name__ == "__main__" block, so `python
        script.py` just does nothing or errors on indentation —
        they need pytest itself to run, and rely on fixtures
        (like the pytest-playwright plugin's `page` fixture) that
        may not even be installed. Rather than guess and produce a
        confusing traceback, detect this up front and say so
        clearly.
        """

        has_main_block = "__main__" in script_text

        has_pytest_markers = (
            "@pytest" in script_text
            or "def test_" in script_text
            or "class Test" in script_text
        )

        return has_pytest_markers and not has_main_block

    # --------------------------------------------------

    def is_playwright_installed(self):

        try:

            import playwright  # noqa: F401

            return True

        except ImportError:

            return False

    # --------------------------------------------------

    def run_script(self, script_text, tc_number="script", timeout_seconds=None):

        if not self.is_playwright_installed():

            return {
                "success": False,
                "error": (
                    "Playwright isn't installed yet. Run these two "
                    "commands in your terminal, then try again:\n\n"
                    "pip install playwright\n"
                    "playwright install chromium"
                ),
            }

        timeout_seconds = timeout_seconds or DEFAULT_TIMEOUT_SECONDS

        script_text = self._strip_code_fences(script_text)

        if self._looks_like_pytest_style(script_text):

            return {
                "success": False,
                "error": (
                    "This script was generated in an older pytest-"
                    "style format that isn't directly runnable here. "
                    "Click 'Update Automation' on this test case to "
                    "regenerate it in the standalone format, then "
                    "try Execute again."
                ),
            }

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

        safe_name = "".join(
            c if c.isalnum() else "_" for c in tc_number
        )

        script_path = OUTPUT_FOLDER / f"{safe_name}_{timestamp}.py"

        script_path.write_text(script_text, encoding="utf-8")

        self.logger.info(
            f"Running Playwright script: {script_path}"
        )

        start = datetime.now()

        try:

            process = subprocess.run(
                [sys.executable, str(script_path)],
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
            )

            duration = (datetime.now() - start).total_seconds()

            passed = process.returncode == 0

            self.logger.info(
                f"Playwright script finished in {duration:.1f}s — "
                f"{'PASS' if passed else 'FAIL'} (exit code "
                f"{process.returncode})"
            )

            return {
                "success": passed,
                "stdout": process.stdout,
                "stderr": process.stderr,
                "return_code": process.returncode,
                "duration": duration,
                "script_path": str(script_path),
            }

        except subprocess.TimeoutExpired as ex:

            duration = (datetime.now() - start).total_seconds()

            self.logger.warning(
                f"Playwright script timed out after {timeout_seconds}s"
            )

            return {
                "success": False,
                "error": (
                    f"Script did not finish within {timeout_seconds} "
                    f"seconds and was stopped. It may be waiting on "
                    f"a page element that never appeared, or a wrong "
                    f"URL/selector."
                ),
                "stdout": ex.stdout or "",
                "stderr": ex.stderr or "",
                "duration": duration,
                "script_path": str(script_path),
            }

        except Exception as ex:

            self.logger.exception(
                "Playwright script execution failed to start."
            )

            return {
                "success": False,
                "error": str(ex),
                "script_path": str(script_path),
            }