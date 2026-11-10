"""Document-level reasoning for Change Impact: contradictions, open questions, stated scope, risk signals and regression scenarios.

Works only from the text of the compared documents plus the semantic fields the change engine already derived.
Nothing here is specific to a document: statements are compared by what they say about the same subject, and every
conclusion is either a quotation (source fact) or labelled as derived (a question for the business analyst).
Conflicts are reported, never resolved automatically.
"""
import re

from Core.change_impact import semantics
from Core.change_impact.changes import _evidence
from Core.change_impact.util import clip, digest, normalize, stable_id

GENERIC_TOPIC = frozenset(('requirement', 'system', 'user', 'users', 'field', 'data', 'value', 'values', 'screen', 'page', 'interface', 'form', 'section',
                           'information', 'details', 'statement', 'rule', 'step', 'item', 'list', 'case', 'part', 'type', 'name', 'number', 'code',
                           'exporter', 'importer', 'agent', 'authorized', 'authorised', 'their', 'there', 'which', 'shall', 'must', 'should'))
CONSEQUENCE = re.compile(r'\b(error|message|alert|warning|block\w*|prevent\w*|reject\w*|refus\w*|unable|cannot|disabled?|stop\w*|fail\w*|notification|denied)\b', re.I)
PLACEMENT = re.compile(r'\b(?:on|in|below|above|under|next to|within|at|beneath)\s+(?:the\s+)?((?:[A-Z][A-Za-z0-9\-]*)(?:\s+(?:and|of|for|&)\s+[A-Z][A-Za-z0-9\-]*|\s+[A-Z][A-Za-z0-9\-]*){0,5}\s+(?:interface|screen|page|tab|dialog|window|panel))\b')
AREA = re.compile(r'\b((?:[A-Z][A-Za-z0-9\-]*)(?:\s+(?:and|of|for|&)\s+[A-Z][A-Za-z0-9\-]*|\s+[A-Z][A-Za-z0-9\-]*){0,5})\s+(interface|screen|page|grid|form|tab|section|dialog|window|panel|report|api|portal|gateway|service|module|system)\b')
LEADING_SCOPE = re.compile(r'^\s*(?i:in|within|for|on)\s+(?i:the\s+)?([A-Z][A-Za-z0-9\-]*(?:\s+(?:and\s+)?[A-Z][A-Za-z0-9\-]*){0,6}),')
NA_VALUES = frozenset(('n/a', 'na', 'none', 'no', 'nil', '-', 'not applicable', ''))
CONTROL_WORDS = re.compile(r'\b(check\w*|tick\w*|select\w*|accept\w*|agree\w*|confirm\w*)\b', re.I)


def topic_terms(subject):
    words = re.split(r'[^a-z0-9]+', str(subject or '').lower())
    return {w for w in words if len(w) > 3 and w not in GENERIC_TOPIC}


def shares_topic(topic, statement):
    """True when a statement is about the topic: its own subject overlaps, or its text names every (up to two) topic words."""
    if not topic:
        return False
    if statement['topic'] & topic:
        return True
    need = min(2, len(topic))
    return len(topic & topic_terms(statement['text'])) >= need


def expand_acronym(pool_text, acronym):
    """"FI" -> "Financial Instrument" when the document spells it out (initials of consecutive capitalised words)."""
    acronym = str(acronym or '').strip()
    if not re.fullmatch(r'[A-Z]{2,6}', acronym):
        return ''
    explicit = re.search(r'\b((?:[A-Z][a-z]+\s+){1,4}[A-Z][a-z]+)\s*\(\s*%s\s*\)' % re.escape(acronym), pool_text)
    if explicit:
        words = explicit.group(1).split()
        return ' '.join(words[-len(acronym):]) if len(words) >= len(acronym) else explicit.group(1)
    words = re.findall(r'\b[A-Z][a-z]+\b', pool_text)
    n = len(acronym)
    for i in range(len(words) - n + 1):
        if ''.join(w[0] for w in words[i:i + n]) == acronym:
            return ' '.join(words[i:i + n])
    return ''


def expand_conjunction(phrase):
    """"Single Declaration Export and Import" -> ["Single Declaration Export", "Single Declaration Import"]."""
    match = re.match(r'^(.*\s)(\w+)\s+and\s+(\w+)$', phrase.strip())
    if match and len(match.group(1).split()) >= 2 and match.group(2)[:1].isupper() and match.group(3)[:1].isupper():
        return [match.group(1) + match.group(2), match.group(1) + match.group(3)]
    return [phrase.strip()]


def extract_areas(text):
    """Named screens, interfaces, grids and systems in a sentence, e.g. "Review and Validate interface"."""
    found, seen = [], set()
    for match in AREA.finditer(str(text or '')):
        name = '%s %s' % (match.group(1), match.group(2))
        key = name.lower()
        if key in seen or match.group(1).lower() in ('the', 'in', 'on'):
            continue
        seen.add(key)
        kind = 'system' if match.group(2).lower() in ('api', 'portal', 'gateway', 'service', 'system') else ('module' if match.group(2).lower() == 'module' else 'interface')
        before = str(text or '')[:match.start()]
        reference = bool(re.search(r'(?i)\b(?:below|above|under|beneath|underneath|beside|next\s+to|near|after|before|alongside|from)\s+(?:the\s+)?$', before))
        found.append({'name': name, 'kind': kind, 'role': 'reference' if reference else 'host'})
    return found


def scope_modules(text):
    match = LEADING_SCOPE.match(str(text or ''))
    if not match:
        return []
    phrase = match.group(1).strip()
    if len(phrase.split()) < 2:
        return []
    return expand_conjunction(phrase)


# ---------------------------------------------------------------------------------------------------- form facts
def form_facts(document, lex):
    """Facts stated by form fields ("Label | value"): affected modules and stated dependencies on other systems."""
    modules, dependencies, fields = [], [], []
    module_labels = [normalize(x) for x in (lex.get('module_labels') or [])]
    dep_labels = [x for x in (lex.get('dependency_labels') or []) if x]
    dep_pattern = re.compile(r'(?i)(?:%s)\s+([A-Za-z][A-Za-z0-9 .&\-]{1,30}?)\s*(?:\(|\?|$)' % '|'.join(re.escape(x) for x in sorted(dep_labels, key=len, reverse=True))) if dep_labels else None
    for clause in document['clauses']:
        text = clause['text']
        if ' | ' not in text:
            continue
        label, _, value = text.partition(' | ')
        low = normalize(label)
        if not value.strip():
            continue
        fields.append({'label': label.strip(), 'value': value.strip(), 'location': clause.get('location', '')})
        if low in module_labels or any(low.startswith(m + ' ') for m in module_labels):
            for item in value.split(' / '):
                item = item.strip(' ,;')
                if item and normalize(item) not in NA_VALUES:
                    modules.append({'name': item, 'location': clause.get('location', ''), 'evidence': _evidence('changed', document, clause, None, text)})
        match = dep_pattern.search(label) if dep_pattern else None
        if match:
            answer = value.split(' | ')[0].strip()
            if normalize(answer) not in NA_VALUES:
                dependencies.append({'system': match.group(1).strip(), 'answer': clip(answer, 120), 'location': clause.get('location', ''),
                                     'evidence': _evidence('changed', document, clause, None, text)})
    return {'modules': modules, 'dependencies': dependencies, 'fields': fields[:200]}


# ---------------------------------------------------------------------------------------------------- statements
def statements(document):
    """Every sentence of a document with its rule structure (used to compare statements about the same subject)."""
    out = []
    for clause in document['clauses']:
        for unit in semantics.split_units(clause['text']):
            info = semantics.describe_statement(unit)
            deontic = [p for p in info['predicates'] if p['axis'] == 'deontic' and p['role'] == 'outcome']
            conditions = [{'axis': p['axis'], 'subject': normalize(p['subject']), 'value': p['value'], 'surface': p['surface'], 'phrase': p['phrase']}
                          for p in info['predicates'] if p['role'] == 'condition']
            value = deontic[0]['value'] if deontic else None
            if value is None and semantics.STRONG_MODAL.search(info['outcome'] or unit) and CONTROL_WORDS.search(unit):
                value = 2
            out.append({'text': unit, 'clause': clause, 'subject': info['subject'], 'topic': topic_terms(info['subject']), 'deontic': value,
                        'deontic_surface': deontic[0]['surface'] if deontic else '', 'conditions': conditions,
                        'placements': sorted({m.group(1) for m in PLACEMENT.finditer(unit)}), 'info': info,
                        'context': clause.get('context', ''), 'location': clause.get('location', '')})
    return out


def _form_noise(text, lex):
    """Form headers and empty answers ("Alternatives to the Proposed Change | N/A") are not open decisions."""
    if ' | ' not in text:
        return False
    cells = [normalize(c) for c in text.split(' | ')]
    labels = lex.get('admin_labels') or []
    if all(c in labels or c in NA_VALUES for c in cells):
        return True
    return normalize(cells[-1]) in NA_VALUES


def _ref_for(document, statement):
    return _evidence('changed', document, statement['clause'], None, statement['text'])


def _where(statement):
    bits = [statement['context'], statement['location']]
    return ', '.join(b for b in bits if b) or 'document body'


def _mutually_exclusive(a, b):
    for ca in a['conditions']:
        for cb in b['conditions']:
            if ca['axis'] == cb['axis'] and ca['subject'] == cb['subject'] and ca['value'] != cb['value']:
                return True
    return False


def _opposed(da, db):
    if da is None or db is None or da == db:
        return False
    return (da >= 1 and db <= -2) or (db >= 1 and da <= -2) or {da, db} == {2, 1}


def _components(edges, size):
    parent = list(range(size))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x
    for a, b in edges:
        parent[find(a)] = find(b)
    groups = {}
    for i in range(size):
        groups.setdefault(find(i), []).append(i)
    return [g for g in groups.values() if len(g) > 1]


def analyse_document(baseline, changed, changes, policy, lex):
    """Contradictions, open questions and per-change signals. Returns {'questions', 'facts', 'signals', 'statements'}."""
    new_statements = statements(changed)
    old_statements = statements(baseline) if baseline else []
    facts = form_facts(changed, lex)
    new_hashes = {normalize(s['text']) for s in new_statements}
    old_hashes = {normalize(s['text']) for s in old_statements}
    changed_hashes = {normalize(c['modified_text']) for c in changes if c.get('modified_text')}
    questions = []

    def new_question(kind, severity, title, text, items, changes_ids, why):
        qid = stable_id('OQ', kind, title, ' '.join(sorted(normalize(i['text']) for i in items)), length=8)
        return {'id': qid, 'type': kind, 'severity': severity, 'title': title, 'question': text, 'basis': why,
                'statements': [{'quote': clip(i['text'], 400), 'where': _where(i), 'evidence': _ref_for(changed, i)} for i in items[:8]],
                'related_changes': sorted(set(changes_ids))}

    def related(items):
        ids = []
        for change in changes:
            if not change.get('functional'):
                continue
            norm = normalize(change.get('modified_text') or '')
            if any(normalize(i['text']) == norm for i in items):
                ids.append(change['change_id'])
        return ids

    # 1. contradictions: statements about the same subject whose duties are incompatible
    edges, pairs = [], {}
    for i, a in enumerate(new_statements):
        if a['deontic'] is None or not a['topic']:
            continue
        for j in range(i + 1, len(new_statements)):
            b = new_statements[j]
            if b['deontic'] is None or not (a['topic'] & b['topic']):
                continue
            if normalize(a['text']) == normalize(b['text']) or _mutually_exclusive(a, b):
                continue
            touched = normalize(a['text']) in changed_hashes or normalize(b['text']) in changed_hashes
            if not touched:
                continue
            kind = None
            if _opposed(a['deontic'], b['deontic']):
                kind = 'direct' if (bool(a['conditions']) == bool(b['conditions']) and a['conditions'] == b['conditions']) else 'conditional'
            elif a['deontic'] == b['deontic']:
                axes_a = {(c['axis'], c['subject']) for c in a['conditions']}
                axes_b = {(c['axis'], c['subject']) for c in b['conditions']}
                if axes_a and axes_b and axes_a != axes_b and {c['subject'] for c in a['conditions']} & {c['subject'] for c in b['conditions']}:
                    kind = 'overlap'
            if kind:
                edges.append((i, j))
                pairs[(i, j)] = kind
    strong = [(i, j) for (i, j), k in pairs.items() if k in ('direct', 'conditional')]
    for group in _components(strong, len(new_statements)):
        items = [new_statements[i] for i in sorted(group, key=lambda i: new_statements[i]['clause']['anchor'])]
        contexts = sorted({i['context'] for i in items if i['context']})
        subject = next((i['subject'] for i in items if i['subject']), 'the same subject')
        between = ' and '.join(contexts) if len(contexts) > 1 else (contexts[0] if contexts else 'the document')
        text = ('The statements below describe %s differently: some say it is required, others that it is optional or not required. '
                'Which one applies, and under which condition? The tool does not decide between them.' % subject)
        questions.append(new_question('CONTRADICTION', 'High', 'Possible contradiction between %s' % between, text, items, related(items),
                                      'Statements about the same subject carry incompatible duties (mandatory, optional or not allowed). Found by comparing the changed document\'s own sentences; not resolved automatically.'))
    overlap = {}
    for (i, j), kind in pairs.items():
        if kind == 'overlap':
            group = overlap.setdefault(new_statements[i]['subject'] or new_statements[j]['subject'] or 'one duty', {'items': {}, 'conds': set()})
            for k in (i, j):
                group['items'][k] = new_statements[k]
                group['conds'].update(c['phrase'] for c in new_statements[k]['conditions'])
    for subject, group in overlap.items():
        items = [group['items'][k] for k in sorted(group['items'], key=lambda k: new_statements[k]['clause']['anchor'])]
        text = ('The statements govern the same duty for %s but use different conditions (%s). How do these conditions relate, and which wins when more than one is true?'
                % (subject, '; '.join(sorted(group['conds']))))
        questions.append(new_question('AMBIGUITY', 'Medium', 'Overlapping conditions for %s' % subject, text, items, related(items),
                                      'Statements about one subject depend on different conditions; the document does not say how they combine.'))
    # 2. placement of the same subject stated differently
    by_topic = {}
    for s in new_statements:
        if s['placements']:
            for term in (s['topic'] | topic_terms(s['text'])):
                by_topic.setdefault(term, []).append(s)
    placed = set()
    for term, items in sorted(by_topic.items()):
        places = sorted({p for s in items for p in s['placements']})
        key = tuple(places)
        if len(places) > 1 and key not in placed and any(normalize(s['text']) in changed_hashes or semantics.has_term(s['text'], lex.get('advisory') or []) for s in items):
            placed.add(key)
            questions.append(new_question('AMBIGUITY', 'Medium', 'Placement of %s' % term, 'The document places %s in different locations (%s). Which location is intended?' % (term, '; '.join(places)),
                                          items, related(items), 'Different screen locations are named for the same subject.'))
    # 3. statements the document itself marks as open
    advisory = lex.get('advisory') or []
    topics_of_changes = set()
    for change in changes:
        if change.get('functional'):
            topics_of_changes |= topic_terms(change.get('subject'))
    for s in new_statements:
        if _form_noise(s['text'], lex):
            continue
        hits = semantics.has_term(s['text'], advisory)
        if not hits or s['text'] in (q['question'] for q in questions):
            continue
        overlapping = bool(s['topic'] & topics_of_changes) or len(topic_terms(s['text']) & topics_of_changes) >= min(2, max(1, len(topics_of_changes)))
        questions.append(new_question('UNCONFIRMED', 'Medium' if overlapping else 'Low', 'Open item in the document',
                                      'The document marks this as not decided (%s). Confirm the decision before relying on it.' % ', '.join(sorted(set(hits))[:3]),
                                      [s], related([s]) if overlapping else [],
                                      'Related to a changed subject.' if overlapping else 'Not related to a changed statement; listed because the document marks it open.'))
    # 4. consequence not stated for a changed duty (one question per subject)
    missing = {}
    for change in changes:
        if not change.get('functional') or change['change_type'] == 'REMOVED':
            continue
        topic = topic_terms(change.get('subject'))
        required = any(d.get('new_value') in (2,) for d in change.get('deltas', []) if d['kind'] == 'flip' and d['axis'] == 'deontic') or \
            (change.get('modified_text') and semantics.STRONG_MODAL.search(change['modified_text']) and CONTROL_WORDS.search(change['modified_text']))
        if not required or not topic:
            continue
        if any(shares_topic(topic, s) and CONSEQUENCE.search(s['text']) for s in new_statements):
            continue
        target = next((s for s in new_statements if normalize(s['text']) == normalize(change['modified_text'])), None)
        if target:
            entry = missing.setdefault(change['subject'], {'items': [], 'ids': []})
            entry['items'].append(target)
            entry['ids'].append(change['change_id'])
    for subject, entry in missing.items():
        questions.append(new_question('MISSING', 'Medium', 'Consequence not stated for %s' % subject,
                                      'What must happen when %s is not completed: an error message, a blocked submission, or nothing? The document states the duty but not the result.' % subject,
                                      entry['items'], entry['ids'], 'No sentence about this subject mentions an error, message or blocked action.'))
    # 5. confirm intent when a rule was only inverted
    covered = {cid for q in questions if q['type'] in ('CONTRADICTION', 'AMBIGUITY') for cid in q['related_changes']}
    for change in changes:
        if change['change_id'] in covered:
            continue
        flips = [d for d in change.get('deltas', []) if d['kind'] == 'flip']
        others = [d for d in change.get('deltas', []) if d['kind'] not in ('flip',)]
        if change.get('functional') and flips and not others and any(d.get('inversion') for d in flips):
            questions.append({'id': stable_id('OQ', 'INTENT', change['change_id'], length=8), 'type': 'CONFIRM_INTENT', 'severity': 'Medium',
                              'title': 'Confirm the reversal for %s' % (change.get('subject') or change['change_id']),
                              'question': 'The only edit in this statement is a reversed meaning (%s). Is the reversal intended?' % '; '.join(d['summary'].rstrip('.') for d in flips[:3]),
                              'basis': 'A change made only of negations or opposite words can be a deliberate rule change or a drafting edit. Derived from the difference; to be confirmed with the business analyst.',
                              'statements': [{'quote': clip(change.get('modified_text') or '', 400), 'where': (change.get('occurrences') or [{}])[0].get('location', ''),
                                              'evidence': change['evidence'][-1] if change['evidence'] else None}],
                              'related_changes': [change['change_id']]})
    unique, seen = [], set()
    for q in questions:
        if q['id'] not in seen:
            seen.add(q['id'])
            unique.append(q)
    order = {'High': 0, 'Medium': 1, 'Low': 2}
    unique.sort(key=lambda q: (order.get(q['severity'], 3), q['type'], q['id']))
    signals = change_signals(changes, new_statements, old_statements, facts, unique, lex)
    return {'questions': unique, 'facts': {k: v for k, v in facts.items() if k != 'fields'}, 'signals': signals,
            'statements': len(new_statements), 'conflicts': len(strong)}


# ---------------------------------------------------------------------------------------------------- signals for risk and impact
def change_signals(changes, new_statements, old_statements, facts, questions, lex):
    """Evidence the risk model needs, per change. Every flag carries the quotation that supports it."""
    pool = new_statements + old_statements
    question_by_change = {}
    for q in questions:
        for cid in q.get('related_changes', []):
            question_by_change.setdefault(cid, []).append(q)
    out = {}
    modules_document = [m['name'] for m in facts.get('modules', [])]
    for change in changes:
        text = ' '.join(x for x in (change.get('original_text'), change.get('modified_text'), change.get('context')) if x)
        topic = topic_terms(change.get('subject'))
        signal = {'regulatory': [], 'financial': [], 'blocking': False, 'ui': False, 'integration': False, 'modules': [], 'areas': [],
                  'conflicts': 0, 'open_questions': 0, 'inversions': 0, 'conditions': 0, 'deltas': 0}
        if change.get('administrative'):
            out[change['change_id']] = signal
            continue
        for key, terms in (('regulatory', lex.get('regulatory')), ('financial', lex.get('financial'))):
            hits = semantics.has_term(text, terms or [])
            if hits:
                signal[key].append({'via': 'change', 'terms': sorted(set(hits))[:4], 'quote': clip(change.get('modified_text') or change.get('original_text') or '', 220)})
            elif topic:
                for s in pool:
                    hit = semantics.has_term(s['text'], terms or []) if shares_topic(topic, s) else []
                    if hit:
                        signal[key].append({'via': 'subject', 'terms': sorted(set(hit))[:4], 'quote': clip(s['text'], 220), 'where': _where(s)})
                        break
        signal['blocking'] = bool(semantics.has_term(text, lex.get('user_blocking') or []))
        signal['ui'] = bool(semantics.has_term(text, lex.get('user_facing') or []))
        signal['integration'] = any(d['kind'] == 'api' for d in change.get('deltas', [])) or bool((change.get('statement') or {}).get('api'))
        sentence_modules = scope_modules(change.get('modified_text') or change.get('original_text') or '')
        signal['modules'] = sentence_modules or modules_document
        signal['module_source'] = 'sentence' if sentence_modules else ('form' if modules_document else '')
        signal['areas'] = extract_areas(text)
        signal['inversions'] = sum(1 for d in change.get('deltas', []) if d.get('inversion'))
        signal['conditions'] = sum(1 for d in change.get('deltas', []) if d.get('role') == 'condition')
        signal['deltas'] = len([d for d in change.get('deltas', []) if d['kind'] != 'wording']) or len(change.get('deltas', []))
        qs = question_by_change.get(change['change_id'], [])
        signal['conflicts'] = sum(1 for q in qs if q['type'] == 'CONTRADICTION')
        signal['open_questions'] = len(qs)
        out[change['change_id']] = signal
    return out


# ---------------------------------------------------------------------------------------------------- unchanged material
def unchanged_api_details(detection):
    """API / interface details present and identical in both versions: no evidence they are impacted."""
    found = {}
    for item in detection.get('unchanged_clause_list', []):
        text = item['text']
        if not semantics.API_MARKERS.search(text):
            continue
        info = semantics.describe_statement(text)
        keys = ['Method ID %s' % m for m in info['method_ids']]
        keys += [re.split(r'\s+(?:Request|Response)\b', n.strip())[0].strip() for n in re.findall(r'(?i)\bAPI\s*\d*\s*:\s*([A-Za-z][A-Za-z0-9 \-/]{3,60})', text)]
        keys += [i for i in info['identifiers'][:4] if not re.fullmatch(r'[A-Z0-9]{2,}', i) and re.search(r'[a-z][A-Z_]|_', i)]
        for key in keys:
            if key:
                found.setdefault(key, {'name': key, 'location': item.get('location', ''), 'context': item.get('context', ''), 'quote': clip(text, 220), 'anchor': item['anchor']})
    return list(found.values())[:40]


def unchanged_requirement_groups(detection, changes, document):
    """Unchanged requirement blocks of the same document (grouped by their "Requirement N" label) for regression of existing behaviour."""
    groups = {}
    for item in detection.get('unchanged_clause_list', []):
        context = item.get('context')
        if not context or not semantics.STRONG_MODAL.search(item['text']):
            continue
        group = groups.setdefault(context, {'context': context, 'location': item.get('location', ''), 'quote': clip(item['text'], 240), 'anchor': item['anchor'],
                                            'statements': 0, 'scope_modules': [],
                                            'evidence': _evidence('unchanged', document, dict(item, ref=item.get('ref', ''), section=item.get('section', '')), None, item['text'])})
        group['statements'] += 1
        for module in scope_modules(item['text']):
            if module not in group['scope_modules']:
                group['scope_modules'].append(module)
    return [g for k, g in sorted(groups.items(), key=lambda kv: kv[1]['anchor'])]


# ---------------------------------------------------------------------------------------------------- scenarios
def _required(change):
    for d in change.get('deltas', []):
        if d['kind'] == 'flip' and d['axis'] == 'deontic':
            return {2: True, 1: False, -2: None}.get(d.get('new_value'))
    text = change.get('modified_text') or ''
    preds = [p for p in ((change.get('statement') or {}).get('predicates') or []) if p['axis'] == 'deontic']
    if preds:
        return {2: True, 1: False}.get(preds[0]['value'])
    return True if semantics.STRONG_MODAL.search(text) else None


def _execution(change, text):
    kinds = []
    if semantics.has_term(text, ['api', 'method id', 'endpoint', 'payload', 'json', 'request body', 'response body']) or any(d['kind'] == 'api' for d in change.get('deltas', [])):
        kinds.append('API')
    if semantics.has_term(text, ['interface', 'screen', 'page', 'field', 'checkbox', 'button', 'dropdown', 'grid', 'form', 'display*', 'click*', 'check*', 'submit*', 'undertaking', 'declaration', 'label']):
        kinds += ['Manual', 'Playwright']
    if semantics.has_term(text, ['persist*', 'stored', 'store', 'saved', 'database', 'record*', 'table', 'column']):
        kinds.append('SQL')
    return sorted(set(kinds), key=['Manual', 'Playwright', 'API', 'SQL'].index) or ['Manual']


def scenarios_for_change(change, modules, open_questions=()):
    """Regression scenarios derived from one change's rule deltas. ``open_questions`` lists the ids of unresolved conflicts that touch the change."""
    pending = list(open_questions)
    if change.get('administrative') or not change.get('functional'):
        return []
    subject = change.get('subject') or change.get('requirement') or 'the requirement'
    text = ' '.join(x for x in (change.get('modified_text'), change.get('original_text')) if x)
    execution = _execution(change, text)
    required = _required(change)
    has_duty = any(d['kind'] == 'flip' and d['axis'] == 'deontic' for d in change.get('deltas', []))
    control = bool(CONTROL_WORDS.search(change.get('modified_text') or change.get('original_text') or '')) or has_duty
    done = 'checked' if CONTROL_WORDS.search(change.get('modified_text') or change.get('original_text') or '') else 'provided'
    outcome_new = change.get('outcome_new') or change.get('modified_text') or ''
    out = []

    def add(title, reason, expected, basis, primary=False, extra=None):
        out.append(dict({'title': title, 'reason': reason, 'expected': expected, 'basis': basis, 'open_questions': pending,
                         'primary': primary, 'change_ids': [change['change_id']], 'execution': execution, 'modules': modules,
                         'evidence': change['evidence'][-1:] if change['evidence'] else []}, **(extra or {})))

    states_for = lambda flip: [s for s in (flip['new'], flip['old']) if s]
    controls = ['%s is %s' % (subject, done), '%s is not %s' % (subject, done)] if control else ['the rule is exercised']
    cond_flips = [d for d in change.get('deltas', []) if d['kind'] == 'flip' and d['role'] == 'condition']
    for flip in cond_flips:
        who = flip.get('subject') or 'the condition'
        for state in states_for(flip):
            holds = state == flip['new']
            for state_control in controls:
                checked = not state_control.endswith('is not %s' % done)
                if holds and control:
                    expected = ('Accepted: the changed wording requires the check in this case.' if checked and required else
                                'Not accepted or flagged: the changed wording requires the check, but the message or blocked action is not stated (BA clarification).' if (not checked and required) else
                                'Both outcomes are accepted: the changed wording makes it optional.' if required is False else 'Per the changed wording: %s' % clip(outcome_new, 160))
                    basis = 'SOURCE' if (checked and required) or required is False else 'PENDING_BA'
                elif holds:
                    expected, basis = 'Per the changed wording: %s' % clip(outcome_new, 180), 'SOURCE'
                else:
                    expected = 'Not stated by the changed wording (it previously said: %s). Awaiting BA clarification.' % clip(change.get('condition_old') or change.get('original_text') or '', 140)
                    basis = 'PENDING_BA'
                add('%s is %s; %s' % (who, state, state_control),
                    'The condition "%s" changed from "%s" to "%s".' % (who, flip['old'], flip['new']), expected, basis,
                    primary=holds and not checked and bool(control))
    out_flips = [d for d in change.get('deltas', []) if d['kind'] == 'flip' and d['role'] == 'outcome']
    if out_flips and not cond_flips:
        flip = out_flips[0]
        for state_control in controls:
            checked = not state_control.endswith('is not %s' % done)
            expected = ('Accepted.' if checked else ('Accepted: the changed wording makes it %s.' % flip['new'] if flip['new_value'] in (1,) else
                                                      'Refused or flagged: the changed wording makes it %s; the message is not stated (BA clarification).' % flip['new'])) if control else 'Follows the new rule: %s.' % flip['new']
            add('%s: %s' % (subject, state_control), 'The duty changed from %s to %s.' % (flip['old'], flip['new']), expected,
                'SOURCE' if (flip['new_value'] in (1,) or checked) else 'PENDING_BA', primary=not checked)
    for flip in [d for d in change.get('deltas', []) if d['kind'] == 'flip' and d['role'] == 'outcome' and cond_flips]:
        pass   # the condition scenarios above already exercise the outcome of the same sentence
    for flip in [d for d in change.get('deltas', []) if d['kind'] == 'flip' and d['axis'] in ('visibility', 'enablement', 'state', 'validity', 'approval')]:
        for state in (flip['new'], flip['old']):
            add('%s: %s' % (subject, state), '%s changed from %s to %s.' % (flip['axis_name'], flip['old'], flip['new']),
                'Per the changed wording: %s' % flip['new'] if state == flip['new'] else 'Previous behaviour (%s) must no longer occur.' % flip['old'], 'SOURCE', primary=state == flip['new'])
    for d in [x for x in change.get('deltas', []) if x['kind'] == 'numeric']:
        value = d['new'] or d['old']
        for label, expected in (('at the new value %s' % value, 'Per the changed wording.'), ('one step beyond the new value %s' % value, 'Boundary behaviour per the comparator in the wording.'),
                                ('the previous value %s' % (d['old'] or '(none)'), 'Must no longer behave as the old limit.')):
            add('%s: %s' % (subject, label), 'The value changed from %s to %s.' % (d['old'] or '(none)', d['new'] or '(none)'), expected, 'SOURCE', primary=label.startswith('at'))
    for d in [x for x in change.get('deltas', []) if x['kind'] == 'api']:
        add('%s: request and response with "%s"' % (subject, clip(d['new'] or d['old'], 40)), d['summary'], 'Per the provider contract; the value that changed is accepted and the old value is refused or ignored as the contract states.', 'SOURCE', primary=True)
    if change['change_type'] == 'ADDED':
        add('New statement: %s' % clip(change.get('modified_text') or '', 90), 'A new statement was added.', 'Per the new wording: %s' % clip(change.get('modified_text') or '', 180), 'SOURCE', primary=True)
        add('New statement, alternative and failure paths: %s' % clip(subject or change.get('modified_text') or '', 60), 'No existing test can cover new wording.', 'Not stated; derive from the owner of the requirement.', 'PENDING_BA')
    if change['change_type'] == 'REMOVED':
        add('Removed behaviour is gone: %s' % clip(change.get('original_text') or '', 90), 'The statement no longer appears.', 'The previous behaviour no longer occurs and dependent flows still work.', 'PENDING_BA', primary=True)
    if not out and change['change_type'] == 'MODIFIED':
        add('Reworded statement: %s' % clip(change.get('modified_text') or '', 90), 'The wording changed; no rule-level difference was detected.', 'Behaviour is unchanged unless the BA says otherwise.', 'PENDING_BA')
    if len(modules) > 1 and change['change_type'] == 'MODIFIED':
        for module in modules:
            add('Submission validation in %s: %s' % (module, clip(subject, 50)), 'The changed statement applies to more than one module (%s).' % ', '.join(modules),
                'Per the changed wording for this module.' + (' Conflicting statements exist; see the open question.' if pending else ''), 'SOURCE', extra={'modules': [module]})
    return out


def merge_scenarios(items):
    """The same scenario produced by several changes is one scenario that lists every related change."""
    merged, order = {}, []
    for item in items:
        key = normalize(item['title'])
        if key not in merged:
            merged[key] = item
            order.append(key)
            continue
        base = merged[key]
        base['change_ids'] = sorted(set(base['change_ids']) | set(item['change_ids']))
        base['primary'] = base['primary'] or item['primary']
        if base['basis'] == 'SOURCE' and item['basis'] == 'PENDING_BA':
            base['basis'] = 'PENDING_BA'
        if item['reason'] not in base['reason']:
            base['reason'] += ' ' + item['reason']
        for ev in item.get('evidence', []):
            if all(ev['ref'] != x['ref'] for x in base['evidence']):
                base['evidence'].append(ev)
    return [merged[k] for k in order]
