"""QA AI Studio - grounded Test Case generation engine.

Why this module exists
----------------------
The earlier generator asked the model ONE question over the 5 most similar chunks of the whole
Knowledge scope, through the general chat pipeline. That pipeline cut the prompt at 12,000
characters (the type and format instructions were at the END, so they were the part removed),
deleted repeated lines from the knowledge text, replayed earlier batches as chat history and
de-duplicated lines of the answer. Test types were only listed by name, never defined, and any
type the model invented was kept.

This engine fixes the cause instead of the symptom:

* the WHOLE selected source document is read and split into REQUIREMENTS (Core.requirement_extractor);
  each requirement is cut into windows of about 3,000 characters, so the model always sees the
  complete requirement and the instructions at the same time;
* every case must quote the statement of the document that it verifies; the quote is located in the
  document by the program (not by the model) and the case is rejected when it cannot be found;
* only the test types ticked by the user are defined in the prompt and accepted in the result;
  a type is requested for a section only when the section contains evidence for it;
* the answer is parsed from a labelled block format that survives multi-line steps;
* every case is checked deterministically: required fields, allowed type, type/polarity
  consistency, grounding in the section text, invented quoted messages, duplicates;
* the requested number of cases is honoured and spread across sections and types, and the
  result carries a plain-language quality report (what was kept, removed and why).

Everything here is deterministic except the single ``llm`` callable that is passed in, so the logic
can be tested without a model.
"""

from __future__ import annotations

import copy
import math
import re
import time
from typing import Callable, Dict, Iterable, List, Optional

from Core.requirement_extractor import (  # noqa: E402
    extract_requirements, find_statement, validate_requirements, window_evidence, _statements,
)
from Core import business_rules as br  # noqa: E402
from Core import test_case_quality as quality  # noqa: E402

# ---------------------------------------------------------------------------
# Test type catalogue
# ---------------------------------------------------------------------------

TYPE_DEFINITIONS: Dict[str, str] = {
    "Functional": "the business function or rule works as the document describes it (one function, one outcome).",
    "Positive": "valid data, allowed role and correct sequence: the action succeeds and the documented result occurs.",
    "Negative": "invalid, missing, forbidden or out-of-sequence data/action: the system refuses it and behaves as documented (error, block, no change).",
    "Boundary": "values exactly at, just inside and just beyond a limit written in the document (length, range, count, size, date window).",
    "Validation": "field rules written in the document: mandatory, data type, format, length, allowed values, cross-field rules.",
    "Integration": "data or status exchanged with another system or module that the document names.",
    "API": "request, response, method, parameters, status codes or error payload of an API that the document names.",
    "UI": "screens, labels, buttons, tabs, defaults, enabled/disabled state and messages exactly as the document names them.",
    "Database": "what must be stored, updated, linked or must NOT be stored after an action, as the document states.",
    "Security": "roles, permissions, authentication or access that the document restricts.",
    "Performance": "timing, volume or capacity statements that the document makes.",
    "Compatibility": "browsers, devices, file formats or versions that the document names.",
}

# Types that must not be requested for a section unless the section contains a matching word.
# Without this, a model asked for "API" tests on a screen-only CRF invents endpoints.
EVIDENCE_WORDS: Dict[str, tuple] = {
    "Integration": ("integrat", "interface", "external", "exchange", "third part", "gateway", "web service",
                    "webservice", "synchron", "sync ", "send to", "receive", "notif", "other system", "bank",
                    "customs", "oga", "psw", "weboc", "pra", "fbr", "nadra", "secp"),
    "API": ("api", "endpoint", "request", "response", "payload", "json", "xml", "http", "rest", "soap",
            "status code", "token", "webhook"),
    "Database": ("database", "table", "column", "stored", "store", "saved", "save", "record", "persist",
                 "insert", "update", "audit", "history", "log"),
    "Security": ("role", "permission", "authoriz", "authoris", "authent", "access", "login", "log in", "password",
                 "token", "encrypt", "privilege", "restricted", "user type", "otp", "captcha", "signature",
                 "approve", "approval"),
    "Performance": ("second", "minute", "millisecond", "concurrent", "throughput", "response time", "performance",
                    "load", "timeout", "time out", "within", "sla", "capacity", "volume"),
    "Compatibility": ("browser", "chrome", "firefox", "edge", "safari", "mobile", "device", "resolution",
                      "compatib", "pdf", "excel", "xlsx", "csv", "version", "format"),
    "Boundary": ("max", "min", "length", "limit", "range", "digit", "character", "between", "exceed", "up to",
                 "at least", "at most", "not more", "not less", "days", "size", "mb", "kb", "greater", "less than"),
}

ALIASES: Dict[str, str] = {
    "functional testing": "Functional", "functionality": "Functional", "business": "Functional",
    "positive testing": "Positive", "happy path": "Positive", "valid": "Positive",
    "negative testing": "Negative", "invalid": "Negative", "error handling": "Negative", "exception": "Negative",
    "boundary value": "Boundary", "boundary value analysis": "Boundary", "bva": "Boundary", "limit": "Boundary",
    "field validation": "Validation", "data validation": "Validation", "validation testing": "Validation",
    "input validation": "Validation", "mandatory": "Validation",
    "integration testing": "Integration", "interface": "Integration",
    "api testing": "API", "service": "API", "web service": "API",
    "user interface": "UI", "ui testing": "UI", "gui": "UI", "usability": "UI", "ux": "UI",
    "db": "Database", "data": "Database", "data integrity": "Database", "sql": "Database",
    "authorization": "Security", "authorisation": "Security", "authentication": "Security",
    "access control": "Security", "permission": "Security", "role": "Security",
    "load": "Performance", "stress": "Performance", "speed": "Performance",
    "browser": "Compatibility", "cross browser": "Compatibility", "device": "Compatibility",
}

IMPORTANCE_ALIASES = {
    "high": "High", "critical": "High", "blocker": "High", "major": "High", "p0": "High", "p1": "High", "1": "High",
    "medium": "Medium", "normal": "Medium", "moderate": "Medium", "p2": "Medium", "2": "Medium",
    "low": "Low", "minor": "Low", "trivial": "Low", "cosmetic": "Low", "p3": "Low", "p4": "Low", "3": "Low",
}

EXECUTION_ALIASES = {
    "manual": "Manual", "playwright": "Playwright", "ui": "Playwright", "browser": "Playwright", "web": "Playwright",
    "api": "API", "service": "API", "sql": "SQL", "database": "SQL", "db": "SQL",
}

NEGATIVE_CUES = ("error", "reject", "not allowed", "invalid", "cannot", "can not", "can't", "unable", "prevent",
                 "denied", "fail", "blocked", "block", "disabled", "mandatory", "required", "not accept",
                 "not saved", "not be saved", "not be", "no record", "refus", "message", "warn", "restricted",
                 "unauthor", "forbidden", "must not", "should not", "without", "empty", "blank", "missing",
                 "exceed", "duplicate", "expired")
NEGATIVE_TITLE_CUES = ("invalid", "blank", "empty", "without", "missing", "exceed", "unauthor", "duplicate",
                       "incorrect", "wrong", "not allowed", "cannot", "reject", "expired", "unable", "mandatory field",
                       "forbidden", "denied", "fails", "failure", "negative")

STOPWORDS = set("""
the and for with that this from are was were has have had will shall should must can may not but any all
each into onto upon when then than also such only more most some over under after before above below
user system application screen page field value data test case verify ensure check that which whose
their there these those they them been being does done else here once other using used use via per
""".split())

REQUIRED_TEXT_FIELDS = ("scenario", "test_case", "steps", "expected_result")
PLACEHOLDERS = {"", "n/a", "na", "none", "tbd", "todo", "...", "-", "--", "null", "not applicable", "<...>"}

DEFAULT_PRECONDITION = "The user is signed in to the application with access to this function."

# ---------------------------------------------------------------------------
# Small text helpers
# ---------------------------------------------------------------------------


def _words(text: str) -> List[str]:
    out = []
    for token in re.findall(r"[a-z0-9][a-z0-9_\-/\.]*[a-z0-9]|[a-z0-9]", str(text or "").lower()):
        token = token.strip("._-/")
        if len(token) < 4 or token in STOPWORDS:
            continue
        if token.endswith("s") and len(token) > 5:
            token = token[:-1]
        out.append(token)
    return out


def _clean(value) -> str:
    text = str(value or "").replace("\r", "")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _is_placeholder(value) -> bool:
    return _clean(value).lower() in PLACEHOLDERS


# ---------------------------------------------------------------------------
# Windows (sections) of the source document
# ---------------------------------------------------------------------------


def merge_chunks(chunks: Iterable[dict]) -> str:
    """Join consecutive chunks of one document, removing the overlap the chunker added."""
    result = ""
    for chunk in chunks:
        text = _clean(chunk.get("text") if isinstance(chunk, dict) else chunk)
        if not text:
            continue
        if not result:
            result = text
            continue
        overlap = 0
        for size in range(min(320, len(result), len(text)), 24, -1):
            if result.endswith(text[:size]):
                overlap = size
                break
        result += ("\n\n" if not overlap else "") + text[overlap:]
    return result


def split_windows(text: str, max_chars: int = 3000) -> List[str]:
    """Cut the document into sections at paragraph boundaries (never in the middle of a line)."""
    paragraphs = [p for p in re.split(r"\n\s*\n", text or "") if p.strip()]
    windows, current = [], ""
    for paragraph in paragraphs:
        while len(paragraph) > max_chars:  # a single enormous paragraph: cut at a line/sentence end
            cut = max(paragraph.rfind("\n", 0, max_chars), paragraph.rfind(". ", 0, max_chars))
            cut = cut if cut > max_chars // 2 else max_chars
            piece, paragraph = paragraph[:cut + 1].strip(), paragraph[cut + 1:].strip()
            # repeat the last line of the piece at the start of the rest, so a sentence that continues the previous
            # one (a condition and its consequence) is never read without it
            last_line = piece.rsplit("\n", 1)[-1].strip()
            if paragraph and 0 < len(last_line) < max_chars // 3:
                paragraph = last_line + "\n" + paragraph
            if current:
                windows.append(current)
                current = ""
            windows.append(piece)
        if not paragraph:
            continue
        if current and len(current) + len(paragraph) + 2 > max_chars:
            windows.append(current)
            current = ""
        current = (current + "\n\n" + paragraph) if current else paragraph
    if current:
        windows.append(current)
    # a short tail (a few table rows, a last sentence) is read with the previous section instead of on its own
    if len(windows) >= 2 and len(windows[-1]) < max_chars // 5 and len(windows[-2]) + len(windows[-1]) <= int(max_chars * 1.3):
        tail = windows.pop()
        head, last = windows[-1], tail.split("\n", 1)
        windows[-1] = head + ("\n" + last[1] if len(last) > 1 and head.endswith(last[0]) else "\n" + tail)
    return [w for w in windows if len(w.strip()) >= 40]


RULE_WORDS = ("must", "shall", "should", "mandatory", "required", "validate", "validation", "only", "allowed",
              "not allowed", "error", "message", "if ", "when ", "otherwise", "status", "button", "field",
              "unique", "maximum", "minimum", "format", "numeric", "approve", "reject", "submit", "save")


def testability(window: str) -> int:
    lowered = window.lower()
    return sum(lowered.count(word) for word in RULE_WORDS)

# ---------------------------------------------------------------------------
# Evidence: which test types does the text give a basis for?
# ---------------------------------------------------------------------------

BASELINE_TYPES = ("Functional", "Positive")
# Types for which the extractor looks for evidence (a limit, an access rule, an API, ...).
EVIDENCE_TYPES = ("Negative", "Validation", "Boundary", "Integration", "API", "UI", "Database", "Security",
                  "Performance", "Compatibility")


def split_types(evidence, selected: List[str], testable: bool = True):
    """Return (allowed, unsupported): a type is allowed only if the text gives a basis for it.

    ``evidence`` is the dict produced by the requirement extractor, or plain text (then it is analysed here).
    """
    if isinstance(evidence, str):
        evidence = window_evidence(evidence)
    allowed, unsupported = [], []
    for name in selected:
        if name in BASELINE_TYPES or name not in EVIDENCE_TYPES:
            ok = testable
        else:
            ok = bool(evidence.get(name))
        (allowed if ok else unsupported).append(name)
    return allowed, unsupported


def types_with_evidence(evidence, selected: List[str]) -> List[str]:
    return split_types(evidence, selected)[0]


# ---------------------------------------------------------------------------
# Units of work: requirement-aligned windows
# ---------------------------------------------------------------------------


def _attach_rules_and_units(requirement: dict) -> dict:
    """Atomic business rules and coverage units of one requirement (computed once, reused everywhere)."""
    if "coverage_units" not in requirement:
        requirement["rules"] = br.extract_rules(requirement)
        requirement["coverage_units"] = quality.coverage_units(requirement, requirement["rules"]) if requirement.get("testable", True) else []
    return requirement


def _units_in_window(requirement: dict, piece: str) -> List[str]:
    """Ids of the requirement's coverage units whose text (or mapping codes) appear in this window."""
    flat = quality.key(piece)
    ids = []
    for unit in requirement.get("coverage_units") or []:
        if unit["kind"] == "mapping":
            if any(re.search(r"\b" + re.escape(code) + r"\b", piece) for code in unit.get("codes") or []):
                ids.append(unit["id"])
        elif quality.key(unit["text"])[:120] in flat:
            ids.append(unit["id"])
    return ids


def build_units(text: str, window_chars: int = 3000) -> dict:
    """Source text -> requirement units -> rules + coverage units -> windows (each window knows its requirement)."""
    extracted = extract_requirements(text)
    summary = validate_requirements(extracted)
    units = []
    skipped = []
    if extracted["units"]:
        for requirement in extracted["units"]:
            _attach_rules_and_units(requirement)
            if not requirement["testable"]:
                skipped.append(requirement)
                continue
            pieces = split_windows(requirement["text"], window_chars) or [requirement["text"]]
            for piece in pieces:
                units.append({"index": len(units), "text": piece, "requirement": requirement,
                              "evidence": window_evidence(piece) if len(pieces) > 1 else requirement["evidence"],
                              "unit_ids": _units_in_window(requirement, piece) if len(pieces) > 1
                              else [u["id"] for u in requirement["coverage_units"]]})
    else:  # nothing testable could be identified: fall back to plain sections of the text
        for number, piece in enumerate(split_windows(text, window_chars), start=1):
            lines = [ln for ln in piece.splitlines() if ln.strip()]
            requirement = {"ref": f"INT-REQ-{number:03d}", "ref_kind": "internal", "title": "", "text": piece,
                           "statements": _statements(lines), "evidence": window_evidence(piece), "testable": True}
            _attach_rules_and_units(requirement)
            units.append({"index": len(units), "text": piece, "requirement": requirement,
                          "evidence": requirement["evidence"], "unit_ids": [u["id"] for u in requirement["coverage_units"]]})
    return {"units": units, "extraction": summary, "requirements": extracted["units"], "skipped": skipped}


def _window_units(item: dict) -> List[dict]:
    wanted = set(item.get("unit_ids") or [])
    return [u for u in (item["requirement"].get("coverage_units") or []) if u["id"] in wanted]


def _window_weight(item: dict) -> int:
    """Planning weight of a window: the priority of the coverage units it holds (falls back to rule-word density)."""
    units = _window_units(item)
    if units:
        return sum(quality.PRIORITY_WEIGHT.get(u["priority"], 1) for u in units)
    return max(1, testability(item["text"]))


def plan_units(units: List[dict], requested, selected: Optional[List[str]] = None) -> List[dict]:
    """Decide how many cases to ask for in each window, spreading them over the REQUIREMENTS first.

    requested == "all": ask for what the window's coverage units support (3 to 14).
    requested == N: aim for N plus a 40% margin for removals. Every requirement gets a share when the
    margin allows it; with fewer cases than requirements, the requirements with the most (and most
    important) coverage units are covered first.
    """
    if not units:
        return []
    if str(requested).lower() == "all":
        plan = []
        for u in units:
            mine = _window_units(u)
            if mine:
                quota = sum(1 for x in mine if x["priority"] != "Low") + (1 if any(x["priority"] == "Low" for x in mine) else 0)
            else:
                quota = round(len(u["text"]) / 280)
            plan.append(dict(u, quota=max(3, min(14, quota))))
        return plan
    plan = _plan_by_share(units, requested)
    # a selected type that only a small section supports (a documented limit, an access rule) must not be lost
    # just because that section is short: add the section with one case when no planned section supports the type
    for name in (selected or []):
        if name not in EVIDENCE_TYPES or any((p.get("evidence") or {}).get(name) for p in plan):
            continue
        extra = next((u for u in units if (u.get("evidence") or {}).get(name) and u["index"] not in {p["index"] for p in plan}), None)
        if extra is not None:
            plan.append(dict(extra, quota=1))
    return sorted(plan, key=lambda p: p["index"])


def _plan_by_share(units: List[dict], requested) -> List[dict]:
    target = math.ceil(max(1, int(requested)) * 1.4)
    order, weight = [], {}
    for unit in units:
        ref = unit["requirement"]["ref"]
        if ref not in weight:
            order.append(ref)
            weight[ref] = 0
        weight[ref] += _window_weight(unit)
    if target >= len(order):
        quota = {ref: 1 for ref in order}
        spare = target - len(order)
    else:
        ranked = sorted(order, key=lambda r: weight[r], reverse=True)[:target]
        quota = {ref: 1 for ref in ranked}
        spare = 0
    total_weight = sum(weight[r] for r in quota)
    shares = {r: spare * weight[r] / total_weight for r in quota}
    for ref in quota:
        quota[ref] += int(shares[ref])
    left = spare - sum(int(v) for v in shares.values())
    for ref in sorted(quota, key=lambda r: shares[r] - int(shares[r]), reverse=True)[:max(0, left)]:
        quota[ref] += 1
    plan = []
    for ref in quota:
        mine = [u for u in units if u["requirement"]["ref"] == ref]
        w = {u["index"]: _window_weight(u) for u in mine}
        wsum = sum(w.values())
        given = {u["index"]: 0 for u in mine}
        for _ in range(quota[ref]):  # hand out one case at a time to the window with the largest unmet share
            best = max(mine, key=lambda u: w[u["index"]] / wsum * quota[ref] - given[u["index"]])
            given[best["index"]] += 1
        for u in mine:
            if given[u["index"]]:
                plan.append(dict(u, quota=min(14, given[u["index"]])))
    return plan


def type_plan(allowed: List[str], quota: int, offset: int, claims: Optional[Dict[str, int]] = None) -> Dict[str, int]:
    """Spread the section quota over the allowed types.

    ``claims`` counts what earlier sections already asked for: the least-asked types go first, so a type that only
    one section supports (a documented limit, an access rule) is not crowded out by the common types. Without
    ``claims`` the start rotates with ``offset``.
    """
    if not allowed:
        return {}
    if claims is not None:
        order = sorted(allowed, key=lambda name: (claims.get(name, 0), allowed.index(name)))
        if quota < len(order):
            plan = {name: 1 for name in order[:quota]}
        else:
            base, extra = divmod(quota, len(order))
            plan = {name: base + (1 if k < extra else 0) for k, name in enumerate(order)}
        for name, count in plan.items():
            claims[name] = claims.get(name, 0) + count
        return {name: plan[name] for name in allowed if name in plan}
    if quota < len(allowed):  # fewer cases than types: pick a rotating subset
        chosen = [allowed[(offset + k) % len(allowed)] for k in range(quota)]
        return {name: chosen.count(name) for name in dict.fromkeys(chosen)}
    base, extra = divmod(quota, len(allowed))
    plan = {}
    for k, name in enumerate(allowed):
        plan[name] = base + (1 if (k - offset) % len(allowed) < extra else 0)
    return plan


# ---------------------------------------------------------------------------
# Parsing the answer
# ---------------------------------------------------------------------------

FIELD_LABELS = {
    "scenario": ("scenario", "name / scenario / requirement", "name", "requirement", "scenario / requirement"),
    "test_case": ("test case", "title", "test case title", "test case name"),
    "test_type": ("test type", "type", "test types"),
    "importance": ("importance", "priority", "severity"),
    "execution": ("execution", "execution type", "execution method"),
    "pre_conditions": ("pre-conditions", "pre conditions", "preconditions", "pre-condition", "precondition",
                       "prerequisites", "prerequisite"),
    "steps": ("steps", "test steps", "step"),
    "expected_result": ("expected result", "expected results", "expected", "expected outcome"),
    "source_statement": ("source statement", "source", "evidence", "source text", "document statement"),
}
LABEL_LOOKUP = {label: field for field, labels in FIELD_LABELS.items() for label in labels}
LABEL_RE = re.compile(r"^\s*(?:[-*#>]+\s*)?\**\s*([A-Za-z][A-Za-z /\-]{1,32}?)\s*\**\s*:\s*\**\s*(.*)$")


def _parse_blocks(text: str) -> List[dict]:
    rows, current, field = [], None, None

    def flush():
        nonlocal current
        if current and any(current.get(k) for k in ("scenario", "test_case", "steps")):
            rows.append(current)
        current = None

    for raw in str(text or "").replace("\r", "").split("\n"):
        line = raw.rstrip()
        if re.match(r"^\s*#{1,4}\s*(test\s*case|tc)\b.*$", line, re.I) or re.match(r"^\s*={3,}\s*$", line):
            flush()
            current, field = {}, None
            continue
        match = LABEL_RE.match(line)
        label = match.group(1).strip().lower() if match else ""
        if match and label in LABEL_LOOKUP:
            target = LABEL_LOOKUP[label]
            if current is None or (target == "scenario" and current.get("scenario") and current.get("steps")):
                flush()
                current = {}
            if target in current and target != "steps":  # same label again = next test case without a header line
                flush()
                current = {}
            field = target
            current[field] = match.group(2).strip()
            continue
        if current is not None and field and line.strip():
            current[field] = (current.get(field, "") + "\n" + line.strip()).strip()
    flush()
    return rows


def _parse_table(text: str) -> List[dict]:
    rows = []
    for line in str(text or "").splitlines():
        line = line.strip()
        if not line.startswith("|") or set(line.replace("|", "").strip()) <= set("-: "):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if len(cells) < 7 or cells[0].lower().startswith(("name", "scenario")):
            continue
        rows.append({"scenario": cells[0], "importance": cells[1], "test_type": cells[2], "test_case": cells[3],
                     "pre_conditions": cells[4], "steps": cells[5].replace("<br>", "\n"), "expected_result": cells[6]})
    return rows


def parse_cases(text: str) -> List[dict]:
    """Parse the model answer. Block format first; the old markdown table is accepted as a fallback."""
    if not text or "NO_TESTABLE_CONTENT" in text.upper().replace(" ", "_") and "###" not in text:
        return []
    rows = _parse_blocks(text)
    return rows or _parse_table(text)

# ---------------------------------------------------------------------------
# Normalising and checking one case
# ---------------------------------------------------------------------------


def canonical_types(raw, selected: List[str]) -> List[str]:
    """Map whatever the model wrote to canonical type names that the user actually selected."""
    if isinstance(raw, (list, tuple)):
        parts = [str(p) for p in raw]
    else:
        parts = re.split(r"[,;/&|]| and ", str(raw or ""))
    found = []
    lookup = {name.lower(): name for name in TYPE_DEFINITIONS}
    for part in parts:
        key = re.sub(r"\s+", " ", part.strip().lower()).replace(" test", "").replace(" testing", "").strip()
        name = lookup.get(key) or ALIASES.get(key) or ALIASES.get(key + " testing")
        if name and name in selected and name not in found:
            found.append(name)
    return found


def classify(row: dict, selected: List[str]) -> Optional[str]:
    """Best selected type for a row whose declared type was missing or not selected."""
    text = " ".join(str(row.get(k, "")) for k in ("scenario", "test_case", "steps", "expected_result")).lower()
    title = (str(row.get("scenario", "")) + " " + str(row.get("test_case", ""))).lower()
    scores = {}
    for name in selected:
        score = 0
        hints = EVIDENCE_WORDS.get(name)
        if hints:
            score += sum(1 for h in hints if h in text)
        if name == "Negative" and any(c in title for c in NEGATIVE_TITLE_CUES):
            score += 4
        if name == "Positive" and not any(c in title for c in NEGATIVE_TITLE_CUES):
            score += 1
        if name == "Validation" and any(w in text for w in ("mandatory", "format", "numeric", "required", "length")):
            score += 3
        if name == "UI" and any(w in text for w in ("button", "label", "screen", "tab", "displayed", "visible")):
            score += 2
        if name == "Functional":
            score += 1
        scores[name] = score
    best = max(scores, key=lambda k: scores[k], default=None)
    return best if best and scores[best] > 0 else None


def _polarity_fix(name: str, row: dict, selected: List[str]) -> str:
    """Keep Positive/Negative/Boundary honest: the wording of the case must fit its type."""
    text = " ".join(str(row.get(k, "")) for k in ("test_case", "steps", "expected_result")).lower()
    title = (str(row.get("scenario", "")) + " " + str(row.get("test_case", ""))).lower()
    if name == "Negative" and not any(c in text for c in NEGATIVE_CUES) and "Positive" in selected:
        return "Positive"
    if name == "Positive" and any(c in title for c in NEGATIVE_TITLE_CUES):
        for other in ("Negative", "Validation", "Boundary"):
            if other in selected:
                return other
    if name == "Boundary":
        # step numbers ("1.", "2.") are not test values; a comparison written in words ("exactly equal to") counts
        body = re.sub(r"(?:^|(?<=\s))\d+[.)](?=\s)", "", text)
        if not re.search(r"\d", body) and br.case_comparison(row) is None:
            order = ("Negative", "Validation", "Functional", "Positive") if any(c in title for c in NEGATIVE_TITLE_CUES) \
                else ("Functional", "Positive", "Validation", "Negative")
            for other in order:
                if other in selected:
                    return other
    return name


def _numbered_steps(value: str) -> str:
    lines = [re.sub(r"^\s*(?:\d+[\.\)]|[-*•])\s*", "", ln).strip() for ln in str(value or "").replace("<br>", "\n").split("\n")]
    lines = [ln for ln in lines if ln and ln.lower() not in PLACEHOLDERS]
    if len(lines) == 1 and re.search(r"\s\d+[\.\)]\s", " " + lines[0]):  # "1. a 2. b" on one line
        lines = [p.strip() for p in re.split(r"\s*\b\d+[\.\)]\s+", lines[0]) if p.strip()]
    return "\n".join(f"{i}. {ln}" for i, ln in enumerate(lines, start=1))


def grounding_score(row: dict, window: str) -> float:
    terms = _words(" ".join(str(row.get(k, "")) for k in ("scenario", "test_case", "expected_result")))
    if not terms:
        return 0.0
    haystack = " ".join(_words(window))
    hay_set = set(haystack.split())
    hits = sum(1 for t in set(terms) if t in hay_set or t in haystack)
    return hits / len(set(terms))


def _neutralise_invented_quotes(row: dict, window: str) -> int:
    """A quoted message in Expected Result that is not in the document is invented wording."""
    lowered = window.lower()
    count = 0

    def repl(match):
        nonlocal count
        inner = match.group(2).strip()
        if len(inner) >= 6 and inner.lower() not in lowered:
            count += 1
            return "the documented message"
        return match.group(0)

    row["expected_result"] = re.sub(r"([\"'“‘])([^\"'”’\n]{6,160})([\"'”’])", repl, row.get("expected_result", ""))
    return count


# ---------------------------------------------------------------------------
# Prompt
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = (
    "You are a senior QA engineer at Pakistan Single Window (PSW). You write precise, executable manual test "
    "cases from the requirement text you are given. You use only facts that are written in that text. "
    "You never add commentary before or after the test cases."
)

EXAMPLE_BLOCK = """### TEST CASE
Scenario: Quantity field - mandatory, numeric, maximum 5 digits
Source Statement: The Quantity field is mandatory and accepts numbers of at most 5 digits.
Test Case: Verify that the system rejects a Quantity longer than 5 digits
Test Type: Negative
Importance: High
Execution: Playwright
Pre-Conditions: The user is on the Item Details screen with an empty form
Steps:
1. Enter 123456 in the Quantity field
2. Click Save
Expected Result: The record is not saved and the Quantity field shows a validation error"""


def _prompt_targets(item: dict, covered_units: Optional[set], wanted: int) -> List[dict]:
    """The window's coverage units not covered yet, most important first (the model is asked to cover these)."""
    rank = {"High": 0, "Medium": 1, "Low": 2}
    units = [u for u in _window_units(item) if not covered_units or u["id"] not in covered_units]
    units.sort(key=lambda u: (rank.get(u["priority"], 1), (item.get("unit_ids") or []).index(u["id"])
                              if u["id"] in (item.get("unit_ids") or []) else 0))
    return units[:max(1, wanted + 2)]


def _target_text(unit: dict, requirement: dict) -> str:
    if unit["kind"] != "mapping":
        return unit["text"][:200]
    rule = next((r for r in requirement.get("rules") or [] if r["id"] == unit.get("rule")), {})
    codes = ", ".join((unit.get("codes") or [])[:6]) + (" ..." if len(unit.get("codes") or []) > 6 else "")
    return (f"Table rows {unit['text'][len('Codes '):] if unit['text'].startswith('Codes ') else unit['text']} "
            f"(codes {codes}); the Source Statement is: \"{rule.get('statement', '')[:160]}\"")


def _prompt_rules(item: dict) -> List[str]:
    requirement = item.get("requirement") or {}
    flat = quality.key(item["text"])
    lines = []
    for rule in requirement.get("rules") or []:
        parts = [rule.get("statement", "")] + list(rule.get("outcomes") or [])
        anchors = [quality.key(part)[:60] for part in parts if quality.key(part)]
        if rule["kind"] == "mapping" or any(anchor in flat for anchor in anchors):
            lines.append(br.describe_rule(rule))
    return lines[:6]


def build_window_prompt(item: dict, label: str, position: int, total: int, plan: Dict[str, int],
                        covered: List[str], selected: List[str], focus: Optional[str] = None,
                        retry_note: str = "", covered_units: Optional[set] = None) -> str:
    window = item["text"]
    requirement = item.get("requirement") or {}
    wanted = sum(plan.values())
    definitions = "\n".join(f"- {name}: {TYPE_DEFINITIONS[name]}" for name in plan)
    mix = "\n".join(f"- {name}: {count}" for name, count in plan.items())
    basis = []
    for name in plan:
        if name in EVIDENCE_TYPES:
            for sentence in (item.get("evidence") or {}).get(name, [])[:2]:
                basis.append(f"- {name}: \"{sentence[:170]}\"")
    basis_block = ("\nBasis found in the text for the specialised types (build those cases on these statements):\n"
                   + "\n".join(basis) + "\n") if basis else ""
    avoid = ""
    if covered:
        avoid = "\nAlready covered (do not repeat):\n" + "\n".join(f"- {title[:110]}" for title in covered[-12:]) + "\n"
    request = ""
    if focus and len(focus.split()) > 3:
        request = f"\nUSER REQUEST (write cases for the part of the text that this request is about): {focus.strip()[:300]}\n"
    targets = _prompt_targets(item, covered_units, wanted)
    target_block = ("\nCOVERAGE TARGETS (statements of this section that still need a test case, most important first; "
                    "write one case per target before writing a second case for any target):\n"
                    + "\n".join(f"- [{u['priority']}] {_target_text(u, requirement)}" for u in targets) + "\n") if targets else ""
    rule_lines = _prompt_rules(item)
    rule_block = ("\nHOW THE RULES OF THIS SECTION MUST BE READ (computed from the text; every Expected Result must agree):\n"
                  + "\n".join(f"- {line[:640]}" for line in rule_lines) + "\n") if rule_lines else ""
    ref = requirement.get("ref", "")
    heading = f"{label} - {ref} (section {position} of {total})" if ref else f"{label} - section {position} of {total}"
    return f"""SOURCE DOCUMENT: {heading}
{request}
DOCUMENT TEXT (the only source of truth):
\"\"\"
{window}
\"\"\"

TASK
Write {wanted} test cases for the text above, using ONLY these test types: {", ".join(plan)}.

What each type means:
{definitions}

Mix to aim for in this section (write fewer if the text gives no basis; never pad):
{mix}
{basis_block}{target_block}{rule_block}{avoid}
RULES
1. Every test case must trace to ONE statement in the text. Copy that statement word for word into "Source Statement", and name the field, rule or requirement in "Scenario".
2. Use the exact field, button, screen, status and value names written in the text. Never invent screens, fields, APIs, tables, roles, limits, rules or message wording. A limit (length, range, count) may be used only if the text states it.
3. "Test Case" is a complete sentence that starts with "Verify that" and says what is checked and what is expected.
4. Steps are numbered, one action each, in the order a tester performs them (at least two steps). Write every step as an instruction to the tester that starts with a verb (Log in, Open, Select, Enter, Click, Submit, Send, Approve, Check, Compare). For behaviour of the system or another system, the tester triggers it and then checks the result on the screen, in the API response or in the other system; never write a step such as "The system approves ...". Use values from the text. When the text gives no concrete value, describe the condition with the words of the text, or mark an example value with "e.g."; never present an example value as a value of the document.
5. Expected Result states what a tester can observe: what is displayed, saved, sent, received, assigned or changed, and the resulting status, field value, amount or receiving system. Never write "as stated in the requirement", "works correctly", "processed successfully" or "the correct result is displayed". If the text gives no message wording, describe the outcome without inventing a message.
6. A Negative case is allowed only for a refusal or restriction that the text states, and a Boundary case only for a limit, threshold or comparison that the text states. Do not invent validation rules, limits, lengths, error messages or "invalid" formats the text does not define.
7. Pre-Conditions name the user role, the login state and the record or data that must already exist (for example the business record and its status).
8. "Test Type" is exactly ONE of: {", ".join(plan)}. A test case checks ONE condition.
9. Importance: High = core flow, money, integration, mandatory rule, security or compliance; Medium = normal rule; Low = cosmetic or navigation.
10. Execution: Playwright only if the steps use the screen and the result is visible on the screen; API only for a direct API request and response; SQL only for a database check; Manual when the result must be checked in another system, in e-mail/SMS or by a person.
11. If the text has no testable behaviour (only headings, revision history, contacts, definitions), reply with exactly: NO_TESTABLE_CONTENT
{retry_note}
FORMAT - repeat this block for every test case and write nothing else. Example of the FORMAT only (do not copy its content):
{EXAMPLE_BLOCK}
"""


# ---------------------------------------------------------------------------
# Execution type: decided from what the steps actually do
# ---------------------------------------------------------------------------

_UI_STEP = re.compile(r"\b(click|select|enter|type|choose|tick|navigate|open|log ?in|log into|sign in|search|filter|"
                      r"upload|submit|press|screen|page|button|menu|submenu|dashboard|tab|dropdown|interface|"
                      r"field|column|displayed?|visible)\b", re.I)
_API_STEP = re.compile(r"\b(api|endpoint|http|https|json|payload|status code|method id|request body|response body|"
                       r"send a request|post request|get request)\b", re.I)
_SQL_STEP = re.compile(r"\b(sql|query|database|stored procedure|table|row)\b", re.I)
_MANUAL_STEP = re.compile(r"\b(e-?mail|sms|notification|physical|print(?:ed|out)?|offline|phone|committee|"
                          r"visual(?:ly)? inspect|bank branch|weboc (?:user|officer|screen|portal))\b", re.I)


def classify_execution(row: dict, hint: str = "", user_choice: str = ""):
    """Pick Manual / Playwright / API / SQL from the objective, steps and verification. Returns (value, note-or-None).

    Kept here for backward compatibility; the decision is made by ``Core.test_case_quality.classify_execution``.
    A person's explicit choice (``user_choice``) is never changed.
    """
    return quality.classify_execution(row, hint, user_choice)


_VAGUE_EXPECTED = re.compile(r"^(?:the )?(?:system |application |it )?(?:should |shall |will )?(?:work|behave|function|respond)s?"
                             r"(?: correctly| properly| as expected)?\.?$|^as expected\.?$|^success(?:ful(?:ly)?)?\.?$", re.I)


# ---------------------------------------------------------------------------
# Normalising and checking one case
# ---------------------------------------------------------------------------


def _key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(value or "").lower()).strip()


def _supports(sentence: str, statements: List[str]) -> bool:
    """True when ``sentence`` is (a part of) one of the evidence ``statements`` for a type."""
    mine = _key(sentence)
    if not mine:
        return False
    mine_words = set(mine.split())
    for other in statements:
        theirs = _key(other)
        if mine in theirs or theirs in mine:
            return True
        words = set(theirs.split())
        if words and len(mine_words & words) / min(len(mine_words), len(words)) >= 0.85:
            return True
    return False


def normalize_row(raw: dict, selected: List[str], item: dict, report: "Report") -> Optional[dict]:
    window = item["text"]
    requirement = item.get("requirement") or {}
    ref = requirement.get("ref", "")
    row = {k: _clean(v) for k, v in raw.items()}
    notes: List[str] = []

    def reject(reason: str, title: str = ""):
        report.drop(reason, ref=ref, title=title or row.get("test_case") or row.get("scenario") or "")
        return None

    if _is_placeholder(row.get("test_case")) and not _is_placeholder(row.get("expected_result")):
        # the model often omits the "Test Case:" line; the objective is the expected outcome, so state it as one
        outcome = str(row["expected_result"]).strip().rstrip(".").split("\n")[0]
        first = outcome.split(" ", 1)[0]
        if first[:1].isupper() and first[1:].islower():
            outcome = outcome[:1].lower() + outcome[1:]
        row["test_case"] = "Verify that " + outcome
        notes.append("The model wrote no Test Case title; it was derived from the Expected Result.")
    for field in REQUIRED_TEXT_FIELDS:
        if _is_placeholder(row.get(field)):
            return reject("missing_field")
    row["steps"] = _numbered_steps(row["steps"])
    row["steps"], rewritten = quality.actor_steps_to_tester(row["steps"])
    if rewritten:
        notes.append(f"{rewritten} step(s) written as 'The <role> does ...' were rewritten as tester actions for that role.")
    if not row["steps"] or len(row["steps"]) < 8 or len(row["expected_result"]) < 8:
        return reject("missing_field")
    if len(row["test_case"].split()) < 4:
        return reject("vague_title")
    if not re.match(r"^(verify|validate|ensure|check|confirm)\b", row["test_case"], re.I) or len(row["test_case"].split()) < 6:
        notes.append("Test Case title is not a complete 'Verify that ...' sentence.")

    declared = row.get("test_type") or row.get("test_types")
    types = canonical_types(declared, selected)
    if not types:
        guess = classify(row, selected)
        if not guess:
            return reject("type_not_selected")
        report.reclassify(row, str(declared or "(none)"), guess,
                          "the declared type is not one of the selected types; chosen from the wording")
        types = [guess]
    primary = _polarity_fix(types[0], row, selected)
    if primary != types[0]:
        report.reclassify(row, types[0], primary, _polarity_reason(types[0], primary))
    if primary in EVIDENCE_TYPES and not (item.get("evidence") or {}).get(primary):
        return reject("unsupported_type")
    row["test_types"] = [primary] + [t for t in types[1:] if t != primary][:1]
    row["test_type"] = ", ".join(row["test_types"])
    if primary != types[0] or not canonical_types(declared, selected):
        notes.append(f"Test type {primary} was assigned by the quality check (declared: {declared or 'none'}).")

    importance = IMPORTANCE_ALIASES.get(re.sub(r"[^a-z0-9]", "", row.get("importance", "").lower()))
    row["importance"] = importance or "Medium"
    hint = EXECUTION_ALIASES.get(row.get("execution", "").strip().lower(), "")
    row["execution"], execution_note = classify_execution(row, hint)
    if execution_note:
        notes.append(execution_note)
    if _is_placeholder(row.get("pre_conditions")):
        row["pre_conditions"] = DEFAULT_PRECONDITION
    row["scenario"] = row["scenario"][:300]
    row["test_case"] = row["test_case"][:300]

    # a case built on a mapping-table target may quote the target description instead of the document sentence:
    # link it to the documented mapping statement (the codes themselves are checked by the business-rule gate)
    quoted = row.get("source_statement", "")
    for unit in requirement.get("coverage_units") or []:
        if unit["kind"] == "mapping" and quoted and quality.jaccard(quality.terms(quoted), quality.terms(unit["text"])) >= 0.7:
            rule = next((r for r in requirement.get("rules") or [] if r["id"] == unit.get("rule")), None)
            if rule:
                row["source_statement"] = rule["statement"]
                notes.append("The case quoted the mapping-table target; it is linked to the documented mapping statement.")
            break
    status, sentence, _score = find_statement(row.get("source_statement", ""), window)
    if status is None:  # the model quoted nothing usable: try to locate the sentence from the case itself
        status, sentence, _score = find_statement(f"{row['scenario']} {row['test_case']}", window, minimum=0.6)
        if status == "verified":
            status = "approximate"
    if status is None:
        return reject("unsupported")
    if status == "approximate":
        notes.append("Source evidence is an approximate match; review it against the document.")
    primary = row["test_types"][0]
    if primary in EVIDENCE_TYPES and not _supports(sentence, (item.get("evidence") or {}).get(primary, [])):
        # the requirement has a basis for this type, but the statement this case quotes is not that basis
        baseline = [t for t in selected if t in BASELINE_TYPES]
        guess = classify(row, baseline) if baseline else None
        if not guess:
            return reject("unsupported_type")
        report.reclassify(row, primary, guess, f"the quoted statement does not state a rule for '{primary}'")
        row["test_types"] = [guess] + [t for t in row["test_types"][1:] if t != guess][:1]
        row["test_type"] = ", ".join(row["test_types"])
        notes.append(f"Test type {guess} was assigned by the quality check: the quoted statement is not a '{primary}' rule.")

    score = grounding_score(row, window)
    if score < 0.30:
        return reject("not_grounded")
    row["_grounding"] = round(score, 2)

    # ---- quality gates (stages 2-5 and 7; duplicates are stage 6, decided across cases) --------------------
    gates = {"source": {"status": status, "sentence": sentence}}
    ok, relevance_score = quality.relevance(row, sentence)
    gates["relevance"] = {"ok": ok, "score": relevance_score}
    findings = br.check_case(row, requirement.get("rules") or [])
    verdict = br.verdict_of(findings)
    gates["logic"] = {"verdict": verdict, "findings": findings}
    if verdict == "contradiction":
        report.contradiction(row, ref, [f["message"] for f in findings if f["verdict"] == "contradiction"])
        return reject("contradiction")
    expected = quality.expected_quality(row, sentence)
    gates["expected"] = expected
    if any(issue.startswith(("Expected Result is vague", "Expected Result refers to the requirement")) for issue in expected["issues"]):
        report.weak_expected.append({"requirement": ref, "title": row.get("test_case", "")[:140], "issues": expected["issues"]})
        return reject("weak_expected")
    gates["completeness"] = quality.completeness(row, requirement)
    gates["basis"] = quality.basis(row, requirement, sentence, verdict)
    gates["type"] = {"types": list(row["test_types"]), "note": next((n for n in notes if n.startswith("Test type")), "")}
    gates["execution"] = {"value": row["execution"], "note": execution_note or ""}
    report.invented_quotes += _neutralise_invented_quotes(row, window)
    row["_gates"] = gates

    row["requirement_ref"] = ref
    row["requirement_ref_kind"] = requirement.get("ref_kind", "internal")
    row["requirement_title"] = requirement.get("title", "")
    row["source_evidence"] = sentence
    row["evidence_status"] = status
    row["quality_notes"] = notes
    row["source_statement"] = sentence
    return row


def _polarity_reason(old: str, new: str) -> str:
    if old == "Negative":
        return "labelled Negative, but the wording describes a successful outcome"
    if old == "Positive":
        return "labelled Positive, but the title describes invalid, missing or rejected input"
    if old == "Boundary":
        return "labelled Boundary, but no value or limit appears in the case"
    return "the wording fits the new type better"


def reference_label(row: dict) -> str:
    ref = row.get("requirement_ref") or ""
    return f"{ref} (internal reference)" if row.get("requirement_ref_kind") == "internal" else ref


def finish_row(row: dict, class_codes: Optional[Dict[str, List[str]]] = None) -> dict:
    """Put the requirement reference into the Requirement/Scenario cell and evidence + quality results into Comments."""
    label = reference_label(row)
    if label and not row["scenario"].startswith(label):
        row["scenario"] = f"{label}: {row['scenario']}"[:360]
    gates = row.get("_gates") or {}
    values = list(row.get("parameter_values") or [])
    if row.get("merged_variants") and len(values) > 1:
        count = len([ln for ln in row["steps"].splitlines() if ln.strip()])
        row["steps"] += f"\n{count + 1}. Repeat steps 1-{count} for each value in the test data: {', '.join(values)}"
    lines = []
    if label:
        title = row.get("requirement_title")
        lines.append(f"Source: {label}" + (f" - {title}" if title else ""))
    if row.get("source_evidence"):
        lines.append(f"Evidence ({row.get('evidence_status', 'verified')}): \"{row['source_evidence'][:400]}\"")
    status = row.get("quality_status")
    if status:
        reasons = row.get("quality_reasons") or []
        lines.append(f"Quality status: {status}" + (" - " + "; ".join(reasons)[:500] if reasons else ""))
        logic = (gates.get("logic") or {}).get("verdict", "not_applicable")
        basis = (gates.get("basis") or {}).get("category", "documented")
        lines.append("Checks: source " + str((gates.get("source") or {}).get("status", "verified"))
                     + " | business rule " + {"consistent": "verified", "ambiguous": "ambiguous"}.get(logic, "no checkable rule")
                     + " | basis " + basis
                     + " | expected result " + ("observable" if (gates.get("expected") or {}).get("ok", True) else "needs review")
                     + " | completeness " + ("complete" if (gates.get("completeness") or {}).get("ok", True) else "incomplete"))
    for text in (gates.get("basis") or {}).get("labels", []):
        lines.append("Test data: " + text)
    if values and row.get("merged_variants"):
        lines.append("Parameterized test data: " + ", ".join(values) + " (same rule and outcome; run once per value)")
        others = sorted(set((class_codes or {}).get(values[0], [])) - set(values))
        if others:
            lines.append("Other documented values of the same class (data-driven suite): " + ", ".join(others))
    for dependency in (gates.get("completeness") or {}).get("dependencies", []):
        lines.append("Dependency: " + dependency)
    if row.get("covers"):
        lines.append("Covers: " + ", ".join(row["covers"]))
    if row.get("quality_notes"):
        lines.append("Quality: " + " | ".join(row["quality_notes"]))
    if lines:
        row["comments"] = "\n".join(lines)
    row["review_state"] = "Reviewed/Saved" if status in (None, "Accepted") else "Needs Review"
    return row


def _signature(row: dict) -> set:
    return set(_words(row.get("scenario", "") + " " + row.get("test_case", "")))


def _similar(a: set, b: set) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _mapping_context(rows_or_rules) -> tuple:
    """(all mapping codes, code -> class id) from the requirements' mapping rules."""
    codes, klass = set(), {}
    for requirement in rows_or_rules:
        for unit in requirement.get("coverage_units") or []:
            if unit["kind"] == "mapping":
                for code in unit.get("codes") or []:
                    codes.add(code)
                    klass.setdefault(code, unit["id"])
    return codes, klass


def dedupe(rows: List[dict], report: "Report", existing: Optional[List[dict]] = None,
           requirements: Optional[List[dict]] = None) -> List[dict]:
    """Return the rows that are not duplicates of ``existing`` or of an earlier row in ``rows``.

    Decisions (each one is recorded in the report):
    * exact duplicate      - same actions and expected result: removed;
    * semantic duplicate   - same rule, same data, same outcome in other words: removed;
    * parameterized        - same rule and outcome with a different DOCUMENTED value of the same class
                             (e.g. another code of the same table group): merged into the first case as test data,
                             so the value stays covered without a near-identical case;
    * distinct             - kept (different condition, rule, outcome or a value of another class).
    """
    codes, klass = _mapping_context(requirements or [])
    units_of = {r.get("ref"): r.get("coverage_units") or [] for r in (requirements or [])}
    kept: List[dict] = []
    pool = list(existing or [])

    def plain_units(row):  # the non-mapping units a case covers (a report name, a statement ...)
        if "_plain_units" not in row:
            covered = quality.units_covered(row, units_of.get(row.get("requirement_ref"), []))
            row["_plain_units"] = {u for u in covered if ".M" not in u}
        return row["_plain_units"]

    for row in rows:
        decision, other = "distinct", None
        mine = quality.parameter_values(row, codes)
        for candidate in pool + kept:
            kind = quality.classify_pair(row, candidate, codes)
            theirs = sorted(set(candidate.get("parameter_values") or quality.parameter_values(candidate, codes)))
            a_units, b_units = plain_units(row), plain_units(candidate)
            same_value = bool(mine) and set(mine) <= set(theirs)
            if kind in ("semantic_duplicate", "parameterized_variation") and a_units and b_units \
                    and not (a_units <= b_units or b_units <= a_units) and not (kind == "semantic_duplicate" and same_value):
                kind = "distinct"  # they verify different documented items (another report, another statement)
            if kind == "parameterized_variation" and mine and set(mine) <= set(theirs):
                kind = "semantic_duplicate"  # this value is already part of the other case's test data
            if kind == "parameterized_variation" and {klass.get(c) for c in mine} != {klass.get(c) for c in theirs}:
                kind = "distinct"  # a value of another documented class (another table group) is its own scenario
            if kind == "distinct" and mine == theirs and quality.type_class(candidate["test_types"][0]) == quality.type_class(row["test_types"][0]) \
                    and not quality.opposite_outcomes(row, candidate) and not quality.different_subjects(row, candidate):
                if _similar(_signature(row), _signature(candidate)) >= 0.78:
                    kind = "semantic_duplicate"
            if kind != "distinct":
                decision, other = kind, candidate
                break
        if decision == "distinct":
            row["parameter_values"] = mine
            kept.append(row)
            continue
        if decision == "parameterized_variation":
            values = other.setdefault("parameter_values", quality.parameter_values(other, codes))
            for value in mine:
                if value not in values:
                    values.append(value)
            other.setdefault("merged_variants", []).append(row.get("test_case", ""))
        row["parameter_values"] = mine
        report.duplicate(row, other, decision)
    return kept


STATUS_RANK = {"Accepted": 0, "Needs Review": 1}


def select_final(rows: List[dict], selected: List[str], requested, unit_weights: Optional[Dict[str, int]] = None) -> List[dict]:
    """Trim to the requested number, in document order.

    Priorities: every selected type that has a case, every requirement that has a case, then the cases that add the
    most uncovered, most important coverage units. Inside each step automatically accepted cases come before cases
    that need review.
    """
    if str(requested).lower() == "all" or len(rows) <= int(requested):
        return rows
    limit = int(requested)
    weights = unit_weights or {}
    rank = {"High": 0, "Medium": 1, "Low": 2}
    covered: set = set()
    per_requirement: Dict[str, int] = {}
    per_type: Dict[str, int] = {}
    remaining = list(range(len(rows)))
    chosen: List[int] = []

    def gain(p):
        return sum(weights.get(u, 1) for u in (rows[p].get("covers") or []) if u not in covered)

    def order_key(p):
        row = rows[p]
        return (gain(p) == 0, STATUS_RANK.get(row.get("quality_status", "Accepted"), 2), -gain(p),
                per_requirement.get(row.get("requirement_ref", ""), 0), rank.get(row.get("importance"), 1), p)

    def take(p):
        remaining.remove(p)
        chosen.append(p)
        row = rows[p]
        ref = row.get("requirement_ref", "")
        per_requirement[ref] = per_requirement.get(ref, 0) + 1
        per_type[row["test_types"][0]] = per_type.get(row["test_types"][0], 0) + 1
        covered.update(row.get("covers") or [])

    # 1. every selected type that has a case, scarcest type first, so a rare type (a documented limit) is not squeezed out
    by_type: Dict[str, List[int]] = {}
    for p in remaining:
        by_type.setdefault(rows[p]["test_types"][0], []).append(p)
    for name in sorted(by_type, key=lambda n: (len(by_type[n]), selected.index(n) if n in selected else 99)):
        if len(chosen) < limit:
            take(min(by_type[name], key=order_key))
    # 2. every requirement that has a case but is not represented yet
    for ref in dict.fromkeys(r.get("requirement_ref", "") for r in rows):
        if len(chosen) >= limit or per_requirement.get(ref):
            continue
        options = [p for p in remaining if rows[p].get("requirement_ref", "") == ref]
        if options:
            take(min(options, key=order_key))
    # 3. fill up: largest coverage gain first, balancing types and requirements on ties
    while len(chosen) < limit and remaining:
        take(min(remaining, key=lambda p: (gain(p) == 0, STATUS_RANK.get(rows[p].get("quality_status", "Accepted"), 2), -gain(p),
                                           per_type.get(rows[p]["test_types"][0], 0),
                                           per_requirement.get(rows[p].get("requirement_ref", ""), 0),
                                           rank.get(rows[p].get("importance"), 1), p)))
    return [rows[p] for p in sorted(chosen)]


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

DROP_TEXT = {
    "missing_field": "incomplete (a required field was empty)",
    "vague_title": "title too short to be a meaningful test case",
    "type_not_selected": "not one of the selected test types",
    "unsupported_type": "a specialised type (limit, access rule, API ...) that the requirement text gives no basis for",
    "unsupported": "not supported: the quoted source statement was not found in the document",
    "not_grounded": "not grounded in the document text",
    "duplicate": "duplicate of another case",
    "contradiction": "contradicts a documented business rule",
    "weak_expected": "expected result is vague or only refers to the requirement",
}


class Report:
    def __init__(self, selected: List[str], requested):
        self.selected = selected
        self.requested = requested
        self.sections = 0
        self.sections_used = 0
        self.no_content_sections = 0
        self.model_calls = 0
        self.model_failures = 0
        self.model_seconds = 0.0
        self.retries = 0
        self.raw_cases = 0
        self.invented_quotes = 0
        self.drops: Dict[str, int] = {}
        self.rejected: List[dict] = []
        self.reclassified: List[dict] = []
        self.by_type: Dict[str, int] = {}
        self.not_generated: Dict[str, str] = {}
        self.requirements: List[dict] = []
        self.extraction: dict = {}
        self.attempted: Dict[str, dict] = {}
        self.type_support: Dict[str, List[str]] = {}
        self.coverage: List[dict] = []
        self.missing_coverage: List[dict] = []
        self.grounding = {"verified": 0, "approximate": 0}
        self.contradictions: List[dict] = []
        self.weak_expected: List[dict] = []
        self.duplicate_decisions: List[dict] = []
        self.unit_weights: Dict[str, int] = {}
        self.case_quality: List[dict] = []
        self.status_counts: Dict[str, int] = {}
        self.unit_coverage: dict = {}
        self.assumptions: List[dict] = []
        self.rules: List[dict] = []
        self.ambiguities: List[str] = []
        self.recommendations: List[str] = []
        self.gate_summary: Dict[str, Dict[str, int]] = {}
        self.repairs: List[dict] = []

    @property
    def retagged(self) -> int:
        return len(self.reclassified)

    def drop(self, reason: str, ref: str = "", title: str = ""):
        self.drops[reason] = self.drops.get(reason, 0) + 1
        if len(self.rejected) < 60:
            self.rejected.append({"requirement": ref, "title": str(title)[:140], "reason": DROP_TEXT.get(reason, reason)})
        if ref:
            self.attempted.setdefault(ref, {"proposed": 0, "accepted": 0})
            self.attempted[ref].setdefault("rejected", {})
            self.attempted[ref]["rejected"][reason] = self.attempted[ref]["rejected"].get(reason, 0) + 1

    def contradiction(self, row: dict, ref: str, messages: List[str]):
        self.contradictions.append({"requirement": ref, "title": str(row.get("test_case", ""))[:160],
                                    "expected_result": str(row.get("expected_result", ""))[:200], "reasons": messages[:3]})

    def duplicate(self, row: dict, other: dict, decision: str):
        label = {"exact_duplicate": "Exact duplicate - removed", "semantic_duplicate": "Semantic duplicate - removed",
                 "parameterized_variation": "Parameterized variation - merged into the first case as test data"}[decision]
        if decision != "parameterized_variation":
            self.drops["duplicate"] = self.drops.get("duplicate", 0) + 1
            ref = row.get("requirement_ref", "")
            if ref:
                self.attempted.setdefault(ref, {"proposed": 0, "accepted": 0}).setdefault("rejected", {})
                self.attempted[ref]["rejected"]["duplicate"] = self.attempted[ref]["rejected"].get("duplicate", 0) + 1
        self.duplicate_decisions.append({"classification": decision, "decision": label,
                                         "case": str(row.get("test_case", ""))[:160], "kept_case": str(other.get("test_case", ""))[:160],
                                         "values": list(row.get("parameter_values") or []), "requirement": row.get("requirement_ref", "")})

    def reclassify(self, row: dict, old: str, new: str, reason: str):
        self.reclassified.append({"test_case": str(row.get("test_case", ""))[:140], "from": old, "to": new, "reason": reason})

    def note_proposed(self, ref: str, count: int):
        self.attempted.setdefault(ref, {"proposed": 0, "accepted": 0})
        self.attempted[ref]["proposed"] += count

    def finish(self, rows: List[dict]):
        self.by_type = {name: 0 for name in self.selected}
        for row in rows:
            for name in row["test_types"][:1]:
                self.by_type[name] = self.by_type.get(name, 0) + 1
            self.grounding[row.get("evidence_status", "verified") if row.get("evidence_status") in self.grounding else "verified"] += 1
        accepted_by_ref: Dict[str, List[dict]] = {}
        for row in rows:
            accepted_by_ref.setdefault(row.get("requirement_ref", ""), []).append(row)
        self.coverage, self.missing_coverage = [], []
        for requirement in self.requirements:
            ref = requirement["ref"]
            mine = accepted_by_ref.get(ref, [])
            verified = sum(1 for r in mine if r.get("evidence_status") == "verified")
            tried = self.attempted.get(ref)
            entry = {"ref": ref, "ref_kind": requirement.get("ref_kind", "internal"), "title": requirement.get("title", ""),
                     "cases": len(mine), "verified_evidence": verified,
                     "types": sorted({r["test_types"][0] for r in mine}),
                     "supported_types": [t for t in self.selected if t in BASELINE_TYPES and requirement.get("testable")
                                         or t in (requirement.get("evidence") or {})]}
            if not requirement.get("testable"):
                entry["status"] = "not_testable"
                entry["reason"] = requirement.get("reason", "")
            elif not mine:
                entry["status"] = "not_covered"
                if not tried:
                    entry["reason"] = "No cases were requested for this requirement (the requested number was smaller than the number of requirements)."
                elif tried.get("accepted"):
                    entry["reason"] = (f"{tried['accepted']} valid case(s) were written, but the requested number of cases was reached "
                                       "with other requirements. Ask for a larger number to include them.")
                elif tried.get("proposed"):
                    entry["reason"] = "The model proposed cases, but none passed the quality checks: " + ", ".join(
                        f"{n} {DROP_TEXT.get(k, k)}" for k, n in (tried.get("rejected") or {}).items()) + "."
                else:
                    entry["reason"] = "The model returned no usable test case for this requirement."
                self.missing_coverage.append({"ref": requirement["ref"], "title": requirement.get("title", ""), "reason": entry["reason"]})
            else:
                entry["status"] = "covered" if verified else "evidence_not_verified"
                entry["reason"] = "" if verified else "Cases exist but their source statement is only an approximate match."
            self.coverage.append(entry)
        for name, count in self.by_type.items():
            if count:
                continue
            basis = self.type_support.get(name, [])
            if name in EVIDENCE_TYPES and not basis:
                self.not_generated[name] = (
                    f"Not generated: no requirement in the selected document states a rule that can be tested as '{name}'"
                    + (" (no numeric limit, range or documented threshold)" if name == "Boundary" else "") + "."
                )
            elif basis:
                self.not_generated[name] = (
                    f"None accepted: the document gives a basis for '{name}' in {', '.join(basis[:6])}, "
                    "but no proposed case passed the quality checks or fitted the requested number."
                )
            else:
                self.not_generated[name] = f"No '{name}' test cases were produced from the document text."

    def finish_quality(self, rows: List[dict], requirements: List[dict]):
        """Statuses, gate results, unit coverage, assumptions and recommendations of the FINAL set."""
        self.status_counts = {}
        self.case_quality, self.assumptions = [], []
        gate_names = ("source", "relevance", "logic", "type", "completeness", "expected", "execution", "duplicate")
        self.gate_summary = {name: {} for name in gate_names}
        for row in rows:
            status = row.get("quality_status", "Accepted")
            self.status_counts[status] = self.status_counts.get(status, 0) + 1
            gates = row.get("_gates") or {}
            values = {
                "source": (gates.get("source") or {}).get("status", "verified"),
                "relevance": "pass" if (gates.get("relevance") or {}).get("ok", True) else "weak",
                "logic": {"consistent": "verified", "not_applicable": "no checkable rule", "ambiguous": "ambiguous"}.get(
                    (gates.get("logic") or {}).get("verdict", "not_applicable"), "no checkable rule"),
                "type": "re-labelled" if (gates.get("type") or {}).get("note") else "pass",
                "completeness": "pass" if (gates.get("completeness") or {}).get("ok", True) else (
                    "insufficient" if (gates.get("completeness") or {}).get("blocking") else "warnings"),
                "expected": "pass" if (gates.get("expected") or {}).get("ok", True) else (
                    "weak" if (gates.get("expected") or {}).get("blocking") else "warnings"),
                "execution": "adjusted" if (gates.get("execution") or {}).get("note") else "pass",
                "duplicate": "parameterized" if row.get("merged_variants") else "unique",
            }
            for name, value in values.items():
                self.gate_summary[name][value] = self.gate_summary[name].get(value, 0) + 1
            basis = (gates.get("basis") or {})
            entry = {"test_case": str(row.get("test_case", ""))[:160], "requirement": row.get("requirement_ref", ""),
                     "status": status, "reasons": list(row.get("quality_reasons") or []), "gates": values,
                     "basis": basis.get("category", "documented"), "basis_reasons": basis.get("reasons", []),
                     "logic_findings": [f["message"] for f in (gates.get("logic") or {}).get("findings", [])][:3],
                     "covers": list(row.get("covers") or []), "execution": row.get("execution", ""),
                     "execution_note": (gates.get("execution") or {}).get("note", ""),
                     "dependencies": (gates.get("completeness") or {}).get("dependencies", []),
                     "warnings": list(row.get("quality_notes") or [])}
            self.case_quality.append(entry)
            if basis.get("category") == "assumption":
                self.assumptions.append({"test_case": entry["test_case"], "requirement": entry["requirement"],
                                         "needs_confirmation": basis.get("reasons", [])})
        # unit coverage against the real denominator
        accepted_units, review_units = {}, {}
        for row in rows:
            target = accepted_units if row.get("quality_status", "Accepted") == "Accepted" else review_units
            for unit_id in row.get("covers") or []:
                target.setdefault(unit_id, []).append(str(row.get("test_case", ""))[:90])
        units = [u for r in requirements if r.get("testable", True) for u in (r.get("coverage_units") or [])]
        listing = []
        for unit in units:
            if unit["id"] in accepted_units:
                state = "covered"
            elif unit["id"] in review_units:
                state = "partially_covered"
            else:
                state = "uncovered"
            listing.append({"id": unit["id"], "requirement": unit["requirement"], "text": unit["text"][:220],
                            "kind": unit["kind"], "priority": unit["priority"], "status": state,
                            "cases": accepted_units.get(unit["id"], []) + review_units.get(unit["id"], []),
                            "basis": unit.get("basis", "")})
        total = len(listing)
        covered = sum(1 for u in listing if u["status"] == "covered")
        partial = sum(1 for u in listing if u["status"] == "partially_covered")
        high = [u for u in listing if u["priority"] == "High"]
        missing_high = [u for u in high if u["status"] != "covered"]  # no automatically accepted case
        self.unit_coverage = {
            "total_units": total, "covered": covered, "partially_covered": partial, "uncovered": total - covered - partial,
            "coverage_percent": round(100.0 * covered / total, 1) if total else 0.0,
            "coverage_percent_including_review": round(100.0 * (covered + partial) / total, 1) if total else 0.0,
            "high_priority_total": len(high), "high_priority_covered": sum(1 for u in high if u["status"] == "covered"),
            "denominator": (f"{total} atomic requirement units extracted from the document (behaviour statements, listed items "
                            "and mapping classes of the testable requirements). 'Covered' counts units with at least one "
                            "automatically accepted case; units covered only by cases that need review are 'partially covered'."),
            "units": listing, "high_priority_missing": [{"id": u["id"], "text": u["text"], "kind": u["kind"], "status": u["status"]}
                                     for u in missing_high],
        }
        self.recommendations = []
        for unit in [u for u in listing if u["status"] != "covered"]:
            if unit["priority"] == "Low" and len(self.recommendations) >= 12:
                continue
            verb = "Review the case that covers" if unit["status"] == "partially_covered" else "Add a case for"
            self.recommendations.append(f"{verb} {unit['id']} [{unit['priority']}, {unit['kind']}]: {unit['text'][:150]}")
        self.rules, self.ambiguities = [], []
        all_rules = []
        for requirement in requirements:
            for rule in requirement.get("rules") or []:
                all_rules.append(rule)
                self.rules.append({"id": rule["id"], "kind": rule["kind"], "requirement": rule.get("requirement", ""),
                                   "reading": br.describe_rule(rule)})
        self.ambiguities = br.mapping_ambiguities(all_rules)

    def lines(self, final_count: int) -> List[str]:
        out = [
            f"Requirements found: {len(self.requirements)} "
            f"({sum(1 for r in self.requirements if r.get('testable'))} testable); "
            f"read {self.sections} section(s), asked the model about {self.sections_used}.",
            f"Requested {self.requested}; the model proposed {self.raw_cases}; {final_count} delivered; "
            f"{max(0, self.raw_cases - final_count)} not used; {self.model_calls} model call(s), "
            f"{self.model_seconds / 60:.1f} min model time.",
        ]
        removed = ", ".join(f"{count} {DROP_TEXT.get(reason, reason)}" for reason, count in self.drops.items() if count)
        if removed:
            out.append("Rejected: " + removed + ".")
        if self.reclassified:
            out.append(f"{len(self.reclassified)} case(s) were re-labelled to another SELECTED type:")
            for item in self.reclassified[:8]:
                out.append(f"  - {item['from']} -> {item['to']}: {item['reason']} ({item['test_case'][:70]})")
        if self.invented_quotes:
            out.append(f"{self.invented_quotes} invented message wording(s) replaced by 'the documented message'.")
        if self.no_content_sections:
            out.append(f"{self.no_content_sections} section(s) had no testable behaviour (headings, history, contacts).")
        if self.model_failures:
            out.append(f"{self.model_failures} model call(s) failed or returned nothing usable (retried {self.retries} time(s)).")
        out.append("Selected types: " + ", ".join(self.selected) + ". Generated: "
                   + ", ".join(f"{name} {count}" for name, count in self.by_type.items()) + ".")
        out.append(f"Source evidence: {self.grounding['verified']} verified against the document, "
                   f"{self.grounding['approximate']} approximate.")
        for entry in self.coverage:
            label = entry["ref"] + (" (internal reference)" if entry["ref_kind"] == "internal" else "")
            out.append(f"{label}: {entry['cases']} case(s), status {entry['status'].replace('_', ' ')}"
                       + (f" - {entry['reason']}" if entry.get("reason") else "."))
        for name, reason in self.not_generated.items():
            out.append(f"{name}: {reason}")
        if self.status_counts:
            out.append("Quality status of the delivered cases: " + ", ".join(f"{k} {v}" for k, v in self.status_counts.items()) + ".")
        if self.contradictions:
            out.append(f"{len(self.contradictions)} proposed case(s) contradicted a documented business rule and were rejected.")
        if self.duplicate_decisions:
            kinds: Dict[str, int] = {}
            for item in self.duplicate_decisions:
                kinds[item["classification"]] = kinds.get(item["classification"], 0) + 1
            out.append("Duplicate decisions: " + ", ".join(f"{v} {k.replace('_', ' ')}" for k, v in kinds.items()) + ".")
        if self.unit_coverage.get("total_units"):
            cov = self.unit_coverage
            out.append(f"Requirement-unit coverage: {cov['covered']} of {cov['total_units']} units covered by accepted cases "
                       f"({cov['coverage_percent']}%), {cov['partially_covered']} only by cases that need review, "
                       f"{cov['uncovered']} not covered; high priority {cov['high_priority_covered']} of {cov['high_priority_total']}.")
            if str(self.requested).lower() != "all" and cov["uncovered"] + cov["partially_covered"]:
                out.append(f"{final_count} case(s) were delivered as requested; this is NOT complete requirement coverage. "
                           "See the uncovered units and recommendations.")
        if self.assumptions:
            out.append(f"{len(self.assumptions)} case(s) rest on assumptions that need business confirmation (marked Needs Review).")
        for note in self.ambiguities:
            out.append("Ambiguity: " + note)
        if str(self.requested).lower() != "all" and final_count < int(self.requested):
            out.append(f"{final_count} of the {self.requested} requested cases: the document supports only this many "
                       "distinct, grounded cases for the selected types.")
        return out

    def as_dict(self, final_count: int) -> dict:
        proposed = self.raw_cases
        return {
            "requested": self.requested, "proposed": proposed, "kept": final_count, "accepted": final_count,
            "rejected": max(0, proposed - final_count),
            "duplicates": self.drops.get("duplicate", 0),
            "unsupported": self.drops.get("unsupported", 0) + self.drops.get("unsupported_type", 0) + self.drops.get("not_grounded", 0),
            "sections": self.sections, "sections_used": self.sections_used, "model_calls": self.model_calls,
            "model_failures": self.model_failures, "retries": self.retries,
            "model_seconds": round(self.model_seconds, 1),
            "repairs": list(self.repairs),
            "repaired": sum(1 for r in self.repairs if str(r.get("outcome", "")).startswith("repaired")),
            "removed": dict(self.drops), "rejected_cases": list(self.rejected),
            "reclassified": list(self.reclassified), "retagged": len(self.reclassified),
            "invented_quotes_replaced": self.invented_quotes,
            "selected_types": list(self.selected), "by_type": dict(self.by_type), "actual_types": {k: v for k, v in self.by_type.items() if v},
            "not_generated": dict(self.not_generated),
            "requirements_found": len(self.requirements), "extraction": dict(self.extraction),
            "requirement_coverage": list(self.coverage), "missing_coverage": list(self.missing_coverage),
            "source_grounding": dict(self.grounding),
            "needs_review": self.status_counts.get("Needs Review", 0),
            "auto_accepted": self.status_counts.get("Accepted", 0),
            "status_counts": dict(self.status_counts),
            "contradictions": len(self.contradictions), "contradiction_cases": list(self.contradictions),
            "weak_expected_rejected": list(self.weak_expected),
            "duplicate_decisions": list(self.duplicate_decisions),
            "parameterized_merged": sum(1 for d in self.duplicate_decisions if d["classification"] == "parameterized_variation"),
            "assumptions": list(self.assumptions),
            "unit_coverage": dict(self.unit_coverage),
            "case_quality": list(self.case_quality),
            "gate_summary": dict(self.gate_summary),
            "business_rules": list(self.rules), "ambiguities": list(self.ambiguities),
            "recommendations": list(self.recommendations),
            "summary": self.lines(final_count),
        }


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------

LLMCall = Callable[[str, str], Optional[str]]  # (prompt, system_prompt) -> text or None

_RETRY_NOTE = ("12. Your previous answer could not be read or had no usable test case. Answer ONLY with the "
               "FORMAT blocks below, one block per test case, including the 'Source Statement' line.")

# ---------------------------------------------------------------------------
# Repair pass: one more model call that rewrites cases blocked only by fixable writing problems
# ---------------------------------------------------------------------------

# reason text (from Core.test_case_quality) -> instruction for the rewrite
_REPAIR_FIXES = [
    (re.compile(r"not what the tester does|descriptive", re.I),
     "Rewrite every step as an action of the tester that starts with a verb (Log in, Open, Select, Enter, Click, Submit, "
     "Send, Approve, Check, Compare). The tester first creates or triggers the condition, then checks the result on the "
     "screen, in the API response or in the receiving system. No step may say what the system does."),
    (re.compile(r"Only one test step", re.I),
     "Write at least three numbered steps: prepare or trigger the condition, perform the action, check the result."),
    (re.compile(r"opposite outcomes", re.I),
     "Keep ONE outcome: choose the path named in the steps (for example the approval path) and state only that outcome."),
    (re.compile(r"correct/appropriate/accordingly", re.I),
     "Replace 'correct', 'appropriate' or 'accordingly' with the value, amount, code, status or receiver written in the text."),
    (re.compile(r"names no observable|observable", re.I),
     "The Expected Result must name what the tester sees: a displayed value, saved status, amount, field or the receiving system."),
    (re.compile(r"vague|restates|copies the requirement|shall", re.I),
     "Write the Expected Result as an observable outcome in the present tense, not as a copy of the requirement sentence."),
]
_NOT_REPAIRABLE = re.compile(r"assum|not stated|not defined|not in the document|ambiguous|approximate match|"
                             r"does not clearly support", re.I)


def repair_instructions(reasons: List[str]) -> List[str]:
    """The rewrite instructions for a Needs Review case, or [] when a rewrite cannot fix it (assumption, evidence)."""
    if not reasons or any(_NOT_REPAIRABLE.search(r) for r in reasons):
        return []
    out = []
    for reason in reasons:
        fixes = [fix for pattern, fix in _REPAIR_FIXES if pattern.search(reason)]
        if not fixes:
            return []  # an unknown blocker: leave it to the reviewer
        out.extend(f for f in fixes if f not in out)
    return out


def _case_block(row: dict) -> str:
    return "\n".join([
        f"Scenario: {row.get('scenario', '')}", f"Source Statement: {row.get('source_statement', '')}",
        f"Test Case: {row.get('test_case', '')}", f"Test Type: {(row.get('test_types') or [row.get('test_type', '')])[0]}",
        f"Importance: {row.get('importance', '')}", f"Execution: {row.get('execution', '')}",
        f"Pre-Conditions: {row.get('pre_conditions', '')}", "Steps:", str(row.get("steps", "")),
        f"Expected Result: {row.get('expected_result', '')}"])


def build_repair_prompt(cases: List[tuple], label: str) -> str:
    """cases: (row, item, instructions). The model rewrites each case; the source text stays the only truth."""
    parts = []
    for number, (row, item, fixes) in enumerate(cases, start=1):
        rules = _statement_rules(item, row.get("source_statement", ""))
        context = _statement_context(row.get("source_statement", ""), item["text"])
        parts.append(
            f"### CASE {number}\nText around the Source Statement:\n\"\"\"\n{context}\n\"\"\"\n"
            + ("Rule reading: " + " | ".join(r[:300] for r in rules) + "\n" if rules else "")
            + "Current test case:\n" + _case_block(row) + "\nFix:\n" + "\n".join(f"- {f}" for f in fixes) + "\n")
    return f"""SOURCE DOCUMENT: {label}

TASK
Each test case below was written from the document but cannot be executed or checked as written. Rewrite each one so that a
tester can run it. Keep its Scenario, Source Statement, Test Type and the behaviour it checks. Apply the listed fixes.
Use only names, values, roles and systems written in the text; never invent screens, fields, messages or limits. When the text
gives no concrete value, describe the condition with the words of the text or mark an example with "e.g.".

{chr(10).join(parts)}
FORMAT - answer with one block per case, in the same order, starting each block with "### TEST CASE <number>", and write
nothing else. Example of the FORMAT only (do not copy its content):
{EXAMPLE_BLOCK.replace('### TEST CASE', '### TEST CASE 1')}
"""


def _statement_rules(item: dict, statement: str) -> List[str]:
    """The rule readings whose statement or outcome is the quoted statement (at most two)."""
    mine = set(_key(statement).split())
    out = []
    for rule in (item.get("requirement") or {}).get("rules") or []:
        parts = [rule.get("statement", "")] + list(rule.get("outcomes") or [])
        best = max((len(mine & set(_key(part).split())) / max(1, len(set(_key(part).split()))) for part in parts if part), default=0)
        if best >= 0.6:
            out.append(br.describe_rule(rule))
    return out[:2]


def _statement_context(statement: str, window: str, width: int = 700) -> str:
    """The part of the window around the quoted statement (the whole window when it is short or not found)."""
    if len(window) <= width:
        return window
    flat = _key(window)
    probe = " ".join(_key(statement).split()[:8])
    position = flat.find(probe) if probe else -1
    if position < 0:
        return window[:width]
    ratio = position / max(1, len(flat))
    start = max(0, int(ratio * len(window)) - width // 3)
    return window[start:start + width]


def parse_repair_answer(text: str, count: int) -> Dict[int, dict]:
    """{case number: raw case} from a repair answer; blocks without a number are taken in order."""
    out: Dict[int, dict] = {}
    pieces = re.split(r"(?im)^\s*#{1,4}\s*(?:test\s*case|case)\s*(\d+)\b.*$", str(text or ""))
    if len(pieces) > 1:
        for i in range(1, len(pieces) - 1, 2):
            number = int(pieces[i])
            rows = parse_cases("### TEST CASE\n" + pieces[i + 1])
            if rows and 1 <= number <= count and number not in out:
                out[number] = rows[0]
    if not out:
        for number, row in enumerate(parse_cases(text)[:count], start=1):
            out[number] = row
    return out


_STATUS_RANK = {"Accepted": 0, "Needs Review": 1}


def _improves(new: dict, old: dict) -> bool:
    a, b = _STATUS_RANK.get(new.get("quality_status"), 9), _STATUS_RANK.get(old.get("quality_status"), 9)
    if a != b:
        return a < b
    return a == 1 and len(new.get("quality_reasons") or []) < len(old.get("quality_reasons") or [])


def generate_cases(
    chunks: List[dict],
    selected_types: List[str],
    requested,
    llm: LLMCall,
    label: str = "Selected document",
    progress: Optional[Callable[[str, str], None]] = None,
    top_up_rounds: int = 1,
    window_chars: int = 3000,
    focus: Optional[str] = None,
    repair_limit: int = 12,
    repair_batch: int = 6,
) -> dict:
    """Requirement extraction -> planning -> generation -> checks -> repair -> selection. Returns {"rows", "report"}."""
    note = progress or (lambda stage, message: None)
    selected = [t for t in dict.fromkeys(selected_types or []) if t in TYPE_DEFINITIONS]
    report = Report(selected, requested)
    if not selected:
        return {"rows": [], "report": report.as_dict(0), "error": "Select at least one supported test type."}

    text = merge_chunks(chunks)
    built = build_units(text, window_chars if len(text) < window_chars * 12 else int(window_chars * 1.4))
    report.extraction = built["extraction"]
    report.requirements = built["requirements"] or [u["requirement"] for u in built["units"]]
    report.sections = len(built["units"])
    for requirement in report.requirements:
        for name in selected:
            if name in BASELINE_TYPES or name not in EVIDENCE_TYPES:
                ok = requirement.get("testable", True)
            else:
                ok = bool((requirement.get("evidence") or {}).get(name))
            if ok:
                report.type_support.setdefault(name, []).append(requirement["ref"])
    plan = plan_units(built["units"], requested, selected)
    if not plan:
        return {"rows": [], "report": report.as_dict(0), "error": "The selected document has no readable text."}
    report.sections_used = len(plan)
    note("Requirements", f"{len(report.requirements)} requirement(s) identified "
                         f"({built['extraction'].get('testable', len(report.requirements))} testable); "
                         + "; ".join(f"{r['ref']}" for r in report.requirements[:8]))

    accepted: List[dict] = []
    covered_titles: List[str] = []
    covered_units: set = set()
    claims: Dict[str, int] = {}
    requirements = report.requirements
    report.unit_weights = {u["id"]: quality.PRIORITY_WEIGHT.get(u["priority"], 1)
                           for r in requirements for u in (r.get("coverage_units") or [])}
    _codes, code_class = _mapping_context(requirements)
    class_codes: Dict[str, List[str]] = {}
    for code, unit_id in code_class.items():
        class_codes.setdefault(unit_id, []).append(code)
    codes_by_value = {code: class_codes[unit_id] for code, unit_id in code_class.items()}

    def assess(row: dict, item: dict):
        """Stage 7 (coverage) and the overall status of a case that passed the rejecting gates."""
        requirement = item["requirement"]
        row["covers"] = quality.units_covered(row, requirement.get("coverage_units") or [])
        gates = row.get("_gates") or {}
        row["quality_status"], row["quality_reasons"] = quality.overall_status(gates)
        for issue in (gates.get("completeness") or {}).get("issues", []) + (gates.get("expected") or {}).get("issues", []):
            if issue not in row["quality_notes"]:
                row["quality_notes"].append(issue)
        if (gates.get("logic") or {}).get("verdict") == "ambiguous":
            row["quality_notes"].append("Business-rule check is ambiguous; review the expected result against the rule.")

    def run_section(item: dict, ordinal: int, quota: int, total_sections: int):
        allowed, _unsupported = split_types(item.get("evidence") or {}, selected, True)
        if not allowed:
            return
        mix = type_plan(allowed, quota, item["index"], claims)
        ref = item["requirement"]["ref"]
        answer, proposed = None, []
        for attempt in (0, 1):
            prompt = build_window_prompt(item, label, ordinal, total_sections, mix, covered_titles, selected, focus,
                                         _RETRY_NOTE if attempt else "", covered_units=covered_units)
            report.model_calls += 1
            answer = None
            started = time.time()
            try:
                answer = llm(prompt, SYSTEM_PROMPT)
            except Exception as error:  # a failing call must not lose the sections already generated
                note("Warning", f"{ref}: model call failed ({error}).")
            report.model_seconds += time.time() - started
            if answer and "NO_TESTABLE_CONTENT" in answer.upper().replace(" ", "_") and "###" not in answer:
                report.no_content_sections += 1
                note("Section", f"{ref}: no testable behaviour.")
                return
            proposed = parse_cases(answer) if answer else []
            if proposed:
                break
            report.model_failures += 1
            if attempt == 0:
                report.retries += 1
                note("Retry", f"{ref}: the answer could not be used; asking once more.")
        if not proposed:
            note("Warning", f"{ref}: no usable test cases after a retry.")
            report.note_proposed(ref, 0)
            return
        report.raw_cases += len(proposed)
        report.note_proposed(ref, len(proposed))
        good = []
        for raw in proposed:
            row = normalize_row(raw, selected, item, report)
            if row:
                row["_section"] = item["index"]
                good.append(row)
                row["_item"] = item
        added = dedupe(good, report, existing=accepted, requirements=requirements)
        for row in added:
            assess(row, item)
            if row["quality_status"] == "Accepted":
                covered_units.update(row["covers"])
        accepted.extend(added)
        covered_titles.extend(row["test_case"] for row in added)
        report.attempted[ref]["accepted"] = report.attempted[ref].get("accepted", 0) + len(added)
        note("Section", f"{ref}: {len(added)} test case(s) kept ({len(proposed)} proposed; "
                        f"{sum(1 for r in added if r['quality_status'] == 'Accepted')} accepted automatically).")

    for ordinal, item in enumerate(plan, start=1):
        run_section(item, ordinal, item["quota"], len(plan))

    def uncovered_weight(item: dict) -> int:
        return sum(quality.PRIORITY_WEIGHT.get(u["priority"], 1) for u in _window_units(item)
                   if u["id"] not in covered_units and u["priority"] != "Low")

    # Coverage pass: a testable requirement that has no accepted case yet gets one more look.
    uncovered_refs = [r["ref"] for r in report.requirements if r.get("testable", True)
                      and not any(a.get("requirement_ref") == r["ref"] for a in accepted)]
    attempted_refs = {item["requirement"]["ref"] for item in plan}
    for ref in uncovered_refs:
        if ref not in attempted_refs:
            continue  # not requested (fewer cases than requirements): reported, not forced
        candidates = [item for item in plan if item["requirement"]["ref"] == ref]
        note("Coverage", f"{ref}: no accepted case yet; asking again.")
        run_section(max(candidates, key=lambda p: (uncovered_weight(p), testability(p["text"]))),
                    plan.index(candidates[0]) + 1, 2, len(plan))
    # "All" mode: one more look at every section whose HIGH/MEDIUM priority units are still uncovered.
    if str(requested).lower() == "all":
        for item in plan:
            missing = uncovered_weight(item)
            if missing >= 3:
                note("Coverage", f"{item['requirement']['ref']}: important statements still uncovered; asking again.")
                run_section(item, plan.index(item) + 1, max(2, min(8, missing // 2)), len(plan))

    rounds = 0
    while (str(requested).lower() != "all" and len(accepted) < int(requested) and rounds < top_up_rounds
           and report.model_calls):
        rounds += 1
        missing = int(requested) - len(accepted)
        richest = sorted(plan, key=lambda p: (-uncovered_weight(p),
                                              sum(1 for a in accepted if a.get("requirement_ref") == p["requirement"]["ref"]),
                                              -testability(p["text"])))[: max(1, min(len(plan), 4))]
        note("Top-up", f"{missing} more case(s) needed; asking again about {len(richest)} section(s).")
        for item in richest:
            run_section(item, plan.index(item) + 1, max(2, math.ceil(missing / len(richest)) + 1), len(plan))
            if len(accepted) >= int(requested):
                break

    # Repair pass: cases blocked only by fixable writing problems are rewritten once and re-checked by the same gates.
    repairable = []
    for row in accepted:
        if row.get("quality_status") == "Needs Review" and row.get("_item"):
            fixes = repair_instructions(row.get("quality_reasons") or [])
            if fixes:
                repairable.append((row, row["_item"], fixes))
    repairable = repairable[:max(0, repair_limit)]
    for start in range(0, len(repairable), max(1, repair_batch)):
        batch = repairable[start:start + max(1, repair_batch)]
        note("Repair", f"Rewriting {len(batch)} case(s) whose steps or expected result could not be executed as written.")
        report.model_calls += 1
        started = time.time()
        answer = None
        try:
            answer = llm(build_repair_prompt(batch, label), SYSTEM_PROMPT)
        except Exception as error:
            note("Warning", f"Repair call failed ({error}); the cases stay marked Needs Review.")
        report.model_seconds += time.time() - started
        rewritten = parse_repair_answer(answer, len(batch)) if answer else {}
        if not rewritten:
            report.model_failures += 1
        for number, (old, item, fixes) in enumerate(batch, start=1):
            entry = {"requirement": old.get("requirement_ref", ""), "case": str(old.get("test_case", ""))[:160],
                     "before": list(old.get("quality_reasons") or []), "outcome": "no rewrite returned", "after": []}
            report.repairs.append(entry)
            raw = rewritten.get(number)
            if not raw:
                continue
            scratch = Report(selected, requested)
            new = normalize_row(raw, selected, item, scratch)
            if not new:
                entry["outcome"] = "rewrite rejected by the checks: " + ", ".join(
                    DROP_TEXT.get(k, k) for k in scratch.drops) if scratch.drops else "rewrite rejected by the checks"
                continue
            if new.get("requirement_ref") != old.get("requirement_ref"):
                entry["outcome"] = "rewrite traced to another requirement; original kept"
                continue
            assess(new, item)
            others = [copy.deepcopy(r) for r in accepted if r is not old]
            if not dedupe([new], scratch, existing=others, requirements=requirements):
                entry["outcome"] = "rewrite duplicates another case; original kept"
                continue
            entry["after"] = list(new.get("quality_reasons") or [])
            if not _improves(new, old):
                entry["outcome"] = "rewrite did not improve the case; original kept"
                continue
            new["_section"], new["_item"] = old.get("_section", 0), item
            new["quality_notes"].append("Rewritten by the repair pass: " + "; ".join(old.get("quality_reasons") or [])[:240])
            accepted[accepted.index(old)] = new
            entry["outcome"] = "repaired - " + new["quality_status"]
            entry["case_after"] = str(new.get("test_case", ""))[:160]
            if new["quality_status"] == "Accepted":
                covered_units.update(new["covers"])

    # merged parameter values may have added coverage after a case was first assessed
    units_by_ref = {r.get("ref"): r.get("coverage_units") or [] for r in requirements}
    for row in accepted:
        row["covers"] = quality.units_covered(row, units_by_ref.get(row.get("requirement_ref"), []))
    ordered = sorted(accepted, key=lambda r: r.get("_section", 0))
    final = select_final(ordered, selected, requested, report.unit_weights)
    for row in final:
        row.pop("_section", None)
        finish_row(row, codes_by_value)
    report.finish(final)
    report.finish_quality(final, requirements)
    for row in final:
        row.pop("_gates", None)
        row.pop("_plain_units", None)
        row.pop("_item", None)
    return {"rows": final, "report": report.as_dict(len(final))}
