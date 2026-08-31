# Core/api_test_generator.py
"""
QA AI Studio
API Test Generator
Version: 5.0

Features:
- RAG based API test generation
- SRS/CRF API information extraction
- Intelligent REST assumptions
- API validation rules
- Positive / Negative scenarios
- Hallucination protection
- Structured output
- Better fallback handling
"""

import logging
import time
import re

from Core.rag_engine import RAGEngine
from Core.prompt_builder import PromptBuilder
from Core.llm_engine import LLMEngine


logger = logging.getLogger("QA_AI_STUDIO")


class APITestGenerator:


    def __init__(self):

        self.rag = RAGEngine()

        self.prompt = PromptBuilder()

        self.llm = LLMEngine()


        logger.info(
            "API Test Generator v5.0 initialized"
        )



    # --------------------------------------------------

    def generate(
        self,
        api_description,
        domain=None,
        module=None,
        knowledge_name=None,
        version=None
    ):

        start_time = time.time()


        try:

            rag = self.rag.get_context(

                question=api_description,

                top_k=5,

                domain=domain,

                module=module,

                knowledge_name=knowledge_name,

                version=version

            )


            context = ""


            if rag.get("success"):

                context = rag.get(
                    "context",
                    ""
                )


            api_info = self._analyze_api_context(
                context
            )


            prompt = self._build_api_prompt(

                api_description,

                context,

                api_info

            )


            result = self.llm.generate(

                prompt=prompt,

                system_prompt=self.prompt.system_prompt()

            )


            if not result["success"]:

                return result



            response = self._validate_response(

                result["response"],

                api_info,

                api_description

            )


            elapsed = round(

                time.time() - start_time,

                2

            )


            logger.info(

                "API test generation completed | Time=%ss",

                elapsed

            )


            return {

                "success": True,

                "api_tests": response,

                "references": rag.get(
                    "references",
                    []
                ),

                "provider": result.get(
                    "provider"
                ),

                "model": result.get(
                    "model"
                ),

                "execution_time": elapsed

            }


        except Exception as exc:


            logger.exception(

                "API Test Generator failed"

            )


            return {

                "success": False,

                "error": str(exc)

            }



    # --------------------------------------------------

    def _analyze_api_context(
        self,
        context
    ):


        if not context:


            return {

                "available": False,

                "methods": [],

                "endpoints": []

            }



        text = str(context)


        methods = []


        for method in [

            "GET",

            "POST",

            "PUT",

            "DELETE",

            "PATCH"

        ]:


            if method in text.upper():

                methods.append(
                    method
                )


        endpoints = re.findall(

            r"/[a-zA-Z0-9_\-/{}]+",

            text

        )


        keywords = [

            "api",

            "endpoint",

            "request",

            "response",

            "method id",

            "service"

        ]


        available = any(

            k in text.lower()

            for k in keywords

        )


        return {


            "available": available,


            "methods": list(
                set(methods)
            ),


            "endpoints": list(
                set(endpoints)
            )

        }



    # --------------------------------------------------

    def _build_api_prompt(

        self,

        requirement,

        context,

        api_info

    ):


        if api_info["available"]:


            api_instruction = """

API details exist in knowledge context.

Generate API specification only from available information.

Do not invent missing values.

"""


        else:


            api_instruction = """

API specification is not available.

Generate a QA API analysis template.

Clearly mention assumptions.

Never create fake endpoints,
authentication,
payloads,
or responses.

"""



        return f"""

You are a Senior API QA Engineer.

Generate enterprise quality API test documentation.


Requirement:

{requirement}



Knowledge Context:

{context if context else "No API context available."}



Detected API Information:

{api_info}



Instructions:

{api_instruction}



Output Format:


API Name:


Endpoint:


HTTP Method:


Authentication:


Headers:


Path Parameters:


Query Parameters:


Request Body:


Expected Response:


API Validation Rules:


Positive Test Scenarios:


Negative Test Scenarios:


Assumptions:


Missing Information:



Rules:

- Never hallucinate APIs.
- Never invent endpoints.
- Never invent payload fields.
- Never invent authentication.
- Use PSW terminology when available.
- Keep output concise.

"""



    # --------------------------------------------------

    def _validate_response(

        self,

        response,

        api_info,

        requirement

    ):


        if not response:

            return self._fallback(
                requirement
            )



        response = response.strip()



        if not api_info["available"]:


            forbidden = [

                "example.com",

                "Bearer token",

                "/api/v1/"

            ]


            for item in forbidden:


                if item.lower() in response.lower():

                    return self._fallback(
                        requirement
                    )



        return response



    # --------------------------------------------------

    def _fallback(

        self,

        requirement

    ):


        return f"""

API Name:

Not Available


Requirement:

{requirement}


Endpoint:

Insufficient API specification available.


HTTP Method:

Not Provided


Authentication:

Not Provided


Headers:

Not Provided


Request Body:

Not Provided


Expected Response:

Not Provided


API Validation Rules:

API contract is required before execution.


Positive Test Scenarios:

1. Validate successful request after API contract availability.


Negative Test Scenarios:

1. Validate invalid request handling after API contract availability.


Assumptions:

None.


Missing Information:

- Endpoint
- HTTP method
- Request payload
- Response schema
- Authentication details

"""