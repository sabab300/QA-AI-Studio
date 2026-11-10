"""
QA AI Studio
Output Formatter

Version: 3.0
Production Ready
"""

import re
from datetime import datetime

from Core.logger import Logger


class OutputFormatter:


    def __init__(self):

        self.logger = Logger.get_logger()


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


        # QA-AI-STUDIO-TASK4: answers from the enhanced pipeline are already
        # formatted. format() strips indentation and removes repeated lines
        # across the whole answer, which corrupts tables, code and lists.
        if not result.get("preformatted"):

            answer = self.format(
                answer
            )


        success = result.get(
            "success",
            False
        )


        # QA-AI-STUDIO-AI-ASSISTANT-ROOT-FIX: this used to be
        # `confidence = result.get("confidence"); if confidence is None:
        # confidence = 85` unconditionally — so ANY upstream failure dict
        # that didn't happen to carry a "confidence" key (an internet
        # search failure from Core/internet_search.py /
        # Core/duckduckgo_provider.py, a "response too short"/"empty AI
        # response" rejection from Core/response_validator.py, an
        # unknown-provider error, etc.) silently became a fake 85%
        # confidence on a blank answer — this is the exact mechanism
        # behind the reported "(no answer) confidence=85%" bug. Per
        # Phase 7's explicit requirement, a genuinely failed generation
        # must never carry a hardcoded/default confidence — only a real
        # SUCCESS gets the 85 fallback (kept for successful generator
        # paths that legitimately don't compute their own confidence
        # score); any failure is always 0.
        confidence = result.get(
            "confidence"
        )


        if confidence is None:

            confidence = 85 if success else 0


        # [AI-DIAG] temporary diagnostic — the exact fields being sent
        # back over the wire as the final /api/ai-assistant/ask response.
        # This is the last boundary before the frontend, so a mismatch
        # here vs. what index.html reads is diagnosable directly from
        # this one line. Safe to remove once the pipeline is confirmed
        # stable.
        self.logger.info(
            "[AI-DIAG] Final API response: success=%s route=%s intent=%s "
            "answer_chars=%s confidence=%s error=%s" % (
                success,
                result.get("route", "local"),
                result.get("intent", "answer"),
                len(answer or ""),
                confidence,
                result.get("error"),
            )
        )


        return {


            "success": success,


            "route": result.get(
                "route",
                "local"
            ),


            "intent": result.get(
                "intent",
                "answer"
            ),


            "answer": answer,


            # QA-AI-STUDIO-TASK4: the enhanced pipeline returns structured
            # source objects (id, file, excerpt, ...); the legacy pipeline
            # returns a list of reference strings. Existing consumers only
            # use the list length, which stays correct for both.
            "sources": (
                result.get("sources")
                if result.get("preformatted")
                and isinstance(result.get("sources"), list)
                else result.get("references", [])
            ),


            "warnings": result.get(
                "warnings",
                []
            ),


            "confidence": confidence,


            # QA-AI-STUDIO-AI-ASSISTANT-ROOT-FIX: the real "error" message
            # produced by whichever stage failed (internet search, RAG,
            # Ollama, response_validator, ai_guard) was previously dropped
            # here entirely — the frontend had no way to show *why* a
            # request failed, even when the backend already knew it. This
            # is additive only: existing consumers that only read
            # "answer"/"confidence"/"success" are unaffected.
            "error": result.get(
                "error"
            ),


            "provider": result.get(
                "provider"
            ),


            "model": result.get(
                "model"
            ),


            "execution_time": result.get(
                "execution_time"
            ),


            "timestamp": datetime.now().isoformat(),

            # QA-AI-STUDIO-TASK4: additive fields for the AI Assistant UI.
            "citations": result.get("citations", []),

            "platform": result.get("platform"),

        }