"""
Explainable question routing for the AI Assistant (Task 4 corrections).

Why this module exists
----------------------
The first Task 4 build chose a mode in ``assistant.keyword_route``:

* any question that matched no keyword fell through to LOCAL (``return "local"``),
  so a general software-engineering question was answered by retrieving unrelated
  enterprise documents and then abstaining (defect T4-010);
* GLOBAL always required live internet search, so it failed completely when the
  search providers were unreachable even for questions a local model can answer
  (defect T4-011).

Design
------
Routing is a pure, deterministic function of the question text, the explicit
scope and the settings. It makes NO LLM call and no network call. It separates
three independent sources of knowledge:

* enterprise documents (the knowledge base)  - needs retrieval
* general model knowledge                    - needs only the selected LLM
* live internet search                       - needs search access and permission

and returns the signals it saw, so every decision can be explained in the UI.
When the question is genuinely ambiguous the decision is made on EVIDENCE (a
retrieval probe) by the caller, never on a keyword alone.
"""

import re

# ------------------------------------------------------------------ signals

# Terms that name the organisation, its systems or its document types.
_STRONG_WORDS = (
    "psw", "weboc", "pitb", "crf", "srs", "brd", "frs", "sop", "esb",
    "single window", "knowledge base", "knowledge hub",
    "selected document", "selected documents", "selected knowledge",
    "uploaded", "attached document", "this document", "these documents",
    "our",
)

# Words that suggest project content but are ordinary English / QA vocabulary.
_WEAK_WORDS = (
    "requirement", "requirements", "validation", "validations", "business rule",
    "business rules", "workflow", "screen", "module", "integration", "test case",
    "test cases", "api", "specification", "acceptance criteria",
)

# File names (report.docx, SRS_v2.pdf ...).
_FILE = re.compile(r"\b[\w\-. ]{2,60}\.(?:docx?|pdf|xlsx?|csv|txt|md|json|xml)\b", re.I)

# Requirement / change-request identifiers: FR-12, REQ_004, CRF-AD-0017, SETN-C-001.
_ID = re.compile(r"\b[A-Z]{2,8}(?:[-_][A-Za-z0-9]{1,8})*[-_]\d{1,6}\b")

# Public standards / algorithms that look like identifiers but are not ours.
_PUBLIC_STD = frozenset((
    "SHA", "MD", "UTF", "ISO", "IEC", "RFC", "HTTP", "HTTPS", "TLS", "SSL", "AES",
    "RSA", "IEEE", "ANSI", "TCP", "UDP", "IPV", "ECMA", "WCAG", "OWASP", "CVE", "CWE",
    "NIST", "PCI", "DES", "SSH", "FIPS", "BASE", "HMAC", "ASCII", "PKCS", "X", "TLSV",
    "HTML", "CSS", "ES", "PEP", "JSR", "ARM", "USB", "GDPR", "SOC",
))

_GENERAL_PHRASES = (
    "difference between", "differences between", "what is", "what are",
    "what does", "define", "definition of", "explain", "how does", "how do",
    "how is", "how are", "why is", "why do", "why are", "best practice",
    "best practices", "advantages", "disadvantages", "benefits of",
    "pros and cons", "example of", "examples of", "compare", " vs ", "versus",
    "in general", "overview of", "introduction to", "principles of",
    "concept of", "meaning of", "types of", "steps to", "role of",
    "tell me about", "summarize the concept", "short points", "three points",
    "in simple terms", "in short",
)

# Things the model cannot know without live data.
_CURRENT = re.compile(
    r"\b(latest|newest|currently|today|tonight|right now|this (week|month|year)|"
    r"recent(ly)?|news|breaking|up[- ]to[- ]date|trending|price of|"
    r"stock (price|market|quote)|weather|exchange rate|release date|"
    r"released (in|on|today|this|last)|as of (20\d\d|today|now)|"
    r"current (version|release|price|status|rate|leader|ceo|president|prime minister|"
    r"champion|events|news|situation|law|regulation|tariff)|"
    r"who is the (current )?(ceo|president|prime minister|chairman|minister|head)|"
    r"live score|202[5-9])\b", re.I)

_EXPLICIT_WEB = re.compile(
    r"\b(search (the )?(web|internet|online)|look (it |this )?up (online|on the (web|internet))|"
    r"google (it|this)|on the (web|internet)|from the (web|internet)|online sources?|"
    r"web search|internet search)\b", re.I)

# Mixed questions: enterprise subject compared with / informed by general practice.
_MIXED = re.compile(
    r"\b(industry|common practice|best practice|standard practice|in general|generally|"
    r"compare[sd]? (with|to|against)|comparable|versus|vs\.?|benchmark|"
    r"recommended practice|international|public (documentation|standard))\b", re.I)

# Words that name a document-derived deliverable, not a concept.
_LISTING = re.compile(
    r"\b(list|enumerate|show|give|extract|summari[sz]e|summary of|all|every|each|complete|full)\b"
    r".{0,60}\b(requirements?|rules?|validations?|fields?|apis?|endpoints?|use cases?|"
    r"documents?|sections?|changes?|criteria|test cases?|dependencies|risks?)\b", re.I)
_BROAD = re.compile(
    r"\b(all|every|each|complete|entire|whole|full)\b.{0,40}\b(requirements?|rules?|validations?|"
    r"fields?|documents?|sections?|criteria|apis?)\b|"
    r"\b(list|enumerate)\b.{0,30}\b(the )?(requirements?|rules?|validations?|fields?|criteria)\b|"
    r"\bsummari[sz]e\b.{0,40}\b(document|crf|srs|requirements?|knowledge)\b|"
    r"\bwhat (are|is) (all )?the (requirements?|rules?|validations?)\b", re.I)

_GENERIC_NAMES = frozenset((
    "all", "global", "general", "testing", "test", "qa", "default", "common",
    "other", "others", "misc", "core", "main", "new", "demo", "sample",
))


def _has(text, phrase):
    return re.search(r"(?<![a-z0-9])" + re.escape(phrase) + r"(?![a-z0-9])", text) is not None


def analyze(prompt, known_terms=()):
    """Return the signals found in ``prompt`` (lower-case evidence lists)."""

    raw = prompt or ""
    q = raw.lower()

    strong = [w for w in _STRONG_WORDS if _has(q, w)]
    # "our" is only a cue when it refers to the organisation's own artefacts
    if "our" in strong:
        strong = [w for w in strong if w != "our"]
        if re.search(r"\bour\s+(system|integration|api|process|requirement|document|"
                         r"crf|srs|module|domain|platform|application|app|service|"
                         r"workflow|screen|database|project|team|data|policy)", q):
            strong.append("our (organisation's own artefacts)")
    strong += [m.group(0) for m in _FILE.finditer(raw)][:3]
    strong += [m.group(0) for m in _ID.finditer(raw)
               if re.split(r"[-_]", m.group(0))[0] not in _PUBLIC_STD][:3]

    weak = [w for w in _WEAK_WORDS if _has(q, w)]
    for term in known_terms or ():
        t = (term or "").strip().lower()
        if len(t) >= 4 and t not in _GENERIC_NAMES and _has(q, t):
            weak.append("hierarchy:" + t)

    general = [p.strip() for p in _GENERAL_PHRASES if p in q]
    current = [m.group(0) for m in _CURRENT.finditer(raw)][:3]
    explicit_web = bool(_EXPLICIT_WEB.search(raw))
    mixed = bool(_MIXED.search(raw))
    listing = bool(_LISTING.search(raw))

    return {
        "enterprise_strong": list(dict.fromkeys(strong)),
        "enterprise_weak": list(dict.fromkeys(weak)),
        "general": general,
        "current_info": current,
        "explicit_web": explicit_web,
        "mixed": mixed,
        "listing": listing,
        "broad": bool(_BROAD.search(raw)),
    }


def wants_search(signals, routing):
    """May / should live internet search be used for a GLOBAL (or HYBRID) answer?

    Returns (use_search, reason). The policy lives in Settings -> AI Routing:

    * ``when_needed`` (default): only when the question asks for current
      information or explicitly asks for a web search;
    * ``always``: every GLOBAL question is searched (previous behaviour);
    * ``never``: the selected LLM answers from its own knowledge.
    """

    if not routing.get("internet_search_enabled", True):
        return False, "Internet search is switched off in Settings."

    policy = (routing.get("global_search_policy") or "when_needed").lower()

    if policy == "never":
        return False, "Search policy is 'never': the selected model answers from its own knowledge."
    if policy == "always":
        return True, "Search policy is 'always'."
    if signals.get("explicit_web"):
        return True, "The question explicitly asks for a web search."
    if signals.get("current_info"):
        return True, ("The question asks for current information ("
                      + ", ".join(signals["current_info"][:2]) + ").")
    return False, "General question: the selected model's own knowledge is used; no search is needed."


def decide(prompt, requested, scope, routing, known_terms=()):
    """Choose the answering mode.

    Returns a dict::

        mode        local | global | hybrid
        auto        True when the choice was made by this function
        probe       True when LOCAL was chosen only tentatively and the caller
                    must escalate to GLOBAL if the knowledge base has no evidence
        reasons     human-readable explanation, in order
        signals     the raw signals (for the UI "How this answer was produced")
    """

    requested = (requested or "auto").lower()
    scope = scope or {}
    sig = analyze(prompt, known_terms)

    def out(mode, reason, auto=False, probe=False):
        return {"mode": mode, "auto": auto, "probe": probe,
                "reasons": [reason], "signals": sig}

    if requested in ("local", "global", "hybrid"):
        return out(requested, f"Mode chosen by user: {requested.upper()}.")

    default = (routing.get("default_mode") or "auto").lower()
    if default in ("local", "global", "hybrid"):
        return out(default, f"Default mode from settings: {default.upper()}.")

    # ---- AUTO --------------------------------------------------------------
    strong, weak = sig["enterprise_strong"], sig["enterprise_weak"]
    general, current = sig["general"], sig["current_info"]

    if scope:
        names = ", ".join(sorted(scope)[:4])
        return out("local",
                   f"Auto: a knowledge scope was selected ({names}), so only that "
                   "scope is searched (LOCAL).", auto=True)

    if strong and (sig["mixed"] or current or sig["explicit_web"]):
        return out("hybrid",
                   "Auto: the question refers to PSW/enterprise material ("
                   + ", ".join(strong[:2]) + ") and also asks for public or current "
                   "information, so HYBRID is used.", auto=True)

    if strong:
        return out("local",
                   "Auto: the question refers to PSW/enterprise material ("
                   + ", ".join(strong[:2]) + "), so the knowledge base is used (LOCAL).",
                   auto=True)

    if current or sig["explicit_web"]:
        return out("global",
                   "Auto: the question needs current/public information; no enterprise "
                   "document is referred to (GLOBAL).", auto=True)

    if general and not weak:
        return out("global",
                   "Auto: this looks like a general-knowledge question with no reference to "
                   "enterprise documents, so the selected model answers directly (GLOBAL). "
                   "No document retrieval was done.", auto=True)

    # Ambiguous: decide on evidence, not on a keyword.
    why = ("Auto: the question could refer to project documents "
           + ("(" + ", ".join(weak[:2]) + ") " if weak else "")
           + "or to general knowledge. The knowledge base is checked first; if it has "
             "no relevant evidence the selected model answers from general knowledge.")
    return out("local", why, auto=True, probe=True)


# ---------------------------------------------------------------- legacy API

def keyword_route(prompt):
    """Backwards-compatible helper kept for callers/tests of the first build.

    Same signature, but it now delegates to ``decide`` instead of defaulting
    every unmatched question to LOCAL."""

    d = decide(prompt, "auto", {}, {"default_mode": "auto"})
    return d["mode"]
