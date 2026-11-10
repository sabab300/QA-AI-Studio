"""Business-rule semantics for Change Impact: what a wording difference actually means.

A line-by-line text difference reports that the word "not" appeared. A reviewer needs to know that the condition
"FI is applicable" became "FI is not applicable", which rule that condition controls, and what the old and new
behaviour are. This module reads sentences (never whole documents), finds the *predicates* that carry business meaning
(mandatory / optional / allowed / applicable / exempted / enabled / ...), the negations that flip them, the condition
or outcome role they play, numeric thresholds and identifiers, and compares the old and new sentence on that basis.

Everything is deterministic and generic: the lexicon is language-level (and extendable from the policy file), nothing
refers to a particular document, requirement number or counts. Every statement it produces quotes source wording; text
the engine derives (impact wording, scenarios) is labelled as derived by the callers.
"""
import difflib
import re

from Core.change_impact.util import clip, digest, normalize, token_set

# ---------------------------------------------------------------------------------------------------- lexicon
NEGATIONS = frozenset(("not", "no", "never", "cannot", "can't", "cant", "isn't", "aren't", "wasn't", "weren't", "doesn't", "don't",
                       "didn't", "won't", "shouldn't", "mustn't", "without", "non", "neither", "nor", "n't"))
FILLERS = frozenset(("be", "been", "being", "is", "are", "was", "were", "shall", "will", "must", "should", "may", "can", "to", "it", "also",
                     "only", "then", "remain", "remains", "become", "becomes", "get", "gets", "longer", "really", "always", "necessarily", "the", "a", "an"))
CONDITION_CUES = frozenset(("if", "when", "whenever", "unless", "where", "provided", "once", "until", "upon", "whereas", "while", "incase"))
BOUNDARIES = frozenset((",", ";", ":", "."))
# word -> (axis, value, value_when_negated). Two-valued axes use +1 / -1; "deontic" uses 2 (mandatory), 1 (optional/permitted), -2 (prohibited).
PREDICATES = {
    'deontic': {'mandatory': (2, 1), 'required': (2, 1), 'compulsory': (2, 1), 'necessary': (2, 1), 'obligatory': (2, 1), 'shall': (2, -2), 'must': (2, -2),
                'optional': (1, 2), 'voluntary': (1, 2), 'may': (1, -2), 'allowed': (1, -2), 'permitted': (1, -2), 'permissible': (1, -2), 'can': (1, -2),
                'prohibited': (-2, 1), 'forbidden': (-2, 1), 'disallowed': (-2, 1), 'cannot': (-2, 1), 'restricted': (-2, 1), 'blocked': (-2, 1)},
    'applicability': {'applicable': (1, -1), 'inapplicable': (-1, 1)},
    'exemption': {'exempted': (1, -1), 'exempt': (1, -1)},
    'enablement': {'enabled': (1, -1), 'disabled': (-1, 1)},
    'visibility': {'visible': (1, -1), 'hidden': (-1, 1), 'displayed': (1, -1), 'shown': (1, -1)},
    'approval': {'approved': (1, -1), 'accepted': (1, -1), 'rejected': (-1, 1), 'declined': (-1, 1)},
    'validity': {'valid': (1, -1), 'invalid': (-1, 1)},
    'state': {'active': (1, -1), 'inactive': (-1, 1), 'available': (1, -1), 'unavailable': (-1, 1), 'eligible': (1, -1), 'ineligible': (-1, 1),
              'checked': (1, -1), 'unchecked': (-1, 1), 'selected': (1, -1), 'unselected': (-1, 1), 'editable': (1, -1), 'readonly': (-1, 1),
              'supported': (1, -1), 'unsupported': (-1, 1), 'blank': (1, -1), 'empty': (1, -1)},
}
AXIS_LABEL = {
    'deontic': {2: 'mandatory', 1: 'optional', -2: 'not allowed'},
    'applicability': {1: 'applicable', -1: 'not applicable'},
    'exemption': {1: 'exempted', -1: 'not exempted'},
    'enablement': {1: 'enabled', -1: 'disabled'},
    'visibility': {1: 'visible', -1: 'hidden'},
    'approval': {1: 'approved', -1: 'rejected'},
    'validity': {1: 'valid', -1: 'invalid'},
    'state': {1: 'on / available', -1: 'off / unavailable'},
}
AXIS_NAME = {'deontic': 'Mandatory / optional', 'applicability': 'Applicability', 'exemption': 'Exemption', 'enablement': 'Enablement',
             'visibility': 'Visibility', 'approval': 'Approval', 'validity': 'Validity', 'state': 'State'}
COMPARATORS = {'greater': '>', 'more': '>', 'above': '>', 'exceed': '>', 'exceeds': '>', 'higher': '>', 'over': '>', 'later': '>',
               'less': '<', 'lower': '<', 'below': '<', 'fewer': '<', 'under': '<', 'earlier': '<', 'before': '<',
               'minimum': '>=', 'least': '>=', 'min': '>=', 'maximum': '<=', 'most': '<=', 'max': '<=', 'within': '<=', 'upto': '<='}
API_MARKERS = re.compile(r'\b(api|method\s*id|endpoint|payload|request body|response body|parameter|property name|json|xml|http|https|soap|webhook|datatype|data type|query string)\b', re.I)
DATE_LIKE = re.compile(r'^\d{1,4}[-/.]\d{1,2}[-/.]\d{1,4}$')
NUMBER_LIKE = re.compile(r'^[+-]?\d+(?:[.,]\d+)?%?$')
REFERENCE_LIKE = re.compile(r'^(?=.*\d)[A-Za-z0-9][A-Za-z0-9_./\\-]{3,}$')
IDENTIFIER_LIKE = re.compile(r'^(?:[a-z]+(?:[A-Z][a-z0-9]+)+|[A-Za-z0-9]+(?:_[A-Za-z0-9]+)+|[A-Z]{2,}[A-Za-z0-9]*)$')
TOKEN = re.compile(r"[A-Za-z0-9_]+(?:['’][A-Za-z]+)?(?:[./\\-][A-Za-z0-9_]+)*|[^\sA-Za-z0-9_]")
SENTENCE_SPLIT = re.compile(r'(?<=[.!?;])\s+(?=[A-Z(\[“"])')
GERUND = re.compile(r'^(?:[a-z]+ing)\s+(?:of\s+)?', re.I)
DETERMINER = re.compile(r'^(?:a|an|the|each|every|any|all|this|that|these|those)\s+', re.I)
CUT_WORDS = (' from ', ' by ', ' for ', ' in ', ' on ', ' at ', ' to ', ' with ', ' which ', ' that ', ' where ', ' as ', ' of the ', ' under ', ' below ', ' above ')
STRONG_MODAL = re.compile(r'\b(shall|must|should|will|needs? to|has to|have to)\b', re.I)
VALIDATION_CUES = re.compile(r'\b(check(?:ed|ing|s)?|accept(?:ed|ance|ing)?|submit(?:ted|ting)?|submission|validat\w*|verif\w+|confirm\w*|mandatory|required|declar\w+|undertak\w+|consent|agree\w*)\b', re.I)


def _lexicon(policy):
    """Policy-level additions (policy['semantics']) extend the built-in language lexicon."""
    extra = ((policy or {}).get('semantics') or {})
    return {
        'admin_labels': [normalize(x) for x in extra.get('administrative_labels', [])],
        'regulatory': [x.lower() for x in extra.get('regulatory_terms', [])],
        'financial': [x.lower() for x in extra.get('financial_terms', [])],
        'user_blocking': [x.lower() for x in extra.get('submission_terms', [])],
        'user_facing': [x.lower() for x in extra.get('user_interface_terms', [])],
        'advisory': [x.lower() for x in extra.get('open_item_terms', [])],
        'form_labels': extra.get('form_label_patterns', {}),
        'facets': extra.get('requirement_facets', {}),
        'module_labels': [normalize(x) for x in extra.get('module_labels', [])],
        'dependency_labels': [x.lower() for x in extra.get('dependency_labels', [])],
    }


def has_term(text, terms):
    """Whole-word / word-prefix match (a term ending with '*' matches any continuation)."""
    low = ' ' + str(text or '').lower() + ' '
    hits = []
    for term in terms:
        if not term:
            continue
        if term.endswith('*'):
            pattern = r'(?<![a-z0-9])' + re.escape(term[:-1]) + r'[a-z0-9]*'
        else:
            pattern = r'(?<![a-z0-9])' + re.escape(term) + r'(?![a-z0-9])'
        if re.search(pattern, low):
            hits.append(term.rstrip('*'))
    return hits


# ---------------------------------------------------------------------------------------------------- tokens and units
def tokenize(text):
    """[(surface, lower, start, end)] with punctuation as single tokens."""
    return [(m.group(0), m.group(0).lower(), m.start(), m.end()) for m in TOKEN.finditer(text)]


def split_units(text):
    """Sentences of a clause (a clause can hold several independent statements)."""
    parts = [p.strip() for p in SENTENCE_SPLIT.split(str(text or '')) if p and p.strip()]
    merged = []
    for part in parts:
        # a fragment without a verb-like word or shorter than four words belongs to the previous sentence
        if merged and len(part.split()) < 4 and not STRONG_MODAL.search(part):
            merged[-1] = merged[-1] + ' ' + part
        else:
            merged.append(part)
    return merged or ([str(text).strip()] if str(text or '').strip() else [])


def align_units(old_units, new_units, threshold=0.5):
    """Pair sentences of two versions of the same clause. Returns (pairs, only_old, only_new, equal).

    ``pairs`` are (old, new, similarity) for sentences that differ; identical sentences are counted in ``equal``.
    Negation words are ignored for similarity, so "X is applicable" still matches "X is not applicable".
    """
    equal, old_left, new_left = 0, list(range(len(old_units))), list(range(len(new_units)))
    norm_old = [normalize(u) for u in old_units]
    norm_new = [normalize(u) for u in new_units]
    for j in list(new_left):
        for i in list(old_left):
            if norm_old[i] == norm_new[j]:
                old_left.remove(i)
                new_left.remove(j)
                equal += 1
                break
    sets_old = {i: token_set(old_units[i]) for i in old_left}
    sets_new = {j: token_set(new_units[j]) for j in new_left}
    scored = []
    for i in old_left:
        for j in new_left:
            a, b = sets_old[i], sets_new[j]
            if a and b:
                scored.append((len(a & b) / float(len(a | b)), i, j))
    scored.sort(key=lambda x: (-x[0], x[1], x[2]))
    pairs, used_i, used_j = [], set(), set()
    for score, i, j in scored:
        if score < threshold or i in used_i or j in used_j:
            continue
        pairs.append((old_units[i], new_units[j], round(score, 3)))
        used_i.add(i)
        used_j.add(j)
    # a clause that is one sentence on each side is the same statement even when it was heavily reworded
    if not pairs and len(old_left) == 1 and len(new_left) == 1:
        pairs.append((old_units[old_left[0]], new_units[new_left[0]], 0.0))
        used_i.add(old_left[0])
        used_j.add(new_left[0])
    only_old = [old_units[i] for i in old_left if i not in used_i]
    only_new = [new_units[j] for j in new_left if j not in used_j]
    return pairs, only_old, only_new, equal


# ---------------------------------------------------------------------------------------------------- predicates
def _head(word):
    for axis, words in PREDICATES.items():
        if word in words:
            return axis, words[word]
    return None


def _negated(tokens, index):
    """True when a negation sits directly before the predicate, allowing auxiliary words in between ("is not", "shall be not")."""
    steps, k = 0, index - 1
    while k >= 0 and steps < 4:
        word = tokens[k][1]
        if word in NEGATIONS:
            return True, k
        if word in FILLERS:
            k -= 1
            steps += 1
            continue
        break
    return False, index


def _role(tokens, index):
    """('condition', cue_index) when the predicate sits inside an if/when/unless clause, else ('outcome', None)."""
    k = index - 1
    while k >= 0:
        word = tokens[k][1]
        if word in CONDITION_CUES:
            return 'condition', k
        if word in BOUNDARIES:
            return 'outcome', None
        k -= 1
    return 'outcome', None


def _window_text(text, tokens, first, last):
    first, last = max(0, first), min(len(tokens) - 1, last)
    return text[tokens[first][2]:tokens[last][3]] if tokens and first <= last else ''


def _condition_subject(tokens, cue, index):
    words = [t[0] for t in tokens[cue + 1:index] if t[1] not in FILLERS and t[1] not in NEGATIONS and t[1] not in BOUNDARIES]
    return ' '.join(words[-4:])


def extract_predicates(text):
    """Business-meaning predicates of one sentence, in order. Each carries axis, effective value, role and surface wording."""
    tokens = tokenize(text)
    found = []
    taken = set()
    for index, (surface, low, start, end) in enumerate(tokens):
        head = _head(low)
        if not head:
            continue
        axis, (value, neg_value) = head
        # bare modal verbs only count when they negate a duty ("shall not") - the plain form is just grammar
        negated, neg_at = _negated(tokens, index)
        if axis == 'deontic' and low in ('shall', 'must', 'may', 'can') and not negated:
            continue
        if low == 'cannot':
            negated = False
        effective = neg_value if negated else value
        role, cue = _role(tokens, index)
        first = neg_at if negated else index
        span_first = first
        if role == 'condition' and cue is not None:
            subject = _condition_subject(tokens, cue, first)
            phrase_first = cue + 1
        else:
            subject = ''
            phrase_first = max(0, first - 3)
        phrase = _window_text(text, tokens, phrase_first, index)
        found.append({'axis': axis, 'value': effective, 'negated': negated, 'word': low, 'surface': _window_text(text, tokens, span_first, index),
                      'role': role, 'subject': subject, 'phrase': clip(phrase, 90), 'index': index, 'span': (span_first, index), 'cue': cue})
        taken.add(index)
    return found, tokens


def label_for(axis, value):
    return AXIS_LABEL.get(axis, {}).get(value, str(value))


def meaning(axis, value):
    """Plain wording of what a predicate means, e.g. deontic +1 -> 'optional'."""
    return label_for(axis, value)


def condition_and_outcome(text, tokens=None):
    """Split a sentence into (condition, outcome) wording; either may be ''. Quotes stay verbatim substrings."""
    tokens = tokens or tokenize(text)
    cue = next((i for i, t in enumerate(tokens) if t[1] in CONDITION_CUES), None)
    if cue is None:
        return '', text.strip()
    comma = next((i for i in range(cue + 1, len(tokens)) if tokens[i][1] in (',', ';', ':')), None)
    if cue <= 2 and comma is not None:           # leading "If ..., outcome"
        return _window_text(text, tokens, cue, comma - 1), text[tokens[comma][3]:].strip()
    end = len(tokens) - 1
    while end > cue and tokens[end][1] in BOUNDARIES:
        end -= 1
    return _window_text(text, tokens, cue, end), text[:tokens[cue][2]].strip(' ,;')


def sentence_subject(text, tokens=None, predicate_index=None):
    """The thing a sentence is about: the noun phrase before its first rule verb (or before the changed predicate)."""
    tokens = tokens or tokenize(text)
    modal = next((i for i, t in enumerate(tokens) if STRONG_MODAL.fullmatch(t[0]) and t[1] not in CONDITION_CUES), None)
    anchor = modal
    if anchor is None and predicate_index is not None:
        k = predicate_index
        while k > 0 and (tokens[k - 1][1] in FILLERS or tokens[k - 1][1] in NEGATIONS):
            k -= 1
        anchor = k
    if anchor is None:
        anchor = min(len(tokens) - 1, 6)
    prefix = text[:tokens[anchor][2]] if anchor < len(tokens) else text
    cue = next((i for i, t in enumerate(tokens[:anchor]) if t[1] in CONDITION_CUES), None)
    if cue is not None:
        comma = next((i for i in range(cue + 1, anchor) if tokens[i][1] == ','), None)
        if comma is not None:
            prefix = text[tokens[comma][3]:tokens[anchor][2]]
        else:
            prefix = text[:tokens[cue][2]]
    chunk = prefix.split(',')[-1].strip(' .;:-')
    for _ in range(2):
        chunk = DETERMINER.sub('', chunk)
        chunk = GERUND.sub('', chunk)
    chunk = DETERMINER.sub('', chunk)
    low = ' ' + chunk.lower() + ' '
    for word in CUT_WORDS:
        position = low.find(word)
        if position > 1:
            chunk = chunk[:position - 1]
            low = ' ' + chunk.lower() + ' '
    chunk = ' '.join(chunk.split()[:6]).strip(' .;:-')
    return chunk


def _flatten(tokens, spans, names):
    """Replace predicate spans by placeholders so wording differences outside predicates can be compared."""
    out, k = [], 0
    ordered = sorted(zip(spans, names))
    pointer = 0
    while k < len(tokens):
        if pointer < len(ordered) and ordered[pointer][0][0] == k:
            (first, last), name = ordered[pointer]
            out.append((name, name, tokens[first][2], tokens[last][3]))
            k = last + 1
            pointer += 1
            continue
        out.append(tokens[k])
        k += 1
    return out


def _classify_tokens(words):
    flat = [w for w in words if w not in BOUNDARIES]
    if flat and all(NUMBER_LIKE.match(w) for w in flat):
        return 'numeric'
    if flat and all(DATE_LIKE.match(w) for w in flat):
        return 'date'
    if flat and any(DATE_LIKE.match(w) or REFERENCE_LIKE.match(w) for w in flat) and not any(w.isalpha() and len(w) > 3 and not REFERENCE_LIKE.match(w) for w in flat):
        return 'reference'
    return ''


def _comparator(tokens, index):
    for k in range(index - 1, max(-1, index - 5), -1):
        if tokens[k][1] in COMPARATORS:
            return COMPARATORS[tokens[k][1]], tokens[k][1]
        if tokens[k][1] in BOUNDARIES:
            break
    return '', ''


def _unit_word(tokens, index):
    for k in range(index + 1, min(len(tokens), index + 3)):
        word = tokens[k][1]
        if word == '%' or (word.isalpha() and len(word) > 2 and word not in FILLERS and word not in NEGATIONS):
            return tokens[k][0]
        break
    return ''


# ---------------------------------------------------------------------------------------------------- pair analysis
def analyse_pair(old, new, lex=None):
    """Compare the old and new wording of one statement. Returns the rule-level deltas and the statement structure."""
    old_preds, old_tokens = extract_predicates(old)
    new_preds, new_tokens = extract_predicates(new)
    deltas, names_old, names_new = [], {}, {}

    def key(pred):
        return (pred['axis'], pred['role'])

    grouped_new = {}
    for pred in new_preds:
        grouped_new.setdefault(key(pred), []).append(pred)
    used_new = set()
    ordinals = {}
    for pred in old_preds:
        k = key(pred)
        ordinal = ordinals.get(k, 0)
        ordinals[k] = ordinal + 1
        partner = grouped_new.get(k, [])[ordinal] if ordinal < len(grouped_new.get(k, [])) else None
        if partner is None:
            names_old[id(pred)] = '⟦%s-%s-old-%d⟧' % (k[0], k[1], ordinal)
            deltas.append(_delta_removed_predicate(pred))
            continue
        used_new.add(id(partner))
        placeholder = '⟦%s-%s-%d⟧' % (k[0], k[1], ordinal)
        names_old[id(pred)] = placeholder
        names_new[id(partner)] = placeholder
        if pred['value'] != partner['value']:
            deltas.append(_delta_flip(pred, partner, old, new, old_tokens, new_tokens))
    for pred in new_preds:
        if id(pred) not in used_new:
            names_new[id(pred)] = '⟦%s-%s-new⟧' % (pred['axis'], pred['role'])
            deltas.append(_delta_added_predicate(pred))
    flat_old = _flatten(old_tokens, [p['span'] for p in old_preds], [names_old[id(p)] for p in old_preds])
    flat_new = _flatten(new_tokens, [p['span'] for p in new_preds], [names_new[id(p)] for p in new_preds])
    matcher = difflib.SequenceMatcher(a=[t[1] for t in flat_old], b=[t[1] for t in flat_new], autojunk=False)
    api_sentence = bool(API_MARKERS.search(old + ' ' + new))
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == 'equal':
            continue
        a_words = [t[0] for t in flat_old[i1:i2]]
        b_words = [t[0] for t in flat_new[j1:j2]]
        a_text = ' '.join(a_words)
        b_text = ' '.join(b_words)
        kind = _classify_tokens(a_words + b_words) if (a_words and b_words) or tag != 'replace' else ''
        if kind == '' and a_words and b_words:
            kind = _classify_tokens(a_words) if _classify_tokens(a_words) == _classify_tokens(b_words) else ''
        has_comparator = bool(b_words and _comparator(flat_new, j1)[0]) or bool(a_words and _comparator(flat_old, i1)[0])
        if kind in ('numeric', 'reference') and api_sentence and not has_comparator:
            kind = 'api'      # a number or code inside an interface description is an interface detail, not a business threshold
        if kind == 'numeric':
            comp, comp_word = _comparator(flat_new, j1) if b_words else ('', '')
            deltas.append({'kind': 'numeric', 'role': 'statement', 'axis': 'numeric', 'subject': '', 'old': a_text, 'new': b_text,
                           'comparator': comp or _comparator(flat_old, i1)[0], 'unit': _unit_word(flat_new, j2 - 1) if b_words else '',
                           'summary': 'Value changed from %s to %s%s.' % (a_text or '(none)', b_text or '(none)', (' (%s)' % (comp_word or '')).replace(' ()', ''))})
        elif kind in ('date', 'reference'):
            deltas.append({'kind': kind, 'role': 'statement', 'axis': kind, 'subject': '', 'old': a_text, 'new': b_text,
                           'summary': '%s changed from %s to %s.' % ('Date' if kind == 'date' else 'Reference value', a_text or '(none)', b_text or '(none)')})
        elif kind == 'api' or (api_sentence and any(IDENTIFIER_LIKE.match(w) or NUMBER_LIKE.match(w) for w in a_words + b_words)):
            deltas.append({'kind': 'api', 'role': 'statement', 'axis': 'api', 'subject': '', 'old': a_text, 'new': b_text,
                           'summary': 'API or interface detail changed from "%s" to "%s".' % (clip(a_text, 60) or '(none)', clip(b_text, 60) or '(none)')})
        else:
            deltas.append({'kind': 'wording', 'role': 'statement', 'axis': 'wording', 'subject': '', 'old': clip(a_text, 160), 'new': clip(b_text, 160),
                           'summary': _wording_summary(a_text, b_text)})
    subject = sentence_subject(new, new_tokens, next((p['index'] for p in new_preds if p['role'] == 'outcome'), None)) or sentence_subject(old, old_tokens)
    old_cond, old_out = condition_and_outcome(old, old_tokens)
    new_cond, new_out = condition_and_outcome(new, new_tokens)
    for delta in deltas:
        if delta['kind'] == 'flip' and delta['role'] == 'outcome' and not delta['subject']:
            delta['subject'] = subject
            delta['summary'] = delta['summary'].replace('(subject)', subject or 'the statement')
    administrative = bool(deltas) and all(d['kind'] in ('date', 'reference') for d in deltas) and not (old_preds or new_preds)
    if administrative:
        subject = ''
    return {'deltas': deltas, 'subject': subject, 'condition_old': old_cond, 'condition_new': new_cond, 'outcome_old': old_out, 'outcome_new': new_out,
            'administrative': administrative, 'predicates_old': _public(old_preds), 'predicates_new': _public(new_preds)}


def _wording_summary(a_text, b_text):
    if a_text and b_text:
        return 'Wording changed from "%s" to "%s".' % (clip(a_text, 90), clip(b_text, 90))
    if b_text:
        return 'Wording added: "%s".' % clip(b_text, 120)
    return 'Wording removed: "%s".' % clip(a_text, 120)


def _public(preds):
    return [{k: v for k, v in p.items() if k not in ('index', 'span', 'cue')} for p in preds]


def _surface(pred):
    return pred['surface'] if pred['surface'] else pred['word']


def _delta_flip(old_pred, new_pred, old, new, old_tokens, new_tokens):
    axis = new_pred['axis']
    condition = new_pred['role'] == 'condition'
    subject = new_pred['subject'] or old_pred['subject']
    old_label, new_label = label_for(axis, old_pred['value']), label_for(axis, new_pred['value'])
    role_word = 'Condition' if condition else 'Outcome'
    who = (subject + ' ') if subject else ''
    summary = '%s changed: %s%s → %s%s' % (role_word, who, _surface(old_pred), who, _surface(new_pred))
    if label_for(axis, old_pred['value']) not in (_surface(old_pred),) or label_for(axis, new_pred['value']) not in (_surface(new_pred),):
        summary += ' (meaning: %s → %s)' % (old_label, new_label)
    return {'kind': 'flip', 'axis': axis, 'axis_name': AXIS_NAME.get(axis, axis), 'role': new_pred['role'], 'subject': subject,
            'old': old_label, 'new': new_label, 'old_surface': _surface(old_pred), 'new_surface': _surface(new_pred),
            'old_phrase': old_pred['phrase'], 'new_phrase': new_pred['phrase'], 'old_value': old_pred['value'], 'new_value': new_pred['value'],
            'inversion': old_pred['value'] == -new_pred['value'] or (axis == 'deontic' and {old_pred['value'], new_pred['value']} == {2, 1}),
            'summary': summary + '.'}


def _delta_removed_predicate(pred):
    label = label_for(pred['axis'], pred['value'])
    who = (pred['subject'] + ' ') if pred['subject'] else ''
    return {'kind': 'predicate_removed', 'axis': pred['axis'], 'axis_name': AXIS_NAME.get(pred['axis'], pred['axis']), 'role': pred['role'],
            'subject': pred['subject'], 'old': label, 'new': '', 'old_surface': _surface(pred), 'old_phrase': pred['phrase'],
            'summary': '%s no longer stated: %s%s.' % ('Condition' if pred['role'] == 'condition' else 'Outcome', who, _surface(pred))}


def _delta_added_predicate(pred):
    label = label_for(pred['axis'], pred['value'])
    who = (pred['subject'] + ' ') if pred['subject'] else ''
    return {'kind': 'predicate_added', 'axis': pred['axis'], 'axis_name': AXIS_NAME.get(pred['axis'], pred['axis']), 'role': pred['role'],
            'subject': pred['subject'], 'old': '', 'new': label, 'new_surface': _surface(pred), 'new_phrase': pred['phrase'],
            'summary': '%s newly stated: %s%s.' % ('Condition' if pred['role'] == 'condition' else 'Outcome', who, _surface(pred))}


# ---------------------------------------------------------------------------------------------------- single statements
def describe_statement(text):
    """Structure of one statement without a baseline (added / removed / proposed): its rules, conditions, numbers and identifiers."""
    preds, tokens = extract_predicates(text)
    condition, outcome = condition_and_outcome(text, tokens)
    subject = sentence_subject(text, tokens, next((p['index'] for p in preds if p['role'] == 'outcome'), None))
    numbers = []
    for index, (surface, low, start, end) in enumerate(tokens):
        if NUMBER_LIKE.match(low) and not DATE_LIKE.match(low):
            comp, comp_word = _comparator(tokens, index)
            numbers.append({'value': surface, 'comparator': comp, 'comparator_word': comp_word, 'unit': _unit_word(tokens, index)})
    identifiers = sorted({t[0] for t in tokens if IDENTIFIER_LIKE.match(t[0]) and len(t[0]) > 3})[:10]
    method_ids = sorted(set(re.findall(r'(?i)method\s*id\s*(?:->|:|=|→)?\s*["“]?(\d{2,6})', text)))
    return {'subject': subject, 'condition': condition, 'outcome': outcome, 'predicates': _public(preds), 'numbers': numbers[:8],
            'identifiers': identifiers, 'method_ids': method_ids, 'api': bool(API_MARKERS.search(text)),
            'modal': bool(STRONG_MODAL.search(text)), 'deontic': [p for p in _public(preds) if p['axis'] == 'deontic']}


# ---------------------------------------------------------------------------------------------------- classification
def is_administrative(clause_text, label, analysis, lex):
    """Administrative / reference information: a form field, number, date or signature block, not system behaviour."""
    head = normalize(str(clause_text).split(' | ')[0]) if ' | ' in str(clause_text) else ''
    labels = lex.get('admin_labels') or []
    if labels:
        for candidate in (head, normalize(label)):
            if candidate and any(candidate == item or candidate.startswith(item + ' ') for item in labels):
                return True
    return bool(analysis and analysis.get('administrative'))


def policy_categories(text, policy):
    """Category by whole-word keyword evidence (a stem ending in '*' matches any continuation).

    Substring matching reported "rest" inside "request" and "api" inside "capital"; keywords now match whole words.
    """
    hits = {}
    for category, words in (policy.get('category_keywords') or {}).items():
        found = has_term(text, [w if len(w) > 6 and not w.endswith(('s', 'e', 'y')) else w for w in words])
        if found:
            hits[category] = found
    return hits


def semantic_category(deltas, text, fallback, administrative):
    """Category refined by the business meaning of the deltas (never by a document-specific word)."""
    if administrative:
        return 'Administrative'
    kinds = {d['kind'] for d in deltas}
    axes = {d.get('axis') for d in deltas}
    if 'api' in kinds:
        return 'API Contract'
    if axes & {'deontic', 'validity', 'approval'} or (axes & {'applicability', 'exemption'} and VALIDATION_CUES.search(text or '')):
        return 'Validation'
    if axes & {'applicability', 'exemption', 'numeric'}:
        return 'Business Rule'
    if axes & {'visibility', 'enablement', 'state'}:
        return 'Data Field' if fallback in ('Uncategorised', 'Functional Requirement') else fallback
    return fallback


def facet(category, facets=None):
    table = {'Validation': 'validation', 'Business Rule': 'business rule', 'Data Field': 'field behaviour', 'API Contract': 'API contract',
             'Integration': 'integration', 'Workflow': 'workflow', 'User Permission': 'access rule', 'Database Behavior': 'data handling',
             'Error Handling': 'error handling', 'Non-Functional Requirement': 'non-functional requirement', 'Administrative': 'reference information',
             'Functional Requirement': 'requirement', 'Uncategorised': 'requirement'}
    table.update(facets or {})
    return table.get(category, 'requirement')


def requirement_title(subject, category, facets=None):
    subject = (subject or '').strip()
    if not subject:
        return ''
    subject = subject[:1].upper() + subject[1:]
    return '%s %s' % (subject, facet(category, facets))


# ---------------------------------------------------------------------------------------------------- narrative (derived wording)
def impact_text(change):
    """Business / technical / QA impact sentences derived from the deltas. Each entry is {'text', 'basis'}.

    basis 'SOURCE' means the sentence only restates what the documents say; 'DERIVED' means the engine reasoned from
    the kind of change (an interpretation to confirm with the business analyst), never a claim of fact.
    """
    deltas = [d for d in change.get('deltas', []) if d['kind'] not in ('wording',)] or change.get('deltas', [])
    business, technical, qa = [], [], []
    subject = change.get('subject') or 'the requirement'
    if change.get('administrative'):
        business.append({'text': 'Reference or administrative information changed (%s). No business rule or system behaviour is described as changed.'
                         % '; '.join(clip(d['summary'], 120) for d in change.get('deltas', [])[:3]), 'basis': 'SOURCE'})
        technical.append({'text': 'No technical impact is stated. Update document references or traceability records only.', 'basis': 'DERIVED'})
        qa.append({'text': 'No functional regression is required for this edit. Check that test documents cite the current reference.', 'basis': 'DERIVED'})
        return {'business': business, 'technical': technical, 'qa': qa}
    for d in deltas[:6]:
        axis = d.get('axis')
        if d['kind'] == 'flip' and d['role'] == 'condition':
            who = d.get('subject') or 'the condition'
            business.append({'text': 'The condition that controls when %s applies changed from "%s" to "%s". Cases that used to trigger the rule may no longer trigger it, '
                             'and the reverse.' % (subject, d.get('old_phrase') or d['old'], d.get('new_phrase') or d['new']), 'basis': 'DERIVED'})
            technical.append({'text': 'The validation or workflow branch that tests "%s" (%s) must use the new truth value; both states of "%s" need to be handled.'
                              % (who, AXIS_NAME.get(axis, axis).lower(), who), 'basis': 'DERIVED'})
            qa.append({'text': 'Test "%s" in both states (%s and %s) and combine each with the outcome %s.' % (who, d['old'], d['new'], subject), 'basis': 'DERIVED'})
        elif d['kind'] == 'flip' and axis == 'deontic':
            business.append({'text': 'The duty for %s changed from %s to %s%s.' % (subject, d['old'], d['new'],
                                                                              ' (the opposite)' if d.get('inversion') else ''), 'basis': 'DERIVED'})
            technical.append({'text': 'Check any submission or validation rule that enforces "%s": whether it is now required, optional or refused must follow the new wording.' % subject, 'basis': 'DERIVED'})
            qa.append({'text': 'Verify submission with %s completed and not completed; confirm the system result matches %s.' % (subject, d['new']), 'basis': 'DERIVED'})
        elif d['kind'] == 'flip':
            business.append({'text': '%s of %s changed from %s to %s.' % (AXIS_NAME.get(axis, axis), subject, d['old'], d['new']), 'basis': 'DERIVED'})
            technical.append({'text': 'The attribute "%s" for %s must follow the new state.' % (AXIS_NAME.get(axis, axis).lower(), subject), 'basis': 'DERIVED'})
            qa.append({'text': 'Verify both %s and %s for %s.' % (d['old'], d['new'], subject), 'basis': 'DERIVED'})
        elif d['kind'] == 'numeric':
            business.append({'text': 'A numeric value changed from %s to %s%s. Everything that relies on the old limit changes with it.' % (d['old'] or '(none)', d['new'] or '(none)', (' ' + d['unit']) if d.get('unit') else ''), 'basis': 'DERIVED'})
            technical.append({'text': 'Update the threshold in validation, configuration or integration data that uses %s.' % (d['old'] or 'the old value'), 'basis': 'DERIVED'})
            qa.append({'text': 'Boundary tests: the old value, the new value, one step below and one step above.', 'basis': 'DERIVED'})
        elif d['kind'] == 'api':
            business.append({'text': 'An interface detail changed (%s).' % clip(d['summary'], 140), 'basis': 'SOURCE'})
            technical.append({'text': 'The request, response or identifier shown changed; confirm the contract with the provider and update clients.', 'basis': 'DERIVED'})
            qa.append({'text': 'API contract and backward-compatibility checks for the changed identifier.', 'basis': 'DERIVED'})
        elif d['kind'] in ('predicate_added', 'predicate_removed'):
            business.append({'text': d['summary'], 'basis': 'SOURCE'})
            technical.append({'text': 'Rule structure changed; check every branch of the related validation.', 'basis': 'DERIVED'})
            qa.append({'text': 'Add tests for the %s branch.' % ('new' if d['kind'] == 'predicate_added' else 'removed'), 'basis': 'DERIVED'})
        elif d['kind'] == 'wording':
            business.append({'text': d['summary'], 'basis': 'SOURCE'})
    if change.get('change_type') == 'ADDED':
        business.insert(0, {'text': 'A new statement was added to the requirements: "%s".' % clip(change.get('modified_text') or '', 200), 'basis': 'SOURCE'})
        qa.append({'text': 'No existing test can cover new wording; design tests from the statement itself.', 'basis': 'DERIVED'})
    if change.get('change_type') == 'REMOVED':
        business.insert(0, {'text': 'A statement no longer appears in the changed version: "%s".' % clip(change.get('original_text') or '', 200), 'basis': 'SOURCE'})
        qa.append({'text': 'Tests that verified the removed behaviour may need to be retired or updated after a reviewer decides.', 'basis': 'DERIVED'})
    return {'business': _dedupe_text(business), 'technical': _dedupe_text(technical), 'qa': _dedupe_text(qa)}


def _dedupe_text(items):
    seen, out = set(), []
    for item in items:
        if item['text'] not in seen:
            seen.add(item['text'])
            out.append(item)
    return out


def signature(change_type, old_unit, new_unit):
    """Identity of a logical change independent of where it occurs: the same difference in many places is one change."""
    return digest([change_type, normalize(old_unit or ''), normalize(new_unit or '')])
