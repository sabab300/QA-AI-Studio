"""
QA AI Studio
Prompt Builder

Version: 5.0
"""

from Config import settings


class PromptBuilder:

    NO_INFORMATION = (
        "Insufficient information found in the Knowledge Base."
    )

    def __init__(self):

        self.profile = getattr(
            settings,
            "PROMPT_PROFILE",
            "balanced"
    )

    # --------------------------------------------------
    # Common Rules
    # --------------------------------------------------

    def _common_rules(self):

        return self.clean_prompt(f"""
        General Rules

        - Answer ONLY from the supplied Knowledge Base Context.
        - Never invent requirements.
        - Never invent workflows.
        - Never invent APIs.
        - Never invent UI screens.
        - Never invent buttons.
        - Never invent database tables.
        - Never invent database columns.
        - Never invent validations.
        - Never invent business rules.
        - Never guess.
        - Never assume.
        - Prefer PSW terminology.
        - Keep responses professional.
        - Avoid duplicate information.
        - If information is missing, clearly state:

        {self.NO_INFORMATION}
        """)

    # --------------------------------------------------
    # System Prompt
    # --------------------------------------------------

    def system_prompt(self):

        profile = self.profile

        if profile == "quality":

            return self.clean_prompt("""
You are a Senior QA Engineer at Pakistan Single Window (PSW).

Your responsibility is to assist Software QA Engineers using ONLY the supplied Knowledge Base.

Rules

- Answer ONLY from supplied context.
- Never fabricate information.
- Never invent requirements.
- Never invent workflows.
- Never invent APIs.
- Never invent UI screens.
- Never invent database schema.
- Never assume missing business logic.
- Clearly mention when information is unavailable.
- Produce structured professional responses.
- Prefer PSW terminology.
""")

        elif profile == "fast":

            return self.clean_prompt(f"""
Answer ONLY from supplied context.

Never guess.

Never invent information.

Be concise.

If information is unavailable, say:

{self.NO_INFORMATION}
""")

        else:

            return self.clean_prompt("""
You are a Senior QA Engineer.

Rules

- Use supplied Knowledge Base only.
- Never fabricate information.
- Never assume missing details.
- Mention insufficient information whenever required.
- Produce professional structured responses.
""")

    # --------------------------------------------------
    # Context Block
    # --------------------------------------------------

    def _context_block(

        self,

        context

    ):

        context = str(context or "").strip()

        if context:

            return self.clean_prompt(f"""
Knowledge Base Context

The following information was retrieved from QA AI Studio Knowledge Base.

Treat this as the ONLY source of truth.

<context>

{context}

</context>

Never use outside knowledge.
""")

        return self.clean_prompt(f"""
Knowledge Base Context

No relevant information was found in the Knowledge Base.

Do NOT invent information.

Always respond with:

{self.NO_INFORMATION}
""")

    # --------------------------------------------------
    # Prompt Cleaner
    # --------------------------------------------------

    def clean_prompt(self, prompt):

        if not prompt:
            return ""

        while "\n\n\n" in prompt:
            prompt = prompt.replace("\n\n\n", "\n\n")

        return prompt.strip()

    # --------------------------------------------------
    # QA Answer Prompt
    # --------------------------------------------------

    def build_answer_prompt(

        self,

        question,

        context

    ):

        return self.clean_prompt(f"""
{self._context_block(context)}

{self._common_rules()}

User Question

{question}

Additional Instructions

- Merge duplicate information.
- Never repeat the same point.
- Never infer missing business logic.
- Never create new workflows.
- If the answer is unavailable, clearly state:

{self.NO_INFORMATION}

Response Format

### Direct Answer

### Explanation

### Important Details

### Limitations (if any)

### Source Summary
""")

    # --------------------------------------------------
    # Test Case Prompt
    # --------------------------------------------------

    def build_test_case_prompt(

        self,

        context,

        test_types,

        number_of_cases="all"

    ):

        types = ", ".join(test_types)

        return self.clean_prompt(f"""
{self._context_block(context)}

{self._common_rules()}

You are a Senior QA Engineer at Pakistan Single Window (PSW).

Task

Generate ONLY test cases supported by the supplied Knowledge Base Context.

Generate {number_of_cases} test cases.

Generate ONLY these test types:

{types}

Rules

- Generate test cases ONLY from supplied context.
- Never invent functionality.
- Never invent UI screens.
- Never invent buttons.
- Never invent APIs.
- Never invent validations.
- Never invent workflows.
- Never create test cases for features not present.
- Cover every important requirement once.
- Avoid duplicate scenarios.
- Prefer business scenarios over generic validation scenarios.
- Expected Result must directly map to supplied requirement.
- One row = one complete test case.

If context is insufficient, output ONLY:

{self.NO_INFORMATION}

Return ONLY a markdown table.

Columns

| Name / Scenario / Requirement | Importance | Test Type | Test Case | Pre-Conditions | Steps | Expected Result |
""")

    # --------------------------------------------------
    # Bug Report Prompt
    # --------------------------------------------------

    def build_bug_prompt(

        self,

        issue,

        context

    ):

        return self.clean_prompt(f"""
{self._context_block(context)}

{self._common_rules()}

Issue

{issue}

Generate a professional QA Bug Report.

Rules

- Never invent Environment.
- Never invent Browser.
- Never invent OS.
- Never invent Build Number.
- Never invent Version.
- Use "Not Provided" whenever information is unavailable.

Format

Title:
Module:
Environment:
Build:
Preconditions:
Steps to Reproduce:
Actual Result:
Expected Result:
Severity:
Priority:
Possible Root Cause:
Recommendation:
""")

    # --------------------------------------------------
    # SQL Prompt
    # --------------------------------------------------

    def build_sql_prompt(

        self,

        request,

        context

    ):

        return self.clean_prompt(f"""
{self._context_block(context)}

{self._common_rules()}

Requirement

{request}

Rules

- Generate SQL ONLY if database schema exists in supplied context.
- Never invent table names.
- Never invent column names.
- Never invent JOINs.
- Never invent database relationships.
- Use ONLY supplied schema.
- Include SQL comments where useful.

If schema is unavailable output ONLY:

Insufficient database schema information.
""")

    # --------------------------------------------------
    # API Prompt
    # --------------------------------------------------

    def build_api_prompt(

        self,

        request,

        context

    ):

        return self.clean_prompt(f"""
{self._context_block(context)}

{self._common_rules()}

Requirement

{request}

Generate API specification ONLY from supplied context.

Include

Endpoint
HTTP Method
Headers
Authentication
Path Parameters
Query Parameters
Request Body
Expected Response
Validation Rules
Negative Test Cases

Rules

- Never invent endpoints.
- Never invent methods.
- Never invent payload.
- Never invent authentication.
- Never invent headers.

If API information is unavailable, respond:

Insufficient API specification found.

Do not fabricate missing information.
""")

    # --------------------------------------------------
    # Automation Prompt
    # --------------------------------------------------

    def build_automation_prompt(

        self,

        request,

        context,

        automation_type=None,

    ):
        """
        QA-AI-STUDIO-API-SQL-AUTOMATION-LIFECYCLE-ROOT-FIX: this used
        to be the SOLE prompt for every automation type — Playwright,
        API, and SQL alike were all told "Generate production-quality
        Selenium Python automation... Use Page Object Model... By.ID"
        regardless of automation_type, which is exactly why an
        API-type Test Case's "Add Automation" produced a full
        Selenium/Page-Object script instead of anything related to a
        real HTTP request. Playwright's own branch below is left
        completely unchanged (same text, same behavior, out of scope
        for this fix) — only API and SQL get their own, type-correct
        prompt builders.
        """

        if automation_type == "API":

            return self.build_api_automation_prompt(request, context)

        if automation_type == "SQL":

            return self.build_sql_automation_prompt(request, context)

        return self.clean_prompt(f"""
{self._context_block(context)}

{self._common_rules()}

Requirement

{request}

Generate production-quality Selenium Python automation.

Requirements

- Use Page Object Model.
- Use explicit waits.
- Never use hardcoded sleep().
- Use readable code.
- Add useful comments only.
- Include assertions.
- Include exception handling.
- Never invent URLs.
- Never invent locators.
- Never invent page names.

If locator information is unavailable, use placeholders such as:

By.ID, "<locator_required>"

Do not generate automation unrelated to the supplied requirement.
""")

    def build_api_automation_prompt(self, request, context):
        """
        API Automation's script is DOCUMENTATION/REFERENCE ONLY — the
        real execution engine (Core/api_automation_runner.py) never
        parses or runs this text; it sends the bound, imported
        endpoint's own structured request directly. This prompt must
        therefore never ask for a Selenium/browser script, and must
        never invent an endpoint the requirement doesn't actually
        supply — the requirement text passed in here already comes
        from TestExecutionManager._build_api_requirement(), which
        only ever includes REAL, imported endpoint details.
        """

        return self.clean_prompt(f"""
{self._context_block(context)}

{self._common_rules()}

Requirement

{request}

Write a short, readable Python `requests`-based reference script that
documents the real HTTP request described above — method, URL,
headers, and body exactly as given, never invented.

Requirements

- Use the `requests` library only.
- Never use Selenium, WebDriver, or any browser automation.
- Never use By.ID/XPATH/NAME or any locator syntax — this is not a
  UI script.
- Base the request strictly on the endpoint details supplied above;
  never invent a URL, method, header, or body value.
- Include a comment noting this script is for reference — the actual
  execution engine sends this exact request directly, this script is
  not executed by the platform.
- Include a basic status-code assertion.

If the requirement does not actually supply a real, bound API
endpoint (method + URL), respond with exactly this single line and
nothing else:

Insufficient bound API endpoint information.
""")

    def build_sql_automation_prompt(self, request, context):
        """
        SQL Automation must only ever produce a single, read-only
        SELECT/WITH statement — never Python, never a browser/API
        script, never a write statement. The runner
        (Core/sql_automation_runner.py's validate_readonly_sql())
        independently re-validates this before it can be saved,
        Activated, or executed, so this prompt is a first line of
        defense, not the only one.
        """

        return self.clean_prompt(f"""
{self._context_block(context)}

{self._common_rules()}

Requirement

{request}

Write a single, read-only SQL query (SELECT or WITH only) that
verifies the requirement above against the database schema supplied
in the context.

Requirements

- Output ONLY the SQL query — no Python, no Selenium, no explanation
  text, no markdown code fences.
- SELECT or WITH statements only. Never INSERT, UPDATE, DELETE, DROP,
  ALTER, CREATE, or TRUNCATE.
- Exactly one statement — no semicolon-separated multiple statements.
- Only reference tables/columns that actually appear in the supplied
  schema — never invent a table or column name.

If no real database schema was supplied in the context above, respond
with exactly this single line and nothing else:

Insufficient schema information — schema unavailable, do not invent
executable SQL.
""")

    # --------------------------------------------------
    # Global AI Answer Prompt
    # --------------------------------------------------

    def build_global_answer_prompt(

        self,

        question,

        context

    ):

        return self.clean_prompt(f"""
Reference Information

{context}

Question

{question}

Instructions

- Answer ONLY using the supplied reference information.
- Never invent facts.
- Never assume missing information.
- Keep the response concise.
- If information is unavailable, clearly state:

Insufficient reference information available.

Response Format

### Direct Answer

### Explanation

### Important Details

### Limitations (if any)

### Source Summary

""")