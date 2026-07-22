"""
QA AI Studio
Prompt Builder

Version: 3.0
"""


class PromptBuilder:


    # --------------------------------------------------
    # System Prompt
    # --------------------------------------------------

    def system_prompt(self):

        return """
You are a Senior QA Engineer at Pakistan Single Window (PSW).

Rules:

- Answer ONLY from the provided context.
- Do NOT invent information.
- If information is unavailable, clearly state it.
- Be concise and technical.
- Prefer structured output.
- Mention document references if available.
"""


    # --------------------------------------------------
    # QA Answer Prompt
    # --------------------------------------------------

    def build_answer_prompt(

        self,

        question,

        context

    ):

        return f"""
Context:

{context}


Question:

{question}


Provide the answer in this format:

### Direct Answer

### Explanation

### Important Details

### Source Summary
"""


    # --------------------------------------------------
    # Test Case Prompt
    # --------------------------------------------------

    def build_test_case_prompt(

        self,

        requirement,

        context,

        number_of_cases=10

    ):

        if number_of_cases == "all":

            instruction = """
Generate all possible QA test cases.

Cover:

- Positive scenarios
- Negative scenarios
- Boundary cases
- Validation scenarios
- Integration scenarios
- Error handling scenarios
"""

        else:

            instruction = f"""
Generate exactly {number_of_cases} QA test cases.

Stop after completing {number_of_cases} test cases.

Do not generate additional cases.
"""


        return f"""
Context:

{context}


Requirement:

{requirement}


{instruction}


Keep each test case concise and practical.

Format:

Test ID:
Title:
Objective:
Preconditions:
Test Data:
Steps:
Expected Result:
Priority:
Severity:
"""


    # --------------------------------------------------
    # Bug Report Prompt
    # --------------------------------------------------

    def build_bug_prompt(

        self,

        issue,

        context

    ):

        return f"""
Context:

{context}


Issue:

{issue}


Generate a professional bug report.

Format:

Title:
Module:
Environment:
Preconditions:
Steps to Reproduce:
Actual Result:
Expected Result:
Severity:
Priority:
Root Cause:
Recommendation:
"""


    # --------------------------------------------------
    # SQL Prompt
    # --------------------------------------------------

    def build_sql_prompt(

        self,

        request,

        context

    ):

        return f"""
Context:

{context}


Requirement:

{request}


Generate SQL only.

Include comments if required.
"""


    # --------------------------------------------------
    # API Prompt
    # --------------------------------------------------

    def build_api_prompt(

        self,

        request,

        context

    ):

        return f"""
Context:

{context}


Requirement:

{request}


Generate:

Endpoint:
Method:
Headers:
Request Body:
Expected Response:
Negative Test Cases:
"""


    # --------------------------------------------------
    # Automation Prompt
    # --------------------------------------------------

    def build_automation_prompt(

        self,

        request,

        context

    ):

        return f"""
Context:

{context}


Requirement:

{request}


Generate production-quality Selenium Python automation code.
""""""
QA AI Studio
Answer Generator
Version: 3.0
"""

from Core.logger import Logger
from Core.qa_assistant import QAAssistant


class AnswerGenerator:

    def __init__(self):

        self.logger = Logger.get_logger()

        self.qa = QAAssistant()

    # --------------------------------------------------

    def generate(
        self,
        question,
        top_k=5
    ):

        try:

            self.logger.info(
                f"Generating answer: {question}"
            )

            result = self.qa.ask(

                question=question,

                top_k=top_k

            )

            if not result.get("success"):

                return result

            return {

                "success": True,

                "question": question,

                "answer": result.get("answer"),

                "context": result.get("context"),

                "references": result.get(

                    "references",

                    []

                ),

                "provider": result.get(

                    "provider"

                ),

                "model": result.get(

                    "model"

                )

            }

        except Exception as error:

            self.logger.error(

                f"Answer Generator failed: {error}"

            )

            return {

                "success": False,

                "error": str(error)

            }