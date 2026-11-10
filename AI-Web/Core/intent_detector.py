"""
QA AI Studio
Intent Detector

Version: 2.0 (Task 4)

The previous version returned the FIRST regex hit in dictionary order, so any
requirement question containing words like "database", "query", "json",
"error", "issue", "script" or "api" was sent to the SQL / API / bug /
automation generator instead of being answered from the knowledge base.

Now a generator intent is chosen only when the user is actually asking for
that artefact (a specific noun phrase such as "test cases", "SQL query",
"bug report", "automation script", "API tests"). Interrogative questions
("What is a test case?", "How does the API validate the HS code?") are
always answered from knowledge.
"""

import re

_GEN_VERB = re.compile(
    r"\b(generate|write|create|draft|produce|build|prepare|make|give me|"
    r"suggest|list|design|develop|convert|automate)\b", re.I)

_INTERROGATIVE = re.compile(
    r"^\s*(what|why|how|who|when|where|which|whom|is|are|was|were|does|do|did|"
    r"can|could|should|would|explain|describe|define|tell me|summari[sz]e|"
    r"compare|difference|list the (rules|requirements|validations))\b", re.I)

# (intent, pattern) in priority order; each must name the artefact itself.
_ARTEFACT = (
    ("bug", re.compile(r"\b(bug|defect) (report|ticket|description)\b|"
                       r"\breport (a|the) (bug|defect)\b", re.I)),
    ("sql", re.compile(r"\bsql (query|queries|statement|script)\b|"
                       r"\b(write|generate|create) (me )?(a |an |the )?(sql|query)\b|"
                       r"\bselect\b.+\bfrom\b", re.I)),
    ("automation", re.compile(
        r"\b(selenium|playwright|pytest|cypress)\b.*\b(script|test|code|suite)\b|"
        r"\bautomation (script|code|suite)\b|\bautomate (this|the|these)\b", re.I)),
    ("api", re.compile(r"\bapi (test|tests|test cases|testing)\b|\bpostman\b|"
                       r"\bendpoint (test|tests)\b", re.I)),
    ("test_case", re.compile(r"\btest[- ]?cases?\b|\btest scenarios?\b", re.I)),
)


class IntentDetector:

    def detect(self, question):

        text = (question or "").strip()

        if not text:
            return "answer"

        has_verb = bool(_GEN_VERB.search(text))

        # A question is answered from knowledge unless it explicitly asks to
        # generate something.
        if _INTERROGATIVE.search(text) and not has_verb:
            return "answer"

        for intent, pattern in _ARTEFACT:
            if pattern.search(text):
                # "write ..."/"generate ..." or a bare artefact request such
                # as "test cases for the CRF form"
                if has_verb or intent in ("test_case", "bug"):
                    return intent

        return "answer"
