"""Change identification clean-up (engine 2.0).

The comparison engine reports differences sentence by sentence. A business reader thinks in REQUIREMENTS, so here:

* sentences changed inside the same clause (the same table row or paragraph) become ONE changed requirement;
* a requirement id written in the text (PSW-API-002) becomes the reference of the change;
* document headers, table header rows, incomplete fragments and QA-authoring instructions ("QA AI Studio should generate test cases
  with ...") are set aside with a stated reason: they are not product requirements and must not drive dependencies or risk;
* the same requirement repeated in several places is reported once, with every place kept.

Nothing is invented: every text here is a verbatim string of the compared documents.
"""
import re

from Core.change_impact.changes import CATEGORY_ORDER
from Core.change_impact.util import clip, normalize, stable_id
from Core.cia import terms as T

FIGURE_LINE = re.compile(r'^\s*(?:figure|fig\.|screenshot|image|picture|table|diagram)\s*\d+(?:\.\d+)*\s*[:.\-\u2013]', re.I)
HEADING_LINE = re.compile(r'^\s*(?:requirement|req\.?|section|item|change)\s*\d+(?:\.\d+)*\s*[:.\-\u2013]\s*[^.]{0,60}$', re.I)
CAPTION = re.compile(r'^\s*(figure|fig\.|screenshot|image|picture|table|diagram)\b', re.I)


def change_text(change):
    return ' '.join(x for x in (change.get('modified_text'), change.get('original_text')) if x)


def display_text(change):
    return (change.get('modified_text') or change.get('original_text') or '').strip()


def reference_from_text(text):
    """The first requirement id written in the text, in document order."""
    best = None
    for m in T.NAMESPACED_ID.finditer(str(text or '')):
        if T.norm_id(m.group(0)).split('-')[0] in T.NOT_ID_PREFIX:
            continue
        best = m if best is None or m.start() < best.start() else best
        break
    plain = T.PLAIN_ID.search(str(text or ''))
    if plain and (best is None or plain.start() < best.start()) and not (best and best.start() <= plain.start() < best.end()):
        best = plain
    return T.norm_id(best.group(0)) if best else ''


def _group_key(change):
    occ = (change.get('occurrences') or [{}])[0]
    return (occ.get('location') or '', occ.get('anchor'), change.get('change_kind'))


def merge_by_requirement(changes):
    """One changed requirement per clause. Returns a new list; the inputs are not modified."""
    groups, order = {}, []
    for change in changes:
        key = _group_key(change)
        if key[1] is None:
            key = (key[0], id(change), key[2])
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(change)
    merged = []
    for key in order:
        members = groups[key]
        merged.append(members[0] if len(members) == 1 else _merge(members))
    return merged


def _merge(members):
    base = dict(members[0])
    texts_new = _unique(m.get('modified_text') for m in members)
    texts_old = _unique(m.get('original_text') for m in members)
    base['modified_text'] = ' '.join(texts_new) or None
    base['original_text'] = ' '.join(texts_old) or None
    seen, evidence = set(), []
    for m in members:
        for e in m.get('evidence') or []:
            if e['ref'] not in seen:
                seen.add(e['ref'])
                evidence.append(e)
    base['evidence'] = evidence
    base['deltas'] = [d for m in members for d in (m.get('deltas') or [])]
    types = {m.get('change_type') for m in members}
    if len(types) > 1:
        base['change_type'] = 'MODIFIED' if 'MODIFIED' in types else members[0].get('change_type')
    cats = [m.get('category') for m in members if m.get('category')]
    base['category'] = min(cats, key=lambda c: CATEGORY_ORDER.index(c) if c in CATEGORY_ORDER else 99) if cats else base.get('category')
    base['administrative'] = all(m.get('administrative') for m in members)
    base['functional'] = not base['administrative']
    base['occurrences'] = [o for m in members for o in (m.get('occurrences') or [])]
    base['occurrence_count'] = len(base['occurrences'])
    base['merged_ids'] = [m['change_id'] for m in members]
    base['merged_count'] = len(members)
    base['reference'] = next((m.get('reference') for m in members if m.get('reference') and not str(m['reference']).startswith('Table')), base.get('reference') or '')
    base['description'] = '%s (+%d more statement(s) of the same requirement)' % (members[0].get('description') or '', len(members) - 1)
    base['subject'] = next((m.get('subject') for m in members if m.get('subject')), '')
    return base


def _unique(values):
    out = []
    for v in values:
        v = (v or '').strip()
        if v and v not in out:
            out.append(v)
    return out


def internal_reference(change):
    """A stable internal reference for a requirement that carries no identifier in the document.

    It is derived from the requirement's own text, so the same requirement gets the same reference in every run, and it is
    always shown as internal: it is never a document identifier.
    """
    return 'INT-' + stable_id('R', normalize(display_text(change)), length=6).split('-')[-1].upper()


def with_reference(change):
    """Reference of a changed requirement, with its kind.

    document   an identifier written in the text (PSW-API-002) or the document's own heading ("Requirement 1");
    internal   nothing is written in the document: a stable INT-xxxxxx reference is generated and marked as internal.
    A table row or location label ("Table 2, Row 3") is not a requirement reference.
    """
    text = display_text(change)
    ref = reference_from_text(text)
    if ref:
        change['reference'], change['reference_kind'] = ref, 'document'
        return change
    current = str(change.get('reference') or '').strip()
    if current and not re.match(r'^(table|row|cell|body|header|footer)\b', current, re.I):
        change['reference_kind'] = 'document'
        return change
    change['reference'], change['reference_kind'] = internal_reference(change), 'internal'
    return change


def noise_reason(change):
    text = display_text(change)
    if change.get('administrative'):
        return 'Administrative text (title, header, approval or revision detail)'
    if CAPTION.match(text) and (len(text.split()) <= 14 or FIGURE_LINE.match(text)):
        return 'Figure or screenshot caption'
    if T.form_field_row(text):
        return 'Change-request form field (not a requirement)'
    if T.json_fragment(text):
        return 'Sample data line (not a requirement)'
    if HEADING_LINE.match(text):
        return 'Label or heading without a requirement'
    kind = T.noise_kind(text)
    if kind:
        return kind
    if len(T.keywords(text)) < 2:
        return 'Label or heading without a requirement'
    if ' | ' in text and len(text.split()) <= 12 and len(T.context_words(text, T.hard_keys(text))) < 2:
        return 'Label or heading without a requirement'          # a short "label | value" table row that carries no requirement words
    return ''


def dedupe(changes):
    """The same requirement text in several places is one change with several occurrences."""
    out, index = [], {}
    for change in changes:
        key = normalize(display_text(change))
        if key in index:
            base = index[key]
            base['occurrences'] = list(base.get('occurrences') or []) + list(change.get('occurrences') or [])
            base['occurrence_count'] = len(base['occurrences'])
            known = {e['ref'] for e in base.get('evidence') or []}
            base['evidence'] = list(base.get('evidence') or []) + [e for e in change.get('evidence') or [] if e['ref'] not in known]
            base['duplicates_removed'] = base.get('duplicates_removed', 0) + 1
            places = [o.get('location') or ('position %s' % o.get('anchor')) for o in base['occurrences']]
            base['occurrence_note'] = 'The same requirement occurs %d times in the document (%s); it is reported once.' % (base['occurrence_count'], '; '.join(places[:8]))
            continue
        index[key] = change
        out.append(change)
    return out


def prepare(changes):
    """(functional requirement-level changes, [(change, reason)] set aside)."""
    combined = dedupe([with_reference(c) for c in merge_by_requirement(changes)])
    kept, aside = [], []
    for change in combined:
        reason = noise_reason(change)
        (aside if reason else kept).append((change, reason) if reason else change)
    return kept, aside
