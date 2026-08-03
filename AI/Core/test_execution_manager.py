# Create: AI/Core/test_execution_manager.py

"""
QA AI Studio
Test Execution Manager

Version: 1.0

Service layer used by the QA Automation UI. Wraps:
    • TestCaseRepository — list / status / result persistence
    • AutomationGenerator — turns a test case into an automation
      script for a chosen framework (Playwright / Selenium / API / SQL)

Deliberately does NOT execute generated automation scripts
automatically. Running unreviewed, LLM-generated code via exec() or
a shell is a real security risk — that requires a properly sandboxed
runner, which is a separate, later phase. For now, "Automated" test
cases store their generated script for the QA engineer to review and
run themselves; "Manual" test cases get a real Pass/Fail/Blocked
result recorded directly.
"""

from Core.test_case_repository import TestCaseRepository
from Core.automation_generator import AutomationGenerator
from Core.llm_engine import LLMEngine
from Core.logger import Logger
import json
import re


class TestExecutionManager:

    def __init__(self):

        self.logger = Logger.get_logger()

        self.repository = TestCaseRepository()

        self.automation_generator = AutomationGenerator()

        self.llm = LLMEngine()

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

        self.repository.update_automation(
            test_case_id,
            automation_type,
            script
        )

        return script

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

        # Reserved for a future phase once a sandboxed runner exists.
        # Always False today, on purpose.
        return False