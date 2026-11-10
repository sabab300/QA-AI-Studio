"""Cross-system dependency discovery.

A dependency is only reported as such when the source text states a relationship
(for example "sends data to Customs") or when a reviewer already confirmed it in an
earlier analysis. A system that is merely mentioned in the same requirement is
reported as a HYPOTHESIS that needs verification. Hypotheses are never traversed.
"""
import re
import sqlite3
from collections import Counter, deque

from Core.change_impact.util import clip, digest, normalize, stable_id, loads

GENERIC_NAMES = frozenset(('data', 'system', 'user', 'users', 'report', 'reports', 'test', 'tests', 'module', 'domain', 'common',
                           'general', 'main', 'core', 'admin', 'other', 'default', 'service', 'services', 'application', 'portal'))
INTEGRATION_RELATIONS = frozenset(('sends_to', 'receives_from', 'calls', 'integrates_with'))
SENTENCE_SPLIT = re.compile(r'(?<=[.;:])\s+|\n+')


class SystemCatalog:
    """Known systems = Modules and Knowledge entities in the canonical hierarchy, plus API collection names."""

    def __init__(self, db, limit=800):
        self.db = db
        self.limit = limit

    def systems(self):
        conn = self.db.get_connection()
        conn.row_factory = sqlite3.Row
        out = []
        try:
            tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if 'domains' in tables:
                for r in conn.execute('SELECT name FROM domains LIMIT ?', (self.limit,)):
                    out.append({'name': r['name'], 'kind': 'domain', 'domain': r['name'], 'module': '', 'knowledge': ''})
            if {'domains', 'modules'} <= tables:
                for r in conn.execute('SELECT d.name AS domain,m.name AS module FROM modules m JOIN domains d ON d.id=m.domain_id LIMIT ?', (self.limit,)):
                    out.append({'name': r['module'], 'kind': 'module', 'domain': r['domain'], 'module': r['module'], 'knowledge': ''})
            if 'knowledge_entities' in tables:
                for r in conn.execute('SELECT d.name AS domain,m.name AS module,k.name AS knowledge FROM knowledge_entities k '
                                      'JOIN modules m ON m.id=k.module_id JOIN domains d ON d.id=m.domain_id LIMIT ?', (self.limit,)):
                    out.append({'name': r['knowledge'], 'kind': 'knowledge', 'domain': r['domain'], 'module': r['module'], 'knowledge': r['knowledge']})
            if 'api_collections' in tables:
                for r in conn.execute("SELECT name,domain,module,knowledge_name FROM api_collections WHERE COALESCE(status,'Active')='Active' LIMIT ?", (self.limit,)):
                    if r['name']:
                        out.append({'name': r['name'], 'kind': 'api_collection', 'domain': r['domain'] or '', 'module': r['module'] or '', 'knowledge': r['knowledge_name'] or ''})
        finally:
            conn.close()
        seen, result = set(), []
        for s in out:
            key = (s['kind'], normalize(s['name']), s['domain'], s['module'])
            if len(normalize(s['name'])) >= 4 and normalize(s['name']) not in GENERIC_NAMES and key not in seen:
                seen.add(key)
                result.append(s)
        return result


def system_label(system):
    parts = [system.get('domain'), system.get('module'), system.get('knowledge')]
    label = ' › '.join(p for p in parts if p)
    return label or system.get('name', '')


def owner_system(owner):
    return {'name': owner['module'], 'kind': 'module', 'domain': owner['domain'], 'module': owner['module'], 'knowledge': owner['knowledge_name']}


def _word_in(norm_text, norm_name):
    return bool(norm_name) and re.search(r'(?<![\w])' + re.escape(norm_name) + r'(?![\w])', norm_text) is not None


def _inside_longer_name(sentence, name):
    """True when the catalog name occurs only as one word of a longer capitalised name ("Single Declaration Export"), or as an
    ordinary lower-case word ("the export declaration"). Neither is a reference to the catalog entry itself."""
    pattern = re.compile(r'(?<![\w])' + re.escape(name).replace(r'\ ', r'\s+') + r'(?![\w])', re.IGNORECASE)
    hits = list(pattern.finditer(sentence))
    if not hits:
        return True
    for m in hits:
        if not m.group(0)[:1].isupper():
            continue  # lower-case use is plain language, not a proper name
        before = re.search(r"([A-Za-z][\w'-]*)\s+$", sentence[:m.start()])
        after = re.match(r"\s+([A-Za-z][\w'-]*)", sentence[m.end():])
        if (before and before.group(1)[:1].isupper()) or (after and after.group(1)[:1].isupper()):
            continue  # part of a longer name
        return False
    return True


def identity_supported(system, owner, ambiguous, doc_norm, sentence_norm):
    """A catalog entry is only a candidate when the document actually points at it: it belongs to the owning domain, or the
    document names its domain (in the sentence when the same name exists in several domains)."""
    if normalize(system.get('domain')) == normalize(owner.get('domain')):
        return True
    domain = normalize(system.get('domain'))
    if not domain:
        return False
    return _word_in(sentence_norm if ambiguous else doc_norm, domain)


def _mentions(sentence, catalog, owner, doc_norm=''):
    lowered = ' ' + normalize(sentence) + ' '
    owner_names = {normalize(owner['module']), normalize(owner['knowledge_name']), normalize(owner['domain'])}
    counts = Counter(normalize(x['name']) for x in catalog if x['kind'] != 'domain')
    found = []
    for system in catalog:
        if system['kind'] == 'domain':
            continue
        name = normalize(system['name'])
        if name in owner_names:
            continue
        match = re.search(r'(?<![\w])' + re.escape(name) + r'(?![\w])', lowered)
        if not match:
            continue
        if _inside_longer_name(sentence, system['name']):
            continue
        if not identity_supported(system, owner, counts[name] > 1, doc_norm or lowered, lowered):
            continue
        found.append((match.start(), system))
    found.sort(key=lambda x: x[0])
    deduped, seen = [], set()
    for position, system in found:
        key = (system['kind'], normalize(system['name']), system['domain'], system['module'])
        if key not in seen:
            seen.add(key)
            deduped.append((position, system))
    return deduped


def _relation(sentence, relation_phrases):
    lowered = ' ' + normalize(sentence) + ' '
    best = None
    for relation, phrases in relation_phrases.items():
        for phrase in phrases:
            index = lowered.find(' ' + phrase + ' ')
            if index >= 0 and (best is None or index < best[0]):
                best = (index, relation, phrase)
    return best


def dependency_key(source, target, relation):
    return digest('|'.join((normalize(system_label(source)), normalize(system_label(target)), relation)))[:24]


CONFIRMED_IMPACT = 'Confirmed Impact'
POTENTIAL_IMPACT = 'Potential Impact'


IDENTITY_TEXT = {'domain': 'Knowledge Hub domain', 'module': 'Knowledge Hub module', 'knowledge': 'Knowledge Hub knowledge entity',
                 'api_collection': 'API collection in QA Automation'}
UNKNOWN_IDENTITY = 'Named in the document; not found in Knowledge Hub'


def _catalog_label(name, catalog, owner, internal=False, doc_norm=''):
    """Return (label, catalog entry or None). A stated name resolves to a Knowledge Hub entry only when it is in the owning domain or the
    document names the entry's domain; the same name in an unrelated domain (for example another team's test domain) is never used.
    A module the document names belongs to the owning domain ("Domain > name"); an external system keeps its own name."""
    key = normalize(name)
    candidates = [x for x in catalog if normalize(x['name']) == key]
    own = normalize(owner.get('domain'))
    chosen = None
    for x in candidates:
        if x['kind'] != 'domain' and normalize(x['domain']) == own:
            chosen = x
            break
    if chosen is None:
        for x in candidates:
            if x['kind'] == 'domain' or (x['domain'] and _word_in(doc_norm, normalize(x['domain']))):
                chosen = x
                break
    if chosen is not None:
        return system_label(chosen), chosen
    domain = owner.get('domain') or ''
    return (('%s \u203a %s' % (domain, name)) if internal and domain and normalize(domain) not in key else name), None


TEXT_IDENTITY = 'Described in the changed text; not a Knowledge Hub entry'


def _identity_for(kind, entry):
    if kind in ('interface', 'logic', 'api'):
        return TEXT_IDENTITY
    return identity_of(entry)


def identity_of(entry):
    return IDENTITY_TEXT.get((entry or {}).get('kind'), UNKNOWN_IDENTITY) if entry else UNKNOWN_IDENTITY


def discover(changes, owner, catalog, confirmed, policy, insight=None, pool_text=''):
    """Return (system_findings, dependency_edges).

    Every finding states its certainty: ``Confirmed Impact`` (the changed text or the document itself states it) or
    ``Potential Impact`` (derived or co-mentioned; needs verification). Administrative changes do not impact systems.
    ``confirmed`` maps dependency_key -> {'analysis_id','actor','at'} for human-confirmed edges.
    """
    limits = policy.get('limits', {})
    max_depth = int(limits.get('max_dependency_depth', 2))
    max_nodes = int(limits.get('max_graph_nodes', 250))
    relation_phrases = policy.get('relation_phrases', {})
    own = owner_system(owner)
    doc_norm = ' ' + normalize(pool_text) + ' '
    own_labels = {normalize(system_label(own)), normalize('%s \u203a %s' % (owner.get('domain'), owner.get('module')))}
    findings, edges, seen_findings = [], {}, set()
    signals = (insight or {}).get('signals', {})
    facts = (insight or {}).get('facts', {})
    functional = [c for c in changes if not c.get('administrative')]

    def add_finding(finding):
        if finding['finding_id'] not in seen_findings and len(findings) < max_nodes * 4:
            seen_findings.add(finding['finding_id'])
            findings.append(finding)

    def system_finding(change, label, kind, certainty, assurance, status, impact_types, basis, evidence, impact_class=None, extra=None, entry=None):
        payload = {'source': change['reference'] or change['change_id'], 'component': label, 'component_kind': kind, 'impact_types': impact_types,
                   'dependency': None, 'depth': 1, 'certainty': certainty, 'basis': basis, 'identity': _identity_for(kind, entry)}
        payload.update(extra or {})
        add_finding({'finding_id': stable_id('SYS', change['change_id'], label, certainty, kind), 'kind': 'SYSTEM', 'change_id': change['change_id'],
                     'title': label, 'impact_class': impact_class or ('Direct' if certainty == CONFIRMED_IMPACT else 'Potential - requires verification'),
                     'severity': '', 'assurance': assurance, 'verification_status': status, 'payload': payload, 'evidence': evidence})

    for change in functional:
        texts = [(e, e['quote']) for e in change['evidence']]
        classes = _classes_for_category(change['category'])
        sig = signals.get(change['change_id'], {})
        # Direct: the system that owns the changed document
        add_finding({
            'finding_id': stable_id('SYS', change['change_id'], system_label(own), 'direct'),
            'kind': 'SYSTEM', 'change_id': change['change_id'],
            'title': system_label(own), 'impact_class': 'Direct', 'severity': '',
            'assurance': 'CONFIRMED' if change['change_kind'] == 'ACTUAL_DIFF' else 'EVIDENCE_BACKED',
            'verification_status': 'Confirmed by version difference' if change['change_kind'] == 'ACTUAL_DIFF' else 'Proposed change; not yet applied',
            'payload': {'source': change['reference'] or change['change_id'], 'component': system_label(own), 'component_kind': 'owner',
                        'impact_types': ['Direct'] + classes, 'dependency': None, 'depth': 0, 'certainty': CONFIRMED_IMPACT, 'identity': 'Knowledge Hub: the Knowledge document that owns the changed requirement',
                        'basis': 'The changed requirement is part of this Knowledge document.'},
            'evidence': change['evidence'][:2]})
        evidence_last = change['evidence'][-1:] or []
        # Modules and interfaces the changed statement itself names
        for module in (sig.get('modules') or []):
            label, known = _catalog_label(module, catalog, owner, internal=True, doc_norm=doc_norm)
            if normalize(label) in own_labels:
                continue
            stated_in_sentence = sig.get('module_source') == 'sentence'
            certainty = CONFIRMED_IMPACT if stated_in_sentence else POTENTIAL_IMPACT
            system_finding(change, label, 'module', certainty, 'EVIDENCE_BACKED' if stated_in_sentence else 'SUGGESTED',
                           'The changed statement is written for this module' if stated_in_sentence else
                           'The change request lists this module as the place of change; the requirement itself does not name it',
                           ['Direct' if stated_in_sentence else 'Potential - requires verification'] + classes,
                           ('The changed statement begins with its scope: "%s".' % module) if stated_in_sentence else
                           'Stated by the document\'s module field; not tied to this individual requirement.', evidence_last, entry=known)
        for area in (sig.get('areas') or []):
            if area.get('role') == 'reference':
                # "below the Financial Instrument Information grid": a position reference; the statement does not say the grid itself changes
                system_finding(change, area['name'], area['kind'], POTENTIAL_IMPACT, 'SUGGESTED', 'Named only as a position reference in the changed statement',
                               ['Potential - requires verification', 'User interface'],
                               'The changed statement uses this %s only to say where the new element goes; nothing says the %s itself changes.' % (area['kind'], area['kind']),
                               evidence_last, extra={'parent_modules': sig.get('modules') or []})
                continue
            system_finding(change, area['name'], area['kind'], CONFIRMED_IMPACT, 'EVIDENCE_BACKED', 'Named by the changed statement',
                           ['Direct', 'User interface'] if area['kind'] == 'interface' else ['Direct'],
                           'The changed statement places the rule on this %s.' % area['kind'], evidence_last, extra={'parent_modules': sig.get('modules') or []})
        # Logic the changed rule depends on (derived: the document does not say it changes)
        subjects = {}
        for delta in change.get('deltas', []):
            if delta.get('role') == 'condition' and delta.get('subject'):
                subjects.setdefault(delta['subject'], set()).add(delta.get('axis_name') or delta.get('axis'))
        for subject, axes in sorted(subjects.items()):
            expanded = insight_expand(pool_text, subject)
            name = '%s%s %s logic' % (expanded or subject, (' (%s)' % subject) if expanded and expanded != subject else '', ' / '.join(sorted(a.lower() for a in axes)))
            system_finding(change, name, 'logic', POTENTIAL_IMPACT, 'SUGGESTED', 'Derived: the changed rule depends on this state; the document does not say it changes',
                           ['Potential - requires verification', 'Validation'],
                           'The rule\'s condition "%s" changed, so the logic that decides this state is exercised.' % '; '.join(d['summary'].rstrip('.') for d in change.get('deltas', []) if d.get('role') == 'condition')[:240],
                           evidence_last)
        # Integration contract named by the change (API method ids, parameters)
        for delta in change.get('deltas', []):
            if delta['kind'] == 'api':
                system_finding(change, 'API: %s' % clip(delta.get('new') or delta.get('old') or delta['summary'], 60), 'api', CONFIRMED_IMPACT, 'CONFIRMED',
                               'The changed text modifies this API detail', ['Direct', 'Integration'], delta['summary'], evidence_last, impact_class='Integration')
        # Systems the document says need their own change request (document-level statement)
        for dep in facts.get('dependencies', []):
            label, entry = _catalog_label(dep['system'], catalog, owner, doc_norm=doc_norm)
            system_finding(change, label, 'system', POTENTIAL_IMPACT, 'SUGGESTED',
                           'The change request states that "%s" needs a change (answer: %s); it does not say which requirement drives it' % (dep['system'], dep['answer']),
                           ['Potential - requires verification', 'Integration'], 'Stated once for the whole change request, not for this requirement.',
                           [dep['evidence']], impact_class='Integration', extra={'document_level': True}, entry=entry)
        for evidence, quote in texts:
            if evidence['role'] == 'baseline' and change['change_type'] == 'MODIFIED':
                continue  # the changed text is what drives new dependencies
            for sentence in SENTENCE_SPLIT.split(quote or ''):
                sentence = sentence.strip()
                if len(sentence) < 8:
                    continue
                mentions = _mentions(sentence, catalog, owner, doc_norm)
                if not mentions:
                    continue
                relation = _relation(sentence, relation_phrases)
                for position, target in mentions[:4]:
                    if relation:
                        _, rel, phrase = relation
                        source, tgt = (target, own) if rel == 'receives_from' else (own, target)
                        key = dependency_key(source, tgt, rel)
                        prior = confirmed.get(key)
                        assurance = 'CONFIRMED' if prior else 'EVIDENCE_BACKED'
                        status = ('Confirmed by %s in analysis %s' % (prior.get('actor') or 'a reviewer', prior.get('analysis_id'))) if prior else 'Stated in the source text; needs verification'
                        edge = {'key': key, 'source': system_label(source), 'target': system_label(tgt), 'relationship': rel,
                                'assurance': assurance}
                        edges[key] = edge
                        add_finding({
                            'finding_id': stable_id('SYS', change['change_id'], key),
                            'kind': 'SYSTEM', 'change_id': change['change_id'], 'title': system_label(target),
                            'impact_class': 'Integration', 'severity': '', 'assurance': assurance, 'verification_status': status,
                            'payload': {'source': system_label(source), 'component': system_label(target), 'component_kind': target['kind'],
                                        'impact_types': ['Integration'] + classes, 'certainty': CONFIRMED_IMPACT, 'identity': identity_of(target),
                                        'basis': 'The changed text states the relationship "%s"%s.' % (phrase, ' (confirmed by a reviewer earlier)' if prior else '; a reviewer should still verify it'),
                                        'dependency': {'key': key, 'source_system': system_label(source), 'target_system': system_label(tgt),
                                                       'relationship': rel, 'relationship_phrase': phrase, 'sentence': clip(sentence, 300)},
                                        'depth': 1},
                            'evidence': [evidence | {'quote': clip(sentence, 500)}]})
                    else:
                        add_finding({
                            'finding_id': stable_id('SYS', change['change_id'], system_label(target), 'mention'),
                            'kind': 'SYSTEM', 'change_id': change['change_id'], 'title': system_label(target),
                            'impact_class': 'Potential - requires verification', 'severity': '', 'assurance': 'HYPOTHESIS',
                            'verification_status': 'Unverified: mentioned in the same requirement; no relationship is stated',
                            'payload': {'source': change['reference'] or change['change_id'], 'component': system_label(target),
                                        'component_kind': target['kind'], 'impact_types': ['Potential - requires verification'],
                                        'dependency': None, 'depth': 1, 'certainty': POTENTIAL_IMPACT, 'identity': identity_of(target),
                                        'basis': 'A system name in the text is not proof that it must change.',
                                        'note': 'Co-mention only. This is NOT treated as a dependency until a reviewer confirms it.'},
                            'evidence': [evidence | {'quote': clip(sentence, 500)}]})

    # Indirect impact: walk only evidence-backed or confirmed edges, bounded and cycle-safe.
    adjacency = {}
    for edge in list(edges.values()) + [e for e in confirmed.get('__edges__', [])]:
        adjacency.setdefault(edge['source'], []).append(edge)
    start = system_label(own)
    visited, queue, node_count = {start}, deque([(start, 0, [start])]), 1
    while queue:
        node, depth, path = queue.popleft()
        if depth >= max_depth:
            continue
        for edge in adjacency.get(node, []):
            nxt = edge['target']
            if nxt in visited or node_count >= max_nodes:
                continue  # cycle or bound reached
            visited.add(nxt)
            node_count += 1
            new_path = path + [nxt]
            queue.append((nxt, depth + 1, new_path))
            if depth + 1 >= 2:
                for change in functional[:50]:
                    add_finding({
                        'finding_id': stable_id('SYS', 'indirect', change['change_id'], ' > '.join(new_path)),
                        'kind': 'SYSTEM', 'change_id': change['change_id'], 'title': nxt, 'impact_class': 'Indirect', 'severity': '',
                        'assurance': 'EVIDENCE_BACKED', 'verification_status': 'Inferred through %d dependency step(s); needs verification' % (depth + 1),
                        'payload': {'source': system_label(own), 'component': nxt, 'component_kind': 'system', 'certainty': POTENTIAL_IMPACT,
                                    'basis': 'Reached through stated dependencies.',
                                    'impact_types': ['Indirect'], 'dependency': None, 'depth': depth + 1, 'path': new_path},
                        'evidence': []})
    return consolidate_systems(findings), list(edges.values())


def insight_expand(pool_text, subject):
    from Core.change_impact.insight import expand_acronym
    return expand_acronym(pool_text, subject)


def _classes_for_category(category):
    classes = []
    if category in ('Data Field', 'Database Behavior'):
        classes.append('Data')
    if category == 'User Permission':
        classes.append('Security')
    if category in ('Integration', 'API Contract'):
        classes.append('Integration')
    return classes


def confirmed_dependencies(db):
    """Dependencies a human reviewer confirmed in earlier analyses (read-only)."""
    conn = db.get_connection()
    conn.row_factory = sqlite3.Row
    out, edges = {}, []
    try:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not {'ci_review_events', 'ci_findings'} <= tables:
            return {}
        rows = conn.execute(
            "SELECT e.analysis_id,e.actor_name,e.created_at,e.decision,f.payload_json FROM ci_review_events e "
            "JOIN ci_findings f ON f.analysis_id=e.analysis_id AND f.finding_id=e.finding_id "
            "WHERE e.event_type='DEPENDENCY_CONFIRM' ORDER BY e.id").fetchall()
        latest = {}
        for r in rows:  # the most recent human decision on a dependency wins
            dep = (loads(r['payload_json'], {}) or {}).get('dependency')
            if dep and dep.get('key'):
                latest[dep['key']] = (r, dep)
        for key, (r, dep) in latest.items():
            if r['decision'] == 'Confirmed':
                out[key] = {'analysis_id': r['analysis_id'], 'actor': r['actor_name'], 'at': r['created_at']}
                edges.append({'key': key, 'source': dep['source_system'], 'target': dep['target_system'],
                              'relationship': dep['relationship'], 'assurance': 'CONFIRMED'})
        out['__edges__'] = edges
    finally:
        conn.close()
    return out


ASSURANCE_RANK = {'CONFIRMED': 0, 'EVIDENCE_BACKED': 1, 'SUGGESTED': 2, 'HYPOTHESIS': 3}
POTENTIAL_MARK = 'Potential - requires verification'


def consolidate_systems(findings):
    """One record per impacted component.

    The engine produces one detailed finding per change and component. Findings that describe the same component are merged into
    a single record that keeps every requirement reference, change id and piece of evidence, plus one ``per_requirement`` line for each
    detailed finding, so nothing is lost. A component that is confirmed for any requirement is one Confirmed Impact record; its weaker
    findings stay visible in ``per_requirement``. Components that differ (for example two different logic areas) stay separate.
    """
    groups, order = {}, []
    for f in findings:
        p = f['payload']
        key = (normalize(p['component']), p.get('component_kind', ''))
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(f)
    merged = []
    for key in order:
        rows = groups[key]
        strong = [r for r in rows if r['payload'].get('certainty') == CONFIRMED_IMPACT] or rows
        lead = sorted(strong, key=lambda r: (ASSURANCE_RANK.get(r['assurance'], 9), r['change_id']))[0]
        p0 = dict(lead['payload'])
        certainty = CONFIRMED_IMPACT if strong is not rows else POTENTIAL_IMPACT
        change_ids, refs, evidence, seen_ev, per, impact = [], [], [], set(), [], []
        for r in rows:
            rp = r['payload']
            if r['change_id'] not in change_ids:
                change_ids.append(r['change_id'])
            ref = rp.get('source') or r['change_id']
            if ref not in refs:
                refs.append(ref)
            for ev in r.get('evidence') or []:
                if ev.get('ref') not in seen_ev and len(evidence) < 8:
                    seen_ev.add(ev.get('ref'))
                    evidence.append(ev)
            line = {'requirement': ref, 'change_id': r['change_id'], 'certainty': rp.get('certainty'), 'basis': rp.get('basis', '')}
            if line not in per:
                per.append(line)
            for t in rp.get('impact_types') or []:
                if t not in impact and not (certainty == CONFIRMED_IMPACT and t == POTENTIAL_MARK):
                    impact.append(t)
        # one clear basis; differing statements stay visible per requirement
        bases = []
        for line in per:
            if line['basis'] and line['basis'] not in bases:
                bases.append(line['basis'])
        payload = p0
        if not payload.get('dependency'):
            payload['dependency'] = next((r['payload']['dependency'] for r in rows if r['payload'].get('dependency')), None)
        payload.update({'source': ', '.join(refs), 'certainty': certainty, 'impact_types': impact, 'change_ids': change_ids, 'requirement_refs': refs,
                        'merged_findings': len(rows), 'per_requirement': per,
                        'basis': (payload.get('basis') or '') if len(bases) <= 1 else '%s (%d other statement(s), see per-requirement detail)' % (payload.get('basis') or bases[0], len(bases) - 1)})
        if len({bool(r['payload'].get('document_level')) for r in rows}) > 1:
            payload.pop('document_level', None)
        impact_class = lead.get('impact_class')
        merged.append({'finding_id': stable_id('SYS', 'component', normalize(payload['component']), payload.get('component_kind', ''), certainty),
                       'kind': 'SYSTEM', 'change_id': change_ids[0], 'title': payload['component'], 'impact_class': impact_class, 'severity': '',
                       'assurance': lead['assurance'], 'verification_status': lead['verification_status'], 'payload': payload, 'evidence': evidence})
    return merged


def for_change(systems, change_id):
    """The consolidated system records that apply to one change."""
    return [x for x in systems if change_id in (x['payload'].get('change_ids') or [x['change_id']])]
