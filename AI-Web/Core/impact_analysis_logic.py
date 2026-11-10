"""
QA AI Studio
Impact Analysis - deterministic decision rules

QA-AI-STUDIO-IMPACT-ANALYSIS-ACCURACY: pure functions (no I/O, no model call) that sit around the
language model in Core/impact_analysis_service.py. The model proposes; these rules decide what is
allowed to stand:

  * which retrieved text counts as evidence about the TARGET system's behaviour (and which is the
    source change itself, an earlier version of it, or another change request),
  * whether a quoted excerpt really exists in the retrieved text,
  * whether the target already states the requirement (Already Covered), states something different
    (Confirmed Impact), or merely relates to it (Potential Impact),
  * whether two versions of a document really disagree,
  * whether an existing test case is related to a change.

Matching text is never treated as proof of an impact, and the absence of a match is never treated as
proof of no impact.

QA-AI-STUDIO-IMPACT-ANALYSIS-FINAL-VALIDATION: the evidence a target scope can offer depends on the analysis being run.
Independent evidence is admissible by kind - target baseline documents (SRS, design, API, integration ...), other change
requests (CRF to CRF, CRF to another system) and earlier versions of the same document (version comparison, handled by
prior_version_status). Only the source document itself, other versions of it used as "target behaviour", and sibling
documents of the same CRF number are never evidence.
"""

import re
import hashlib

CONFIRMED = "CONFIRMED IMPACT"
POTENTIAL = "POTENTIAL IMPACT"
ALREADY_COVERED = "ALREADY COVERED"
INSUFFICIENT = "INSUFFICIENT EVIDENCE"
NO_DOCUMENTED = "NO DOCUMENTED IMPACT"

CLASSIFICATIONS = (CONFIRMED, POTENTIAL, ALREADY_COVERED, INSUFFICIENT, NO_DOCUMENTED)

ROLE_SOURCE_ITSELF = "SOURCE_ITSELF"
ROLE_SOURCE_FAMILY = "SOURCE_FAMILY"
ROLE_SAME_REQUEST = "SAME_CHANGE_REQUEST"
ROLE_CHANGE_REQUEST = "CHANGE_REQUEST"
ROLE_BASELINE = "BASELINE"

# Roles whose text may be shown to the model and quoted as evidence. The other roles are never evidence of the target.
ADMISSIBLE_ROLES = (ROLE_BASELINE, ROLE_CHANGE_REQUEST)

ROLE_LABELS = {
    ROLE_SOURCE_ITSELF: "The source document itself (or an identical copy of it)",
    ROLE_SOURCE_FAMILY: "Another version of the source document (used only for version comparison)",
    ROLE_SAME_REQUEST: "A sibling document of the same change request or Knowledge version (not independent of the source)",
    ROLE_CHANGE_REQUEST: "Another change request (documents requested behaviour, not implemented behaviour)",
    ROLE_BASELINE: "Target baseline document",
}

EVIDENCE_KIND_LABELS = {
    ROLE_BASELINE: "target document",
    ROLE_CHANGE_REQUEST: "another change request",
}

TARGET_STATES = ("NOT_DOCUMENTED", "MISSING", "DIFFERENT", "ALREADY_IMPLEMENTED", "UNCLEAR")

CHANGE_REQUEST_TYPES = {"crf", "change request", "change request form", "change requests", "cr"}

_CHANGE_REQUEST_MARKERS = ("change request form", "change request number", "section a (to be filled in by the requestor)")

_STOP = frozenset("""
a an and are as at be been being by can could do does for from has have if in into is it its may must no not of on or
per shall should that the their then there these this those to under upon was were which while will with within would
also all any each both than such only same other new existing system user users data field fields change changes
changed requirement requirements required document section interface add added adding based via using use used
""".split())

_ID_PATTERN = re.compile(r"\b([A-Za-z]{2,6}-[A-Za-z]{2,6}-\d{2,5})\b")


# --------------------------------------------------
# Text helpers
# --------------------------------------------------

def normalize(text):
    text = str(text or "")
    text = text.replace("‘", "'").replace("’", "'").replace("“", '"').replace("”", '"')
    text = text.replace("–", "-").replace("—", "-").replace(" ", " ")
    return re.sub(r"\s+", " ", text).strip().lower()


def tokens(text):
    return re.findall(r"[a-z0-9_]+", normalize(text))


def key_terms(*texts):
    """Distinctive words and identifiers of a change (stop words and generic words removed)."""
    found = set()
    for text in texts:
        for word in re.findall(r"[A-Za-z0-9_]+", str(text or "")):
            low = word.lower()
            if low in _STOP or len(low) < 3:
                continue
            found.add(low)
            if re.search(r"[a-z][A-Z]", word):                        # QualityGrade_ID -> quality, grade
                for part in re.findall(r"[A-Z]?[a-z]+|\d+", word):
                    if len(part) >= 3 and part.lower() not in _STOP:
                        found.add(part.lower())
    return found


def containment(terms, text):
    """Share of ``terms`` that occur in ``text`` (0..1)."""
    terms = set(terms or ())
    if not terms:
        return 0.0
    present = set(tokens(text))
    return len(terms & present) / float(len(terms))


def jaccard(a, b):
    ta, tb = set(tokens(a)), set(tokens(b))
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / float(len(ta | tb))


def sentences(text):
    parts = re.split(r"(?<=[.;:!?])\s+|\n+", str(text or ""))
    return [p.strip() for p in parts if len(p.strip()) >= 25]


def relevant_text(terms, chunk_text, fallback=""):
    """The sentences of a chunk that talk about the change (share its key terms); contradictions are judged only on these,
    because a chunk may legitimately contain both 'enabled' and 'disabled' about different things."""
    picked = [s for s in sentences(chunk_text) if containment(terms, s) >= 0.4]
    return " ".join(picked) or fallback


# Settings whose polarity matters to a requirement. Each axis has an affirmative and an opposite vocabulary; a negation directly in
# front of a term ("not mandatory", "is not exempted", "not optional") flips it, so "not mandatory" and "optional" are the same setting.
_POLARITY_AXES = {
    "obligation": ("Mandatory / Not Mandatory", {"mandatory", "compulsory", "obligatory", "required"}, {"optional", "voluntary"},
                   ("Mandatory", "Not mandatory (optional)")),
    "applicability": ("Applicable / Not Applicable", {"applicable"}, {"inapplicable"}, ("Applicable", "Not applicable")),
    "exemption": ("Exempted / Not Exempted", {"exempt", "exempted", "exemption", "exempts"}, set(), ("Exempted", "Not exempted")),
    "permission": ("Allowed / Not Allowed", {"allowed", "permitted", "permissible", "allow", "permit"},
                   {"prohibited", "forbidden", "disallowed", "blocked", "barred"}, ("Allowed", "Not allowed")),
    "enablement": ("Enabled / Disabled", {"enabled", "enable"}, {"disabled", "disable"}, ("Enabled", "Disabled")),
}
_NEGATORS = frozenset(("not", "no", "never", "cannot", "without", "neither"))
_BREAKS = frozenset((",", ";", ":", ".", "(", ")", "|", "/", "[", "]"))


def _polarity_profile(text):
    """{axis: {+1, -1}} - which side of each setting a statement takes (+1 affirmative, -1 opposite), negations applied."""
    low = normalize(text)
    low = re.sub(r"\b(is|are|does|do|can|could|should|would|must|will|was|were|has|have|had)n't\b", r"\1 not", low)
    low = re.sub(r"\bn/a\b", "not applicable", low)
    low = re.sub(r"\bnon[- ]?(?=[a-z])", "not ", low)
    words = re.findall(r"[a-z]+|[,;:.()|/\[\]]", low)
    profile = {}
    for i, word in enumerate(words):
        for axis, (_, positive, negative, _) in _POLARITY_AXES.items():
            if word not in positive and word not in negative:
                continue
            sign = 1 if word in positive else -1
            for previous in reversed(words[max(0, i - 3):i]):
                if previous in _BREAKS:
                    break
                if previous in _NEGATORS:
                    sign = -sign
                    break
            profile.setdefault(axis, set()).add(sign)
    return profile


def polarity_changes(before_text, after_text):
    """Settings that are on opposite sides in the two statements (Mandatory / Not Mandatory, Applicable / Not Applicable, Exempted / Not
    Exempted, Optional / Not Optional, Allowed / Not Allowed, Enabled / Disabled). [{axis, label, before, after}]. A setting that only one
    statement mentions is not a polarity change."""
    a, b = _polarity_profile(before_text), _polarity_profile(after_text)
    found = []
    for axis, (label, _, _, words) in _POLARITY_AXES.items():
        sa, sb = a.get(axis), b.get(axis)
        if sa and sb and not (sa & sb):
            found.append({"axis": axis, "label": label, "before": words[0] if 1 in sa else words[1], "after": words[0] if 1 in sb else words[1]})
    return found


def polarity_note(changes):
    """Plain-language caution when more than one setting flips inside one sentence (it may restate the same rule from its other side)."""
    if len(changes or []) < 2:
        return ""
    return ("More than one setting is inverted in the same statement (%s). This can be the same rule restated from its other side, or a real change "
            "of behaviour; the documents alone cannot tell, so confirm the intended behaviour with the business analyst." % "; ".join("%s: %s to %s" % (c["label"], c["before"], c["after"]) for c in changes))


def has_polarity_terms(text):
    return bool(_polarity_profile(text))


def statement_differs(change_text, target_text):
    """True when the two statements contradict each other: a setting on opposite sides (mandatory / not mandatory, applicable / not applicable,
    exempted / not exempted, optional / not optional, allowed / not allowed, enabled / disabled), a different negation of the same condition,
    or a different number. Equal wording, or a target statement that is merely shorter, never differs."""
    a, b = tokens(change_text), tokens(target_text)
    if not a or not b:
        return False
    if polarity_changes(change_text, target_text):
        return True
    pa, pb = _polarity_profile(change_text), _polarity_profile(target_text)
    decided = any(axis in pb for axis in pa)               # the same setting appears in both and does not contradict
    sa, sb = set(a), set(b)
    overlap = len(sa & sb) / float(min(len(sa), len(sb)))
    if not decided and overlap >= 0.5 and (a.count("not") + a.count("no") + a.count("never")) % 2 != (b.count("not") + b.count("no") + b.count("never")) % 2:
        return True
    na, nb = {t for t in sa if t.isdigit()}, {t for t in sb if t.isdigit()}
    return bool(na and nb and not na <= nb)


# --------------------------------------------------
# Evidence roles
# --------------------------------------------------

def crf_ids(*names):
    out = set()
    for name in names:
        for match in _ID_PATTERN.findall(str(name or "")):
            out.add(match.upper())
    return out


def _same(a, b):
    return str(a if a is not None else "").strip().lower() == str(b if b is not None else "").strip().lower()


def is_change_request_document(meta, text=""):
    if str((meta or {}).get("asset_type") or "").strip():
        return False                       # an API collection / test-case file is an artefact, not a change request narrative
    document_type = str((meta or {}).get("document_type") or "").strip().lower()
    file_name = str((meta or {}).get("file_name") or "").strip().lower()
    if document_type in CHANGE_REQUEST_TYPES or file_name.startswith("crf"):
        return True
    head = normalize(text)[:600]
    return any(marker in head for marker in _CHANGE_REQUEST_MARKERS)


def evidence_role(meta, source, text=""):
    """What a retrieved target chunk actually is, relative to the selected source document.

    SOURCE_ITSELF / SOURCE_FAMILY / SAME_CHANGE_REQUEST are never evidence of the target. CHANGE_REQUEST (a different CRF)
    and BASELINE (anything else in the Knowledge Hub) are admissible; which one applies only changes how the answer is worded."""
    meta = meta or {}
    same_series = (_same(meta.get("domain"), source.get("domain")) and _same(meta.get("module"), source.get("module"))
                   and _same(meta.get("knowledge_name"), source.get("knowledge_name")))
    same_type = _same(meta.get("document_type"), source.get("document_type"))
    same_version_id = (meta.get("document_version_id") not in (None, "") and source.get("document_version_id") not in (None, "")
                       and str(meta.get("document_version_id")) == str(source.get("document_version_id")))
    same_file = _same(meta.get("file_name"), source.get("source_document"))

    # Files of one Knowledge version share a document_version_id, so the id alone does not make a chunk the source: the file must be the same.
    file_known = bool(str(meta.get("file_name") or "").strip() and str(source.get("source_document") or "").strip())
    if (same_version_id and (same_file or not file_known)) or (same_series and same_type and same_file and _same(meta.get("version"), source.get("version"))):
        return ROLE_SOURCE_ITSELF

    if same_series and same_type or same_version_id:
        if not _same(meta.get("version"), source.get("version")):
            return ROLE_SOURCE_FAMILY
        # Another file of the very same Knowledge version belongs to the same package as the source and is not independent of it -
        # except a non-narrative asset (an API collection, a test-case file ...), which is an independent artefact.
        if str(meta.get("asset_type") or "").strip():
            return ROLE_BASELINE
        return ROLE_SAME_REQUEST

    if crf_ids(meta.get("file_name")) & crf_ids(source.get("source_document")):
        return ROLE_SAME_REQUEST

    if is_change_request_document(meta, text):
        return ROLE_CHANGE_REQUEST

    return ROLE_BASELINE


COPY_MIN_CHARACTERS = 200


def tag_chunks(documents, metadata, source, source_chunks=None):
    """Role of every retrieved chunk. A chunk whose text is a (near) verbatim copy of a chunk of the source document is the source again
    - the same file filed under another type, version or system - and is never evidence, whatever it is filed as."""
    source_texts = [normalize(c.get("text") if isinstance(c, dict) else c) for c in source_chunks or []]
    source_texts = [t for t in source_texts if len(t) >= COPY_MIN_CHARACTERS]
    source_set = set(source_texts)
    chunks = []
    for index, (text, meta) in enumerate(zip(documents or [], metadata or [])):
        meta = dict(meta or {})
        role = evidence_role(meta, source, text)
        copy = False
        if role in ADMISSIBLE_ROLES:
            own = normalize(text)
            if len(own) >= COPY_MIN_CHARACTERS and (own in source_set or any(jaccard(own, t) >= 0.97 for t in source_texts)):
                role, copy = ROLE_SOURCE_ITSELF, True
        chunks.append({"text": text, "meta": meta, "role": role, "index": index, "copy_of_source": copy})
    return chunks


def role_counts(chunks):
    counts = {ROLE_BASELINE: 0, ROLE_CHANGE_REQUEST: 0, ROLE_SAME_REQUEST: 0, ROLE_SOURCE_FAMILY: 0, ROLE_SOURCE_ITSELF: 0}
    for chunk in chunks:
        counts[chunk["role"]] = counts.get(chunk["role"], 0) + 1
    return counts


# --------------------------------------------------
# Quotes
# --------------------------------------------------

def verify_quote(quote, chunks):
    """Find the chunk that really contains ``quote``. Returns (index, exact) or (None, False).

    A quote may use an ellipsis to skip text; every piece must be present in the same chunk. A quote whose words are
    almost all present in one chunk (copy/paste noise) is accepted as approximate, never as exact."""
    quote_n = normalize(quote).strip("\"' ")
    if len(quote_n) < 12:
        return None, False
    pieces = [p.strip(" .\"'") for p in re.split(r"…|\.\.\.", quote_n) if len(p.strip(" .\"'")) >= 12] or [quote_n]
    for i, chunk in enumerate(chunks):
        text = normalize(chunk.get("text") if isinstance(chunk, dict) else chunk)
        if all(piece in text for piece in pieces):
            return i, True
    words = tokens(quote_n)
    if len(words) >= 6:
        for i, chunk in enumerate(chunks):
            present = set(tokens(chunk.get("text") if isinstance(chunk, dict) else chunk))
            if sum(1 for w in words if w in present) / float(len(words)) >= 0.92:
                return i, False
    return None, False


# --------------------------------------------------
# Classification
# --------------------------------------------------

def normalize_target_state(value):
    state = re.sub(r"[^A-Z_]", "", str(value or "").strip().upper().replace(" ", "_").replace("-", "_"))
    aliases = {"ALREADY_COVERED": "ALREADY_IMPLEMENTED", "IMPLEMENTED": "ALREADY_IMPLEMENTED", "COVERED": "ALREADY_IMPLEMENTED",
               "NOT_PRESENT": "MISSING", "ABSENT": "MISSING", "CONFLICT": "DIFFERENT", "CONFLICTING": "DIFFERENT",
               "NONE": "NOT_DOCUMENTED", "UNKNOWN": "UNCLEAR"}
    state = aliases.get(state, state)
    return state if state in TARGET_STATES else "UNCLEAR"


def decide(change, parsed, baseline_chunks):
    """Final classification of one source change.

    ``parsed`` is the model's structured answer (or None). ``baseline_chunks`` are the only chunks the model was shown.
    Returns {classification, evidence_strength, target_state, reasons, evidence_index, evidence_exact, key_coverage, downgraded}.
    """
    change = change or {}
    reasons = []
    if parsed is None:
        return _out(INSUFFICIENT, "INSUFFICIENT", "UNCLEAR", ["The classification model did not return a usable answer, so no classification can be made."], None, False, 0.0, True)

    state = normalize_target_state(parsed.get("target_state"))
    llm_class = str(parsed.get("classification") or "").strip().upper()
    component = str(parsed.get("target_component") or "").strip()
    required = str(parsed.get("required_target_change") or "").strip()
    quote = parsed.get("target_evidence_quote") or ""
    if isinstance(quote, list):
        quote = " ... ".join(str(q) for q in quote)

    if "target_state" not in parsed:
        # An answer in the old shape: the model's own CONFIRMED claim is not enough to establish a required change.
        state = "UNCLEAR"
        reasons.append("The model did not state whether the target already implements, lacks or contradicts the change.")

    terms = key_terms(change.get("name"), change.get("change"))
    best_cover = max([containment(terms, c["text"]) for c in baseline_chunks] or [0.0])

    index, exact = verify_quote(quote, baseline_chunks)
    kind = baseline_chunks[index].get("role", ROLE_BASELINE) if index is not None else ROLE_BASELINE
    who = "target baseline" if kind == ROLE_BASELINE else "other change request"
    verified_text = baseline_chunks[index]["text"] if index is not None else ""
    cover = containment(terms, verified_text) if index is not None else best_cover
    statement = relevant_text(terms, verified_text, quote if isinstance(quote, str) else "") if index is not None else ""

    if quote and index is None:
        reasons.append("The quoted target evidence does not appear in the retrieved target text, so it was not accepted.")
    elif quote and not exact:
        reasons.append("The quoted target evidence only approximately matches the retrieved text.")

    if state == "NOT_DOCUMENTED" or (llm_class == NO_DOCUMENTED and not component):
        if best_cover >= 0.5:
            reasons.append("The model found nothing related, but retrieved target text shares most key terms with the change; review manually.")
            return _out(POTENTIAL, "INDIRECT", state, reasons, index, exact, best_cover, True)
        reasons.append("The available target evidence was reviewed and nothing in it relates to this change.")
        return _out(NO_DOCUMENTED, "INSUFFICIENT", state, reasons, index, exact, best_cover, False)

    if state == "ALREADY_IMPLEMENTED":
        if index is None:
            reasons.append("Already-implemented was claimed without a quotable target statement.")
            return _out(INSUFFICIENT, "INSUFFICIENT", state, reasons, index, exact, cover, True)
        if cover >= 0.6 and not statement_differs(change.get("change") or change.get("name"), statement):
            reasons.append("The %s already states the requirement (verified quotation; key terms present; no difference in wording, numbers or conditions)." % who)
            return _out(ALREADY_COVERED, "DIRECT", state, reasons, index, exact, cover, llm_class == CONFIRMED)
        reasons.append("The target statement is related but does not clearly equal the changed requirement, so it is not accepted as already covered.")
        return _out(POTENTIAL, "INDIRECT", state, reasons, index, exact, cover, True)

    if state == "DIFFERENT":
        if index is None or not component or not required:
            reasons.append("A difference was claimed without a verified target quotation, a named target component and the required change.")
            return _out(POTENTIAL if index is not None else INSUFFICIENT, "INDIRECT" if index is not None else "INSUFFICIENT", state, reasons, index, exact, cover, True)
        if not statement_differs(change.get("change") or change.get("name"), statement) and cover >= 0.85:
            reasons.append("The target wording is equivalent to the requested change; a real difference was not demonstrated.")
            return _out(POTENTIAL, "INDIRECT", state, reasons, index, exact, cover, True)
        if not exact:
            reasons.append("The quotation is approximate, so the impact stays Potential until the wording is confirmed.")
            return _out(POTENTIAL, "INDIRECT", state, reasons, index, exact, cover, True)
        reasons.append("The %s states different behaviour for the same component (verified quotation) and the required change is stated." % who)
        return _out(CONFIRMED, "DIRECT", state, reasons, index, exact, cover, False)

    if state == "MISSING":
        if index is None or not component:
            reasons.append("The target component could not be tied to a verified target quotation.")
            return _out(INSUFFICIENT, "INSUFFICIENT", state, reasons, index, exact, cover, True)
        reasons.append("A related target component is documented, but the requested capability is not described there. The absence of a statement in the retrieved text is not proof, so this is Potential Impact.")
        return _out(POTENTIAL, "INDIRECT", state, reasons, index, exact, cover, llm_class == CONFIRMED)

    # UNCLEAR
    if index is not None and (component or best_cover >= 0.3):
        reasons.append("Related target text exists but does not establish whether the target must change.")
        return _out(POTENTIAL, "INDIRECT", state, reasons, index, exact, cover, llm_class == CONFIRMED)
    reasons.append("The retrieved target text does not allow a conclusion.")
    return _out(INSUFFICIENT, "INSUFFICIENT", state, reasons, index, exact, cover, llm_class in (CONFIRMED, POTENTIAL))


def _out(classification, strength, state, reasons, index, exact, cover, downgraded, basis="TARGET_EVIDENCE"):
    return {"classification": classification, "evidence_strength": strength, "target_state": state, "reasons": reasons,
            "evidence_index": index, "evidence_exact": exact, "key_coverage": round(cover, 2), "downgraded": bool(downgraded), "basis": basis}


def no_baseline_decision(chunk_roles, prior_versions_checked=()):
    """No independent evidence (target document, another change request) was retrieved for this change."""
    counts = chunk_roles or {}
    own = (counts.get(ROLE_SOURCE_ITSELF, 0) + counts.get(ROLE_SOURCE_FAMILY, 0) + counts.get(ROLE_SAME_REQUEST, 0))
    if own:
        reason = ("The only target text that matches is the source change request itself, other versions of it or sibling documents of the same change request "
                  "(%d chunk(s)). That is not independent evidence of how the target behaves, so impact cannot be assessed." % own)
    else:
        reason = "No target knowledge was retrieved for this change, so impact cannot be assessed."
    if prior_versions_checked:
        reason += (" The change was also compared with the earlier version(s) %s; that comparison shows only what changed in the document, not what the target does."
                   % ", ".join("v%s" % v for v in prior_versions_checked))
    return _out(INSUFFICIENT if own else NO_DOCUMENTED, "INSUFFICIENT", "NOT_DOCUMENTED", [reason], None, False, 0.0, False)


# --------------------------------------------------
# Scope / analysis type
# --------------------------------------------------

def scope_type(source, target):
    """How the target relates to the source: another system, another module, all modules, or the source's own module."""
    source, target = source or {}, target or {}
    if not _same(source.get("domain"), target.get("domain")):
        return "OTHER_SYSTEM"
    if not str(target.get("module") or "").strip():
        return "ALL_MODULES"
    return "SAME_MODULE" if _same(source.get("module"), target.get("module")) else "OTHER_MODULE"


SCOPE_LABELS = {"OTHER_SYSTEM": "another system", "ALL_MODULES": "all modules of the target system",
                "OTHER_MODULE": "another module", "SAME_MODULE": "the same module"}


def _type_name(meta):
    meta = meta or {}
    return str(meta.get("asset_type") or "").strip() or str(meta.get("document_type") or "").strip() or "document"


def analysis_label(source, target, evidence_chunks=(), version_status=None, basis="TARGET_EVIDENCE"):
    """A short, true description of the comparison that produced a finding, e.g. 'CRF to SRS (another system)'."""
    source_type = str((source or {}).get("document_type") or "").strip() or "Document"
    scope = SCOPE_LABELS[scope_type(source, target)]
    if basis == "VERSION_COMPARISON":
        status = version_status or {}
        return "%s version comparison (v%s to v%s)" % (source_type, status.get("prior_version", "?"), (source or {}).get("version", "?"))
    kinds = []
    for chunk in evidence_chunks or ():
        name = "CRF" if chunk.get("role") == ROLE_CHANGE_REQUEST and not str(chunk["meta"].get("asset_type") or "").strip() and _type_name(chunk["meta"]).lower() != "crf" else _type_name(chunk["meta"])
        if name not in kinds:
            kinds.append(name)
    return "%s to %s (%s)" % (source_type, ", ".join(kinds[:3]) if kinds else "no independent evidence", scope)


# --------------------------------------------------
# Versions
# --------------------------------------------------

def _series(meta):
    return (str(meta.get("domain") or ""), str(meta.get("module") or ""), str(meta.get("knowledge_name") or ""), str(meta.get("document_type") or ""))


def compare_versions(chunks):
    """Do chunks from different versions of the same document series actually disagree?

    Identical text in two versions is NOT a conflict. Text that is clearly the same passage (similar wording) but not equal is.
    """
    comparisons = []
    conflict = False
    seen = set()
    for i, a in enumerate(chunks):
        for b in chunks[i + 1:]:
            ma, mb = a["meta"], b["meta"]
            if _series(ma) != _series(mb) or _same(ma.get("version"), mb.get("version")):
                continue
            sim = jaccard(a["text"], b["text"])
            if sim < 0.5:
                continue
            versions = tuple(sorted((str(ma.get("version")), str(mb.get("version")))))
            identical = normalize(a["text"]) == normalize(b["text"])
            only_a = sorted(set(tokens(a["text"])) - set(tokens(b["text"])))[:12]
            only_b = sorted(set(tokens(b["text"])) - set(tokens(a["text"])))[:12]
            key = (versions, identical, tuple(only_a), tuple(only_b))
            if key in seen:
                continue
            seen.add(key)
            comparisons.append({"versions": list(versions), "status": "IDENTICAL" if identical else "DIFFERENT",
                                "file_names": sorted({str(ma.get("file_name")), str(mb.get("file_name"))}),
                                "terms_only_in_first": only_a if not identical else [], "terms_only_in_second": only_b if not identical else []})
            conflict = conflict or not identical
    return {"conflict": conflict, "comparisons": comparisons[:6]}


def version_key(version):
    """'1.2' / 'v1.2' / 'Version 2' -> (1, 2) / (1, 2) / (2,); None when the version has no number."""
    numbers = re.findall(r"\d+", str(version if version is not None else ""))
    return tuple(int(n) for n in numbers) if numbers else None


def version_order(a, b):
    """-1 if a is older than b, 1 if newer, 0 if equal, None if they cannot be ordered."""
    ka, kb = version_key(a), version_key(b)
    if ka is None or kb is None:
        return None
    width = max(len(ka), len(kb))
    ka, kb = ka + (0,) * (width - len(ka)), kb + (0,) * (width - len(kb))
    return (ka > kb) - (ka < kb)


def nearest_older_version(source_version, documents):
    """The documents of the newest version that is strictly older than ``source_version``, and that version's label."""
    older = [d for d in documents or [] if version_order(d.get("version"), source_version) == -1]
    if not older:
        return [], None
    best = max(older, key=lambda d: version_key(d.get("version")) + (0,) * 4)
    keep = [d for d in older if version_order(d.get("version"), best.get("version")) == 0]
    return keep, best.get("version")


COUNTERPART_MIN_SIMILARITY = 0.10


def select_counterpart(source_chunks, documents):
    """Which of several documents of one version is the earlier counterpart of the source document? A version folder can hold more than one file (a CRF and
    an integration document, say); comparing the source with all of them would report the other files' content as removed. The one that shares the most
    vocabulary with the source is chosen, and only if it shares enough to be plausible. Returns (document or None, the others, similarity)."""
    if not documents:
        return None, [], 0.0
    source_words = set(tokens(" ".join(str(c.get("text") or "") for c in source_chunks or []))) - _STOP
    scored = []
    for doc in documents:
        words = set(tokens(" ".join(str(c.get("text") or "") for c in doc.get("chunks") or []))) - _STOP
        scored.append((len(source_words & words) / float(len(source_words | words) or 1), doc))
    scored.sort(key=lambda item: -item[0])
    best_score, best = scored[0]
    others = [doc for _, doc in scored[1:]]
    if best_score < COUNTERPART_MIN_SIMILARITY:
        # A very short or heavily reworded document can share little vocabulary yet still be the counterpart: accept it when some sentence of the
        # source is clearly a rewording of one of its sentences.
        source_units = [tuple(tokens(t)) for t, _ in _units([{"text": c.get("text"), "chunk_number": 0} for c in source_chunks or []])][:300]
        for _, doc in scored:
            prior_units = [tuple(tokens(t)) for t, _ in _units(doc.get("chunks") or [])][:300]
            if any(len(set(a) & set(b)) / float(len(set(a) | set(b))) >= 0.45 for a in source_units for b in prior_units if a and b):
                return doc, [d for d in documents if d is not doc], round(best_score, 2)
        return None, others, round(best_score, 2)
    return best, others, round(best_score, 2)


def find_earlier_counterpart(source_version, documents, source_chunks):
    """The nearest earlier version that holds a counterpart of the source document, and that counterpart. Versions that hold only other documents are
    skipped and reported. {document, version, others (same-version files not compared), score, skipped [{version, files}]}."""
    remaining = [d for d in documents or [] if version_order(d.get("version"), source_version) == -1]
    skipped = []
    while remaining:
        group, version = nearest_older_version(source_version, remaining)
        if not group:
            break
        doc, others, score = select_counterpart(source_chunks, group)
        if doc is not None:
            return {"document": doc, "version": version, "others": others, "score": score, "skipped": skipped}
        skipped.append({"version": version, "files": [d.get("file_name") for d in group]})
        remaining = [d for d in remaining if version_order(d.get("version"), version) != 0]
    return {"document": None, "version": None, "others": [], "score": 0.0, "skipped": skipped}


def _tokens_contain(haystack_text, needle_text):
    h, n = " ".join(tokens(haystack_text)), " ".join(tokens(needle_text))
    return bool(n) and (" " + n + " ") in (" " + h + " ")


_LABEL = re.compile(r"^\s*(?:requirement|req\.?|item|point|clause)\s*\d+\s*[:.)-]\s*", re.I)
_TERMINATOR_END = re.compile(r"[.;:!?\n]\s*$")


def _overlap(previous, part):
    """Length of the longest text that ends ``previous`` and starts ``part`` (chunks are cut with a fixed overlap), 0 when they do not overlap."""
    for k in range(min(len(previous), len(part), 400), 19, -1):
        if previous[-k:] == part[:k]:
            return k
    return 0


def _joined(chunks):
    """The text of ordered chunks as one string. Chunks overlap by a fixed number of characters and are cut mid-sentence, so the repeated text is
    written once and the sentence stays whole; a boundary after a full stop becomes a line break. Returns (text, [(offset, chunk_number)])."""
    text, offsets = "", []
    for chunk in chunks:
        part = str(chunk.get("text") or "").strip()
        if not part:
            continue
        if text:
            shared = _overlap(text, part)
            if shared:
                if len(part) <= shared:
                    continue
                offsets.append((len(text), chunk.get("chunk_number")))
                text += part[shared:]
                continue
            text += "\n" if _TERMINATOR_END.search(text) else " "
        offsets.append((len(text), chunk.get("chunk_number")))
        text += part
    return text, offsets


def _units(chunks):
    """Sentence-like units of an ordered chunk list: [(unit_text, chunk_number)].
    A leading 'Requirement 3:' label is dropped so a requirement that is only renumbered between versions still compares equal."""
    text, offsets = _joined(chunks)
    units, position = [], 0
    for line in text.split("\n"):
        start_of_line = position
        position += len(line) + 1
        cursor = 0
        for part in re.split(r"(?<=[.;!?])\s+", line):
            found = line.find(part, cursor) if part else -1
            if found < 0:
                continue
            cursor = found + len(part)
            clean = _LABEL.sub("", part.strip()).strip()
            if len(clean) < 15:
                continue
            absolute = start_of_line + found
            number = None
            for offset, chunk_number in offsets:
                if offset <= absolute:
                    number = chunk_number
            units.append((clean, number))
    return units


def statement_for_quote(quote, source_units):
    """The full sentence(s) of the source document that contain a quoted fragment. A fragment found word for word in an earlier version
    does not prove the sentence is unchanged (the earlier version may continue differently), so whole statements are compared."""
    for width in (1, 2, 3):
        for i in range(len(source_units)):
            window = " ".join(u[0] for u in source_units[i:i + width])
            if _tokens_contain(window, quote):
                return window
    return str(quote)


def prior_version_status(change, prior_documents, prior_version, source_chunks=None):
    """Compare a source change with the nearest earlier version of the same document.

    UNCHANGED  every verified source statement of the change appears, as a whole sentence, word for word in the earlier version, so
               the requirement is carried over and is not a change introduced by this version.
    CHANGED    a similar passage exists in the earlier version but its wording differs (quoted from the earlier version).
    ADDED      no similar passage exists in the earlier version.
    NO_PRIOR_VERSION / UNKNOWN when there is nothing to compare with.
    Nothing here is generated: every quotation is taken from the documents."""
    quotes = [q for q in (change or {}).get("source_evidence") or [] if str(q).strip()]
    if not prior_documents or prior_version is None:
        return {"status": "NO_PRIOR_VERSION" if not prior_documents else "UNKNOWN", "prior_version": prior_version, "versions_checked": []}
    if not quotes:
        return {"status": "UNKNOWN", "prior_version": prior_version, "versions_checked": [prior_version]}

    units = []
    for doc in prior_documents:
        for text, number in _units(doc.get("chunks") or []):
            units.append((text, number, doc.get("file_name")))
    unit_tokens = [tokens(u[0]) for u in units]
    windows = []                                   # (token list, text, chunk_number, file_name) for 1..3 consecutive units
    for i in range(len(units)):
        for width in (1, 2, 3):
            part = units[i:i + width]
            if len(part) == width:
                windows.append((sum(unit_tokens[i:i + width], []), " ".join(u[0] for u in part), part[0][1], part[0][2]))

    source_units = _units([{"text": c.get("text"), "chunk_number": (c.get("meta") or {}).get("chunk_number", c.get("chunk_number"))} for c in source_chunks or []])
    statements = [statement_for_quote(q, source_units) for q in quotes]

    verbatim, changed, added = [], [], []
    where = None
    for statement in statements:
        wanted = tokens(statement)
        exact = next((w for w in windows if w[0] == wanted), None)
        if exact is not None:
            verbatim.append(statement)
            where = where or {"prior_file": exact[3], "prior_chunk": exact[2]}
            continue
        best = (0.0, None)
        for w in windows:
            if w[0] and wanted:
                score = len(set(w[0]) & set(wanted)) / float(len(set(w[0]) | set(wanted)))
                if score > best[0]:
                    best = (score, w)
        if best[1] is not None and best[0] >= 0.45:
            flips = polarity_changes(best[1][1], statement)
            changed.append({"current": statement, "prior": best[1][1], "chunk_number": best[1][2], "file_name": best[1][3], "similarity": round(best[0], 2),
                            "polarity": flips, "material": bool(flips or statement_differs(statement, best[1][1]) or best[0] < 0.75)})
        else:
            added.append(statement)

    base = {"prior_version": prior_version, "versions_checked": [prior_version], "quotes_unchanged": len(verbatim)}
    if changed:
        top = max(changed, key=lambda c: (c["material"], -c["similarity"]))
        return dict(base, status="CHANGED", current_quote=top["current"], prior_quote=top["prior"], prior_file=top["file_name"],
                    prior_chunk=top["chunk_number"], similarity=top["similarity"], material=top["material"], polarity=top["polarity"],
                    polarity_note=polarity_note(top["polarity"]))
    if added:
        return dict(base, status="ADDED", current_quote=added[0])
    return dict(base, status="UNCHANGED", current_quote=statements[0], prior_quote=statements[0], **(where or {}))


_REQUIREMENT_WORDS = re.compile(
    r"\b(shall|must|should|will|required|mandatory|compulsory|optional|applicable|exempt\w*|allowed|permitted|prohibited|forbidden|enabled|disabled|"
    r"not allowed|cannot|can not|may not|only|at least|at most|maximum|minimum|no more than|no less than)\b", re.I)


def is_functional(text):
    """Does a sentence state something about behaviour (a requirement, rule, condition or setting) rather than describe the document?"""
    if re.match(r"^(?:Legal references\b|Alternatives to the Proposed Change\b|Whether or not the requested change\b|API\s+\d+\s*:|Property Name\s*:)", (text or '').strip(), re.I):
        return False
    if re.search(r"\b(?:field|parameter|functionality)\b.*\b(?:is added|needs to be implemented|need to be implemented)\b", text or '', re.I):
        return True
    return bool(_REQUIREMENT_WORDS.search(text or "") or has_polarity_terms(text))


def source_inventory(source_chunks):
    """Auditable source statements, independent of model extraction and target impact.

    Exact repetitions and overlapping fragments are not counted as new functional
    requirements. Context is retained for review, never labelled unchanged without
    an actual earlier-version comparison.
    """
    chunks = [{"text": c.get("text"), "chunk_number": (c.get("meta") or {}).get("chunk_number", c.get("chunk_number"))} for c in source_chunks or []]
    units = _requirement_units(chunks, functional_only=False)
    items = []
    for text, number in units:
        administrative = bool(re.match(r"^(?:\|?\s*Change Request Form|Section [A-D]\b|Change Request Number\b|Department Name\b|Request Date\b|Legal references\b|Alternatives to the Proposed Change\b|Whether or not the requested change\b|Impact Analysis by\b|Name\s*\||Member Name\b|Approved/Rejected\b)", text, re.I))
        kind = 'ADMINISTRATIVE' if administrative else 'FUNCTIONAL' if len(text) >= 30 and is_functional(text) else 'CONTEXT'
        statement_id = 'SRC-' + hashlib.sha256(normalize(text).encode('utf-8')).hexdigest()[:16]
        items.append({'statement_id': statement_id, 'text': text, 'chunk': number, 'kind': kind})
    by_key = {tuple(tokens(x['text'])): x['statement_id'] for x in items}
    seen, duplicates = set(), []
    for text, number in _units(chunks):
        text = _collapse_cells(text)
        key = tuple(tokens(text))
        if key in seen and key in by_key:
            duplicates.append({'text': text, 'chunk': number, 'kind': 'DUPLICATE', 'duplicate_of': by_key[key]})
        seen.add(key)
    return {'items': items, 'duplicates': duplicates,
            'counts': {kind: sum(x['kind'] == kind for x in items) for kind in ('FUNCTIONAL', 'ADMINISTRATIVE', 'CONTEXT')} | {'DUPLICATE': len(duplicates)}}


def supplement_source_requirements(footprint, source_chunks):
    """Retain omitted functional statements verbatim, explicitly as source inventory.

    This does not infer a target dependency or manufacture an AI extraction.
    """
    quotes = [q for c in footprint for q in c.get('source_evidence', [])]
    extra = []
    for item in source_inventory(source_chunks)['items']:
        if item['kind'] != 'FUNCTIONAL' or any(_cites(item['text'], q) for q in quotes):
            continue
        extra.append({'type': 'Source requirement', 'name': item['text'], 'change': item['text'],
                      'source_evidence': [item['text']], 'extraction_method': 'SOURCE_INVENTORY',
                      'source_statement_id': item['statement_id']})
    return list(footprint) + extra


def _collapse_cells(text):
    """Merged table cells repeat their text once per column ('A | A | B | B'); keep each cell once."""
    if " | " not in text:
        return text
    return " | ".join(dict.fromkeys(part.strip() for part in text.split(" | ") if part.strip()))


def _requirement_units(chunks, functional_only=True):
    """Sentence-like units of ordered chunks, each once. Chunks overlap and are cut mid-sentence, so the same sentence can appear whole in one chunk and as
    a fragment in the next; a unit that is only a part of another unit of a neighbouring chunk is dropped. [(text, chunk_number)]."""
    seen, units = set(), []
    for text, number in _units(chunks):
        text = _collapse_cells(text)
        key = tuple(tokens(text))
        if len(text) < 15 or key in seen or (functional_only and not (len(text) >= 30 and is_functional(text))):
            continue
        seen.add(key)
        units.append((text, number, " ".join(key)))
    by_chunk = {}
    for i, unit in enumerate(units):
        by_chunk.setdefault(unit[1], []).append(i)
    kept = []
    for i, (text, number, flat) in enumerate(units):
        neighbours = []
        for n in {number, (number - 1) if isinstance(number, int) else number, (number + 1) if isinstance(number, int) else number}:
            neighbours += by_chunk.get(n, [])
        if any(j != i and len(units[j][2]) > len(flat) and (" " + flat + " ") in (" " + units[j][2] + " ") for j in neighbours):
            continue
        kept.append((text, number))
    return kept


def _diff_units(now, before):
    """Compare two lists of units, [(text, chunk)] and [(text, chunk, file)]. Returns (changed, added, removed); every text is verbatim."""
    now_tokens = [tuple(tokens(u[0])) for u in now]
    before_tokens = [tuple(tokens(u[0])) for u in before]
    before_exact, now_exact = set(before_tokens), set(now_tokens)
    now_new = [i for i, t in enumerate(now_tokens) if t not in before_exact]
    before_old = [j for j, t in enumerate(before_tokens) if t not in now_exact]
    changed, added, matched_before = [], [], set()
    for i in now_new:
        a = set(now_tokens[i])
        best, best_j = 0.0, None
        for j in before_old:
            b = set(before_tokens[j])
            score = len(a & b) / float(len(a | b)) if a and b else 0.0
            if score > best:
                best, best_j = score, j
        if best_j is not None and best >= 0.45:
            matched_before.add(best_j)
            flips = polarity_changes(before[best_j][0], now[i][0])
            changed.append({"current": now[i][0], "current_chunk": now[i][1], "prior": before[best_j][0], "prior_chunk": before[best_j][1],
                            "prior_file": before[best_j][2], "similarity": round(best, 2), "polarity": flips, "polarity_note": polarity_note(flips),
                            "material": bool(flips or statement_differs(now[i][0], before[best_j][0]) or best < 0.75)})
        else:
            added.append({"current": now[i][0], "current_chunk": now[i][1]})
    removed = []
    now_sets = [set(t) for t in now_tokens]
    for j in before_old:
        if j in matched_before:
            continue
        b = set(before_tokens[j])
        if b and any(a and len(a & b) / float(len(a | b)) >= 0.45 for a in now_sets):
            continue
        removed.append({"prior": before[j][0], "prior_chunk": before[j][1], "prior_file": before[j][2]})
    return changed, added, removed


def _cites(text, quote):
    """Does a quotation (a verbatim fragment of the document, possibly with an ellipsis) belong to this sentence? Containment either way; a near-copy only when
    no setting (mandatory / applicable / exempted ...) or number differs, so a sentence that says the opposite is never counted as cited."""
    pieces = [p for p in re.split(r"\u2026|\.\.\.", str(quote or "")) if len(p.strip()) >= 12] or [quote]
    if any(_tokens_contain(text, piece) or _tokens_contain(piece, text) for piece in pieces if piece):
        return True
    return jaccard(text, quote) >= 0.9 and not statement_differs(quote, text)


def version_difference(source_chunks, prior_documents, prior_version, findings_quotes=(), limit=25):
    """Differences between the source document and the nearest earlier version, found from the documents alone.

    This does not depend on what the model extracted, so a changed requirement the model overlooked still shows up.
      * changed / added / removed  - functional differences: sentences that state a requirement, rule, condition or setting
                                     (shall / must / mandatory / optional / applicable / exempted / ...). A changed sentence records which settings
                                     flipped (Mandatory / Not Mandatory, Applicable / Not Applicable, Exempted / Not Exempted, Optional / Not Optional ...).
      * other                      - differences in text that does not read as a requirement (request numbers, dates, descriptions); listed, not judged.
      * unassessed                 - functional differences that no finding covers, including every removed requirement (a finding can only
                                     describe what the new version says).
    Returns None when there is no earlier version to compare with. Every text is verbatim from a document."""
    if not prior_documents or prior_version is None:
        return None

    now_chunks = [{"text": c.get("text"), "chunk_number": (c.get("meta") or {}).get("chunk_number", c.get("chunk_number"))} for c in source_chunks or []]
    now = _requirement_units(now_chunks, functional_only=False)
    before = []
    for doc in prior_documents:
        before += [(t, n, doc.get("file_name")) for t, n in _requirement_units(doc.get("chunks") or [], functional_only=False)]

    changed, added, removed = _diff_units(now, before)

    def functional(item):
        return is_functional(item.get("current") or "") or is_functional(item.get("prior") or "")

    f_changed = [d for d in changed if functional(d)]
    f_added = [d for d in added if functional(d)]
    f_removed = [d for d in removed if is_functional(d["prior"])]
    other = {"changed": [d for d in changed if not functional(d)], "added": [d for d in added if not functional(d)],
             "removed": [d for d in removed if not is_functional(d["prior"])]}

    quotes = [q for q in findings_quotes if q]

    def covered(text):
        return any(_cites(text, q) for q in quotes)

    unassessed = ([dict(d, kind="CHANGED") for d in f_changed if d["material"] and not covered(d["current"])]
                  + [dict(d, kind="ADDED") for d in f_added if not covered(d["current"])]
                  + [dict(d, kind="REMOVED") for d in f_removed])
    by_kind = {k: sum(1 for d in unassessed if d["kind"] == k) for k in ("CHANGED", "ADDED", "REMOVED")}
    counts = {"changed": len(f_changed), "added": len(f_added), "removed": len(f_removed),
              "other_changed": len(other["changed"]), "other_added": len(other["added"]), "other_removed": len(other["removed"])}
    return {"prior_version": prior_version, "changed": f_changed[:limit], "added": f_added[:limit], "removed": f_removed[:limit],
            "other": {k: v[:limit] for k, v in other.items()}, "counts": counts,
            "unassessed": unassessed[:limit], "unassessed_count": len(unassessed), "unassessed_by_kind": by_kind,
            "compared": {"source_sentences": len(now), "prior_sentences": len(before)},
            "shown_limit": limit,
            "truncated": any(v > limit for v in counts.values()) or len(unassessed) > limit}


def source_requirement_coverage(source_chunks, quotes, examined_chunks=None, limit=25, deferred_quotes=()):
    """Which requirement-like sentences of the source document were assessed (cited by a finding) and which were not, found from the document itself.

    ``examined_chunks`` are the chunk numbers the model was actually shown (None = all); ``deferred_quotes`` belong to changes found but left out by the
    size limit. Returns {detected, assessed, not_assessed_count, by_reason, not_assessed[{text, chunk, reason}], shown_limit}. A sentence in a chunk the model
    never saw is NOT_EXAMINED, one belonging to a change left out by the size limit is NOT_ASSESSED_LIMIT, and one the model saw but did not extract is NOT_EXTRACTED."""
    chunks = [{"text": c.get("text"), "chunk_number": (c.get("meta") or {}).get("chunk_number", c.get("chunk_number"))} for c in source_chunks or []]
    units = _requirement_units(chunks, functional_only=True)
    quotes = [q for q in quotes if q]
    deferred_quotes = [q for q in deferred_quotes if q]

    def cited(text, among):
        return any(_cites(text, q) for q in among)

    missing, assessed, by_reason = [], 0, {"NOT_EXAMINED": 0, "NOT_ASSESSED_LIMIT": 0, "NOT_EXTRACTED": 0}
    for text, number in units:
        if cited(text, quotes):
            assessed += 1
            continue
        if examined_chunks is not None and number not in examined_chunks:
            reason = "NOT_EXAMINED"
        elif cited(text, deferred_quotes):
            reason = "NOT_ASSESSED_LIMIT"
        else:
            reason = "NOT_EXTRACTED"
        by_reason[reason] += 1
        missing.append({"text": text, "chunk": number, "reason": reason})
    return {"detected": len(units), "assessed": assessed, "not_assessed_count": len(missing), "by_reason": by_reason,
            "not_examined_count": by_reason["NOT_EXAMINED"], "not_assessed": missing[:limit], "shown_limit": limit}


DOCUMENT_CHANGE_LABELS = {
    "UNCHANGED": "UNCHANGED FROM EARLIER VERSION",
    "CHANGED_MATERIAL": "CONFIRMED DOCUMENT CHANGE",
    "CHANGED_WORDING": "WORDING-ONLY CHANGE",
    "ADDED": "NEW IN THIS VERSION",
    "NO_PRIOR_VERSION": "NOT COMPARED (no earlier version of this document)",
    "UNKNOWN": "NOT COMPARED (earlier version unreadable)",
}


def document_change(status):
    """What the documents say changed between versions - kept apart from what that means for the target system (the finding's classification)."""
    status = status or {}
    kind = status.get("status") or "UNKNOWN"
    key = ("CHANGED_MATERIAL" if status.get("material") else "CHANGED_WORDING") if kind == "CHANGED" else kind
    return {"status": kind, "label": DOCUMENT_CHANGE_LABELS.get(key, DOCUMENT_CHANGE_LABELS["UNKNOWN"]), "material": bool(status.get("material")) if kind == "CHANGED" else kind == "ADDED",
            "prior_version": status.get("prior_version"), "prior_quote": status.get("prior_quote") or "", "current_quote": status.get("current_quote") or "",
            "polarity": status.get("polarity") or [], "polarity_note": status.get("polarity_note") or ""}


def decision_from_version(status, source_version):
    """The system-impact classification a version comparison supports on its own (used when it is the only evidence, or the requirement is unchanged).

    A difference between two versions of a document is a confirmed DOCUMENT change. It is never by itself a Confirmed SYSTEM impact: whether the target
    must change depends on what the target does, which only an independent target document or another change request can show. So a changed or added
    requirement is at most Potential here. Returns None for NO_PRIOR_VERSION / UNKNOWN."""
    kind = (status or {}).get("status")
    prior = status.get("prior_version") if status else None
    if kind == "UNCHANGED":
        return _out(ALREADY_COVERED, "DIRECT", "UNCHANGED_FROM_PRIOR_VERSION",
                    ["The requirement text is identical (word for word) in version %s, so it is not a change introduced by version %s." % (prior, source_version)],
                    None, True, 1.0, False, basis="VERSION_COMPARISON")
    if kind == "CHANGED" and status.get("material"):
        flips = status.get("polarity") or []
        detail = (" Changed setting(s): %s." % "; ".join("%s: %s to %s" % (c["label"], c["before"], c["after"]) for c in flips)) if flips else ""
        return _out(POTENTIAL, "INDIRECT", "DOCUMENT_CHANGE_CONFIRMED_SYSTEM_IMPACT_UNVERIFIED",
                    ["Document change confirmed: version %s states this requirement differently from version %s (both passages quoted).%s "
                     "System impact is not confirmed: no independent target document or other change request shows what the target does today." % (source_version, prior, detail)],
                    None, True, status.get("similarity", 0.0), False, basis="VERSION_COMPARISON")
    if kind == "CHANGED":
        return _out(POTENTIAL, "INDIRECT", "REWORDED_FROM_PRIOR_VERSION",
                    ["Version %s words this requirement slightly differently from version %s, with no demonstrated change of meaning. Confirm whether the difference is editorial." % (source_version, prior)],
                    None, True, status.get("similarity", 0.0), False, basis="VERSION_COMPARISON")
    if kind == "ADDED":
        return _out(POTENTIAL, "INDIRECT", "NEW_IN_THIS_VERSION",
                    ["No passage corresponding to this requirement exists in version %s, so it was introduced in version %s. Whether the target already implements it was not verified against an independent target document." % (prior, source_version)],
                    None, False, 0.0, False, basis="VERSION_COMPARISON")
    return None


def locate_in_documents(quote, documents):
    """Which other versions of the source contain this requirement text verbatim (after whitespace/case normalisation)?"""
    found = []
    quote_n = normalize(quote).strip("\"' ")
    if len(quote_n) < 20:
        return found
    for doc in documents:
        text = normalize(" ".join(c["text"] for c in doc["chunks"]))
        if quote_n in text:
            found.append({"file_name": doc["file_name"], "version": doc["version"], "document_type": doc.get("document_type")})
    return found


# --------------------------------------------------
# Test cases
# --------------------------------------------------

TEXT_FIELDS = ("scenario", "test_case", "steps", "expected_result", "pre_conditions", "title", "description")


def match_test_cases(change, rows, target_knowledge_names, lookup_ok=True, text_fields=TEXT_FIELDS, target_knowledge_ids=None):
    """Existing test cases related to a change. A match means 'an existing test mentions the same thing',
    never 'the changed behaviour is verified'.

    Identity: a row belongs to the target when its Knowledge source (document) id is one of ``target_knowledge_ids`` (the ids of the source and of the
    target evidence documents) or when its Knowledge name is one of ``target_knowledge_names``. The name identifies the Knowledge within the selected
    domain and module; the id identifies the exact document the test was generated from, which may be an earlier document version of the same Knowledge.
    Every match reports its Knowledge, source ids, document version, workbook version and which of the two identities matched.

    Workbook versions: rows annotated by ``Core.test_case_scope`` as SUPERSEDED (an older workbook version of the same source) never count as
    existing coverage. A scenario that matches only in an older version is reported under ``superseded_matches`` and the change is treated as having no
    current matching test."""
    if not lookup_ok:
        return {"classification": "LOOKUP UNAVAILABLE", "test_cases": [], "lookup_ok": False,
                "note": "The test-case repository could not be read, so coverage is unknown (not 'none')."}
    name = normalize(change.get("name"))
    terms = key_terms(change.get("name"), change.get("change"))
    distinctive = {t for t in terms if len(t) >= 5 or any(ch.isdigit() for ch in t)}
    if len(name) < 3 and len(distinctive) < 2:
        return {"classification": "NO MATCHING COVERAGE FOUND", "test_cases": [], "lookup_ok": True, "note": "The change has no distinctive term to search for."}
    target_ids = {int(i) for i in (target_knowledge_ids or ()) if i is not None and str(i).lstrip("-").isdigit()}
    related, review = [], []
    matches = []                                           # one entry per matching CURRENT test case ROW (a tc_id repeats across workbook versions and sources)
    superseded = []
    for row in rows or []:
        haystack = normalize(" ".join(str(row.get(f) or "") for f in text_fields))
        hay_tokens = set(tokens(haystack))
        phrase_hit = len(name) >= 6 and name in haystack
        hits = distinctive & hay_tokens
        if not (phrase_hit or (len(hits) >= 2 and len(hits) / float(max(1, len(distinctive))) >= 0.4)):
            continue
        identifier = row.get("tc_id") or row.get("tc_number") or "row-%s" % row.get("id")
        row_sources = row.get("source_knowledge_ids") or []
        if isinstance(row_sources, str):
            row_sources = []
        by_source = bool(row_sources and target_ids and set(row_sources) & target_ids)
        by_knowledge = bool(row.get("knowledge_name") and row.get("knowledge_name") in target_knowledge_names)
        in_target = by_source or by_knowledge
        identity = "SOURCE_ID" if by_source else "KNOWLEDGE_NAME" if by_knowledge else "NONE"
        entry = {"row_id": row.get("id"), "test_case_id": identifier, "test_case_version_id": row.get("test_case_version_id"),
                 "scenario": str(row.get("scenario") or row.get("title") or "")[:160], "knowledge_name": row.get("knowledge_name"), "in_target_knowledge": in_target,
                 "source_knowledge_ids": list(row_sources), "workbook_version": row.get("workbook_version"),
                 "latest_workbook_version": row.get("latest_workbook_version"), "document_version_id": row.get("document_version_id"),
                 "scope_status": row.get("scope_status") or "UNKNOWN", "identity_basis": identity}
        if entry["scope_status"] == "SUPERSEDED":
            superseded.append(entry)                       # an older workbook revision: shown, never counted
            continue
        (related if in_target else review).append(identifier)
        matches.append(entry)
    extra = {"superseded_matches": superseded} if superseded else {}
    if related:
        unique = list(dict.fromkeys(related))              # the same identifier from several versions is one test case, shown once; the rows are in "matches"
        return dict({"classification": "RELATED EXISTING TEST", "test_cases": unique, "matches": [m for m in matches if m["in_target_knowledge"]], "lookup_ok": True}, **extra)
    if review:
        unique = list(dict.fromkeys(review))
        return dict({"classification": "REVIEW REQUIRED", "test_cases": unique, "matches": matches, "lookup_ok": True}, **extra)
    if superseded:
        versions = sorted({m["workbook_version"] for m in superseded if m["workbook_version"] is not None})
        return {"classification": "NO MATCHING COVERAGE FOUND", "test_cases": [], "lookup_ok": True, "superseded_matches": superseded,
                "note": "The only matching scenario(s) belong to older workbook version(s) %s, not the current one, so they are not counted as existing coverage."
                        % (", ".join(str(v) for v in versions) or "of this source")}
    return {"classification": "NO MATCHING COVERAGE FOUND", "test_cases": [], "lookup_ok": True}


# --------------------------------------------------
# Source coverage
# --------------------------------------------------

_REQ_LABEL = re.compile(r"(?im)\brequirement\s+(\d+)\s*:")


def requirement_segments(text):
    """Split a change request's text at its 'Requirement N:' labels. {label: text}."""
    text = str(text or "")
    marks = [(m.start(), m.group(1)) for m in _REQ_LABEL.finditer(text)]
    segments = {}
    for i, (pos, label) in enumerate(marks):
        end = marks[i + 1][0] if i + 1 < len(marks) else len(text)
        segments.setdefault("Requirement %s" % label, "")
        segments["Requirement %s" % label] += text[pos:end] + "\n"
    return segments


def uncovered_requirements(source_text, evidence_quotes):
    """Requirement labels in the source that none of the extracted changes cite."""
    segments = requirement_segments(source_text)
    cited = [normalize(q) for q in evidence_quotes if q]
    missing = []
    for label, body in segments.items():
        body_n = normalize(body)
        if not any(q and len(q) >= 12 and q in body_n for q in cited):
            missing.append(label)
    return missing


# --------------------------------------------------
# Result consistency
# --------------------------------------------------

def _warning_codes(result):
    return {w.get("code") for w in (result.get("coverage") or {}).get("warning_details", [])}


def validate_result(result):
    """Cross-check a finished analysis result against itself: findings, evidence, version comparison, test coverage, coverage figures and warnings must
    agree. Returns a list of plain-language problems (empty = consistent). Nothing is changed; this only reports."""
    issues = []
    findings = result.get("findings") or []
    summary = result.get("summary") or {}
    coverage = result.get("coverage") or {}
    codes = _warning_codes(result)
    diff = result.get("version_difference")

    counts = {name: sum(1 for f in findings if f.get("classification") == name) for name in CLASSIFICATIONS}
    shown = {CONFIRMED: summary.get("confirmed"), POTENTIAL: summary.get("potential"), ALREADY_COVERED: summary.get("already_covered"),
             INSUFFICIENT: summary.get("insufficient_evidence"), NO_DOCUMENTED: summary.get("no_documented_impact")}
    for name in CLASSIFICATIONS:
        if shown[name] != counts[name]:
            issues.append("The summary shows %s %s but the findings contain %s." % (shown[name], name.lower(), counts[name]))
    if summary.get("coverage_warnings") != len(coverage.get("warning_details", [])) or len(coverage.get("warnings", [])) != len(coverage.get("warning_details", [])):
        issues.append("The number of coverage warnings differs between the summary and the warning list.")

    for f in findings:
        fid = f.get("finding_id")
        cls, basis = f.get("classification"), f.get("classification_basis_kind")
        change = f.get("document_change") or {}
        if cls not in CLASSIFICATIONS:
            issues.append("%s has an unknown classification." % fid)
            continue
        if f.get("system_impact") != cls:
            issues.append("%s: the system impact label differs from its classification." % fid)
        if "confirmed_target_impact" in f and f["confirmed_target_impact"] != (cls == CONFIRMED):
            issues.append("%s: confirmed target impact disagrees with its classification." % fid)
        if f.get("target_assessment") == "NOT_EXECUTED_LIMIT" and basis != "VERSION_COMPARISON" and cls != INSUFFICIENT:
            issues.append("%s: target search was not executed but the finding does not report insufficient evidence." % fid)
        if cls == CONFIRMED:
            quote = f.get("supporting_quote") or {}
            if basis != "TARGET_EVIDENCE":
                issues.append("%s is Confirmed Impact on the strength of a version comparison alone; a document change is not a confirmed system impact." % fid)
            elif not quote.get("exact") or not f.get("target_evidence"):
                issues.append("%s is Confirmed Impact without a verified quotation from independent target evidence." % fid)
        if basis == "TARGET_EVIDENCE" and f.get("supporting_quote"):
            ids = {e.get("evidence_id") for e in f.get("target_evidence") or []}
            if f["supporting_quote"].get("evidence_id") not in ids:
                issues.append("%s quotes an evidence item (%s) that is not in its evidence list." % (fid, f["supporting_quote"].get("evidence_id")))
        if f.get("change_status") != change.get("status"):
            issues.append("%s: the change status and the document change disagree." % fid)
        if change.get("status") == "UNCHANGED":
            if cls != ALREADY_COVERED or basis != "VERSION_COMPARISON":
                issues.append("%s is unchanged from the earlier version but is not reported as Already Covered by version comparison." % fid)
            if (f.get("test_case_coverage") or {}).get("classification") != "NOT ASSESSED":
                issues.append("%s is unchanged from the earlier version but has test-case coverage assessed as if it were a new change." % fid)
        elif basis == "VERSION_COMPARISON" and cls == ALREADY_COVERED:
            issues.append("%s is Already Covered by version comparison although the requirement changed between versions." % fid)
        if "test_case_coverage" not in f:
            issues.append("%s has no test-case coverage entry (assessed or explicitly not assessed)." % fid)

        if diff and diff.get("complete", True) and not diff.get("truncated"):
            current = change.get("current_quote") or ""
            listed = [d["current"] for d in diff.get("changed", []) + diff.get("added", [])] + [d["current"] for d in (diff.get("other") or {}).get("changed", [])]
            near = any(_tokens_contain(current, t) or _tokens_contain(t, current) or jaccard(current, t) >= 0.5 for t in listed) if current else False
            if change.get("status") in ("CHANGED", "ADDED") and current and not near:
                issues.append("%s reports a document change that the whole-document version comparison does not list." % fid)
            if change.get("status") == "UNCHANGED" and current and near:
                issues.append("%s is reported as unchanged but the whole-document version comparison lists this passage as changed." % fid)

    if diff:
        if diff.get("unassessed_count") and "VERSION_DIFFERENCE_NOT_ASSESSED" not in codes:
            issues.append("The version comparison has unassessed differences but no warning says so.")
        if (diff.get("counts") or {}).get("removed") and "REQUIREMENTS_REMOVED" not in codes:
            issues.append("Requirements were removed between versions but no warning says so.")
        if diff.get("complete") is False and "VERSION_COMPARISON_PARTIAL" not in codes:
            issues.append("The version comparison is incomplete but no warning says so.")

    source = coverage.get("source") or {}
    if (source.get("sections_not_analysed") or source.get("chunks_not_read")) and not (codes & {"SOURCE_PARTIAL", "SOURCE_SECTION_FAILED"}):
        issues.append("Part of the source document was not analysed but no warning says so.")
    if (coverage.get("source_requirements") or {}).get("not_assessed_count") and "SOURCE_REQUIREMENTS_NOT_ASSESSED" not in codes:
        issues.append("Source requirements were not assessed but no warning says so.")
    if coverage.get("changes_not_assessed") and "CHANGE_LIMIT" not in codes:
        issues.append("Changes were left out by the size limit but no warning says so.")
    tests = coverage.get("test_cases") or {}
    if tests.get("complete") is False and tests.get("total") is not None and "TEST_CASES_PARTIAL" not in codes:
        issues.append("Only part of the test cases was searched but no warning says so.")
    return issues


def validate_report(result):
    """The report text must carry what the result says: every finding, the document-change counts, the unassessed changes and the coverage limits."""
    text = result.get("report_text") or ""
    issues = []
    if not text:
        return issues
    for f in result.get("findings") or []:
        if f.get("finding_id") and f["finding_id"] not in text:
            issues.append("The report does not mention %s." % f["finding_id"])
    diff = result.get("version_difference")
    if diff and diff.get("unassessed_count") and "not covered by any finding" not in text.lower():
        issues.append("The report does not list the version differences that no finding covers.")
    cov = result.get("coverage") or {}
    if (cov.get("source_requirements") or {}).get("not_assessed_count") and "not assessed" not in text.lower():
        issues.append("The report does not list the source requirements that were not assessed.")
    tests = cov.get("test_cases") or {}
    if tests.get("status_note") and tests["status_note"] not in text:
        issues.append("The report omits the test-search status.")
    if cov.get("target_statements_not_assessed") and "target search not executed" not in text.lower():
        issues.append("The report omits source statements whose target search was not executed.")
    return issues
