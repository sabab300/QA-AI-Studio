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
from Core.logger import Logger
import json
import re


class TestExecutionManager:

    def __init__(self):

        self.logger = Logger.get_logger()

        self.repository = TestCaseRepository()

        self.automation_generator = AutomationGenerator()

        self.llm = LLMEngine()

        self.playwright_runner = PlaywrightRunner()

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

            requirement = (
                f"Generate a complete, standalone Python Playwright "
                f"script for the following test case. It MUST be "
                f"directly runnable with 'python script.py' — no "
                f"pytest, no fixtures, no external test framework.\n\n"
                f"Required structure:\n"
                f"from playwright.sync_api import sync_playwright\n\n"
                f"def run(playwright):\n"
                f"    browser = playwright.chromium.launch(headless=False)\n"
                f"    page = browser.new_page()\n"
                f"    # steps go here, using page.goto/click/fill/etc.\n"
                f"    # use assert statements to check expected results\n"
                f"    browser.close()\n\n"
                f"if __name__ == '__main__':\n"
                f"    with sync_playwright() as playwright:\n"
                f"        run(playwright)\n"
                f"    print('TEST PASSED')\n\n"
                f"If a URL is not clear from the test case, use a "
                f"clearly marked placeholder like "
                f"'https://REPLACE_WITH_ACTUAL_URL' near the top so "
                f"it's obvious it needs to be filled in before running "
                f"against a real environment.\n\n"
                f"Test Case: {test_case.get('test_case', '')}\n"
                f"Pre-Conditions: {test_case.get('pre_conditions', '')}\n"
                f"Steps: {test_case.get('steps', '')}\n"
                f"Expected Result: {test_case.get('expected_result', '')}\n\n"
                f"Return ONLY the Python code, no explanation before "
                f"or after, no markdown code fences."
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

        script = result.get("automation_code", "")

        script = self._strip_code_fences(script)

        self.repository.update_automation(
            test_case_id,
            automation_type,
            script
        )

        return script

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
        return (
            test_case.get("automation_type") == "Playwright"
            and bool(test_case.get("automation_script"))
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

        script = test_case.get("automation_script")

        if not script:

            raise ValueError(
                "No automation script generated yet for this test "
                "case — use Add Automation first."
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