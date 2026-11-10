"""QA AI Studio - atomic business rules and business-rule consistency checks.

Why this module exists
----------------------
"Source verified" only proved that the sentence a test case quotes exists in the document. It did not
prove that the case's conditions and expected result AGREE with that sentence. Real model output showed
cases such as "the BG amount is just above the reserved amount -> the trader is notified to provide an
additional BG", which is the opposite of the documented rule.

This module reads the requirement text (any document, any domain) and extracts rules that can be checked
mechanically:

* comparison rules   - a condition or constraint with an operator (less than, at least, equals or
                       exceeds, not available, insufficient ...) and the outcome that depends on it;
* restriction rules  - "<subject> with <condition> will not be permitted to <action> until <resolution>";
* mapping rules      - "If <entity> is of <group>, ... to <target>" plus the codes listed in the tables
                       under that group heading (the table placement is recorded as *derived*);
* enumeration rules  - a field/status that may only take the quoted values listed in a statement.

``check_case(case, rules)`` then compares a generated case with the rules and returns findings with a
verdict: ``consistent``, ``contradiction`` or ``ambiguous``. Nothing here knows about PSW, CESS, BGs or
collectorates: every term comes from the document being processed.
"""

from __future__ import annotations

import re
from typing import Dict, List, Optional, Tuple

# --------------------------------------------------------------------------- comparison operators

# Regions on the number line relative to a threshold T: value < T, value = T, value > T.
REGIONS = {
    "lt": {"lt"}, "eq": {"eq"}, "gt": {"gt"},
    "ge": {"eq", "gt"}, "le": {"lt", "eq"},
}

OP_WORDS = {"lt": "less than", "le": "at most", "eq": "equal to", "ge": "at least (equal to or greater than)",
            "gt": "greater than"}

# Order matters: compound operators first.
_OPERATOR_PATTERNS: List[Tuple[str, str]] = [
    ("ge", r"equals? or exceeds?|equal to or (?:greater|more|higher) than|(?:greater|more|higher) than or equal to|"
           r"at least|not less than|no less than|a minimum of|minimum of|or more\b|or above\b|meets? (?:the )?minimum|>=|≥"),
    ("le", r"equal to or (?:less|lower|fewer) than|(?:less|lower|fewer) than or equal to|at most|not more than|no more than|"
           r"not exceed(?:ing|s)?|does not exceed|up to|a maximum of|maximum of|or less\b|or below\b|<=|≤"),
    ("lt", r"just below|slightly below|just under|(?:is |are )?below|(?:is |are )?under\b|less than|lower than|fewer than|"
           r"short of|insufficient|not sufficient|not enough|not (?:be )?available|unavailable|falls? short|"
           r"does not (?:cover|meet)|less\b"),
    ("eq", r"exactly equal(?:s)?(?: to)?|exactly (?:the same|matches)|equal(?:s)? to|equals\b|exactly at|"
           r"same as|at the (?:boundary|threshold|limit)|exactly"),
    ("gt", r"just above|slightly above|just over|(?:is |are )?above|(?:is |are )?over\b|greater than|more than|higher than|"
           r"exceed(?:s|ing|ed)?|beyond|sufficient|enough|available\b|covers?\b"),
]
_OPERATOR_RES = [(op, re.compile(r"\b(?:" + pattern + r")", re.I)) for op, pattern in _OPERATOR_PATTERNS]

_NEGATION = re.compile(r"\b(?:not|no|never|without|none|neither|nor|doesn't|does not|isn't|is not|aren't|are not|"
                       r"won't|will not|cannot|can't|fails? to|unable)\b", re.I)

_CONDITION_LEAD = re.compile(r"^\s*(?:if|when|whenever|in (?:the )?cases? (?:where|when)|in case|where|once|unless|provided that)\b", re.I)

# Outcomes that ask for more / refuse = the constraint is NOT satisfied.
_DEMAND = re.compile(r"\b(?:notif\w*|asked|requested|required|prompted|reminded)\b[^.]{0,40}\b(?:to )?(?:provide|submit|"
                     r"supply|top up|increase|deposit|settle|furnish)\b|\b(?:reject\w*|refus\w*|block\w*|prevent\w*|denied|"
                     r"not (?:be )?(?:saved|accepted|allowed|permitted|processed|submitted)|validation error|error)\b", re.I)
_ACCEPT = re.compile(r"\b(?:accept\w*|saved|satisf\w*|approved|processed|completed|cleared|released|allowed|permitted|"
                     r"proceeds?|succeed\w*|successful\w*)\b", re.I)

STOP = set("""the a an and or of to for in on at by with from is are be been being will shall should must can may
that this these those it its as into upon than then such any all each which who whose when where there their
system user request requests value amount""".split())


def _terms(text: str) -> List[str]:
    out = []
    for word in re.findall(r"[a-z0-9][a-z0-9'’]*", str(text or "").lower()):
        word = word.replace("’", "'").rstrip("'s").strip("'")
        if len(word) < 2 or word in STOP:
            continue
        if word.endswith("s") and len(word) > 4 and not word.endswith("ss"):
            word = word[:-1]
        out.append(word)
    return out


def detect_operator(text: str) -> Optional[str]:
    """Return lt/le/eq/ge/gt for the first comparison phrase in ``text`` (compound phrases win)."""
    lowered = str(text or "")
    best = None
    for op, regex in _OPERATOR_RES:
        match = regex.search(lowered)
        if match and (best is None or match.start() < best[1] or (match.start() == best[1] and len(match.group(0)) > best[2])):
            best = (op, match.start(), len(match.group(0)))
    return best[0] if best else None


def _negated_before(text: str, position: int, window: int = 30) -> bool:
    return bool(_NEGATION.search(text[max(0, position - window):position]))


# --------------------------------------------------------------------------- rule extraction


def _sentences(text: str) -> List[str]:
    out = []
    for line in str(text or "").splitlines():
        line = line.strip()
        if not line or line.count(" | ") >= 1:
            continue
        out.extend(p.strip() for p in re.split(r"(?<=[.!?])\s+(?=[A-Z‘'“\"(])", line) if p.strip())
    return out


def _split_condition(sentence: str) -> Tuple[str, str]:
    """'If X, Y' / 'In cases where X, Y' -> (X, Y). Returns ('', sentence) when there is no condition."""
    match = _CONDITION_LEAD.match(sentence)
    if not match:
        inner = re.search(r",\s*(?:if|when|unless|provided that)\s+(.+)$", sentence, re.I)
        if inner:
            return inner.group(1).strip(" ."), sentence[:inner.start()].strip()
        return "", sentence
    rest = sentence[match.end():].strip()
    comma = rest.find(",")
    if comma < 0:
        then = re.search(r"\bthen\b", rest, re.I)
        if then:
            return rest[:then.start()].strip(), rest[then.end():].strip(" .")
        return rest, ""
    return rest[:comma].strip(), rest[comma + 1:].strip(" .")


def _subject_reference(clause: str, op: str) -> Tuple[str, str]:
    """Name the two quantities a comparison clause relates (best effort, used for explanations and matching)."""
    clause = clause.strip(" .")
    m = re.search(r"(?P<ref>.+?)\s+(?:is|are)\s+not\s+(?:be\s+)?available\s+(?:within|in|from)\s+(?:the\s+)?(?P<subj>.+)", clause, re.I)
    if m:
        return f"amount available in {m.group('subj')}", m.group("ref")
    for _op, regex in _OPERATOR_RES:
        m = regex.search(clause)
        if m and _op == op:
            left, right = clause[:m.start()].strip(), clause[m.end():].strip()
            left = re.sub(r"^(?:the\s+)?(?:trader|user|system)\s+will\s+\w+\s+(?:a\s+)?notification\s+to\s+provide\s+", "", left, flags=re.I)
            left = re.sub(r"\s+(?:that|which|is|are|must be|shall be|should be|will be)$", "", left, flags=re.I)
            right = re.sub(r"^(?:to|than|the)\s+", "", right, flags=re.I)
            return left[-80:].strip(" ,"), right[:80].strip(" ,")
    return "", ""


def _comparison_rules(ref: str, sentences: List[str]) -> List[dict]:
    rules = []
    previous = None  # (condition, op, outcome terms) of the previous conditional sentence
    for index, sentence in enumerate(sentences):
        condition, outcome = _split_condition(sentence)
        op = detect_operator(condition) if condition else None
        if condition and op:
            subject, reference = _subject_reference(condition, op)
            rule = {"id": f"{ref}#C{len(rules) + 1}", "kind": "comparison_trigger", "requirement": ref,
                    "statement": sentence, "condition": condition, "op": op, "trigger": sorted(REGIONS[op]),
                    "subject": subject, "reference": reference, "outcomes": [outcome] if outcome else [],
                    "outcome_terms": sorted(set(_terms(outcome)))}
            rules.append(rule)
            previous = rule
        elif previous is not None and not _CONDITION_LEAD.match(sentence) and len({t for t in set(_terms(sentence)) & (
                set(previous["outcome_terms"]) | set(_terms(previous["condition"]))) if len(t) >= 4}) >= 2:
            # a consequence sentence that continues the previous condition ("... will be reserved. The trader will
            # receive a notification ...") inherits that condition
            previous["outcomes"].append(sentence)
            previous["outcome_terms"] = sorted(set(previous["outcome_terms"]) | set(_terms(sentence)))
            previous["statement"] += " " + sentence
        else:
            previous = None
        # constraint inside the sentence: "<subject> that equals or exceeds <reference>", "must be at least ..."
        for op_name, regex in _OPERATOR_RES:
            if op_name not in ("ge", "le"):
                continue
            match = regex.search(sentence)
            if not match or (condition and match.start() < len(condition) + 5):
                continue
            subject, reference = _subject_reference(sentence, op_name)
            if not reference:
                continue
            rules.append({"id": f"{ref}#K{len(rules) + 1}", "kind": "constraint", "requirement": ref,
                          "statement": sentence, "op": op_name, "satisfied": sorted(REGIONS[op_name]),
                          "subject": subject, "reference": reference,
                          "terms": sorted(set(_terms(subject)) | set(_terms(reference)))})
            break
    return rules


_RESTRICTION = re.compile(
    r"^(?:any |an? |the |every )?(?P<subject>.+?)\s+(?:with|having|that has|who has)\s+(?:an? |the )?(?P<condition>.+?)\s+"
    r"(?:will|shall|must|should|may)\s+not\s+(?:be\s+)?(?:permitted|allowed|able)\s+to\s+(?P<action>.+?)"
    r"(?:\s+until\s+(?P<resolution>.+?))?\.?$", re.I)


def _restriction_rules(ref: str, sentences: List[str]) -> List[dict]:
    rules = []
    for sentence in sentences:
        match = _RESTRICTION.match(sentence.strip())
        if not match:
            continue
        rules.append({"id": f"{ref}#R{len(rules) + 1}", "kind": "restriction", "requirement": ref, "statement": sentence,
                      "subject": match.group("subject"), "condition": match.group("condition"),
                      "action": match.group("action"), "resolution": match.group("resolution") or "",
                      "condition_terms": sorted(set(_terms(match.group("condition")))),
                      "action_terms": sorted(set(_terms(match.group("action"))))})
    return rules


_QUOTED = re.compile(r"[‘'“\"]([^‘’'“”\"\n]{2,60})[’'”\"]")


def _enumeration_rules(ref: str, sentences: List[str]) -> List[dict]:
    rules = []
    for sentence in sentences:
        values = [v.strip().strip(".,;") for v in _QUOTED.findall(sentence)]
        if len(values) < 2 or not re.search(r"\b(?:or|either)\b", sentence, re.I):
            continue
        if re.search(r"\brenamed\b", sentence, re.I):
            continue
        first_quote = _QUOTED.search(sentence).start()
        heads = list(re.finditer(r"\b(status(?:es)?|type|types|values?|options?|categor(?:y|ies)|states?)\b", sentence[:first_quote], re.I))
        head = heads[-1] if heads else None  # the field word nearest before the listed values
        rules.append({"id": f"{ref}#E{len(rules) + 1}", "kind": "enumeration", "requirement": ref, "statement": sentence,
                      "field": re.sub(r"es$", "", head.group(1).lower()) if head and head.group(1).lower().startswith("status") else (head.group(1).lower() if head else "value"), "values": values})
    return rules


_MAPPING = re.compile(
    r"\bif\s+(?:the\s+)?(?P<entity>[\w ]{2,40}?)\s+(?:is|are|belongs? to)\s+(?:of|from|in|under)?\s*(?:the\s+)?"
    r"(?P<key>[A-Za-z][\w\-]{1,30})(?:\s+(?:region|province|area|zone|group|category))?\s*,\s*(?P<action>.+?)\s+"
    r"(?:to|for|with)\s+(?:the\s+)?(?P<target>[A-Z][A-Za-z0-9]{1,15})\b", re.I)
_CODE = re.compile(r"^[A-Z][A-Z0-9]{2,7}$")


def _mapping_rules(ref: str, text: str, sentences: List[str]) -> List[dict]:
    pairs = []
    for sentence in sentences:
        match = _MAPPING.search(sentence)
        if match:
            pairs.append({"key": match.group("key"), "target": match.group("target"), "entity": match.group("entity").strip(),
                          "statement": sentence})
    if not pairs:
        return []
    lines = [ln.strip() for ln in str(text or "").splitlines()]

    def group_of(line: str):
        if not line or " | " in line or len(line) > 80:
            return None
        words = re.findall(r"[A-Za-z][\w\-]*", line)
        for pair in pairs:
            key = pair["key"].lower()
            if any(w.lower() == key or (len(key) >= 2 and w.lower().startswith(key) and len(w) <= len(key) + 2) for w in words):
                return pair
        return None

    groups: List[dict] = []
    current, category, sub, head_noun, in_table = None, "", "", "", False
    for line in lines:
        if not line:
            continue
        pair = group_of(line)
        if pair is not None:
            nouns = re.findall(r"[A-Za-z]+", line)
            head_noun = nouns[-1].lower() if nouns else ""
            current = {"key": pair["key"], "target": pair["target"], "heading": line.strip(" :"), "codes": {},
                       "statement": pair["statement"], "entity": pair["entity"]}
            groups.append(current)
            category, sub, in_table = "", "", False
            continue
        if current is None:
            continue
        if " | " in line:
            cells = [c.strip() for c in line.split("|")]
            code = cells[0] if cells else ""
            if _CODE.match(code):
                entry = current["codes"].setdefault(code, {"code": code, "description": cells[1] if len(cells) > 1 else "",
                                                           "category": category, "sub_category": sub})
                entry.setdefault("rows", 0)
                entry["rows"] = entry["rows"] + 1
            in_table = True
            continue
        if re.match(r"^(?:Requirement|Section)\b", line, re.I) or len(line) > 90 or line.endswith("."):
            current = None  # the tables of this rule have ended
            continue
        words = re.findall(r"[A-Za-z]+", line.lower())
        if head_noun and words and words[-1].rstrip("s") == head_noun.rstrip("s"):
            category, sub = line.strip(" :"), ""
        else:
            sub = line.strip(" :")
        in_table = False
    rules = []
    for group in groups:
        if not group["codes"]:
            continue
        rules.append({"id": f"{ref}#M{len(rules) + 1}", "kind": "mapping", "requirement": ref,
                      "statement": group["statement"], "key": group["key"], "target": group["target"],
                      "heading": group["heading"], "entity": group["entity"], "codes": group["codes"],
                      "basis": "derived from the table placement under the heading '" + group["heading"] + "'"})
    return rules


def mapping_ambiguities(rules: List[dict]) -> List[str]:
    """Plain-language notes where a mapping depends on document layout or is not unique."""
    notes = []
    maps = [r for r in rules if r["kind"] == "mapping"]
    owners: Dict[str, List[str]] = {}
    categories: Dict[str, List[str]] = {}
    for rule in maps:
        for code, entry in rule["codes"].items():
            owners.setdefault(code, []).append(rule["target"])
            categories.setdefault(entry.get("category") or "(no category)", []).append(rule["target"])
    for code, targets in owners.items():
        if len(set(targets)) > 1:
            notes.append(f"Code {code} is listed under more than one group ({', '.join(sorted(set(targets)))}); its assignment is ambiguous.")
    for category, targets in categories.items():
        distinct = sorted(set(targets))
        if len(distinct) == 1 and len(maps) > 1 and category != "(no category)":
            notes.append(f"'{category}' codes appear only under the group mapped to {distinct[0]}; they are assigned to "
                         f"{distinct[0]} because of where the table sits in the document (no sentence states it). Confirm with the business analyst.")
    return notes


def extract_rules(requirement: dict) -> List[dict]:
    """All checkable rules of one requirement unit (``Core.requirement_extractor`` unit or {ref, text})."""
    ref = requirement.get("ref", "")
    text = requirement.get("text", "")
    sentences = _sentences(text)
    rules = []
    rules += _comparison_rules(ref, sentences)
    rules += _restriction_rules(ref, sentences)
    rules += _enumeration_rules(ref, sentences)
    rules += _mapping_rules(ref, text, sentences)
    return rules


def _clip(text: str, size: int) -> str:
    if len(text) <= size:
        return text
    return text[:size].rsplit(" ", 1)[0] + " ..."


def describe_rule(rule: dict) -> str:
    """One line for the prompt / report that states how the rule must be read."""
    kind = rule["kind"]
    if kind == "comparison_trigger":
        when = OP_WORDS[rule["op"]]
        quantities = (f" ({rule['subject']} {when} {rule['reference']})" if rule.get("subject") and rule.get("reference") else "")
        outcomes = _clip("; ".join(o.strip(" .") for o in rule["outcomes"]), 300)
        return (f"Only when \"{rule['condition']}\"{quantities}: {outcomes}. When the condition does not hold, "
                "these outcomes do not occur.")
    if kind == "constraint":
        return (f"\"{rule['subject']}\" must be {OP_WORDS[rule['op']]} \"{rule['reference']}\" "
                f"(values {', '.join('below' if r == 'lt' else 'equal' if r == 'eq' else 'above' for r in rule['satisfied'])} "
                "the reference satisfy it; the other side does not).")
    if kind == "restriction":
        return (f"With \"{rule['condition']}\", {rule['subject']} cannot {rule['action']}"
                + (f" until {rule['resolution']}" if rule.get("resolution") else "") + ".")
    if kind == "enumeration":
        return f"The {rule['field']} must be one of: " + ", ".join(f"'{v}'" for v in rule["values"]) + "."
    if kind == "mapping":
        codes = sorted(rule["codes"])
        cats = sorted({e.get("category") for e in rule["codes"].values() if e.get("category")})
        return (f"Codes listed under '{rule['heading']}'" + (f" ({', '.join(cats)})" if cats else "")
                + f" -> {rule['target']} ({len(codes)} codes, {rule['basis']}).")
    return rule.get("statement", "")


# --------------------------------------------------------------------------- case analysis

_NUMBER = re.compile(r"(?<![\w.])(\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?(?![\w])")


def _case_text(case: dict, *fields) -> str:
    return " ".join(str(case.get(f, "") or "") for f in fields)


def case_comparison(case: dict, reference_terms: Optional[set] = None) -> Optional[str]:
    """How the case's test value relates to the threshold: lt / eq / gt (None = the case states no comparison)."""
    text = _case_text(case, "test_case", "pre_conditions", "steps", "scenario")
    # numeric comparison: "reserved amount of 10000" ... "Enter 9999"
    named = re.search(r"\b(?:reserved|required|minimum|maximum|threshold|limit|balance|available|existing|reference)\b[^.\n]{0,30}?"
                      r"(?:of|is|=|:|as)\s*(?:rs\.?|pkr)?\s*(\d[\d,]*)", text, re.I)
    if named:
        threshold = float(named.group(1).replace(",", ""))
        others = [float(n.replace(",", "")) for n in _NUMBER.findall(text[named.end():]) if n]
        others += [float(n.replace(",", "")) for n in _NUMBER.findall(text[:named.start()]) if n]
        others = [n for n in others if n != threshold and n > 9]
        if others:
            value = others[0]
            return "lt" if value < threshold else "gt"
        if re.search(r"\b(?:same|equal|exactly)\b", text, re.I):
            return "eq"
    op = detect_operator(text)
    if op in ("ge", "le"):
        return None  # "at least" in a CASE does not tell which side the test value is on
    return op


def case_reference_text(case: dict) -> str:
    """The words right after the case's comparison phrase ("just above THE RESERVED AMOUNT")."""
    text = _case_text(case, "test_case", "pre_conditions", "steps", "scenario")
    best = None
    for _op, regex in _OPERATOR_RES:
        match = regex.search(text)
        if match and (best is None or match.start() < best.start()):
            best = match
    return " ".join(_terms(text[best.end():best.end() + 40])) if best else ""


def outcome_polarity(expected: str, terms: List[str]) -> Optional[bool]:
    """Does the expected result say the rule outcome HAPPENS (True), does NOT happen (False) or not mention it (None)?

    The clause (sentence / conjunct) that holds the outcome words is examined; a negation anywhere in that clause
    before the last outcome word ("The trader does NOT receive a notification") means the outcome does not happen.
    """
    text = str(expected or "")
    lowered = text.lower()
    best = None
    for clause_match in re.finditer(r"[^.;]+", lowered):
        clause = clause_match.group(0)
        for part in re.split(r"\b(?:and|but|while|whereas)\b", clause):
            hits = []
            for term in terms:
                if len(term) < 3:
                    continue
                for match in re.finditer(r"\b" + re.escape(term if len(term) <= 4 else term[:len(term) - 1]), part):
                    hits.append(match.end())
            if len(hits) > (best[0] if best else 0):
                best = (len(hits), part, max(hits))
    if not best:
        return None
    _count, part, last = best
    return not bool(_NEGATION.search(part[:last]))


def _overlap(a, b) -> int:
    return len(set(a) & set(b))


def check_case(case: dict, rules: List[dict]) -> List[dict]:
    """Findings for one case. Each finding: {rule, kind, verdict, message}. Empty list = no rule applies."""
    findings = []
    expected = str(case.get("expected_result", ""))
    body = _case_text(case, "test_case", "pre_conditions", "steps", "scenario")
    all_text = body + " " + expected
    case_terms = set(_terms(all_text))
    comparison_verdicts = []

    for rule in rules:
        kind = rule["kind"]
        if kind == "comparison_trigger":
            if _overlap(case_terms, rule["outcome_terms"] + _terms(rule["condition"])) < 2:
                continue
            c = case_comparison(case)
            condition_terms = set(_terms(rule["condition"]))
            polarity = outcome_polarity(expected, [t for t in rule["outcome_terms"] if t not in STOP and t not in condition_terms])
            if c is None or polarity is None:
                continue
            should = c in rule["trigger"]
            verdict = "consistent" if should == polarity else "contradiction"
            side = {"lt": "below", "eq": "equal to", "gt": "above"}[c]
            message = (f"The case tests a value {side} the threshold and expects the outcome to "
                       f"{'occur' if polarity else 'not occur'}; by the document the outcome "
                       f"{'occurs' if should else 'does not occur'} in that case (rule: {describe_rule(rule)})")
            # the case compares against a quantity that only EXISTS once the rule has fired (e.g. a "reserved" amount):
            # then the trigger condition has already held, and "the outcome does not occur" cannot be confirmed
            outcome_only = {t for t in rule["outcome_terms"] if len(t) >= 5} - condition_terms
            reference = case_reference_text(case)
            if verdict == "consistent" and not polarity and any(t in reference for t in outcome_only):
                verdict = "ambiguous"
                message = ("The case compares with a quantity that only exists after the rule's outcome has happened "
                           f"('{reference.strip()[:60]}'), yet expects the outcome not to occur; the document does not "
                           f"settle this - review it (rule: {describe_rule(rule)})")
            comparison_verdicts.append(verdict)
            findings.append({"rule": rule["id"], "kind": kind, "verdict": verdict, "message": message})
        elif kind == "constraint":
            if _overlap(case_terms, rule["terms"]) < 2:
                continue
            c = case_comparison(case)
            if c is None:
                continue
            demand = bool(_DEMAND.search(expected))
            accept_hit = bool(_ACCEPT.search(expected))
            negated_demand = bool(re.search(r"\b(?:not|no)\b[^.]{0,25}\b(?:notif\w*|asked|requested|error|reject\w*)", expected, re.I))
            if negated_demand:
                accepts = True
            elif demand:
                accepts = False
            elif accept_hit:
                accepts = True
            else:
                continue
            satisfied = c in rule["satisfied"]
            verdict = "consistent" if satisfied == accepts else "contradiction"
            side = {"lt": "below", "eq": "equal to", "gt": "above"}[c]
            message = (f"The tested value is {side} the reference, which {'satisfies' if satisfied else 'does not satisfy'} "
                       f"the rule, but the expected result {'treats it as satisfied' if accepts else 'treats it as not satisfied'} "
                       f"(rule: {describe_rule(rule)})") if verdict == "contradiction" else (
                f"Consistent with the rule: {describe_rule(rule)}")
            comparison_verdicts.append(verdict)
            findings.append({"rule": rule["id"], "kind": kind, "verdict": verdict, "message": message})
        elif kind == "restriction":
            if _overlap(case_terms, rule["condition_terms"]) < max(1, min(2, len(rule["condition_terms"]))):
                continue
            cond_present = None
            cond_text = rule["condition"].lower()
            low_body = body.lower()
            pos = low_body.find(cond_text[:max(6, len(cond_text) // 2)])
            if pos >= 0:
                cond_present = not _negated_before(low_body, pos, 18)
            if rule.get("resolution") and re.search(r"\b(?:settled|resolved|cleared|paid|positive|zero)\b", low_body):
                cond_present = False if cond_present is None else cond_present and not re.search(
                    r"\b(?:has been|is|was|after)\b[^.]{0,30}\b(?:settled|resolved|cleared|paid)", low_body)
            if cond_present is None:
                continue
            blocked = bool(re.search(r"\b(?:not (?:be )?(?:permitted|allowed|able)|reject\w*|block\w*|prevent\w*|refus\w*|"
                                     r"denied|cannot|can't|not submitted|unable)\b", expected, re.I))
            allowed = bool(re.search(r"\b(?:success\w*|submitted|accepted|allowed|permitted|proceeds?|created)\b", expected, re.I)) and not blocked
            if not blocked and not allowed:
                continue
            should_block = cond_present
            verdict = "consistent" if should_block == blocked else "contradiction"
            findings.append({"rule": rule["id"], "kind": kind, "verdict": verdict,
                             "message": ("Consistent with the rule: " if verdict == "consistent" else
                                         "The expected result does not follow the restriction: ") + describe_rule(rule)})
        elif kind == "enumeration":
            field = rule["field"].rstrip("s") if rule["field"] != "status" else "status"
            # only values quoted right after the field word ("status 'X'") are claims about this field
            claimed = re.findall(r"\b" + re.escape(field) + r"\w*\s*(?:of|is|are|=|:|as)?\s*[‘'“\"]([^‘’'“”\"\n]{2,60})[’'”\"]",
                                 all_text, re.I)
            allowed = {v.lower() for v in rule["values"]}
            foreign = [q.strip() for q in claimed if q.strip().lower() not in allowed]
            inclusion = re.search(r"\b(?:fetch\w*|display\w*|list\w*|shown|returns?|include\w*|appear\w*)\b", expected, re.I)
            exclusion = re.search(r"\b(?:not (?:be )?(?:fetch\w*|display\w*|listed|shown|returned|included))\b", expected, re.I)
            if foreign and inclusion and not exclusion:
                findings.append({"rule": rule["id"], "kind": kind, "verdict": "contradiction",
                                 "message": f"Expects {rule['field']} value(s) {', '.join(foreign)} to be included, "
                                            f"but the document allows only: {', '.join(rule['values'])}."})
            elif claimed and not foreign:
                findings.append({"rule": rule["id"], "kind": kind, "verdict": "consistent",
                                 "message": f"The {rule['field']} values agree with the document: {', '.join(rule['values'])}."})
        elif kind == "mapping":
            codes = {code for code in re.findall(r"\b[A-Z][A-Z0-9]{2,7}\b", all_text)}
            mine = {code for code in codes if code in rule["codes"]}
            if not mine:
                continue
            targets = {r["target"] for r in rules if r["kind"] == "mapping"}
            named = [t for t in targets if re.search(r"\b" + re.escape(t) + r"\b", expected)]
            if not named:
                continue
            for code in sorted(mine):
                wrong = [t for t in named if t != rule["target"]]
                if wrong and rule["target"] not in named:
                    findings.append({"rule": rule["id"], "kind": kind, "verdict": "contradiction",
                                     "message": f"{code} is listed under '{rule['heading']}', which the document maps to "
                                                f"{rule['target']}; the case expects {', '.join(wrong)} "
                                                f"({rule['basis']})."})
                elif rule["target"] in named:
                    findings.append({"rule": rule["id"], "kind": kind, "verdict": "consistent",
                                     "message": f"{code} -> {rule['target']} agrees with the table under '{rule['heading']}'."})
    # several comparison interpretations that disagree = the case does not say which quantity it compares
    if "contradiction" in comparison_verdicts and "consistent" in comparison_verdicts:
        for finding in findings:
            if finding["kind"] in ("comparison_trigger", "constraint"):
                finding["verdict"] = "ambiguous"
                finding["message"] += " [the case does not make clear which amount is compared; review it]"
    findings.extend(check_arithmetic(all_text))
    return findings


_ARITH = re.compile(r"(\d[\d,]*(?:\.\d+)?)\s*([-+x×*/])\s*(\d[\d,]*(?:\.\d+)?)\s*=\s*(\d[\d,]*(?:\.\d+)?)")
_PERCENT_OF = re.compile(r"(\d+(?:\.\d+)?)\s*%\s*of\s*(?:rs\.?\s*|pkr\s*)?(\d[\d,]*(?:\.\d+)?)\s*(?:=|is|equals|gives|results in)\s*"
                         r"(?:rs\.?\s*|pkr\s*)?(\d[\d,]*(?:\.\d+)?)", re.I)


def check_arithmetic(text: str) -> List[dict]:
    """Simple calculations written in a case must be correct."""
    out = []

    def num(value):
        return float(value.replace(",", ""))

    for a, op, b, c in _ARITH.findall(text):
        x, y, z = num(a), num(b), num(c)
        value = {"-": x - y, "+": x + y, "x": x * y, "×": x * y, "*": x * y, "/": x / y if y else None}[op]
        if value is not None and abs(value - z) > 0.01 * max(1, abs(z)):
            out.append({"rule": "arithmetic", "kind": "calculation", "verdict": "contradiction",
                        "message": f"Calculation {a} {op} {b} = {c} is wrong (it is {value:g})."})
    for p, base, result in _PERCENT_OF.findall(text):
        value = num(p) / 100 * num(base)
        if abs(value - num(result)) > 0.01 * max(1, num(result)):
            out.append({"rule": "arithmetic", "kind": "calculation", "verdict": "contradiction",
                        "message": f"{p}% of {base} is {value:g}, not {result}."})
    return out


def verdict_of(findings: List[dict]) -> str:
    verdicts = {f["verdict"] for f in findings}
    if "contradiction" in verdicts:
        return "contradiction"
    if "ambiguous" in verdicts:
        return "ambiguous"
    if "consistent" in verdicts:
        return "consistent"
    return "not_applicable"
