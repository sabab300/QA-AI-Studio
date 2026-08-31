"""
QA AI Studio
Response Validator

Version: 2.0
"""



from Core.ai_guard import AIGuard
from Core.output_formatter import OutputFormatter


class ResponseValidator:

    def __init__(self):

        self.minimum_length = {
            "answer": 20,
            "test_case": 50,
            "bug": 50,
            "sql": 10,
            "api": 30,
            "automation": 50
        }

        self.guard = AIGuard()

        self.formatter = OutputFormatter()

    # --------------------------------------------------
    # Clean Response
    # --------------------------------------------------

    def clean(self, response):

        if not isinstance(response, str):

            return ""

        response = response.replace(
            "<|im_end|>",
            ""
        )

        response = response.replace(
            "<|im_start|>",
            ""
        )

        response = response.replace(
            "\r",
            ""
        )

        response = response.replace(
            "\t",
            " "
        )

        lines = []

        previous = ""

        for line in response.split("\n"):

            line = line.rstrip()

            if not line and previous == "":

                continue

            if line == previous:

                continue

            previous = line

            lines.append(line)

        return "\n".join(lines).strip()


    # --------------------------------------------------
    # Detect SQL Hallucination
    # --------------------------------------------------

    def validate_sql(self, response, context):

        lower_response = response.lower()
        lower_context = context.lower()

        schema_keywords = [

            "create table",
            "table name",
            "column",
            "schema",
            "database structure"

        ]

        has_schema = any(
            keyword in lower_context
            for keyword in schema_keywords
        )

        if not has_schema:

            sql_patterns = [

                "select ",
                "insert ",
                "update ",
                "delete ",
                "from ",
                "join "

            ]

            if any(
                pattern in lower_response
                for pattern in sql_patterns
            ):

                return {

                    "success": False,

                    "error":
                    "SQL generation blocked. Database schema information is unavailable.",

                    "response": response

                }

        return None


    # --------------------------------------------------
    # Detect API Hallucination
    # --------------------------------------------------

    def validate_api(self, response, context):

        lower_response = response.lower()
        lower_context = context.lower()

        api_definition_keywords = [

            "endpoint",
            "api url",
            "http method",
            "request body",
            "response schema"

        ]

        has_api_details = any(

            keyword in lower_context

            for keyword in api_definition_keywords

        )

        if not has_api_details:

            invented_api = [

                "endpoint:",
                "http method:",
                "/api/",
                "authorization:",
                "bearer"

            ]

            if any(

                item in lower_response

                for item in invented_api

            ):

                return {

                    "success": False,

                    "error":
                    "API generation blocked. API contract information is unavailable.",

                    "response": response

                }

        return None


    # --------------------------------------------------
    # Detect Bug Hallucination
    # --------------------------------------------------

    def validate_bug(self, response, context):

        lower_context = context.lower()

        evidence_keywords = [

            "actual result",
            "expected result",
            "issue",
            "defect",
            "bug description",
            "observed"

        ]

        has_issue_evidence = any(

            keyword in lower_context

            for keyword in evidence_keywords

        )

        if not has_issue_evidence:

            lower_response = response.lower()

            invented_result = [

                "actual result:",
                "system allows",
                "system does not",
                "validation failed"

            ]

            if any(

                item in lower_response

                for item in invented_result

            ):

                return {

                    "success": False,

                    "error":
                    "Bug report blocked. No defect evidence available.",

                    "response": response

                }

        return None


    # --------------------------------------------------
    # Validate
    # --------------------------------------------------

    def validate(

        self,

        response,

        intent=None,

        context=""

    ):

        response = self.clean(response)

        guard_result = self.guard.validate(
            response=response,
            context=context,
            intent=intent
        )

        if not guard_result.get("success"):

            return guard_result

        response = guard_result["response"]

        response = self.formatter.format(
            response,
            intent or "answer"
        )


        if not response:

            return {

                "success": False,

                "error": "Empty AI response.",

                "response": ""

            }


        minimum = self.minimum_length.get(
            intent,
            20
)

        if len(response) < minimum:

            return {

                "success": False,

                "error": "AI response too short.",

                "response": response

            }


        if intent == "sql":

            result = self.validate_sql(

                response,

                context

            )

            if result:

                return result


        if intent == "api":

            result = self.validate_api(

                response,

                context

            )

            if result:

                return result


        if intent == "bug":

            result = self.validate_bug(

                response,

                context

            )

            if result:

                return result



        hallucination_keywords = [

            "assuming",

            "i assume",

            "i'm assuming",

            "probably",

            "might be",

            "could be"

        ]


        lower = response.lower()

        warnings = []


        for keyword in hallucination_keywords:

            if keyword in lower:

                warnings.append(

                    f"Possible hallucination: '{keyword}'"

                )


        confidence = guard_result.get(
            "confidence",
            1.0
        )

        if warnings:

            confidence = max(
                confidence - 0.2,
                0.0
            )


        return {

            "success": True,

            "response": response,

            "warnings": warnings + guard_result.get(
                "warnings",
                []
            ),

            "confidence": confidence

        }