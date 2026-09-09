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
freeze the app. Selenium/SQL still only generate + store a script
for manual review; they haven't gotten a runner yet. API test cases
can now ALSO be run for real (Core/api_automation_runner.py) —
the operator explicitly chooses "Execute Against Real Server" (vs.
"Generate Script (Ollama/AI)", today's manual-review behaviour) —
which sends an actual HTTP request built from the REAL, imported
endpoint record, never from parsing/executing the AI-generated
script text.

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

# BUGFIX (shared Core defect, also present on Desktop): same
# CWD-relative-path issue fixed in playwright_runner.py's
# OUTPUT_FOLDER — see that comment for the full explanation.
RECORDINGS_FOLDER = Path(__file__).resolve().parent.parent / "Output" / "Recordings"

class TestExecutionManager:

    def __init__(self, force_headless=False, initialize_automation=True):

        self.logger = Logger.get_logger()

        self.repository = TestCaseRepository()

        if not initialize_automation:
            return

        self.automation_generator = AutomationGenerator()

        self.llm = LLMEngine()

        # WEB PORT ADDITION: force_headless=True on a server (no
        # display) — see PlaywrightRunner.__init__'s docstring.
        self.playwright_runner = PlaywrightRunner(force_headless=force_headless)

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
    # Import test cases from a spreadsheet
    #
    # QA Automation previously only ever saw test cases that came
    # out of QA Engineering's AI generation, simply because that was
    # the only code path that ever wrote a row into `test_cases` —
    # there was no gate keeping other rows out, just nothing else
    # that inserted any. This gives someone who already has
    # hand-written test cases (in the same layout Excel Exporter
    # already produces — see Core/excel_exporter.py) a way to bring
    # them in directly, so they show up in Test Execution Automation
    # and are automation-eligible exactly like AI-generated ones,
    # with no AI/RAG step involved at all.
    # --------------------------------------------------

    # Column header -> test_cases field, matched case/spacing/
    # punctuation-insensitively so both Excel Exporter's own output
    # and a reasonably-named hand-built sheet both work. Listed in
    # the same order Excel Exporter writes them, for reference.
    _IMPORT_FIELD_ALIASES = {
        "scenario": (
            "namescenariorequirement", "scenario", "requirement",
            "name",
        ),
        "importance": (
            "importancehighmediumlow", "importance", "priority",
        ),
        "test_type": ("testtype", "type"),
        "test_case": ("testcase", "testcasetitle", "title"),
        "pre_conditions": (
            "preconditions", "precondition", "prerequisites",
        ),
        "steps": ("steps", "teststeps"),
        "expected_result": ("expectedresult", "expected"),
        "execution_type": ("executiontype", "executionmode"),
        "execution_tool": ("executiontool", "automationtool", "tool"),
    }

    def import_test_cases_from_excel(
        self, file_path, domain, module, knowledge_name, version=None,
        document_type=None, source_knowledge_ids=None, test_case_document_name=None,
    ):
        """
        Reads an .xlsx file's first sheet, maps its header row onto
        test_cases fields via _IMPORT_FIELD_ALIASES, and inserts one
        row per data row via TestCaseRepository.save_generated_cases
        — the exact same insert path QA Engineering's AI generation
        uses, so TC numbering, defaults (status='Manual',
        automation_type='None'), and downstream automation-eligibility
        all behave identically regardless of where the row came from.

        A row with no usable "Test Case" text is skipped rather than
        inserted with a blank required field — reported back in
        "skipped" so the caller can tell the user.
        """

        summary = {
            "success": False,
            "imported": 0,
            "skipped": 0,
            "skipped_duplicates": 0,
            "rejected": 0,
            "errors": [],
            "error": None,
        }

        try:

            from openpyxl import load_workbook

            workbook = load_workbook(file_path, data_only=True)

            sheet = workbook.worksheets[0]

        except Exception as ex:

            summary["error"] = f"Could not read file: {ex}"

            return summary

        rows_iter = sheet.iter_rows(values_only=True)

        try:

            header_row = next(rows_iter)

        except StopIteration:

            summary["error"] = "The sheet is empty."

            return summary

        def normalize(text):

            return re.sub(r"[^a-z0-9]", "", str(text or "").lower())

        column_fields = {}

        for column_index, header in enumerate(header_row):

            normalized_header = normalize(header)

            for field, aliases in self._IMPORT_FIELD_ALIASES.items():

                if normalized_header in aliases:

                    column_fields[column_index] = field

                    break

        # ROOT CAUSE (confirmed 2026-09-09 by reproducing an actual
        # POST /api/automation/test-cases/import against a file this
        # app itself produces): "Execution Type" / "Execution Tool"
        # used to be hard-required SHEET-LEVEL columns here, but
        # Core/excel_exporter.py's ExcelExporter.export_test_cases()
        # -- the real "Export as Excel" QA Engineering already offers,
        # called from Core/test_case_generator.py -- has NEVER written
        # those two columns at all (QA Engineering only drafts
        # Scenario/Importance/Test Type/Test Case/Pre-Conditions/
        # Steps/Expected Result/Actual Result; deciding automation
        # type/tool is Automation's own job, later, per test case).
        # The result: the extremely natural "export from QA
        # Engineering, then import into Automation" round trip always
        # failed with HTTP 400 "Required column(s) missing: Execution
        # Type, Execution Tool." — even though the SAMPLE template
        # (which does include both columns) imports fine, making the
        # feature look broken/inconsistent rather than obviously so.
        # Fix: these two are now OPTIONAL at the sheet level. A file
        # that omits them entirely imports every row as Execution
        # Type=Manual (Execution Tool blank — always valid per the
        # existing per-row rule below), matching how a test case
        # created any other way in this app starts out
        # (status='Manual', automation_type='None' — see this
        # method's own docstring above). A file that DOES include
        # either column still has every row's value validated exactly
        # as before (Manual/Automatable; Automatable requires a
        # registered tool) — this only relaxes the "column missing
        # entirely" rejection, never the per-row business rule.
        required_columns = {
            "scenario": "Scenario", "importance": "Importance",
            "test_type": "Test Type(s)", "test_case": "Test Case",
            "pre_conditions": "Preconditions", "steps": "Steps",
            "expected_result": "Expected Result",
        }
        missing = [label for field, label in required_columns.items() if field not in column_fields.values()]
        if missing:
            summary["error"] = "Required column(s) missing: " + ", ".join(missing) + "."
            return summary
        has_execution_type_column = "execution_type" in column_fields.values()

        parsed_rows = []
        tool_aliases = {}
        for tool in self.repository.list_execution_tools():
            label = str(tool.get("label") or "").strip()
            for value in (tool.get("code"), label, tool.get("automation_type")):
                if value:
                    tool_aliases[normalize(value)] = label

        for row_number, data_row in enumerate(rows_iter, start=2):

            if data_row is None or all(
                cell in (None, "") for cell in data_row
            ):

                continue

            row = {}

            for column_index, field in column_fields.items():

                value = (
                    data_row[column_index]
                    if column_index < len(data_row)
                    else None
                )

                row[field] = str(value).strip() if value is not None else ""

            row_errors = []
            for field, label in required_columns.items():
                if not str(row.get(field) or "").strip():
                    row_errors.append(f"{label} is required")
            if has_execution_type_column:
                execution_type = str(row.get("execution_type") or "").strip().lower()
                if execution_type not in {"manual", "automatable"}:
                    row_errors.append("Execution Type must be Manual or Automatable")
                else:
                    row["execution_type"] = execution_type.title()
            else:
                # No Execution Type column in this sheet at all -- see
                # the sheet-level comment above: default to Manual
                # rather than rejecting every row in a legitimately
                # QA-Engineering-exported file.
                execution_type = "manual"
                row["execution_type"] = "Manual"
            raw_tool = str(row.get("execution_tool") or "").strip()
            if execution_type == "automatable":
                mapped_tool = tool_aliases.get(normalize(raw_tool))
                if not raw_tool:
                    row_errors.append("Execution Tool is required for an Automatable Test Case")
                elif not mapped_tool:
                    row_errors.append(f'Execution Tool "{raw_tool}" is not registered')
                else:
                    row["execution_tool"] = mapped_tool
            else:
                row["execution_tool"] = ""
            if row_errors:
                summary["rejected"] += 1
                summary["errors"].append(f"Row {row_number}: " + "; ".join(row_errors) + ".")
                continue

            parsed_rows.append(row)

        if not parsed_rows:
            summary["error"] = (
                "No valid rows were found. " + " ".join(summary["errors"][:3])
                if summary["errors"] else
                "No rows had any text in the 'Test Case' column — nothing to import."
            )

            return summary

        # Keep the central repository canonical: repeated rows in the
        # workbook, or rows already imported into this same reviewed
        # document/source scope, are skipped instead of duplicated.
        existing = self.repository.list_all_test_cases(
            domain=domain, module=module, knowledge_name=knowledge_name,
            version=version or "1.0", document_type=document_type,
            limit=10000, offset=0,
        )["test_cases"]
        selected_sources = {int(value) for value in (source_knowledge_ids or [])}
        signatures = {
            normalize(row.get("test_case")) for row in existing
            if (not selected_sources or set(row.get("source_knowledge_ids") or []) == selected_sources)
            and str(row.get("test_case_document_name") or "") == str(test_case_document_name or "")
        }
        unique_rows = []
        for row in parsed_rows:
            signature = normalize(row.get("test_case"))
            if not signature or signature in signatures:
                summary["skipped"] += 1
                summary["skipped_duplicates"] += 1
                continue
            signatures.add(signature)
            unique_rows.append(row)
        parsed_rows = unique_rows
        if not parsed_rows:
            if summary["skipped_duplicates"]:
                summary["success"] = True
                return summary
            summary["error"] = "No valid new Test Cases were found."
            return summary

        try:

            saved_ids = self.repository.save_generated_cases(
                domain=domain,
                module=module,
                knowledge_name=knowledge_name,
                version=version or "1.0",
                rows=parsed_rows,
                document_type=document_type,
                source_knowledge_ids=source_knowledge_ids,
                test_case_document_name=test_case_document_name,
            )

        except Exception as ex:

            summary["error"] = str(ex)

            return summary

        summary["success"] = True

        summary["imported"] = len(saved_ids)

        summary["skipped"] += len(parsed_rows) - len(saved_ids)

        return summary

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

            requirement = self._build_playwright_requirement(
                test_case, domain, module, knowledge_name,
                environment_block,
            )

        elif automation_type == "API":

            requirement = self._build_api_requirement(
                test_case, domain, module, knowledge_name
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

    # --------------------------------------------------
    # Playwright automation grounding
    # (Manage Knowledge -> URL Knowledge Capture — see
    # discovery_repository.py)
    # --------------------------------------------------

    def _build_playwright_requirement(
        self, test_case, domain, module, knowledge_name,
        environment_block,
    ):
        """
        Grounds Playwright script generation in real, captured URL
        Knowledge elements when any have been recorded for this
        Domain / Module / Knowledge Name (Manage Knowledge -> URL
        Knowledge Capture) — the same "ground it in something real"
        fix already applied to API Automation via an imported
        Postman Collection (see _build_api_requirement() below),
        applied here so the AI stops guessing plausible-looking but
        fake selectors from the test case's plain-English wording
        alone.

        Falls back to the original plain-English-only prompt,
        completely unchanged, whenever nothing has been captured for
        this scope — nobody who hasn't used URL Knowledge Capture yet
        sees any difference.
        """

        generic_requirement = (
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

        try:

            from Core.discovery_repository import DiscoveryRepository

            elements = DiscoveryRepository().get_elements_for_scope(
                domain, module, knowledge_name
            )

        except Exception:

            self.logger.exception(
                "Could not check for captured URL Knowledge elements "
                "— falling back to the plain-text Playwright prompt."
            )

            return generic_requirement

        if not elements:

            return generic_requirement

        matched = self._select_relevant_elements(test_case, elements)

        elements_block = self._format_elements_for_prompt(matched)

        return (
            f"Generate ONLY the body steps for a Playwright test as "
            f"a FLAT list of simple Python statements — one action "
            f"per line, using ONLY the real, captured UI element(s) "
            f"below wherever a step refers to a matching "
            f"field/button/link — these came from an actual "
            f"recorded screen (URL Knowledge Capture), so their "
            f"locator is REAL and must be used EXACTLY as given, "
            f"character for character. Do NOT invent, guess, or "
            f"rewrite a different selector than what is shown below, "
            f"even if it looks more plausible than the test case "
            f"wording. Some locators below are XPath expressions "
            f"(they start with '//') — that is correct and "
            f"intentional (usually because the element's id/name "
            f"changes between page loads and a plain id/CSS selector "
            f"would break); Playwright accepts an XPath string "
            f"directly as the first argument to "
            f"page.fill()/page.click()/etc, with NO special prefix "
            f"or wrapper needed — use it exactly as given, never "
            f"convert it to a different selector style.\n\n"
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
            f"{environment_block}"
            f"If the exact URL for a step isn't clear from the "
            f"test case, use 'https://REPLACE_WITH_ACTUAL_URL'.\n\n"
            f"{elements_block}\n"
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

    def _select_relevant_elements(self, test_case, elements, max_elements=8):
        """
        Mirrors _select_relevant_endpoints()'s keyword-overlap
        matching below, applied to captured URL Knowledge elements
        instead of API endpoints — keeps the elements block short
        enough not to bloat the prompt on a capture with many
        screens, while still always returning SOMETHING (the first
        max_elements, capture order) rather than nothing when no
        element's name shares any word with the test case.
        """

        test_text = " ".join(
            str(test_case.get(field, ""))
            for field in ("test_case", "steps", "expected_result")
        ).lower()

        test_words = set(re.findall(r"[a-z0-9]+", test_text))

        scored = []

        for element in elements:

            element_text = " ".join(
                str(element.get(field, ""))
                for field in ("name", "page_name", "step_name")
            ).lower()

            element_words = set(re.findall(r"[a-z0-9]+", element_text))

            score = len(test_words & element_words)

            scored.append((score, element))

        scored.sort(key=lambda pair: pair[0], reverse=True)

        return [element for _, element in scored[:max_elements]]

    def _format_elements_for_prompt(self, elements):

        lines = ["Real captured UI element(s) for this scope:\n"]

        for element in elements:

            location = " / ".join(
                part for part in (
                    element.get("page_name"), element.get("step_name")
                ) if part
            )

            lines.append(
                f"- {element.get('name') or '(unnamed)'} "
                f"({element.get('element_type', 'element')})"
                + (f" — on {location}" if location else "")
            )

            lines.append(f"  Locator: {element.get('locator', '')}")

            alternates = element.get("alternate_locators") or []

            if alternates:

                alt_str = "; ".join(
                    f"{a.get('strategy', '')}: {a.get('locator', '')}"
                    for a in alternates
                )

                lines.append(f"  Alternates: {alt_str}")

            if element.get("placeholder"):

                lines.append(f"  Placeholder: {element['placeholder']}")

            if element.get("is_required"):

                lines.append("  Required: yes")

            lines.append("")

        return "\n".join(lines)

    # --------------------------------------------------
    # API automation grounding
    # (QA Automation -> API Upload — see api_collection_repository.py)
    # --------------------------------------------------

    def _build_api_requirement(
        self, test_case, domain, module, knowledge_name
    ):
        """
        Grounds "API" automation-script generation in a real,
        uploaded Postman Collection when one has been imported for
        this Domain / Module / Knowledge Name (QA Automation -> API
        Upload) — the same "ground it in something real" fix already
        applied to Playwright via URL Knowledge, applied here to API
        scripts, so the AI stops inventing plausible-looking but
        fake endpoints/fields.

        Falls back to the original plain-English-only prompt,
        completely unchanged, whenever nothing has been imported for
        this scope — nobody who hasn't used API Upload yet sees any
        difference.
        """

        generic_requirement = (
            f"Generate a API automation script for the "
            f"following test case.\n\n"
            f"Test Case: {test_case.get('test_case', '')}\n"
            f"Pre-Conditions: {test_case.get('pre_conditions', '')}\n"
            f"Steps: {test_case.get('steps', '')}\n"
            f"Expected Result: {test_case.get('expected_result', '')}\n\n"
            f"Return only the API script, ready to run, "
            f"with brief comments explaining each step."
        )

        try:

            from Core.api_collection_repository import (
                ApiCollectionRepository,
            )

            endpoints = ApiCollectionRepository().get_endpoints_for_scope(
                domain, module, knowledge_name
            )

        except Exception:

            self.logger.exception(
                "Could not check for an imported API collection — "
                "falling back to the plain-text API prompt."
            )

            return generic_requirement

        if not endpoints:

            return generic_requirement

        matched = self._select_relevant_endpoints(test_case, endpoints)

        endpoints_block = self._format_endpoints_for_prompt(matched)

        return (
            f"Generate a Python automation script (using the "
            f"'requests' library) for the following test case, "
            f"using ONLY the real, uploaded API endpoint(s) below — "
            f"these came from an imported Postman Collection, so "
            f"their method / URL / headers / body are REAL and must "
            f"be used exactly as given. Do NOT invent, guess, or "
            f"substitute a different endpoint, path, or field name "
            f"than what is shown below, even if it seems more "
            f"plausible than the test case wording.\n\n"
            f"{endpoints_block}\n"
            f"Test Case: {test_case.get('test_case', '')}\n"
            f"Pre-Conditions: {test_case.get('pre_conditions', '')}\n"
            f"Steps: {test_case.get('steps', '')}\n"
            f"Expected Result: {test_case.get('expected_result', '')}\n\n"
            f"Use Python's 'requests' library. Assert on the "
            f"response status code and any relevant response fields "
            f"per the Expected Result. Return only the script, ready "
            f"to run, with brief comments explaining each step — no "
            f"markdown fences."
        )

    def _select_relevant_endpoints(
        self, test_case, endpoints, max_endpoints=3
    ):
        """
        A collection can hold dozens of endpoints — sending all of
        them would bloat the prompt and bury the one that actually
        matters for this test case. Scores each endpoint by simple
        keyword overlap between the test case's own text and the
        endpoint's name / folder path / URL, and keeps only the top
        matches.

        If nothing scores above zero (the test case wording shares
        no words with any endpoint), this still returns up to
        max_endpoints endpoints — from the imported collection,
        which is always more real than nothing — rather than
        silently sending none and falling back to a fully-guessed
        script.
        """

        test_text = " ".join(
            str(test_case.get(field, ""))
            for field in ("test_case", "steps", "expected_result")
        ).lower()

        test_words = set(re.findall(r"[a-z0-9]+", test_text))

        scored = []

        for endpoint in endpoints:

            endpoint_text = " ".join(
                str(endpoint.get(field, ""))
                for field in ("name", "folder_path", "url_raw")
            ).lower()

            endpoint_words = set(re.findall(r"[a-z0-9]+", endpoint_text))

            score = len(test_words & endpoint_words)

            scored.append((score, endpoint))

        scored.sort(key=lambda pair: pair[0], reverse=True)

        top = [endpoint for _, endpoint in scored[:max_endpoints]]

        if top and scored[0][0] == 0:

            self.logger.info(
                "No keyword overlap between this test case and any "
                "imported API endpoint for this scope — including "
                "the first %d endpoint(s) from the collection as a "
                "best-effort default.",
                len(top),
            )

        return top

    def get_relevant_endpoints_for_test_case(
        self, test_case, endpoints=None, max_endpoints=1
    ):
        """
        Public wrapper around _select_relevant_endpoints() for
        callers outside automation-script generation — specifically
        "Execute Against Real Server" (see
        Core/api_automation_runner.py and
        App/UI/QAAutomation/test_execution_page.py's
        run_api_automation_rows()), which needs to find which REAL,
        imported endpoint a given API-type test case should actually
        be run against.

        Uses the exact same keyword-overlap matching already used to
        ground AI script generation (_build_api_requirement()), so
        "what actually gets called" and "what the AI was told to
        write a script against" are never two different endpoints
        for the same test case. Defaults to the single best match
        (max_endpoints=1) — unlike generation, which can reasonably
        show the AI a few candidates, real execution has to pick
        exactly one endpoint to send a request to.

        Pass `endpoints` in when the caller already fetched them
        (e.g. running a whole batch of test cases against the same
        collection) to avoid re-querying the database per test case;
        otherwise this looks them up itself from the test case's own
        domain/module/knowledge_name.
        """

        if endpoints is None:

            from Core.api_collection_repository import (
                ApiCollectionRepository,
            )

            endpoints = ApiCollectionRepository().get_endpoints_for_scope(
                test_case.get("domain", ""),
                test_case.get("module", ""),
                test_case.get("knowledge_name", ""),
            )

        if not endpoints:

            return []

        return self._select_relevant_endpoints(
            test_case, endpoints, max_endpoints=max_endpoints
        )

    def _format_endpoints_for_prompt(self, endpoints):

        lines = ["Real uploaded API endpoint(s) for this scope:\n"]

        for endpoint in endpoints:

            url = endpoint.get("url_resolved") or endpoint.get(
                "url_raw", ""
            )

            lines.append(
                f"- {endpoint.get('method', '')} {url}"
            )

            if endpoint.get("folder_path"):

                lines.append(f"  Folder: {endpoint['folder_path']}")

            try:

                headers = json.loads(endpoint.get("headers_json") or "[]")

            except (TypeError, ValueError):

                headers = []

            if headers:

                header_str = ", ".join(
                    f"{h.get('key', '')}: {h.get('value', '')}"
                    for h in headers
                )

                lines.append(f"  Headers: {header_str}")

            if endpoint.get("body_mode"):

                lines.append(
                    f"  Body ({endpoint['body_mode']}): "
                    f"{endpoint.get('body_raw', '')}"
                )

            if endpoint.get("auth_type"):

                lines.append(f"  Auth: {endpoint['auth_type']}")

            if endpoint.get("example_response_status"):

                lines.append(
                    f"  Example response: "
                    f"{endpoint['example_response_status']} "
                    f"{endpoint.get('example_response_body', '')}"
                )

            lines.append("")

        return "\n".join(lines)

    # --------------------------------------------------
    # Generate automation directly from an API collection
    # — no test case required up front
    #
    # generate_automation() above always required an existing
    # test_case_id, which meant an imported Postman Collection with
    # no test cases written against it yet couldn't be automated at
    # all — QA Automation was entirely downstream of either QA
    # Engineering's AI generation or a manually-authored test case
    # (see import_test_cases_from_excel() above). This closes that
    # gap for API collections specifically (SQL/other future sources
    # would follow the same shape): a lightweight, clearly-labelled
    # test case is auto-created per endpoint so the script, its
    # results, and Execute all hang off the exact same test_cases
    # plumbing everything else already uses — nothing downstream
    # needs to know this test case didn't come from a person or the
    # AI generator.
    # --------------------------------------------------

    def generate_automation_from_collection(
        self, domain, module, knowledge_name, version=None,
        automation_type="API", endpoint_ids=None,
    ):
        """
        For every endpoint imported under this Domain / Module /
        Knowledge Name (or just `endpoint_ids`, if given), reuses an
        existing auto-created test case for that exact endpoint if
        one was already generated before, otherwise creates one, then
        runs it through the normal generate_automation() flow.

        Returns:
            {
                "success": bool,
                "generated": int,
                "skipped": int,
                "results": [
                    {
                        "endpoint_id", "endpoint_name", "test_case_id",
                        "success", "error",
                    },
                    ...
                ],
                "error": str | None,
            }
        """

        summary = {
            "success": False,
            "generated": 0,
            "skipped": 0,
            "results": [],
            "error": None,
        }

        try:

            from Core.api_collection_repository import (
                ApiCollectionRepository,
            )

            endpoints = ApiCollectionRepository().get_endpoints_for_scope(
                domain, module, knowledge_name
            )

        except Exception as ex:

            summary["error"] = f"Could not read the API collection: {ex}"

            return summary

        if endpoint_ids:

            endpoints = [
                endpoint for endpoint in endpoints
                if endpoint.get("id") in set(endpoint_ids)
            ]

        if not endpoints:

            summary["error"] = (
                "No API collection is imported under this Domain / "
                "Module / Knowledge Name — import one first (Upload "
                "New Knowledge -> Source Type: API Collection)."
            )

            return summary

        existing_cases = self.repository.list_test_cases(
            domain, module, knowledge_name
        )

        existing_by_marker = {
            test_case.get("test_case"): test_case
            for test_case in existing_cases
        }

        for endpoint in endpoints:

            method = endpoint.get("method", "")

            url = endpoint.get("url_resolved") or endpoint.get(
                "url_raw", ""
            )

            name = endpoint.get("name") or "(unnamed)"

            marker = f"[API Collection Endpoint] {method} {url}"

            existing = existing_by_marker.get(marker)

            result_row = {
                "endpoint_id": endpoint.get("id"),
                "endpoint_name": name,
                "test_case_id": None,
                "success": False,
                "error": None,
            }

            try:

                if existing:

                    test_case_id = existing["id"]

                else:

                    row = {
                        "scenario": f"API Collection endpoint: {name}",
                        "importance": "Medium",
                        "test_type": "API",
                        "test_case": marker,
                        "pre_conditions": "",
                        "steps": f"Call {method} {url}",
                        "expected_result": (
                            "The endpoint returns a successful "
                            "response, per the imported collection."
                        ),
                    }

                    saved_ids = self.repository.save_generated_cases(
                        domain=domain,
                        module=module,
                        knowledge_name=knowledge_name,
                        version=version or "1.0",
                        rows=[row],
                    )

                    if not saved_ids:

                        raise RuntimeError(
                            "Could not create a test case for this "
                            "endpoint."
                        )

                    test_case_id = saved_ids[0]

                result_row["test_case_id"] = test_case_id

                self.generate_automation(
                    test_case_id=test_case_id,
                    automation_type=automation_type,
                    domain=domain,
                    module=module,
                    knowledge_name=knowledge_name,
                    version=version,
                )

                result_row["success"] = True

                summary["generated"] += 1

            except Exception as ex:

                result_row["error"] = str(ex)

                summary["skipped"] += 1

                self.logger.exception(
                    "Could not generate automation for endpoint %s",
                    name,
                )

            summary["results"].append(result_row)

        summary["success"] = summary["generated"] > 0

        if not summary["success"] and not summary["error"]:

            summary["error"] = (
                "Automation generation failed for every endpoint — "
                "see the per-endpoint errors above."
            )

        return summary

    # --------------------------------------------------

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

    def scan_script_for_dynamic_locators(self, script_text):
        """
        Called right after Manual Recording finishes (see
        test_execution_page.py's on_recording_finished()) to flag
        locators that look dynamically generated BEFORE the first
        replay, instead of only discovering them when a replay
        fails. Playwright's own codegen recorder picks its own
        locators while the operator drives the browser — this can't
        change what it recorded, only flag what it recorded for
        review. See scan_script_for_dynamic_locators() in
        playwright_runner.py for the actual detection logic (it's a
        plain module-level function there, not on PlaywrightRunner,
        since it never needs a live browser/subprocess — just the
        script's own text).

        Returns a list of {"line_number", "code", "locator", "value"}
        dicts, empty if nothing looks risky.
        """

        from Core.playwright_runner import (
            scan_script_for_dynamic_locators as _scan,
        )

        return _scan(script_text)


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

        # QA-AUTOMATION-FINAL-ARCHITECTURE-04 hidden-bug fix (ported
        # from the Desktop port — see AI/Core/test_execution_manager
        # .py's matching comment): defense in depth for "execution
        # must re-validate immediately before running", on top of
        # /test-cases/{id}/validate. Confirmed real by finding
        # genuinely broken persisted scripts on disk
        # (AI/App/Output/AutomationRuns/TC003_*.py — "unterminated
        # string literal"). Treated the same as "didn't even run" (no
        # Pass/Fail recorded) rather than a Fail, since the test
        # itself never actually executed.
        valid, syntax_error = self._validate_python_syntax(script)

        if not valid:

            return {
                "success": False,
                "error": (
                    f"Active script has a Python syntax problem and "
                    f"was not run: {syntax_error}"
                ),
            }

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

    # --------------------------------------------------
    # Interactive execution — pause on a broken locator, ask the
    # operator, retry, and (if confirmed) persist the fix.
    #
    # This is the direct fix for "Playwright gets stuck on a
    # Locator/element/value with no way to correct it and keep
    # going": for a single, operator-watched Execute (never a
    # multi-test batch/regression run — see
    # App/UI/QAAutomation/test_execution_page.py's run_next_execution(),
    # which only takes this path when exactly one test case is
    # queued), a failing step pauses instead of failing the whole
    # run, asks for a corrected Locator/value, retries, and — once
    # confirmed by the operator — updates the stored script so the
    # NEXT run doesn't need to repair it again.
    # --------------------------------------------------

    def execute_playwright_interactive(
        self, test_case_id, on_step_failed, on_repaired=None,
        on_locator_verified=None, max_repair_rounds=3,
    ):
        """
        Interactive counterpart to execute_playwright(). Works for
        both the AI-generated script's flat, guaranteed-shape body
        (see PlaywrightRunner._extract_interactive_parts()) and a
        Manually Recorded script in Playwright codegen's standard
        `def run(playwright): ...` / `with sync_playwright() as
        playwright: run(playwright)` shape (see
        PlaywrightRunner._extract_generic_interactive_parts()). Only
        for something that matches NEITHER shape does this return
        {"interactive_supported": False} immediately, so the caller
        should fall back to execute_playwright() in that case.
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

        result = self.playwright_runner.run_script_interactive(
            script,
            tc_number=test_case.get("tc_number", "script"),
            on_step_failed=on_step_failed,
            on_repaired=on_repaired,
            on_locator_verified=on_locator_verified,
            max_repair_rounds=max_repair_rounds,
        )

        if not result.get("interactive_supported"):

            return result

        if "error" in result and not result.get("stdout"):

            return result

        outcome = "Pass" if result.get("success") else "Fail"

        if not result.get("cancelled"):

            self.repository.update_result(test_case_id, outcome)

            result["outcome"] = outcome

        return result

    def apply_script_repairs(self, test_case_id, repairs):
        """
        Called after an interactive run the operator confirms they
        want to keep — takes the CLEAN stored script (never the
        harness-wrapped one that actually ran) and replaces each
        repaired step's original line with its corrected line, then
        saves it back exactly like a manual edit via View Script
        would.

        Interactive execution now runs against EITHER the
        AI-generated script (flat, guaranteed shape) or a Manually
        Recorded one (Playwright codegen's standard shape — see
        PlaywrightRunner._extract_generic_interactive_parts()), so
        the repaired text has to go back into whichever slot was
        actually executed — get_active_script()'s own MANUAL/AUTO
        rule decides which one that was. Saving into the wrong slot
        would silently discard the fix: Execute would keep running
        the untouched script next time, since get_active_script()
        wouldn't be looking at the slot we just updated.

        Each replacement only touches the FIRST remaining occurrence
        of its original line, so if the exact same statement
        appears more than once in the script, earlier repairs don't
        get clobbered by a later one targeting different text.
        """

        test_case = self.repository.get_test_case(test_case_id)

        if not test_case:

            raise ValueError(
                f"Test case {test_case_id} not found."
            )

        use_recorded_slot = (
            (test_case.get("active_script_source") or "AUTO").upper()
            == "MANUAL"
            and test_case.get("recorded_script")
        )

        if use_recorded_slot:

            script = test_case.get("recorded_script") or ""

        else:

            script = test_case.get("automation_script") or ""

        for repair in repairs:

            original = repair.get("original")

            corrected = repair.get("corrected")

            if not original or corrected is None or original == corrected:

                continue

            script = script.replace(original, corrected, 1)

        if use_recorded_slot:

            self.update_recorded_script(test_case_id, script)

        else:

            self.update_script(test_case_id, "Playwright", script)

        return script

    def suggest_locator_fix(self, event, test_case=None):
        """
        Called (from a background thread — an Ollama round-trip can
        take several seconds even locally) after the operator's OWN
        corrected Locator/value has ALSO failed a retry — this is
        the "AI Analysis and suggest resolution" step, only reached
        once a human attempt didn't work, never on the very first
        failure. Grounds the suggestion in the REAL page at the
        moment of failure (the accessibility snapshot captured by
        the interactive harness — see
        Core/playwright_runner.py's INTERACTIVE_HARNESS_TEMPLATE),
        not a guess from the test case text alone, so it can
        recognize things like a native <select> (role: combobox)
        needing select_option() instead of fill()/click(), or a
        calendar's day cells (role: option/gridcell) needing a
        role-based click instead of a plain CSS selector.

        Returns {"success": True, "corrected_code": "...",
        "explanation": "..."} or {"success": False, "error": "..."}
        — never raises, so a bad/unreachable local model degrades to
        "let the operator try again manually" rather than crashing
        the run.
        """

        accessibility_lines = event.get("accessibility") or []

        accessibility_block = (
            "\n".join(f"- {line}" for line in accessibility_lines[:150])
            or "(no accessibility information captured)"
        )

        # 2026-09-09: dom_candidates gives the model REAL, ready-to-use
        # selector strings for whatever stable attributes actually
        # exist on the page (see playwright_runner.py's
        # _qa_capture_dom_candidates()) — id/data-testid/name — rather
        # than only role/name pairs it then has to guess how to turn
        # into correct Playwright syntax itself. This is the fix for
        # "AI suggestion isn't reliably finding a useful locator": the
        # model can quote one of these candidates directly.
        dom_candidates = event.get("dom_candidates") or []

        candidates_block = (
            "\n".join(
                "- {0}  (<{1}>{2}{3})".format(
                    c.get("selector", ""),
                    c.get("tag", ""),
                    (" text=\"%s\"" % c["text"]) if c.get("text") else "",
                    "" if c.get("visible", True) else " NOT VISIBLE",
                )
                for c in dom_candidates[:80]
                if c.get("selector")
            )
            or "(no stable-attribute elements captured)"
        )

        test_case_block = ""

        if test_case:

            test_case_block = (
                f"Test Case: {test_case.get('test_case', '')}\n"
                f"Steps: {test_case.get('steps', '')}\n\n"
            )

        prompt = (
            "A Playwright automation step just failed. Suggest a "
            "corrected replacement for ONLY that one line of code, "
            "using the REAL elements actually present on the page "
            "right now (listed below) — do not invent an element, "
            "id, or attribute value that isn't in one of the two "
            "lists below.\n\n"
            f"{test_case_block}"
            f"Failing step (step {event.get('step')}): "
            f"{event.get('code', '')}\n"
            f"Error: {event.get('error', '')}\n"
            f"Page URL: {event.get('url', '')}\n"
            f"Page Title: {event.get('title', '')}\n\n"
            "Known stable selectors on this page right now — PREFER "
            "one of these verbatim when one fits the failing step; "
            "they are already confirmed to exist:\n"
            f"{candidates_block}\n\n"
            "Other real elements on the page (role: name), for "
            "anything not covered above:\n"
            f"{accessibility_block}\n\n"
            "Locator strategy, in priority order — use the highest "
            "one that actually fits an element from the lists above:\n"
            "1. a data-testid/data-test/data-cy/data-qa attribute "
            "selector from the stable-selectors list\n"
            "2. a stable #id selector from the stable-selectors list\n"
            "3. page.get_by_role(\"role\", name=\"...\") using an "
            "exact role/name pair from the accessibility list\n"
            "4. a label/placeholder/text-based locator (e.g. "
            "page.get_by_label(...), page.get_by_text(...))\n"
            "5. a robust CSS selector (prefer an attribute selector "
            "like [name=\"...\"] over a fragile nth-child chain)\n"
            "6. a robust RELATIVE XPath as a last resort — Playwright "
            "auto-detects any locator starting with \"//\" as XPath, "
            "so //input[@name='username'] or "
            "//button[normalize-space()='Login'] are both fully "
            "supported. NEVER use an absolute, index-heavy XPath like "
            "/html/body/div[3]/div[1]/button[2] — that is exactly the "
            "kind of brittle locator this repair exists to replace.\n\n"
            "Common causes worth checking: a native <select> "
            "dropdown needs page.select_option(selector, "
            "label=\"...\") or value=/index= — NOT fill() or "
            "click(). A custom (non-native) dropdown or calendar "
            "usually needs the container clicked open first, then "
            "the specific option/day clicked by role and visible "
            "text, e.g. page.get_by_role(\"option\", "
            "name=\"...\").click().\n\n"
            "Respond with ONLY a JSON object, no explanation "
            "outside it, no markdown fences:\n"
            '{"corrected_code": "the one corrected Python '
            'statement", "strategy": "which of the 6 numbered '
            'strategies above you used", "confidence": "high, '
            'medium, or low", "explanation": "one short sentence on '
            'why"}'
        )

        result = self.llm.generate(
            prompt=prompt,
            temperature=0.2,
            max_tokens=500,
        )

        if not result.get("success"):

            return {
                "success": False,
                "error": result.get(
                    "error", "Could not reach the AI model."
                ),
            }

        raw_response = self._strip_code_fences(
            result.get("response", "")
        )

        try:

            parsed = json.loads(raw_response)

            corrected_code = (parsed.get("corrected_code") or "").strip()

            if not corrected_code:

                raise ValueError("Empty corrected_code")

            if corrected_code.strip() == (event.get("code") or "").strip():

                # The model just echoed the already-failing line back —
                # never present that as if it were a fix (the operator
                # would retry the exact same broken statement and get
                # the exact same failure again).
                raise ValueError(
                    "AI returned the same failing code unchanged"
                )

            # Best-effort static grounding check (2026-09-09): does the
            # suggested code actually reference one of the REAL
            # selectors/elements we told the model about, rather than
            # something it invented? This can't replace actually
            # running it against the live page (that still happens for
            # real when the operator clicks Apply/Retry, via the exact
            # same step-failure/retry protocol as a manual fix — see
            # playwright_runner.py's _qa_run_step()), but it gives the
            # operator an honest signal BEFORE they spend a retry
            # attempt on it, per the "don't blindly accept an AI
            # locator" requirement.
            known_selectors = [
                c.get("selector", "") for c in dom_candidates if c.get("selector")
            ]

            grounded = any(
                sel and sel in corrected_code for sel in known_selectors
            ) or any(
                # role/name-based suggestions won't literally contain a
                # CSS selector — fall back to checking whether any
                # accessible name we showed the model appears quoted
                # in the suggestion.
                line.split(": ", 1)[-1] and line.split(": ", 1)[-1] in corrected_code
                for line in accessibility_lines[:150]
            )

            return {
                "success": True,
                "corrected_code": corrected_code,
                "strategy": parsed.get("strategy", ""),
                "confidence": parsed.get("confidence", ""),
                "explanation": parsed.get("explanation", ""),
                "grounded": grounded,
            }

        except (TypeError, ValueError, json.JSONDecodeError):

            return {
                "success": False,
                "error": (
                    "The AI's response wasn't in the expected "
                    "format — try again manually, or Ask AI once "
                    "more.\n\nRaw response: " + raw_response[:300]
                ),
            }

    def cancel_interactive_run(self):

        self.playwright_runner.cancel_current_run()

    # --------------------------------------------------

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
