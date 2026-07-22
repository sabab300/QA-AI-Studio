"""
QA AI Studio
Intent Detector

Version: 1.0
"""

import re


class IntentDetector:

    def __init__(self):

        self.patterns = {

            "sql": [

                r"\bsql\b",

                r"\bquery\b",

                r"\bdatabase\b",

                r"\bselect\b",

                r"\bjoin\b"

            ],

            "automation": [

                r"\bselenium\b",

                r"\bautomation\b",

                r"\bpytest\b",

                r"\bplaywright\b",

                r"\bscript\b"

            ],

            "api": [

                r"\bapi\b",

                r"\bendpoint\b",

                r"\bpostman\b",

                r"\brest\b",

                r"\bjson\b"

            ],

            "bug": [

                r"\bbug\b",

                r"\bdefect\b",

                r"\berror\b",

                r"\bissue\b",

                r"\bproblem\b"

            ],

            "test_case": [

                r"\btest case\b",

                r"\btest cases\b",

                r"\bscenario\b",

                r"\btest scenario\b"

            ],

            "answer": [

                r"\bwhat\b",

                r"\bwhy\b",

                r"\bhow\b",

                r"\bexplain\b",

                r"\bdescribe\b"

            ]

        }

    # --------------------------------------------------

    def detect(

        self,

        question

    ):

        text = question.lower()

        for intent, patterns in self.patterns.items():

            for pattern in patterns:

                if re.search(

                    pattern,

                    text

                ):

                    return intent

        return "answer"