# Create: AI/Core/test_execution_manager.py

"""
QA AI Studio
Test Execution Manager

Version: 1.0

Service layer used by the QA Automation UI. Wraps:
    • TestCaseRepository — list / status / result persistence
    • AutomationGenerator — turns a test case into an automation
      script for a chosen framework (Playwright / Selenium / API / SQL)

Playwright scripts now actually EXECUTE (Core/playwright_runner.py),
in an isolated subprocess with a timeout — a hung or bad script can't
freeze the app. Selenium/API/SQL still only generate + store a
script for manual review; they haven't gotten a runner yet.

Since this runs AI-generated code in a real browser against
whatever URL is inside the script, the UI shows a one-time
confirmation before each run, and strongly encourages reviewing the
script first — especially before pointing it at a real/production
PSW environment rather than a test/UAT one.
"""

from Core.test_case_repository import TestCaseRepository
from Core.automation_generator import AutomationGenerator
from Core.llm_engine import LLMEngine
from Core.playwright_runner import PlaywrightRunner
from Core.test_environment_config import TestEnvironmentConfig
from Core.logger import Logger
import json
import re
import ast
import subprocess
import sys
from datetime import datetime
from pathlib import Path

RECORDINGS_FOLDER = Path("Output") / "Recordings"

class TestExecutionManager:

    def __init__(self):

        self.logger = Logger.get_logger()

        self.repository = TestCaseRepository()

        self.automation_generator = AutomationGenerator()

        self.llm = LLMEngine()

        self.playwright_runner = PlaywrightRunner()

        self.environment_config = TestEnvironmentConfig()

    # --------------------------------------------------
    # Listing
    # --------------------------------------------------

    def list_test_cases(self, domain, module, knowledge_name):

        return self.repository.list_test_cases(
            domain,
            module,
            knowledge_name
        )

    # --------------------------------------------------
    # Status / Result
    # --------------------------------------------------

    def set_status(self, test_case_id, status):

        if status not in ("Manual", "Automated"):

            raise ValueError(
                "Status must be 'Manual' or 'Automated'."
            )

        self.repository.update_status(
            test_case_id,
            status
        )

    def record_manual_result(self, test_case_id, result):

        if result not in ("Pass", "Fail", "Blocked"):

            raise ValueError(
                "Result must be 'Pass', 'Fail', or 'Blocked'."
            )

        self.repository.update_result(
            test_case_id,
            result
        )

    # --------------------------------------------------
    # Automation Generation
    # (called from a background thread — this can be slow)
    # --------------------------------------------------

    def generate_automation(
        self,
        test_case_id,
        automation_type,
        domain,
        module,
        knowledge_name,
        version=None,
    ):

        if automation_type not in ("Playwright", "Selenium", "API", "SQL"):

            raise ValueError(
                "automation_type must be one of: "
                "Playwright, Selenium, API, SQL"
            )

        test_case = self.repository.get_test_case(test_case_id)

        if not test_case:

            raise ValueError(
                f"Test case {test_case_id} not found."
            )

        if automation_type == "Playwright":

            environment_block = self.environment_config.as_prompt_block()

            requirement = (
                f"Generate ONLY the body steps for a Playwright test "
                f"as a FLAT list of simple Python statements — one "
                f"action per line.\n\n"
                f"STRICT RULES:\n"
                f"- Do NOT write 'def', 'if', 'for', 'while', 'try', "
                f"'with', or any other block/indented statement.\n"
                f"- Do NOT include imports, browser launch, or "
                f"browser.close() — those are already handled.\n"
                f"- A variable named 'page' (a Playwright Page, "
                f"already created) is available to use directly.\n"
                f"- Use assert statements to verify the Expected "
                f"Result.\n"
                f"- Every line must be independently valid at zero "
                f"indentation.\n"
                f"- Playwright does NOT have a 'By' class — that is "
                f"Selenium, a different library. NEVER write "
                f"By.ID, By.XPATH, By.NAME, or similar. Selectors "
                f"are plain strings passed directly as the first "
                f"argument.\n\n"
                f"WRONG (Selenium-style, will not work):\n"
                f"page.fill(By.ID, \"username\", \"myuser\")\n\n"
                f"CORRECT (Playwright-style):\n"
                f"page.fill(\"#username\", \"myuser\")\n\n"
                f"{environment_block}"
                f"If the exact URL for a step isn't clear from the "
                f"test case, use 'https://REPLACE_WITH_ACTUAL_URL'.\n\n"
                f"Test Case: {test_case.get('test_case', '')}\n"
                f"Pre-Conditions: {test_case.get('pre_conditions', '')}\n"
                f"Steps: {test_case.get('steps', '')}\n"
                f"Expected Result: {test_case.get('expected_result', '')}\n\n"
                f"Example of the exact format expected:\n"
                f"page.goto('https://example.com/login')\n"
                f"page.fill('#username', 'myuser')\n"
                f"page.click('#login-button')\n"
                f"assert page.locator('#welcome').is_visible()\n\n"
                f"Return ONLY the flat list of statements, one per "
                f"line, no explanation, no markdown fences, no "
                f"indentation, no function or block wrapper."
            )

        else:

            requirement = (
                f"Generate a {automation_type} automation script for the "
                f"following test case.\n\n"
                f"Test Case: {test_case.get('test_case', '')}\n"
                f"Pre-Conditions: {test_case.get('pre_conditions', '')}\n"
                f"Steps: {test_case.get('steps', '')}\n"
                f"Expected Result: {test_case.get('expected_result', '')}\n\n"
                f"Return only the {automation_type} script, ready to run, "
                f"with brief comments explaining each step."
            )

        max_repair_attempts = 2 if automation_type == "Playwright" else 0

        result = self.automation_generator.generate(
            requirement=requirement,
            domain=domain,
            module=module,
            knowledge_name=knowledge_name,
            version=version,
        )

        if not result.get("success"):

            raise RuntimeError(
                result.get("error", "Automation generation failed.")
            )

        raw_output = result.get("automation_code", "")

        if automation_type == "Playwright":

            environment = self.environment_config.load()

            base_url = environment.get("base_url") or (
                "https://REPLACE_WITH_ACTUAL_URL"
            )

            script = self._build_playwright_script(
                raw_output, base_url
            )

            valid, error = self._validate_python_syntax(script)

            attempt = 0

            while not valid and attempt < max_repair_attempts:

                attempt += 1

                self.logger.warning(
                    f"Assembled script has invalid syntax "
                    f"(repair attempt {attempt}/{max_repair_attempts}): "
                    f"{error}"
                )

                # Only the BODY needs repairing — the wrapper
                # structure around it is ours and is always correct.
                raw_output = self._repair_script(raw_output, error)

                script = self._build_playwright_script(
                    raw_output, base_url
                )

                valid, error = self._validate_python_syntax(script)

            if not valid:

                raise RuntimeError(
                    f"The AI's test steps could not be assembled "
                    f"into valid Python after {max_repair_attempts} "
                    f"repair attempts. Last error: {error}\n\n"
                    f"Use View Script to fix it by hand and Save, "
                    f"or try 'Update Automation' again."
                )

        else:

            script = self._strip_code_fences(raw_output)

        self.repository.update_automation(
            test_case_id,
            automation_type,
            script
        )

        return script


    def _build_playwright_script(self, raw_steps_text, base_url):
        """
        Takes the AI's flat, unindented list of Playwright statements
        and wraps it in a guaranteed-correct script structure — WE
        control the indentation here, not the model, which is what
        actually fixes the recurring IndentationError.
        """

        raw_steps_text = self._strip_code_fences(raw_steps_text)

        # Lines the model might redundantly include despite
        # instructions not to — drop them so we don't get duplicate
        # browser setup/teardown or a broken nested structure.
        boilerplate_markers = (
            "import ", "def run", "def main", "if __name__",
            "with sync_playwright", "playwright.chromium.launch",
            "browser.close()", "browser = ", "print('test passed')",
            "print(\"test passed\")", "page = browser.new_page",
        )

        body_lines = []

        for raw_line in raw_steps_text.split("\n"):

            line = raw_line.strip()

            if not line:

                continue

            if any(
                line.lower().startswith(marker)
                for marker in boilerplate_markers
            ):

                continue

            body_lines.append(line)

        if not body_lines:

            body_lines = [
                "# AI did not return any usable steps — "
                "edit this script manually."
            ]

        indented_body = "\n".join(
            f"    {line}" for line in body_lines
        )

        return (
            "from playwright.sync_api import sync_playwright\n\n"
            "def run(playwright):\n"
            "    browser = playwright.chromium.launch(headless=False)\n"
            "    page = browser.new_page()\n"
            f"    page.goto('{base_url}')\n"
            f"{indented_body}\n"
            "    browser.close()\n\n"
            "if __name__ == '__main__':\n"
            "    with sync_playwright() as playwright:\n"
            "        run(playwright)\n"
            "    print('TEST PASSED')\n"
        )


    def update_script(self, test_case_id, automation_type, script_text):
        """
        Saves a manually-edited script, without calling the AI.
        """

        self.repository.update_automation(
            test_case_id,
            automation_type,
            script_text,
        )

    # --------------------------------------------------
    # Manual Recording (hand-driven, via Playwright's OWN codegen
    # recorder — an alternative to AI-generated scripts)
    # --------------------------------------------------

    def record_manual_script(
        self, test_case_id, start_url=None, on_process_started=None
    ):
        """
        Launches Playwright's codegen recorder in a REAL, visible
        browser and BLOCKS until the operator closes it — this must
        be called from a background thread, never the UI thread (see
        App/UI/QAAutomation/test_execution_worker.py's
        ManualRecordingWorker).

        `on_process_started`, if given, is called once with the
        running subprocess.Popen so the caller can offer a "Cancel
        Recording" button — terminating a Popen is safe from any
        thread.
        """

        test_case = self.repository.get_test_case(test_case_id)

        if not test_case:

            raise ValueError(f"Test case {test_case_id} not found.")

        if not self.playwright_runner.is_playwright_installed():

            raise RuntimeError(
                "Playwright isn't installed yet. Run these two "
                "commands in your terminal, then try again:\n\n"
                "pip install playwright\n"
                "playwright install chromium"
            )

        url = (start_url or "").strip()

        if not url:

            environment = self.environment_config.load()

            url = (environment.get("base_url") or "").strip()

        if not url:

            raise ValueError(
                "No starting URL given, and no Base URL is set in "
                "Test Environment Settings. Set one of those first "
                "so the recorder knows where to open the browser."
            )

        RECORDINGS_FOLDER.mkdir(parents=True, exist_ok=True)

        # Microsecond resolution, not just seconds — two recordings
        # for the same test case started within the same second
        # would otherwise collide on this filename, and the second
        # call's existence check could find the FIRST call's
        # already-written file before its own subprocess has done
        # anything, silently returning stale content instead of the
        # new (possibly cancelled/empty) recording.
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S%f")

        output_path = (
            RECORDINGS_FOLDER / f"tc{test_case_id}_{timestamp}.py"
        )

        self.logger.info(
            f"Launching Playwright codegen recorder against {url} "
            f"for test case {test_case_id}."
        )

        process = subprocess.Popen(
            [
                sys.executable, "-m", "playwright", "codegen",
                "--target", "python",
                "-o", str(output_path),
                url,
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

        if on_process_started:

            on_process_started(process)

        # Blocks here until the operator closes the recorder browser
        # (or Cancel Recording terminates the process) — codegen only
        # writes the output file on exit, there's no "in-progress"
        # file to poll.
        _, stderr = process.communicate()

        if not output_path.exists():

            raise RuntimeError(
                "The recorder closed without producing a script. "
                "This usually means it was closed immediately, or "
                "Playwright's browsers aren't installed (run: "
                "playwright install chromium).\n\n"
                f"{(stderr or '').strip()[-800:]}"
            )

        script = output_path.read_text(encoding="utf-8").strip()

        if not script:

            raise RuntimeError(
                "The recorder produced an empty script — no actions "
                "were captured. Try again and interact with the "
                "page before closing the recorder window."
            )

        self.repository.update_recorded_script(test_case_id, script)

        return script


    def update_recorded_script(self, test_case_id, script_text):
        """
        Saves a manually-edited version of the RECORDED script (as
        opposed to update_script(), which edits the AI-generated
        one) — without re-launching the recorder.
        """

        self.repository.update_recorded_script(
            test_case_id, script_text
        )


    def set_active_script(self, test_case_id, source):
        """
        `source`: "AUTO" (the AI-generated script) or "MANUAL" (the
        hand-recorded one). Controls which one Execute actually runs
        — see get_active_script().
        """

        if source not in ("AUTO", "MANUAL"):

            raise ValueError("source must be 'AUTO' or 'MANUAL'.")

        self.repository.set_active_script_source(
            test_case_id, source
        )


    @staticmethod
    def get_active_script(test_case):
        """
        Returns whichever script should actually be used for Execute
        / as View Script's default tab, per this test case's
        active_script_source. Falls back to the AI-generated script
        if MANUAL is selected but nothing has actually been recorded
        — so a stale selection can never silently make Execute find
        nothing to run.
        """

        source = (
            test_case.get("active_script_source") or "AUTO"
        ).upper()

        if source == "MANUAL" and test_case.get("recorded_script"):

            return test_case.get("recorded_script")

        return test_case.get("automation_script")

    def check_script_syntax(self, script_text):
        """
        Returns None if valid, or an error description if not —
        used by the Edit Script dialog to warn (not block) on save.
        """

        valid, error = self._validate_python_syntax(script_text)

        return None if valid else error


    def _repair_script(self, broken_script, error_description):
        """
        Shows the model the exact code it produced and the exact
        syntax error, and asks it to fix ONLY that — this works far
        better than blind resampling, since at low temperature the
        model tends to reproduce the same mistake from the same
        original prompt.
        """

        prompt = (
            f"This Playwright test code has a problem: "
            f"{error_description}\n\n"
            f"{broken_script}\n\n"
            f"Fix ONLY that specific problem. Keep every other line, "
            f"selector, and value exactly the same. Return ONLY the "
            f"corrected code, no explanation, no markdown code fences."
        )

        result = self.llm.generate(
            prompt=prompt,
            temperature=0.2,
            max_tokens=1500,
        )

        if not result.get("success"):

            # Couldn't even call the model for the repair — just
            # return the broken script unchanged, the outer loop
            # will report it as still-invalid.
            return broken_script

        return self._strip_code_fences(
            result.get("response", "")
        )


    def _validate_python_syntax(self, script_text):

        try:

            ast.parse(script_text)

        except SyntaxError as ex:

            return False, f"Line {ex.lineno}: {ex.msg}"

        # Syntactically valid Python can still be wrong in ways
        # ast.parse() can't catch — this is a known, specific
        # mistake the model makes: writing Selenium's By.ID/By.XPATH
        # locators inside Playwright code, where 'By' doesn't exist
        # and causes a NameError at runtime, not at file-write time.
        selenium_leak = re.search(
            r"\bBy\.(ID|XPATH|NAME|CLASS_NAME|CSS_SELECTOR|"
            r"LINK_TEXT|TAG_NAME)\b",
            script_text,
        )

        if selenium_leak:

            return False, (
                f"Uses Selenium's '{selenium_leak.group(0)}' locator, "
                f"which doesn't exist in Playwright and causes "
                f"NameError: name 'By' is not defined at runtime. "
                f"Playwright selectors are plain strings, e.g. "
                f"page.fill('#username', 'value') instead of "
                f"page.fill(By.ID, 'username', 'value')."
            )

        return True, None


    def _strip_code_fences(self, text):
        """
        Local LLMs frequently wrap code in markdown fences
        (```python ... ```) even when explicitly told not to.
        Writing that straight to a .py file causes a SyntaxError on
        line 1 — this pulls just the code out, with or without
        surrounding explanation text, and copes with a truncated
        response that's missing its closing fence.
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

        # No closing fence (truncated response) — just drop a
        # leading opening-fence line if there is one.
        lines = text.split("\n")

        if lines and lines[0].strip().startswith("```"):

            lines = lines[1:]

        if lines and lines[-1].strip() == "```":

            lines = lines[:-1]

        return "\n".join(lines).strip()


    # --------------------------------------------------
    # AI Suggestion: which automation type fits this test case?
    # (called from a background thread — this is an LLM call)
    # --------------------------------------------------

    VALID_TYPES = ("Playwright", "Selenium", "API", "SQL")

    def suggest_automation_type(self, test_case_id):

        test_case = self.repository.get_test_case(test_case_id)

        if not test_case:

            raise ValueError(
                f"Test case {test_case_id} not found."
            )

        prompt = (
            "You are a QA automation architect. Look at this test "
            "case and decide which ONE automation approach fits it "
            "best:\n\n"
            "- Playwright: browser/UI automation (clicking, forms, "
            "navigating web pages)\n"
            "- Selenium: older browser/UI automation, same use case "
            "as Playwright\n"
            "- API: testing a REST/SOAP API request and response "
            "directly, no browser\n"
            "- SQL: verifying data directly in a database, no UI or "
            "API involved\n\n"
            f"Test Case: {test_case.get('test_case', '')}\n"
            f"Pre-Conditions: {test_case.get('pre_conditions', '')}\n"
            f"Steps: {test_case.get('steps', '')}\n"
            f"Expected Result: {test_case.get('expected_result', '')}\n\n"
            "Respond with ONLY this JSON, nothing else:\n"
            '{"suggested_type": "Playwright|Selenium|API|SQL", '
            '"reason": "one short sentence"}'
        )

        result = self.llm.generate(
            prompt=prompt,
            temperature=0.1,
            max_tokens=150,
        )

        if not result.get("success"):

            raise RuntimeError(
                result.get("error", "AI suggestion failed.")
            )

        return self._parse_suggestion(
            result.get("response", "")
        )


    def _parse_suggestion(self, raw_text):

        # Strip markdown code fences if the model added them anyway.
        cleaned = re.sub(
            r"```(?:json)?|```", "", raw_text
        ).strip()

        try:

            data = json.loads(cleaned)

            suggested_type = data.get("suggested_type", "")

            reason = data.get("reason", "")

            if suggested_type in self.VALID_TYPES:

                return {
                    "suggested_type": suggested_type,
                    "reason": reason or "No reason given.",
                }

        except (json.JSONDecodeError, AttributeError):

            pass

        # Fallback: the model didn't return clean JSON — just look
        # for one of the valid type names anywhere in the text so a
        # slightly messy response still works instead of failing.
        for candidate in self.VALID_TYPES:

            if candidate.lower() in raw_text.lower():

                return {
                    "suggested_type": candidate,
                    "reason": (
                        "AI response wasn't in the expected format, "
                        "but mentioned this type."
                    ),
                }

        # Total fallback — default to API since it's the safest,
        # most broadly-applicable guess when we truly can't tell.
        return {
            "suggested_type": "API",
            "reason": (
                "Could not determine a confident suggestion — "
                "defaulted to API. Please review and change if needed."
            ),
        }


    # --------------------------------------------------
    # Execution
    # --------------------------------------------------
    #
    # Deliberately conservative:
    #   Manual     -> caller records a real result directly
    #   Automated  -> no script present  -> tell the caller to
    #                  generate one first
    #               -> script present    -> NOT auto-run; caller
    #                  should surface the script for manual review
    # --------------------------------------------------

    def can_auto_execute(self, test_case):

        # Real execution exists for Playwright now. Selenium/API/SQL
        # still require manual review via View Script — same
        # reasoning as before, they just haven't gotten a runner yet.
        # get_active_script() checks BOTH the AI-generated and the
        # manually recorded script, whichever this test case is set
        # to use.
        return (
            test_case.get("automation_type") == "Playwright"
            and bool(self.get_active_script(test_case))
        )

    def execute_playwright(self, test_case_id, timeout_seconds=None):
        """
        Actually runs the generated Playwright script in a real
        browser (subprocess-isolated, timeout-protected — see
        Core/playwright_runner.py). Records the Pass/Fail result
        against the test case afterward.
        """

        test_case = self.repository.get_test_case(test_case_id)

        if not test_case:

            raise ValueError(
                f"Test case {test_case_id} not found."
            )

        script = self.get_active_script(test_case)

        if not script:

            raise ValueError(
                "No automation script available for this test case "
                "yet — use Add Automation (AI-generated) or Record "
                "Manually first."
            )

        result = self.playwright_runner.run_script(
            script,
            tc_number=test_case.get("tc_number", "script"),
            timeout_seconds=timeout_seconds,
        )

        if "error" in result and not result.get("stdout"):

            # Didn't even run (e.g. Playwright not installed) —
            # don't record a Pass/Fail result for this, since the
            # test itself never actually executed.
            return result

        outcome = "Pass" if result.get("success") else "Fail"

        self.repository.update_result(test_case_id, outcome)

        result["outcome"] = outcome

        return result