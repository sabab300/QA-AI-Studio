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

        context

    ):

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