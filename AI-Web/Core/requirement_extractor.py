"""QA AI Studio - generic requirement extraction and validation.

Source document text  ->  requirement units  ->  validation (testable or not, with the reason)

Nothing here knows about a particular document, domain or CRF.  A requirement unit is found from
the STRUCTURE of the text:

* explicit headings such as "Requirement 3: ...", "FR-12 ...", "BR 4.1 - ...", "Business Rule 2: ..."
  (the identifier is kept exactly as written and marked ``ref_kind = "document"``);
* when the text has no such heading, sections of the document become units with a generated
  reference ``INT-REQ-001`` ... marked ``ref_kind = "internal"``.  An internal reference is never an
  identifier from the document and must always be shown as internal.

For every unit the extractor also lists the statements, quoted field names, validation rules,
numeric limits, access rules and integration statements it contains.  The test engine uses these
as EVIDENCE: a test type is only requested for a requirement that actually contains evidence for it.
"""

from __future__ import annotations

import re
from typing import Dict, List

# --------------------------------------------------------------------------- patterns

_LABELLED = re.compile(
    r"(?P<label>Functional Requirement|Non[- ]?Functional Requirement|Business Rule|Requirement|Req\.?|Use Case|User Story)"
    r"\s*(?:No\.?|Number|#)?\s*(?P<num>[A-Za-z]{0,3}[-_ ]?\d+(?:\.\d+)*)\s*[:\-–—.)]\s*(?P<title>\S.*)?$",
    re.I,
)
_CODED = re.compile(r"^\s*(?P<id>(?:FR|NFR|BR|SR|RQ|REQ|UC|US)[-_ ]?\d+(?:[.\-]\d+)*)\s*[:\-–—.)]?\s*(?P<title>\S.*)?$")
_NUMBERED = re.compile(r"^\s*(?P<id>\d+(?:\.\d+){0,3})[.)]?\s+(?P<title>[A-Z][^.|]{3,110})$")

# Parts of a form/document that are administration, not behaviour.
_NON_REQUIREMENT = re.compile(
    r"^\s*(?:Section\s+[A-Z]\b|Approved/Rejected|Alternatives to the|Impact Analysis by|Technical Impact Review|"
    r"Change (?:Request )?(?:Review )?Committee|Revision History|Document Control|Table of Contents|"
    r"Name\s*\|\s*Designation|Member Name|Page \d+ of \d+|\|\s*Change Request Form)",
    re.I,
)
_NOISE = re.compile(r"not a valid (?:binary )?format|traceback \(most recent|\bNullReference|\bStackOverflow", re.I)

_MODAL = re.compile(
    r"\b(shall|must|should|will|may|can|cannot|can't|is required|are required|is not (?:be )?(?:permitted|allowed)|"
    r"not be (?:permitted|allowed)|needs? to|has to|have to|is to be|are to be)\b", re.I)
_CHANGE = re.compile(
    r"\b(renamed|added|removed|replaced|updated|displayed|fetch(?:ed)?|populated|shared|mapped|assigned|marked|"
    r"calculated|deducted|reserved|notified|adjusted|returns?|retrieves?|include[sd]?|sent|sends?|transmitted|"
    r"received?|stores?|stored|saved?|recorded|becomes?|shows?|appears?|enabled|disabled|approved|rejected)\b", re.I)

BOUNDARY_RE = re.compile(
    r"\b(?:maximum|minimum|max\.?|min\.?|at least|at most|up to|not more than|not less than|no more than|no less than|"
    r"more than|less than|greater than|fewer than|exceed(?:s|ing)?|between\s+\S+\s+and|within\s+\d+|"
    r"equal(?:s)? to|negative|zero|positive balance)\b|\b\d+\s*(?:digits?|characters?|chars?|days?|hours?|minutes?|"
    r"seconds?|mb|kb|gb|%|percent|records?|items?|years?|months?)\b|\b\d{1,3}\s?%", re.I)
_NEGATIVE_RE = re.compile(
    r"\b(not|cannot|can't|unable|invalid|reject|rejected|mandatory|required|only if|only when|unless|until|"
    r"must be|prohibit|forbid|denied|negative balance|expired|duplicate|missing|without)\b", re.I)
_VALIDATION_RE = re.compile(
    r"\b(mandatory|required|valid|invalid|format|numeric|alphanumeric|unique|duplicate|length|characters?|digits?|"
    r"must be|not be (?:permitted|allowed)|only|allowed values?|range|minimum|maximum|negative)\b", re.I)
_SECURITY_RE = re.compile(
    r"\b(role|permission|authori[sz]|authenticat|password|token|privilege|access|"
    r"restricted|officer|customs agent|approver|only (?:the )?(?:\w+ )?(?:user|officer|agent|admin)|not be permitted)\b", re.I)
_INTEGRATION_RE = re.compile(
    r"\b(api|web ?service|interface with|integrat\w+|shares?|shared|fetch(?:es|ed)?|retriev\w+|call(?:s|ed)?|"
    r"method id|notify|notification|synchroni[sz]\w*|send(?:s)?|receiv\w+|webhook)\b", re.I)
_API_RE = re.compile(r"\b(api|endpoint|request|response|payload|json|xml|http|method id|property|properties|status code)\b", re.I)
_DATABASE_RE = re.compile(
    r"\b(stored|store[sd]?|saved|persist\w*|record(?:ed|s)?|reserved|deduct\w*|adjust\w*|balance|audit|history|"
    r"database|table|column|update[sd]?)\b", re.I)
_PERFORMANCE_RE = re.compile(
    r"\b(\d+\s*(?:ms|milliseconds?|seconds?|minutes?)|response time|concurrent|throughput|sla|timeout|"
    r"within \d+|capacity|per second)\b", re.I)
_COMPAT_RE = re.compile(r"\b(browser|chrome|firefox|edge|safari|mobile|tablet|device|resolution|compatib\w+|"
                        r"windows|android|ios|pdf|xlsx|csv)\b", re.I)
_UI_RE = re.compile(r"\b(screen|page|button|option|menu|submenu|dashboard|interface|column|filter|field|tab|label|"
                    r"dropdown|list|click|select|display\w*|populated|link|form)\b", re.I)

_QUOTED = re.compile(r"[‘'“\"]([^‘’'“”\"\n]{2,60})[’'”\"]")

# --------------------------------------------------------------------------- helpers


def _norm_ref(label: str, number: str) -> str:
    label = re.sub(r"\.$", "", label.strip())
    label = {"req": "Requirement"}.get(label.lower(), label)
    number = re.sub(r"\s+", "", number) if re.match(r"^[A-Za-z]{1,3}[-_ ]", number) else number.strip()
    return f"{label[:1].upper()}{label[1:]} {number}".strip()


def _match_heading(line: str):
    """Return (ref, title, prefix_text) when ``line`` holds a requirement heading (possibly after a label)."""
    stripped = line.strip()
    if not stripped or stripped.count("|") >= 2:
        return None
    for start in (0, *[m.end() for m in re.finditer(r"[:\-–—]\s+(?=[A-Za-z]{3,}[ A-Za-z]{0,30}\s*\d)", stripped[:90])]):
        match = _LABELLED.match(stripped[start:])
        if match:
            title = (match.group("title") or "").strip()
            return _norm_ref(match.group("label"), match.group("num")), title, stripped[:start].strip()
    match = _CODED.match(stripped)
    if match and len(stripped) < 220:
        return match.group("id").upper().replace("_", "-").replace(" ", "-"), (match.group("title") or "").strip(), ""
    return None


def _sentences(line: str) -> List[str]:
    parts = re.split(r"(?<=[.!?])\s+(?=[A-Z‘'“\"(])", line.strip())
    return [p.strip() for p in parts if p.strip()]


def _is_table_row(line: str) -> bool:
    return line.count(" | ") >= 1


def _statements(lines: List[str]) -> List[str]:
    found = []
    for line in lines:
        if _is_table_row(line):
            continue
        for sentence in _sentences(line):
            if len(sentence) >= 12 and (_MODAL.search(sentence) or _CHANGE.search(sentence)):
                found.append(sentence)
    return found


def _fields(text: str) -> List[str]:
    names = []
    for match in _QUOTED.finditer(text):
        value = match.group(1).strip().strip(".,;:")
        if 2 <= len(value) <= 60 and len(value.split()) <= 7 and value.lower() not in {n.lower() for n in names}:
            names.append(value)
    for match in re.finditer(r"\b([A-Z][\w/\-]*(?: [A-Za-z][\w/\-]*){0,3}) (?:field|column|filter|button|option|property)\b", text):
        value = match.group(1).strip()
        if value.lower() not in {n.lower() for n in names} and len(value) > 2:
            names.append(value)
    return names[:25]


def _hits(regex: re.Pattern, statements: List[str]) -> List[str]:
    return [s for s in statements if regex.search(s)]


def _evidence(statements: List[str], text: str) -> Dict[str, List[str]]:
    """Statements that give a basis for each kind of test (empty list = no basis in this requirement)."""
    return {
        "Functional": statements[:],
        "Positive": statements[:],
        "Negative": _hits(_NEGATIVE_RE, statements),
        "Validation": _hits(_VALIDATION_RE, statements),
        "Boundary": [s for s in statements if BOUNDARY_RE.search(s)],
        "Integration": _hits(_INTEGRATION_RE, statements),
        "API": _hits(_API_RE, statements),
        "UI": _hits(_UI_RE, statements),
        "Database": _hits(_DATABASE_RE, statements),
        "Security": _hits(_SECURITY_RE, statements),
        "Performance": _hits(_PERFORMANCE_RE, statements),
        "Compatibility": _hits(_COMPAT_RE, statements),
    }


def window_evidence(text: str) -> Dict[str, List[str]]:
    """Evidence per test type for any piece of text (used for windows cut from a long requirement)."""
    lines = [ln for ln in str(text or "").splitlines() if ln.strip()]
    statements = _statements(lines)
    return {name: found for name, found in _evidence(statements, text).items() if found}


# --------------------------------------------------------------------------- extraction


def clean_noise(text: str):
    """Remove lines that are extraction/system errors, not document content. Returns (text, removed_lines)."""
    kept, removed = [], []
    for line in str(text or "").splitlines():
        (removed if _NOISE.search(line) else kept).append(line)
    return "\n".join(kept), removed


def _build_unit(ref, kind, title, lines, order):
    body = "\n".join(lines).strip()
    statements = _statements(lines)
    evidence = _evidence(statements, body)
    tables = sum(1 for ln in lines if _is_table_row(ln))
    unit = {
        "ref": ref, "ref_kind": kind, "title": title.strip()[:200], "order": order,
        "text": ((f"{ref}: {title}".strip(": ") + "\n") if title else (ref + "\n")) + body,
        "statements": statements, "fields": _fields(body),
        "evidence": {name: found for name, found in evidence.items() if found},
        "reference_data_rows": tables,
    }
    unit["testable"] = bool(statements)
    unit["reason"] = "" if statements else "no behaviour statement (shall/must/will/...) found; context or reference data only"
    return unit


def extract_requirements(text: str) -> dict:
    """Split ``text`` into requirement units.  Returns {units, context, warnings, mode}."""
    clean, noise = clean_noise(text)
    lines = [ln.rstrip() for ln in clean.splitlines()]
    warnings = [f"Removed an extraction/system error line from the text: '{ln.strip()[:80]}'" for ln in noise]

    headings = []  # (line_index, ref, title, prefix)
    for index, line in enumerate(lines):
        hit = _match_heading(line)
        if hit:
            headings.append((index, *hit))
    explicit = [h for h in headings if h[3] is not None]
    if not explicit:  # no "Requirement n" headings: numbered section headings ("2. Item Details screen") are the next best structure
        numbered = []
        for index, line in enumerate(lines):
            match = _NUMBERED.match(line)
            if match and not _NON_REQUIREMENT.match(line):
                numbered.append((index, f"Section {match.group('id')}", match.group("title").strip(), ""))
        if len(numbered) >= 2:
            explicit = numbered

    units, context = [], []
    if len(explicit) >= 1:
        first = explicit[0][0]
        context = [ln for ln in lines[:first] if ln.strip()]
        for position, (index, ref, title, prefix) in enumerate(explicit):
            end = explicit[position + 1][0] if position + 1 < len(explicit) else len(lines)
            body = []
            for ln in lines[index + 1:end]:
                if _NON_REQUIREMENT.match(ln):
                    break
                body.append(ln)
            body = [ln for ln in body if ln.strip()]
            if prefix and position == 0:
                context.append(prefix)
            if not title and body:
                title, body = body[0][:120], body[1:]
            units.append(_build_unit(ref, "document", title, body, position + 1))
        mode = "explicit" if headings else "numbered"
    else:
        mode = "sections"
        buffer, count = [], 0
        for ln in lines:
            if _NON_REQUIREMENT.match(ln):
                if buffer:
                    count += 1
                    units.append(_build_unit(f"INT-REQ-{count:03d}", "internal", _guess_title(buffer), buffer, count))
                    buffer = []
                continue
            if ln.strip():
                buffer.append(ln)
            if sum(len(b) for b in buffer) > 2400 and ln.strip().endswith((".", ":")):
                count += 1
                units.append(_build_unit(f"INT-REQ-{count:03d}", "internal", _guess_title(buffer), buffer, count))
                buffer = []
        if buffer:
            count += 1
            units.append(_build_unit(f"INT-REQ-{count:03d}", "internal", _guess_title(buffer), buffer, count))

    seen, duplicates = {}, 0
    for unit in units:
        key = unit["ref"].lower()
        if key in seen:  # a repeated heading in the same document must still be addressable
            duplicates += 1
            unit["ref"] = f"{unit['ref']} (repeat {duplicates})"
        seen[key] = unit
    if duplicates:
        warnings.append(f"{duplicates} requirement heading(s) appear more than once; the repeats were given '(repeat n)' labels.")
    return {"units": units, "context": context, "warnings": warnings, "mode": mode}


def _guess_title(lines: List[str]) -> str:
    for ln in lines:
        if len(ln) < 110 and not _is_table_row(ln):
            return ln.strip(": ")
    return (lines[0][:100] if lines else "").strip()


# --------------------------------------------------------------------------- validation


def validate_requirements(result: dict) -> dict:
    """Mark each unit as testable or not and summarise.  Nothing is dropped silently."""
    units = result.get("units") or []
    testable = [u for u in units if u["testable"]]
    skipped = [{"ref": u["ref"], "title": u["title"], "reason": u["reason"]} for u in units if not u["testable"]]
    return {
        "mode": result.get("mode"), "total": len(units), "testable": len(testable), "skipped": skipped,
        "warnings": list(result.get("warnings") or []),
        "context_lines": len(result.get("context") or []),
    }


def find_statement(statement: str, window: str, minimum: float = 0.6):
    """Locate the document sentence that ``statement`` quotes.

    Returns (status, sentence, score): status is "verified" (the statement is in the text, allowing
    for spacing/quote differences), "approximate" (most of its words are in one sentence) or None.
    """
    def tokens(value):
        return re.findall(r"[a-z0-9]+", str(value or "").lower())

    wanted = tokens(statement)
    if len(wanted) < 3:
        return None, "", 0.0
    flat = " ".join(tokens(window))
    if " ".join(wanted) in flat:
        for line in re.split(r"\n+", window):
            for sentence in _sentences(line) or [line]:
                if " ".join(wanted) in " ".join(tokens(sentence)):
                    return "verified", sentence.strip(), 1.0
        return "verified", str(statement).strip(), 1.0
    best, best_score = "", 0.0
    wanted_set = set(wanted)
    for line in re.split(r"\n+", window):
        for sentence in _sentences(line) or [line]:
            have = set(tokens(sentence))
            if not have:
                continue
            score = len(wanted_set & have) / len(wanted_set)
            if score > best_score:
                best, best_score = sentence.strip(), score
    if best_score >= 0.85:
        return "verified", best, best_score
    if best_score >= minimum:
        return "approximate", best, best_score
    return None, best, best_score
