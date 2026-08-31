"""
QA AI Studio
AI Guard

Version: 1.1
"""

import re


class AIGuard:

    def __init__(self):

        self.blocked_patterns = [

            r"<\|im_start\|>",
            r"<\|im_end\|>",
            r"<think>.*?</think>",
            r"</think>",
            r"```text",
            r"```output",
            r"```plaintext"

        ]

        self.hallucination_keywords = [

            "i assume",
            "i'm assuming",
            "assuming",
            "probably",
            "might be",
            "could be",
            "it appears",
            "possibly",
            "likely"

        ]


    # --------------------------------------------------
    # Clean Response
    # --------------------------------------------------

    def clean(self, response):

        if not isinstance(response, str):

            return ""

        text = response

        for pattern in self.blocked_patterns:

            text = re.sub(
                pattern,
                "",
                text,
                flags=re.IGNORECASE | re.DOTALL
            )

        text = text.replace(
            "\r",
            ""
        )

        text = text.replace(
            "\t",
            " "
        )

        text = re.sub(
            r"\n{3,}",
            "\n\n",
            text
        )

        return text.strip()


    # --------------------------------------------------
    # Remove Duplicate Lines
    # --------------------------------------------------

    def remove_duplicate_sentences(self, text):

        if not text:

            return ""

        seen = set()

        cleaned = []

        for line in text.splitlines():

            value = line.strip()

            if not value:

                cleaned.append("")

                continue


            key = value.lower()

            if key in seen:

                continue


            seen.add(key)

            cleaned.append(value)


        return "\n".join(cleaned).strip()


    # --------------------------------------------------
    # Detect Hallucination
    # --------------------------------------------------

    def detect_hallucination(self, text):

        warnings = []

        lower = text.lower()


        for keyword in self.hallucination_keywords:

            if keyword in lower:

                warnings.append(
                    f"Possible hallucination: '{keyword}'"
                )


        return warnings


    # --------------------------------------------------
    # Confidence Score
    # --------------------------------------------------

    def confidence(

        self,

        text,

        references=0

    ):

        score = 100


        length = len(text)


        if length < 100:

            score -= 25


        elif length < 300:

            score -= 10



        warnings = self.detect_hallucination(text)


        score -= len(warnings) * 10



        if references > 0:

            score += min(
                references * 3,
                10
            )


        return max(
            0,
            min(
                100,
                score
            )
        )


    # --------------------------------------------------
    # Process Response
    # --------------------------------------------------

    def process(

        self,

        response,

        references=0

    ):


        text = self.clean(response)


        text = self.remove_duplicate_sentences(
            text
        )


        warnings = self.detect_hallucination(
            text
        )


        confidence = self.confidence(

            text,

            references

        )


        return {

            "response": text,

            "warnings": warnings,

            "confidence": confidence

        }


    # --------------------------------------------------
    # Validate Response
    # --------------------------------------------------

    def validate(

        self,

        response,

        context="",

        intent="answer"

    ):


        reference_count = 0


        if context:

            reference_count = len(
                context.split("\n")
            )


        result = self.process(

            response,

            references=reference_count

        )


        warnings = result.get(
            "warnings",
            []
        )


        cleaned = result.get(
            "response",
            ""
        )


        confidence = result.get(
            "confidence",
            0
        )


        if not cleaned.strip():

            return {

                "success": False,

                "error":
                "Empty AI response.",

                "response":
                "",

                "warnings":
                warnings,

                "confidence":
                confidence

            }


        # Strict validation for QA artifacts

        strict_intents = [

            "test_case",
            "automation",
            "api",
            "sql",
            "bug"

        ]


        if intent in strict_intents:

            if confidence < 40:

                warnings.append(

                    f"Low confidence response for {intent}"

                )


        return {

            "success": True,

            "response": cleaned,

            "warnings": warnings,

            "confidence": confidence

        }