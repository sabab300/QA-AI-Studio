"""QA AI Studio - quality gates for generated test cases.

The generation engine (``Core.test_case_engine``) asks the model for cases and then runs every case through the
gates below. Each gate answers ONE question and records its answer on the case, so the user can see why a case
was accepted, rejected or sent to review:

1. source       - does the quoted source statement exist in the selected document?      (engine + extractor)
2. relevance    - does that statement actually support what the case tests?
3. logic        - do the case's conditions and expected result agree with the documented rule?  (business_rules)
4. type         - does the case genuinely match its test type?                          (engine)
5. completeness - are preconditions, steps and expected result sufficient to execute?
6. expected     - is the expected result observable and specific (not a restatement)?
7. basis        - is the behaviour documented, defensibly derived, or an assumption?
8. duplicate    - unique, exact/semantic duplicate, or a parameterized variation?
9. coverage     - which atomic requirement units does it cover?

The module also builds the atomic COVERAGE UNITS of a requirement (statements, list items, mapping classes) with a
priority, which the engine uses to plan the requested quantity and to report coverage against a real denominator.

Everything is generic: terms come from the processed document, never from a particular CRF.
"""

from __future__ import annotations

import re
from typing import Dict, List, Optional, Tuple

from Core import business_rules as br

# --------------------------------------------------------------------------- helpers

_STOP = set("""the a an and or of to for in on at by with from is are be been being will shall should must can may
that this these those it its as into upon than then such any all each which who whose when where there their
user system verify ensure check test case following changes made also same only per after before""".split())


def terms(text: str) -> List[str]:
    out = []
    for word in re.findall(r"[a-z0-9][a-z0-9'’]*", str(text or "").lower()):
        word = word.replace("’", "'").strip("'")
        if word.endswith("'s"):
            word = word[:-2]
        if len(word) < 3 or word in _STOP:
            continue
        if word.endswith("s") and len(word) > 4 and not word.endswith("ss"):
            word = word[:-1]
        out.append(word)
    return out


def key(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(text or "").lower()).strip()


def jaccard(a, b) -> float:
    a, b = set(a), set(b)
    return len(a & b) / len(a | b) if a and b else 0.0


# --------------------------------------------------------------------------- coverage units

_FINANCIAL = re.compile(r"\b(amount|balance|bank guarantee|bg|bg’s|bgs|cess|percentage|percent|variance|deduct\w*|reserv\w*|"
                        r"waiver|exempt\w*|payment|fee|tax|duty|refund|charge|calculat\w*|adjust\w*|negative)\b", re.I)
_INTEGRATION = re.compile(r"\b(api|method id|web ?service|weboc|interface with|share[sd]?|fetch\w*|retriev\w*|call\w*|"
                          r"notif\w*|send|sent|receiv\w*|transmit\w*|response|request body)\b", re.I)
_RESTRICTION = re.compile(r"\b(not (?:be )?(?:permitted|allowed)|cannot|must not|shall not|only|until|reject\w*|approv\w*)\b", re.I)
_WORKFLOW = re.compile(r"\b(submit\w*|approve\w*|reject\w*|assign\w*|mark\w*|process\w*|select\w*|populat\w*|clearance|status)\b", re.I)
_UI = re.compile(r"\b(column|filter|field|screen|interface|button|option|menu|submenu|dashboard|display\w*|renamed|added|label)\b", re.I)
_NAVIGATION = re.compile(r"^(?:the )?user shall (?:log ?in|log into|sign in|click on the [‘'“\"]?[\w /\-]+[’'”\"]? option(?: from \w+)?\.?$|"
                         r"navigate)", re.I)
_REPORT = re.compile(r"\breports?\b", re.I)


def unit_kind(text: str) -> str:
    if _NAVIGATION.search(text) or re.search(r"\b(?:log ?in(?:to)?|sign in|submenus?)\b", text, re.I) and not _FINANCIAL.search(text):
        return "navigation"
    if _FINANCIAL.search(text) and re.search(r"\b(calculat\w*|variance|deduct\w*|adjust\w*|reserv\w*|balance|percentage)\b", text, re.I):
        return "calculation/financial"
    if re.search(r"\b(not (?:be )?(?:permitted|allowed)|must not|shall not|until)\b", text, re.I):
        return "restriction"
    if _INTEGRATION.search(text):
        return "integration"
    if _REPORT.search(text):
        return "reporting"
    if re.search(r"\brenamed\b|\bproperty\b|\bcolumn\b|\bfilter\b|\bfield\b", text, re.I):
        return "field/UI change"
    if _WORKFLOW.search(text):
        return "workflow"
    if _UI.search(text):
        return "UI"
    return "behaviour"


PRIORITY_WEIGHT = {"High": 3, "Medium": 2, "Low": 1}
_MONEY = re.compile(r"\b(amount|balance|bank guarantee|percentage|percent|variance|deduct\w*|reserv\w*|payment|fee|refund|"
                    r"charge|calculat\w*|adjust\w*)\b", re.I)


def unit_priority(text: str, kind: str) -> str:
    if kind == "navigation":
        return "Low"
    if kind == "reporting":
        return "Medium"
    if kind in ("calculation/financial", "restriction", "integration", "mapping") or _MONEY.search(text):
        return "High"
    if kind == "workflow" and re.search(r"\b(submit|approv|reject|assign|mark)", text, re.I):
        return "High"
    return "Medium"


def _is_lead_in(sentence: str) -> bool:
    return sentence.rstrip().endswith(":") or bool(re.search(r"(?:^|\bthe )following\b", sentence.strip(), re.I))


def coverage_units(requirement: dict, rules: Optional[List[dict]] = None) -> List[dict]:
    """Atomic, independently testable units of one requirement.

    * every behaviour statement (lead-ins such as "The following changes will be made:" are not units);
    * every list item that follows a lead-in line ("Cess Bank Scroll Report");
    * every mapping class (target + category) found by ``business_rules``.
    """
    ref = requirement.get("ref", "")
    units: List[dict] = []
    seen = set()

    def add(kind_prefix, text, kind=None, extra=None):
        k = key(text)
        if not k or k in seen:
            return
        seen.add(k)
        kind = kind or unit_kind(text)
        unit = {"id": f"{ref}.{kind_prefix}{sum(1 for u in units if u['id'].split('.')[-1].startswith(kind_prefix)) + 1}",
                "requirement": ref, "text": text.strip(), "kind": kind, "priority": unit_priority(text, kind),
                "terms": sorted(set(terms(text)))}
        if extra:
            unit.update(extra)
        units.append(unit)

    for statement in requirement.get("statements") or []:
        if _is_lead_in(statement) or re.match(r'^\s*["{\[]', statement) or '":' in statement:
            continue  # lead-in lines and sample payload lines (JSON) are not behaviour
        add("S", statement)
    # list items after a lead-in line
    lines = [ln.strip() for ln in str(requirement.get("text", "")).splitlines()]
    lead = False
    for line in lines:
        if not line:
            continue
        if " | " in line:
            lead = False
            continue
        if (line.endswith(":") and len(line) > 25) or _is_lead_in(line):
            lead = True
            continue
        if lead and len(line) <= 90 and not re.search(r"\b(shall|will|must|should)\b", line, re.I) and not line.endswith("."):
            if re.search(r"\b(report|screen|field|column|status|option|document|form|api)\b", line, re.I):
                add("L", line, "reporting" if _REPORT.search(line) else None)
            continue
        if re.search(r"\b(shall|will|must|should)\b", line, re.I):
            lead = line.endswith(":")
    for rule in rules or []:
        if rule["kind"] != "mapping":
            continue
        classes: Dict[str, List[str]] = {}
        for code, entry in rule["codes"].items():
            classes.setdefault(entry.get("category") or rule["heading"], []).append(code)
        for category, codes in classes.items():
            text = f"Codes under '{rule['heading']}' / '{category}' are assigned to {rule['target']}"
            add("M", text, "mapping", {"codes": sorted(codes), "target": rule["target"], "rule": rule["id"],
                                         "basis": rule["basis"]})
    return units


def units_covered(case: dict, units: List[dict]) -> List[str]:
    """Ids of the units a case covers: its quoted statement, a list item it names, or a mapping class it tests."""
    statement = key(case.get("source_evidence") or case.get("source_statement") or "")
    text = " ".join(str(case.get(k, "")) for k in ("scenario", "test_case", "pre_conditions", "steps", "expected_result",
                                                     "test_data"))
    low = key(text)
    case_terms = set(terms(text))
    codes = set(re.findall(r"\b[A-Z][A-Z0-9]{2,7}\b", text)) | set(case.get("parameter_values") or [])
    covered = []
    for unit in units:
        if unit["kind"] == "mapping":
            if codes & set(unit.get("codes") or []) and re.search(r"\b" + re.escape(unit["target"]) + r"\b", text):
                covered.append(unit["id"])
            continue
        unit_key = key(unit["text"])
        if statement and (unit_key in statement or statement in unit_key) and len(statement) > 12:
            covered.append(unit["id"])
            continue
        if unit["id"].split(".")[-1].startswith("L") and unit_key in low:
            covered.append(unit["id"])
            continue
        wanted = set(unit["terms"])
        if len(wanted) >= 4 and len(wanted & case_terms) / len(wanted) >= 0.8:
            covered.append(unit["id"])
    return covered


# --------------------------------------------------------------------------- relevance

def relevance(case: dict, statement: str) -> Tuple[bool, float]:
    """The quoted statement must share the case's subject: overlap of the case title/scenario with the statement."""
    focus = set(terms(f"{case.get('test_case', '')} {case.get('scenario', '')} {case.get('expected_result', '')}"))
    have = set(terms(statement))
    if not focus or not have:
        return False, 0.0
    score = len(focus & have) / min(len(focus), len(have))
    return score >= 0.25, round(score, 2)


# --------------------------------------------------------------------------- evidence basis

_REJECTION_OUTCOME = re.compile(r"\b(error|validation|invalid|reject\w*|not (?:be )?(?:saved|accepted|allowed|permitted|processed|"
                                r"submitted|marked)|does not (?:mark|save|accept|allow|submit|process)|block\w*|prevent\w*|"
                                r"refus\w*|denied|warning|message)\b", re.I)
_DOC_REJECTION = re.compile(r"\b(not (?:be )?(?:permitted|allowed|able)|cannot|can't|must not|shall not|will not|reject\w*|"
                            r"error|invalid|block\w*|prevent\w*|denied|disabled|mandatory|required field|only if|only when)\b", re.I)
_CONDITION_CUE = re.compile(r"\b(?:invalid|incorrect|wrong|missing|incomplete|without|blank|empty|negative|expired|"
                            r"duplicate|unauthori[sz]ed|less than|below|exceed\w*|above|more than|greater than|insufficient)\s+"
                            r"((?:the |an? )?[a-z][\w’'\-]*(?:\s+[a-z][\w’'\-]*){0,2})", re.I)


def basis(case: dict, requirement: dict, statement: str, rule_verdict: str) -> dict:
    """Documented / Derived / Assumption for one case, with the reasons and illustrative-data labels."""
    req_text = str(requirement.get("text", ""))
    req_low = req_text.lower()
    body = " . ".join(str(case.get(k, "")) for k in ("test_case", "pre_conditions", "steps", "scenario"))
    expected = str(case.get("expected_result", ""))
    reasons, labels = [], []
    category = "documented"

    # 1. a rejection / error outcome must be stated by the document for that condition
    if _REJECTION_OUTCOME.search(expected):
        conditions = [m.group(1).strip().lower() for m in _CONDITION_CUE.finditer(body)]
        conditions = [re.sub(r"^(?:the|an?)\s+", "", c) for c in conditions]
        rejection_statements = [s for s in (requirement.get("statements") or []) if _DOC_REJECTION.search(s)]
        stated = False
        for cond in conditions or [""]:
            cond_terms = [t for t in terms(cond) if t not in ("amount", "value", "information", "data")] or terms(cond)
            for sentence in rejection_statements:
                if cond_terms and all(t in " ".join(terms(sentence)) for t in cond_terms[:2]):
                    stated = True
                    break
            if stated:
                break
        if rule_verdict == "consistent" and not conditions:
            stated = True
        if not conditions and _DOC_REJECTION.search(str(statement or "")):
            stated = True  # the quoted statement itself names the refusal/rejection ("approve or reject the request")
        if not stated:
            category = "assumption"
            reasons.append("The document does not state a rejection, error or validation outcome for the tested condition "
                           f"(\"{str(case.get('test_case', ''))[:110]}\"); the expected behaviour is assumed and needs "
                           "business confirmation.")
    # 2. an 'invalid <field>' concept the document never defines
    invalid_fields = [m.group(1) for m in re.finditer(r"\binvalid[ \t]+((?!the\b|a\b|an\b)[A-Za-z][\w\-]*(?:[ \t]+[A-Za-z][\w\-]*)?)", body, re.I)]
    invalid_fields += [m.group(1) for m in re.finditer(r"\b(?:the |an? )?([A-Za-z][\w\-]*(?: [A-Za-z][\w\-]*)?) (?:is|are) invalid\b", body, re.I)]
    for field in invalid_fields:
        field = field.strip()
        if "invalid" not in req_low and not re.search(r"\b(valid|format|digits?|characters?|length|numeric)\b", req_low):
            category = "assumption"
            reasons.append(f"'Invalid {field}' is not defined by the document (no format, length or validation rule is stated).")
            break
    # 3. quoted values (messages, statuses, labels) that are not in the document
    for value in re.findall(r"[‘'“\"]([^‘’'“”\"\n]{3,80})[’'”\"]", body + " " + expected):
        if value.strip().lower() not in req_low and value.strip().lower() not in str(statement).lower():
            category = "assumption"
            reasons.append(f"The quoted text '{value.strip()[:60]}' does not appear in the document.")
    # 4. numbers in the case that the document does not state = illustrative test data
    doc_numbers = set(re.findall(r"\d[\d,]*", req_text))
    case_numbers = [n for n in re.findall(r"(?<![\w-])\d[\d,]*(?:\.\d+)?(?![\w-])", body + " " + expected)
                    if n.strip(",") not in doc_numbers and not re.match(r"^\d$", n)]
    step_numbers = set(re.findall(r"(?m)^\s*(\d+)[.)]", str(case.get("steps", ""))))
    case_numbers = [n for n in case_numbers if n not in step_numbers]
    if case_numbers:
        derived_from_threshold = rule_verdict in ("consistent",) or bool(re.search(
            r"\b(at least|at most|minimum|maximum|equals? or exceeds?|less than|greater than|threshold|limit)\b", req_low))
        labels.append("Illustrative test data (not stated in the document): " + ", ".join(dict.fromkeys(case_numbers)))
        if category == "documented":
            category = "derived" if derived_from_threshold else "documented"
            if derived_from_threshold:
                reasons.append("Boundary values are derived from the documented comparison; the concrete numbers are illustrative.")
    # 5. a user role the document never names (e.g. "Log in as the system administrator" when only officers are named)
    doc_nouns = {r.split()[-1] for r in roles_in(req_text)}
    if doc_nouns:
        for role in roles_in(body):
            if role.split()[-1] not in doc_nouns:
                category = "assumption"
                reasons.append(f"The role '{role}' is not named in the document (the document names: "
                               + ", ".join(sorted(roles_in(req_text))[:4]) + ").")
                break
    # 6. a positive/negative counterpart of a documented condition (derived, not documented)
    if category == "documented" and re.search(r"\b(?:not|no|without|positive|zero|settled|after)\b", body, re.I) and rule_verdict == "consistent":
        if not key(statement) or not any(w in key(body) for w in key(statement).split()[:3]):
            category = "derived"
            reasons.append("The test condition is the logical counterpart of a documented rule.")
    return {"category": category, "reasons": list(dict.fromkeys(reasons)), "labels": labels}


# --------------------------------------------------------------------------- expected-result quality

_VAGUE = re.compile(
    r"^(?:the )?(?:system|application|it|request|api|process|page|screen|function(?:ality)?)?\s*(?:should |shall |will |is |are )?"
    r"(?:works?|behaves?|functions?|responds?|processe?s?d?|handled?|performs?)\s*(?:correctly|properly|as expected|successfully|fine|"
    r"normally|accordingly)?\.?$|^as expected\.?$|^(?:success|successful(?:ly)?|pass(?:ed)?|ok)\.?$|"
    r"^(?:the )?correct (?:result|output|data|value|information) is (?:displayed|shown|returned)\.?$|"
    r"^(?:the )?(?:request|transaction|operation) is (?:processed|completed) successfully\.?$|"
    r"^(?:the )?api responds? (?:properly|correctly|successfully)\.?$", re.I)
_RESTATE = re.compile(r"\b(?:behaves?|works?|functions?|happens?|occurs?|is (?:done|handled))\s+as\s+(?:stated|described|defined|"
                      r"specified|documented|mentioned|per)\b|\bas (?:stated|described|specified) in the (?:requirement|document|crf|srs)\b|"
                      r"\bas per (?:the )?(?:requirement|document|crf|srs|specification)\b(?!\s+\w+ing)|"
                      r"\bin line with the requirement\b|\bmeets the requirement\b", re.I)
_OBSERVABLE = re.compile(
    r"\b(display\w*|shown|show[sn]?|appear\w*|visible|list\w*|populat\w*|sav\w*|stor\w*|persist\w*|mark\w*|assign\w*|sent|send[s]?|"
    r"shar\w*|transmit\w*|receiv\w*|notif\w*|reject\w*|block\w*|prevent\w*|approv\w*|generat\w*|includ\w*|contain\w*|updat\w*|"
    r"deduct\w*|adjust\w*|reserv\w*|appl(?:y|ied|ies)|return\w*|fetch\w*|retriev\w*|add(?:ed|s)?|renam\w*|enabl\w*|"
    r"disabl\w*|filter\w*|status|equal\w*|match\w*|redirect\w*|open\w*|navigat\w*|download\w*|export\w*|creat\w*|remov\w*|"
    r"exempt\w*|not permitted|not allowed|records?|value|field|column|message|error|response|report)\b", re.I)


def expected_quality(case: dict, statement: str) -> dict:
    """Return {ok, blocking, issues}. Blocking issues reject automatic acceptance."""
    expected = str(case.get("expected_result", "")).strip()
    issues, blocking = [], False
    if _VAGUE.match(expected):
        issues.append("Expected Result is vague (does not state an observable outcome).")
        blocking = True
    if _RESTATE.search(expected):
        issues.append("Expected Result refers to the requirement instead of stating the observable outcome.")
        blocking = True
    if not _OBSERVABLE.search(expected):
        issues.append("Expected Result names no observable output, field, status or integration result.")
        blocking = True
    if re.search(r"\b(?:correct|right|proper|appropriate|relevant|expected|accordingly)\b", expected, re.I):
        context = " ".join(str(case.get(k, "")) for k in ("steps", "pre_conditions"))
        specific = [t for t in re.findall(r"[‘'“\"][^‘’'“”\"]{2,40}[’'”\"]|\b[A-Z]{2,}[A-Za-z]*\b|\b\d[\d,.%]*\b", expected)
                    if t.strip("‘’'“”\"") not in context]
        if not specific:
            issues.append("Expected Result says 'correct/appropriate/accordingly' without naming the expected value or receiver.")
            blocking = True
    if re.search(r"\b(?:approved|accepted|allowed|passed|saved|displayed|sent|submitted|granted)\s+or\s+(?:rejected|denied|blocked|"
                 r"failed|declined|refused)\b|\b(?:rejected|denied|blocked|failed|declined|refused)\s+or\s+(?:approved|accepted|"
                 r"allowed|passed|granted)\b", expected, re.I):
        issues.append("Expected Result allows opposite outcomes (e.g. 'approved or rejected'); a test needs one expected outcome.")
        blocking = True
    exp_terms, src_terms = terms(expected), terms(statement)
    if src_terms and jaccard(exp_terms, src_terms) >= 0.85 and re.search(r"\b(shall|will|should|must)\b", expected, re.I):
        issues.append("Expected Result copies the requirement sentence (shall/will wording) instead of the observed result.")
        blocking = True
    action_terms = {t[:4] for t in terms(" ".join(str(case.get(k, "")) for k in ("test_case", "steps", "pre_conditions", "scenario")))}
    if exp_terms and action_terms and not ({t[:4] for t in exp_terms} & action_terms):
        issues.append("Expected Result does not relate to the test steps or the test objective.")
        blocking = True
    return {"ok": not issues, "blocking": blocking, "issues": issues}


# --------------------------------------------------------------------------- completeness

_ROLE_NOUN = re.compile(r"\b(officers?|agents?|traders?|administrators?|admins?|approvers?|supervisors?|operators?|clerks?|"
                        r"managers?|exporters?|importers?|analysts?)\b", re.I)
_ROLE_SKIP = {"the", "a", "an", "any", "of", "with", "to", "by", "for", "and", "or", "user", "system", "shall", "will",
              "active", "associated", "relevant", "new"}


def _role_matches(text: str) -> List[str]:
    found = []
    for match in _ROLE_NOUN.finditer(text):
        noun = match.group(1).lower().rstrip("s")
        before = re.findall(r"([A-Za-z]+)\s*$", text[:match.start()])
        prefix = before[0] if before else ""
        if prefix and prefix.lower() not in _ROLE_SKIP and (prefix[0].isupper() or prefix.isupper()):
            role = f"{prefix.lower()} {noun}"
        else:
            role = noun
        if role not in found:
            found.append(role)
    return found


_ACTOR_STEP = re.compile(r"^(?P<num>\s*\d+[.)]\s*)(?P<who>(?:[Tt]he|[Aa]n?)\s+(?:[A-Z][\w-]*\s+)?(?i:user|tester|"
                         + _ROLE_NOUN.pattern[3:-3] + r"))\s+(?P<verb>[a-z]+)\b(?P<rest>.*)$")
_OBSERVE_VERBS = {"receives", "sees", "gets", "views", "is", "has", "can", "cannot"}


def _base_verb(verb: str) -> str:
    if verb.endswith("ies") and len(verb) > 4:
        return verb[:-3] + "y"
    if verb.endswith(("sses", "shes", "ches", "xes", "zes")):
        return verb[:-2]
    if verb.endswith("s") and not verb.endswith("ss"):
        return verb[:-1]
    return verb


def actor_steps_to_tester(steps: str) -> Tuple[str, int]:
    """'2. The PRA officer approves the request' -> '2. As the PRA officer, approve the request'.

    Only steps whose subject is a person (user, tester or a role noun) are rewritten; steps that describe what the
    system or another system does are left alone (they stay a finding). Returns (steps, number rewritten)."""
    out, changed = [], 0
    for line in str(steps or "").splitlines():
        match = _ACTOR_STEP.match(line)
        verb = match.group("verb") if match else ""
        if not match or not verb.endswith("s") or verb in ("is", "has", "was"):
            out.append(line)
            continue
        who = match.group("who")
        who = "the " + who.split(None, 1)[1] if who.split(None, 1)[0].lower() in ("a", "an") else who[0].lower() + who[1:]
        if verb in _OBSERVE_VERBS:
            out.append(f"{match.group('num')}Check that {who} {verb}{match.group('rest')}")
        else:
            out.append(f"{match.group('num')}As {who}, {_base_verb(verb)}{match.group('rest')}")
        changed += 1
    return "\n".join(out), changed


# "The system approves ...", "PSW receives ...", "The declaration is cleared" = a description, not a tester action.
_DESCRIPTIVE_STEP = re.compile(r"^(?:(?:[Tt]he|[Aa]n?)\s+\w+|[A-Z][A-Za-z]*[A-Z][A-Za-z]*\s+(?!to\b)[a-z]\w*(?:s|ed)\b)")
_GENERIC_PRE = re.compile(r"^(?:the )?user is (?:signed|logged) in(?: to the application)?(?: with access to this function)?\.?$", re.I)


def roles_in(text: str) -> List[str]:
    found = _role_matches(str(text or ""))
    return [r for r in found if not any(o != r and o.endswith(" " + r) for o in found)]


def completeness(case: dict, requirement: dict) -> dict:
    steps = [ln for ln in str(case.get("steps", "")).splitlines() if ln.strip()]
    pre = str(case.get("pre_conditions", "")).strip()
    issues, blocking, dependencies = [], False, []
    generic_pre = not pre or bool(_GENERIC_PRE.match(pre))
    if len(steps) < 2:
        issues.append("Only one test step was written.")
        if generic_pre:
            blocking = True
            issues.append("Steps and preconditions are not sufficient to execute the case.")
    if generic_pre:
        issues.append("Preconditions are generic (no role, data or state is named).")
    texts = [re.sub(r"^\s*\d+[.)]\s*", "", ln).strip() for ln in steps]
    descriptive = [t for t in texts if _DESCRIPTIVE_STEP.match(t)]
    if texts and len(descriptive) == len(texts):
        blocking = True
        issues.append("The steps describe what the system does, not what the tester does (no executable tester action).")
    elif descriptive:
        issues.append(f"{len(descriptive)} step(s) describe system behaviour instead of a tester action.")
    doc_roles = roles_in(requirement.get("text", ""))
    case_roles = roles_in(" ".join(str(case.get(k, "")) for k in ("pre_conditions", "steps", "test_case")))
    if doc_roles and not case_roles and not re.search(r"\b(?:logged|signed) in as\b|\brole\b", pre, re.I):
        issues.append("Preconditions do not name the user role (the document names: " + ", ".join(doc_roles[:4]) + ").")
    body = " ".join(str(case.get(k, "")) for k in ("pre_conditions", "steps"))
    if re.search(r"\b(?:weboc|external|third[- ]party|bank|api)\b", body, re.I) and not re.search(
            r"\b(?:available|reachable|connected|stub|mock|configured|integration)\b", pre, re.I):
        dependencies.append("Integration dependency: the external system named in the steps must be available (environment).")
    if re.search(r"\b(?:declaration|ntn|record|request|bg|guarantee|account|profile)\b", body, re.I) and not re.search(
            r"\b(?:exist\w*|available|has|have|with|created|submitted|assigned|registered|associated)\b", pre, re.I):
        dependencies.append("Test-data dependency: the business record used in the steps must exist before the test.")
    return {"ok": not blocking and len(issues) == 0, "blocking": blocking, "issues": issues, "dependencies": dependencies}


# --------------------------------------------------------------------------- execution type

_UI_ACT = re.compile(r"^\s*(?:\d+[.)]\s*)?(?:(?:attempt|try|tries|attempts) to\s+)?(?:click|select|choose|enter|type|tick|untick|check|navigate|open|go to|log ?in|log into|"
                     r"sign in|search|filter|upload|submit|press|fill|scroll|expand|view|observe the|verify (?:that )?the (?:screen|page|"
                     r"column|field|button|grid|list))\b", re.I)
_API_ACT = re.compile(r"^\s*(?:\d+[.)]\s*)?(?:send|call|invoke|post|get|put|patch|delete|trigger|issue|fetch|retrieve|request|"
                      r"read|inspect)\b[^\n]*\b(?:api|endpoint|method id|service|payload|http|response)\b|"
                      r"^\s*(?:\d+[.)]\s*)?(?:send|call|invoke|post|put|patch|issue)\b[^\n]*\brequest\b|\b(?:api|endpoint)\b[^\n]*\b(?:with|using) (?:the )?(?:payload|body|parameters?)\b|"
                      r"^\s*(?:\d+[.)]\s*)?(?:inspect|check|verify) the (?:api|json|http) response\b", re.I)
_SQL_ACT = re.compile(r"^\s*(?:\d+[.)]\s*)?(?:run|execute|query|select \*|connect to the database)\b[^\n]*\b(?:sql|query|table|database)\b|"
                      r"\bselect\b[^\n]+\bfrom\b", re.I)
_MANUAL_CUE = re.compile(r"\b(e-?mail|sms|phone|physical|print(?:ed|out)?|offline|committee|visual(?:ly)?|bank branch|"
                         r"another system|other system|in weboc|weboc (?:user|officer|screen|portal)|clearance of|is cleared|"
                         r"manually|human|judg\w*)\b", re.I)
_API_OBJECTIVE = re.compile(r"\b(api response|response (?:body|payload|contains|includes)|json|status code|method id|property)\b", re.I)
_DB_OBJECTIVE = re.compile(r"\b(database|table|column value|stored in|persisted in|record in the|sql)\b", re.I)


def classify_execution(case: dict, hint: str = "", user_choice: str = "") -> Tuple[str, Optional[str]]:
    """Execution type from the OBJECTIVE and the VERIFICATION METHOD, not from single keywords.

    ``user_choice`` (an explicit choice by a person) is always kept. The model's ``hint`` is kept only when the steps
    and the expected result support it.
    """
    if user_choice in ("Manual", "Playwright", "API", "SQL"):
        return user_choice, None
    steps = [ln for ln in str(case.get("steps", "")).splitlines() if ln.strip()]
    expected = str(case.get("expected_result", ""))
    objective = f"{case.get('test_case', '')} {case.get('scenario', '')}"
    ui = sum(1 for s in steps if _UI_ACT.search(s))
    api = sum(1 for s in steps if _API_ACT.search(s))
    sql = sum(1 for s in steps if _SQL_ACT.search(s))
    manual = len(_MANUAL_CUE.findall(" ".join(steps) + " " + expected + " " + str(case.get("pre_conditions", ""))))
    api_objective = bool(_API_OBJECTIVE.search(objective + " " + expected))
    db_objective = bool(_DB_OBJECTIVE.search(objective + " " + expected))
    sql_objective = db_objective or (steps and sql * 2 >= len(steps))
    if api and (api >= ui) and (api_objective or not ui):
        computed = "API"
    elif sql and sql_objective and not ui and not api:
        computed = "SQL"
    elif ui and manual and not api:
        computed = "Manual"   # browser steps, but the result must be checked in another system or by a person
    elif ui:
        computed = "Playwright"
    else:
        computed = "Manual"
    # Manual is kept only when the case cannot be checked by an available tool: no browser/API/SQL steps, or the
    # result must be verified in another system or by a person
    supported = {"Playwright": ui > 0 and not manual, "API": api > 0, "SQL": sql > 0 and bool(sql_objective),
                 "Manual": manual > 0 or not (ui or api or sql)}
    if hint in supported and supported[hint]:
        return hint, None
    if hint in supported and hint != computed:
        why = {"API": "no step sends a request to an API", "SQL": "no step queries the database",
               "Playwright": "the steps are not browser actions or the result must be checked outside the browser",
               "Manual": "the steps are browser actions whose result is visible on the screen"}[hint]
        return computed, (f"Execution type set to {computed}: the steps do not support the suggested {hint}"
                          + (f" ({why})." if why else "."))
    return computed, None


# --------------------------------------------------------------------------- duplicates

def parameter_values(case: dict, mapping_codes: set) -> List[str]:
    text = " ".join(str(case.get(k, "")) for k in ("scenario", "test_case", "pre_conditions", "steps", "expected_result"))
    values = [c for c in re.findall(r"\b[A-Z][A-Z0-9]{2,7}\b", text) if c in mapping_codes]
    return sorted(set(values))


def _strip_values(text: str, values: List[str]) -> str:
    for value in values:
        text = re.sub(r"\b" + re.escape(value) + r"\b", "<v>", text)
    return text


# Functional and Positive both check that the documented behaviour happens; for duplicate detection they are one class.
_TYPE_CLASS = {"Functional": "documented_behaviour", "Positive": "documented_behaviour"}


def type_class(name: str) -> str:
    return _TYPE_CLASS.get(name, name)


def behaviour_key(case: dict, values: List[str]) -> Tuple[str, str, str, frozenset]:
    """(requirement, statement, polarity/type, outcome words) with the parameter values removed."""
    statement = key(case.get("source_evidence", ""))[:160]
    outcome = frozenset(terms(_strip_values(str(case.get("expected_result", "")), values)))
    return (case.get("requirement_ref", ""), statement, (case.get("test_types") or [""])[0], outcome)


_NEGATION = re.compile(r"\b(?:not|no|never|cannot|without)\b", re.I)
# generic outcome pairs: a case expecting one side and a case expecting the other test different conditions
_OPPOSITES = [("approv", "reject"), ("accept", "declin"), ("allow", "den"), ("enabl", "disabl"), ("success", "fail"),
              ("pass", "fail"), ("valid", "invalid"), ("grant", "revok"), ("open", "clos"), ("lock", "unlock")]


def opposite_outcomes(a: dict, b: dict) -> bool:
    """True when the two cases expect opposite outcomes (one negated, or the two sides of an approve/reject pair)."""
    ea, eb = str(a.get("expected_result", "")), str(b.get("expected_result", ""))
    if bool(_NEGATION.search(ea)) != bool(_NEGATION.search(eb)):
        return True
    ta = " ".join([ea, str(a.get("test_case", ""))]).lower()
    tb = " ".join([eb, str(b.get("test_case", ""))]).lower()
    for left, right in _OPPOSITES:
        a_left, a_right = bool(re.search(r"\b" + left, ta)), bool(re.search(r"\b" + right, ta))
        b_left, b_right = bool(re.search(r"\b" + left, tb)), bool(re.search(r"\b" + right, tb))
        if (a_left and not a_right and b_right and not b_left) or (a_right and not a_left and b_left and not b_right):
            return True
    return False


_QUOTED = re.compile(r"[‘'“\"]([^‘’'“”\"\n]{2,60})[’'”\"]")


def different_subjects(a: dict, b: dict) -> bool:
    """True when the cases name different quoted items (another property, field, report or status)."""
    def quoted(c):
        text = " ".join(str(c.get(k, "")) for k in ("test_case", "expected_result"))
        return {key(v) for v in _QUOTED.findall(text) if key(v)}
    qa, qb = quoted(a), quoted(b)
    return bool(qa and qb and qa != qb and not (qa <= qb or qb <= qa))


def classify_pair(a: dict, b: dict, codes: set) -> str:
    """exact_duplicate | semantic_duplicate | parameterized_variation | distinct."""
    def norm(c):
        return key(" ".join(str(c.get(k, "")) for k in ("test_case", "steps", "expected_result")))
    if norm(a) == norm(b) or (key(a.get("steps")) == key(b.get("steps")) and key(a.get("expected_result")) == key(b.get("expected_result"))):
        return "exact_duplicate"
    # a different side of a threshold (below / equal / above) or the opposite outcome is a different test condition
    if br.case_comparison(a) != br.case_comparison(b):
        return "distinct"
    if opposite_outcomes(a, b) or different_subjects(a, b):
        return "distinct"
    va, vb = parameter_values(a, codes), parameter_values(b, codes)
    ka, kb = behaviour_key(a, va + vb), behaviour_key(b, va + vb)
    same_rule = ka[0] == kb[0] and (ka[1] == kb[1] or jaccard(ka[1].split(), kb[1].split()) >= 0.8)
    same_outcome = jaccard(ka[3], kb[3]) >= 0.6
    same_type = type_class(ka[2]) == type_class(kb[2])
    # the same documented value with the same outcome in the same requirement checks the same thing, even when the
    # model quoted a neighbouring sentence of that rule as the source
    if va and set(va) == set(vb) and ka[0] == kb[0] and same_outcome and same_type:
        return "semantic_duplicate"
    if same_rule and same_outcome and same_type:
        if va and vb and set(va) != set(vb):
            return "parameterized_variation"
        if set(va) == set(vb):
            title_sim = jaccard(terms(_strip_values(str(a.get("test_case", "")), va)),
                                terms(_strip_values(str(b.get("test_case", "")), vb)))
            cond_sim = jaccard(terms(str(a.get("pre_conditions", "")) + " " + str(a.get("steps", ""))),
                               terms(str(b.get("pre_conditions", "")) + " " + str(b.get("steps", ""))))
            if va or title_sim >= 0.6 or cond_sim >= 0.6:
                return "semantic_duplicate"
    return "distinct"


# --------------------------------------------------------------------------- status

STATUS_ORDER = ["Accepted", "Needs Review", "Contradiction Detected", "Unsupported", "Duplicate"]


def overall_status(gates: Dict[str, dict]) -> Tuple[str, List[str]]:
    """Accepted only when every mandatory gate passes; reasons list what blocked it."""
    reasons = []
    if gates.get("logic", {}).get("verdict") == "contradiction":
        return "Contradiction Detected", [f["message"] for f in gates["logic"].get("findings", []) if f["verdict"] == "contradiction"]
    if gates.get("source", {}).get("status") not in ("verified",):
        reasons.append("Source evidence is only an approximate match.")
    if not gates.get("relevance", {}).get("ok", True):
        reasons.append("The quoted statement does not clearly support what the case tests.")
    if gates.get("logic", {}).get("verdict") == "ambiguous":
        reasons.append("Business-rule check is ambiguous: " + "; ".join(
            f["message"][:160] for f in gates["logic"].get("findings", []) if f["verdict"] == "ambiguous")[:300])
    if gates.get("basis", {}).get("category") == "assumption":
        reasons.extend(gates["basis"].get("reasons") or ["Behaviour is assumed."])
    if gates.get("expected", {}).get("blocking"):
        reasons.extend(gates["expected"].get("issues") or [])
    if gates.get("completeness", {}).get("blocking"):
        reasons.extend(i for i in gates["completeness"].get("issues") or [] if "sufficient" in i or "Only one" in i
                       or "not what the tester does" in i)
    return ("Needs Review" if reasons else "Accepted"), reasons
