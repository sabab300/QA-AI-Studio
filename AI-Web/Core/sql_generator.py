# Core/sql_generator.py
# QA AI Studio - SQL Generator v6.0

import logging
import time

from Core.ollama_provider import OllamaProvider
from Core.prompt_builder import PromptBuilder


logger = logging.getLogger("QA_AI_STUDIO")


class SQLGenerator:
    """
    Enterprise SQL Generator

    Compatible with:
    - Ollama Provider v6
    - Future Cloud Providers
    - AI Provider Interface
    """

    def __init__(self):

        self.llm = OllamaProvider()

        self.prompt_builder = PromptBuilder()

        logger.info(
            "SQL Generator v6.0 initialized"
        )

    # ---------------------------------------------------------

    def generate(
        self,
        requirement,
        context=None,
        database_schema=None,
        **kwargs
    ):

        start_time = time.time()

        try:

            schema_available = self._has_schema(
                database_schema,
                context
            )

            prompt = self._build_prompt(
                requirement=requirement,
                context=context,
                database_schema=database_schema,
                schema_available=schema_available
            )

            response = self.llm.generate(
                prompt=prompt,
                temperature=0.1,
                max_tokens=1200
            )

            response_text = self._extract_text(response)

            result = self._validate_output(
                response_text,
                schema_available,
                requirement
            )

            elapsed = round(
                time.time() - start_time,
                2
            )

            logger.info(
                "SQL generation completed | Time=%ss",
                elapsed
            )

            return {
                "success": True,
                "answer": result,
                "provider": self._extract_provider(response),
                "model": self._extract_model(response)
            }

        except Exception:

            logger.exception(
                "SQL generation failed"
            )

            return {
                "success": False,
                "answer": self._fallback_sql(requirement),
                "provider": "Ollama",
                "model": self.llm.model
            }

    # ---------------------------------------------------------

    def _extract_text(self, response):

        if response is None:
            return ""

        if isinstance(response, str):
            return response

        if isinstance(response, dict):

            for key in (
                "answer",
                "response",
                "text",
                "content",
                "output"
            ):

                value = response.get(key)

                if isinstance(value, str):
                    return value

        return str(response)

    # ---------------------------------------------------------

    def _extract_provider(self, response):

        if isinstance(response, dict):
            return response.get(
                "provider",
                "Ollama"
            )

        return "Ollama"

    # ---------------------------------------------------------

    def _extract_model(self, response):

        if isinstance(response, dict):
            return response.get(
                "model",
                self.llm.model
            )

        return self.llm.model

    # ---------------------------------------------------------

    def _has_schema(
        self,
        database_schema=None,
        context=None
    ):

        keywords = [

            "table",
            "column",
            "schema",
            "database",
            "create table",
            "select",
            "insert"

        ]

        text = ""

        if database_schema:
            text += str(database_schema).lower()

        if context:
            text += str(context).lower()

        return any(
            keyword in text
            for keyword in keywords
        )

    # ---------------------------------------------------------

    def _build_prompt(
        self,
        requirement,
        context,
        database_schema,
        schema_available
    ):

        if schema_available:

            instruction = """

Generate SQL using ONLY the supplied schema.

Rules:

- Never invent tables.
- Never invent columns.
- Never invent joins.
- Never invent relationships.
- Explain validation purpose.

"""

        else:

            instruction = """

Database schema is unavailable.

Do NOT invent executable SQL.

Provide:

Purpose

Required schema

Example template only

"""

        return f"""

You are a Senior Database QA Engineer.

Requirement

{requirement}

Knowledge Context

{context if context else "No context available."}

Database Schema

{database_schema if database_schema else "Not Available"}

{instruction}

Output Format

Purpose:

SQL Type:

SQL Query:

Expected Result:

Validation Notes:

Assumptions:

Missing Information:

"""

    # ---------------------------------------------------------

    def _validate_output(
        self,
        response,
        schema_available,
        requirement
    ):

        if not response:

            return self._fallback_sql(
                requirement
            )

        response = str(response).strip()

        dangerous = [

            "drop table",
            "create table",
            "alter table"

        ]

        if not schema_available:

            lower = response.lower()

            for item in dangerous:

                if item in lower:

                    return self._fallback_sql(
                        requirement
                    )

        if (
            "Purpose:" not in response
            and
            "SQL Query:" not in response
        ):

            response = f"""

Purpose:

SQL Validation

SQL Type:

Validation Query

SQL Query:

{response}

Expected Result:

Returned data should satisfy validation.

Validation Notes:

Review against approved schema.

"""

        return response

    # ---------------------------------------------------------

    def _fallback_sql(
        self,
        requirement
    ):

        return f"""

Purpose:

Validate

{requirement}

SQL Type:

Schema Required

SQL Query:

-- Database schema required

Expected Result:

Provide:

- Tables

- Columns

- Relationships

Validation Notes:

QA AI Studio intentionally avoided hallucinated SQL.

Assumptions:

None

Missing Information:

Database schema unavailable.

"""

    # ---------------------------------------------------------

    def generate_validation_query(
        self,
        requirement,
        schema=None
    ):

        return self.generate(
            requirement=requirement,
            database_schema=schema
        )