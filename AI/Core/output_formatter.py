"""
QA AI Studio
Output Formatter

Version: 3.0
Production Ready
"""

import re
from datetime import datetime


class OutputFormatter:


    def __init__(self):

        pass


    # --------------------------------------------------
    # Normalize whitespace
    # --------------------------------------------------

    def _normalize_whitespace(
        self,
        text
    ):

        text = text.replace(
            "\r",
            ""
        )

        text = text.replace(
            "\t",
            " "
        )

        text = re.sub(
            r"[ ]{2,}",
            " ",
            text
        )

        text = re.sub(
            r"\n{3,}",
            "\n\n",
            text
        )

        return text.strip()



    # --------------------------------------------------
    # Remove duplicate lines
    # --------------------------------------------------

    def _remove_duplicate_lines(
        self,
        text
    ):

        seen = set()

        output = []


        for line in text.split("\n"):

            value = line.strip()


            if not value:

                output.append("")

                continue


            key = value.lower()


            if key in seen:

                continue


            seen.add(key)

            output.append(value)


        return "\n".join(output).strip()



    # --------------------------------------------------
    # Normalize bullets
    # --------------------------------------------------

    def _fix_bullets(
        self,
        text
    ):

        lines = []


        for line in text.split("\n"):

            value = line.strip()


            if value.startswith("* "):

                value = "- " + value[2:]


            elif value.startswith("•"):

                value = "- " + value[1:].strip()


            lines.append(value)


        return "\n".join(lines)



    # --------------------------------------------------
    # Remove duplicate markdown headings
    # --------------------------------------------------

    def _clean_headings(
        self,
        text
    ):

        headings = set()

        output = []


        for line in text.split("\n"):

            value = line.strip()


            if value.startswith("#"):

                key = value.lower()


                if key in headings:

                    continue


                headings.add(key)


            output.append(value)


        return "\n".join(output)



    # --------------------------------------------------
    # Clean empty bullets
    # --------------------------------------------------

    def _remove_empty_bullets(
        self,
        text
    ):

        lines = []


        for line in text.split("\n"):

            value = line.strip()


            if value in [
                "-",
                "*",
                "•"
            ]:

                continue


            lines.append(value)


        return "\n".join(lines)



    # --------------------------------------------------
    # Main formatter
    # --------------------------------------------------

    def format(
        self,
        response,
        output_type="answer"
    ):

        if not response:

            return ""


        response = self._normalize_whitespace(
            response
        )


        response = self._remove_duplicate_lines(
            response
        )


        response = self._fix_bullets(
            response
        )


        response = self._clean_headings(
            response
        )


        response = self._remove_empty_bullets(
            response
        )


        return response.strip()



    # --------------------------------------------------
    # Enterprise Unified Response
    # --------------------------------------------------

    def format_response(
        self,
        result
    ):


        if not isinstance(result, dict):

            return {

                "success": False,

                "error":
                "Invalid response format"

            }



        answer = (

            result.get("answer")

            or result.get("response")

            or ""

        )


        answer = self.format(
            answer
        )



        confidence = result.get(
            "confidence"
        )


        if confidence is None:

            confidence = 85



        return {


            "success": result.get(
                "success",
                False
            ),


            "route": result.get(
                "route",
                "local"
            ),


            "intent": result.get(
                "intent",
                "answer"
            ),


            "answer": answer,


            "sources": result.get(
                "references",
                []
            ),


            "warnings": result.get(
                "warnings",
                []
            ),


            "confidence": confidence,


            "provider": result.get(
                "provider"
            ),


            "model": result.get(
                "model"
            ),


            "execution_time": result.get(
                "execution_time"
            ),


            "timestamp": datetime.now().isoformat()

        }