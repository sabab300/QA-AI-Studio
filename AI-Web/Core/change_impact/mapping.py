"""Requirement mapping, test-case impact and coverage-gap detection.

Traceability follows Knowledge Hub -> QA Engineering -> QA Automation:

* a test case is a **Confirmed** match when it is related to the analysed document through a recorded source relationship
  (generated from or linked to that source or document version) *and* its content shows evidence for the changed
  requirement (shared distinctive terms);
* it is an **Evidence-backed** match when it belongs to the same Knowledge and its content shows strong evidence;
* any other wording overlap is only a **Suggestion** (possible match);
* a test case is never reported as impacted without evidence, and nothing is created, modified or retired here.
"""
import re

from Core.change_impact.documents import normalize_ref
from Core.change_impact.util import clip, containment, jaccard, normalize, stable_id, token_set, tokens

IDENT = re.compile(r"(?:\b[a-z]+(?:[A-Z][a-z0-9]+)+\b|\b[A-Za-z0-9]+(?:_[A-Za-z0-9]+)+\b|/[A-Za-z0-9_\-{}.]+(?:/[A-Za-z0-9_\-{}.]+)*|\b[A-Za-z]+\.[A-Za-z_]+\b)")
QUOTED = re.compile(r"[\"'“‘]([^\"'”’]{2,60})[\"'”’]")
NUMBER = re.compile(r'\b\d+(?:[.,]\d+)?\b')

SCENARIOS = {
    'Validation': (['Value exactly at the allowed limit', 'Value just beyond the limit is rejected', 'Blank or missing value'], ['Positive', 'Negative', 'Boundary', 'Validation']),
    'User Permission': (['Permitted role succeeds', 'Role without permission is refused', 'Role change takes effect'], ['Security', 'Negative', 'Functional']),
    'API Contract': (['Changed request or response fields are accepted/returned', 'Existing consumers still work', 'Invalid payload returns the documented error'], ['API', 'Integration', 'Negative']),
    'Integration': (['Message reaches the target system end to end', 'Target system unavailable or slow', 'Retry and duplicate handling'], ['Integration', 'Negative', 'Regression']),
    'Database Behavior': (['Data is stored and read back correctly', 'Constraint violation is handled', 'Existing records after the change'], ['Database', 'Regression', 'Functional']),
    'Workflow': (['Every changed status transition', 'Invalid transition is refused', 'Approval or escalation path'], ['Functional', 'Negative', 'Regression']),
    'Data Field': (['Field is shown, saved and retrieved', 'Mandatory versus optional behaviour', 'Existing records display correctly'], ['Functional', 'UI', 'Validation']),
    'Error Handling': (['Error text or code matches the requirement', 'Recovery after the error'], ['Negative', 'Functional']),
    'Non-Functional Requirement': (['Behaviour under expected load', 'Audit or log entry is written'], ['Performance', 'Security']),
    'Business Rule': (['Rule applied exactly at the threshold', 'Rule for each applicable party', 'Worked example from the source'], ['Functional', 'Positive', 'Negative', 'Boundary']),
    'Functional Requirement': (['Main success path', 'Alternative and failure paths'], ['Functional', 'Positive', 'Negative']),
    'Uncategorised': (['Main success path', 'Alternative and failure paths'], ['Functional']),
}
STRONG_CATEGORIES = frozenset(('Validation', 'Business Rule', 'API Contract', 'Data Field', 'User Permission', 'Workflow', 'Database Behavior', 'Integration'))


NOISE = frozenset(('not', 'applicable', 'optional', 'mandatory', 'required', 'exempted', 'allowed', 'shall', 'must', 'should', 'will', 'which', 'checked',
                   'there', 'their', 'these', 'those', 'being', 'where', 'when', 'user', 'users', 'system', 'also', 'with', 'from', 'that', 'this', 'have', 'been'))


def change_terms(change):
    """Distinctive terms that identify what a change touches (values, field names, paths, quoted labels, the rule's subject)."""
    diff = change.get('diff') or {}
    removed = ' '.join(diff.get('removed', [])) if diff else ''
    added = ' '.join(diff.get('added', [])) if diff else ''
    focus_old = removed if change['change_type'] == 'MODIFIED' else (change.get('original_text') or '')
    focus_new = added if change['change_type'] == 'MODIFIED' else (change.get('modified_text') or '')
    whole = ' '.join(x for x in (change.get('original_text'), change.get('modified_text')) if x)
    identifiers = sorted({m.group(0) for m in IDENT.finditer(whole)} | {m.group(1).strip() for m in QUOTED.finditer(whole)})[:20]
    subject_words = sorted(subject_terms(change))
    if not change.get('administrative'):
        identifiers = sorted(set(identifiers) | set(subject_words))[:24]
    numbers = list(diff.get('numbers_changed', [])) if diff else sorted(set(NUMBER.findall(whole)))[:10]
    keywords = sorted({t for t in tokens(focus_old + ' ' + focus_new) if len(t) >= 4 and t not in NOISE})[:25]
    return {'old': [t for t in tokens(focus_old) if t not in NOISE], 'new': tokens(focus_new), 'identifiers': identifiers, 'numbers': numbers,
            'keywords': keywords, 'old_text': clip(removed, 120), 'new_text': clip(added, 120), 'subject': subject_words}


def subject_terms(change):
    """Words naming what the rule is about (its subject and the subjects of its conditions)."""
    words = set()
    for text in [change.get('subject') or ''] + [d.get('subject') or '' for d in change.get('deltas', []) if d.get('role') == 'condition']:
        for w in re.split(r'[^a-z0-9]+', str(text).lower()):
            if len(w) > 3 and w not in NOISE and w not in ('requirement', 'system', 'field', 'data', 'value', 'page', 'screen'):
                words.add(w)
    return words


def evidence_terms(change, pool_text=''):
    """Terms a test case must show to count as evidence for this change: (subject_terms, other_terms)."""
    from Core.change_impact.insight import expand_acronym
    subject = set(subject_terms(change))
    for d in change.get('deltas', []):
        if d.get('role') == 'condition' and d.get('subject'):
            expanded = expand_acronym(pool_text, d['subject'])
            if expanded:
                subject |= {w.lower() for w in expanded.split() if len(w) > 3}
            if re.fullmatch(r'[A-Za-z]{2,5}', d['subject'] or ''):
                subject.add(d['subject'].lower())
    other = set()
    for d in change.get('deltas', []):
        if d['kind'] in ('numeric', 'api'):
            other |= {x.lower() for x in re.findall(r'[A-Za-z0-9_.\-]{3,}', '%s %s' % (d.get('old') or '', d.get('new') or ''))}
    stm = change.get('statement') or {}
    other |= {str(x).lower() for x in (stm.get('identifiers') or [])[:6]} | {str(x).lower() for x in (stm.get('method_ids') or [])}
    return subject, other


def _test_text(test):
    return ' '.join(str(test.get(k) or '') for k in ('scenario', 'test_case', 'pre_conditions', 'steps', 'expected_result'))


def _test_title(test):
    return clip(test.get('test_case') or test.get('scenario') or test.get('tc_number') or '', 160)


def requirement_groups(changes):
    """Functional changes grouped by the logical requirement they belong to (one requirement row per group)."""
    groups, order = {}, []
    for change in changes:
        if change.get('administrative'):
            continue
        key = change.get('requirement_key') or change['change_id']
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(change)
    return [(k, groups[k]) for k in order]


def map_requirements(changes, documents, reader, owner_ids, policy, insight=None):
    """Return (requirement_findings, mapping) where mapping = {change_id: [candidate dicts]}.

    One requirement row per logical requirement: several differences inside "Requirement 2" are one affected requirement
    with several changes, not several requirements. Administrative changes (dates, reference numbers) are not requirements.
    """
    limits = policy.get('limits', {})
    suggestion = float(limits.get('requirement_suggestion_threshold', 0.30))
    versions = reader.knowledge_versions(owner_ids.get('knowledge_entity_id'))
    version_ids = [v['document_version_id'] for v in versions] or [owner_ids.get('document_version_id')]
    version_info = {v['document_version_id']: v for v in versions}
    recorded = reader.requirements([v for v in version_ids if v])
    findings, mapping = [], {c['change_id']: [] for c in changes}
    questions = (insight or {}).get('questions', [])
    for key, group in requirement_groups(changes):
        lead = group[0]
        text = ' '.join(x for c in group for x in (c.get('original_text'), c.get('modified_text')) if x)
        c_tokens = token_set(text)
        ref = normalize_ref(lead.get('reference'))
        old_norms = {normalize(c.get('original_text') or '') for c in group if c.get('original_text')}
        candidates = []
        for r in recorded:
            score = jaccard(c_tokens, token_set(r['description']))
            sr = normalize_ref(r['source_reference'])
            if normalize(r['description']) in old_norms:
                basis, assurance = 'Same wording as the baseline clause', 'EVIDENCE_BACKED'
            elif ref and sr == ref:
                basis, assurance = 'Same requirement reference (%s)' % ref, 'EVIDENCE_BACKED'
            elif score >= suggestion and len(c_tokens & token_set(r['description'])) >= 3:
                basis, assurance = 'Wording overlap %.2f (suggestion only)' % score, 'SUGGESTED'
            else:
                continue
            doc = version_info.get(r['document_version_id'], {})
            candidates.append({'requirement': r, 'basis': basis, 'assurance': assurance, 'score': round(score, 3),
                               'document_type': doc.get('document_type', ''), 'version': doc.get('version', '')})
        candidates.sort(key=lambda c: (c['assurance'] != 'EVIDENCE_BACKED', -c['score'], c['requirement']['id']))
        candidates = candidates[:5]
        for c in group:
            mapping[c['change_id']] = candidates
        kinds = sorted({c['change_type'] for c in group})
        impact = ', '.join({'MODIFIED': 'Modified', 'REMOVED': 'Removed', 'ADDED': 'Added'}.get(k, 'Proposed') for k in kinds)
        ids = [c['change_id'] for c in group]
        related_q = [q['id'] for q in questions if set(q.get('related_changes', [])) & set(ids)]
        title = ('%s - %s' % (lead['reference'], lead['requirement'])) if lead.get('reference') and lead.get('requirement') else (lead.get('requirement') or lead['reference'] or lead['change_id'])
        new_text = ' '.join(clip(c.get('modified_text') or '', 400) for c in group if c.get('modified_text'))
        old_text = ' '.join(clip(c.get('original_text') or '', 400) for c in group if c.get('original_text'))
        findings.append({
            'finding_id': stable_id('REQ', lead['change_id'], 'clause'), 'kind': 'REQUIREMENT', 'change_id': lead['change_id'],
            'title': title, 'impact_class': 'Direct', 'severity': '',
            'assurance': 'CONFIRMED' if lead['change_kind'] == 'ACTUAL_DIFF' else 'EVIDENCE_BACKED',
            'verification_status': 'Changed requirement stated in the analysed document',
            'payload': {'origin': 'SOURCE_CLAUSE', 'reference': lead['reference'], 'requirement': lead.get('requirement'), 'impact': impact,
                        'description': clip(new_text or old_text, 900), 'previous_description': clip(old_text, 900) or None,
                        'change_ids': ids, 'change_count': len(ids), 'document': lead['source_document'], 'version': lead['source_version'],
                        'open_questions': related_q, 'match_basis': 'Changed requirement in the analysed document'},
            'evidence': [e for c in group for e in c['evidence'][-1:]][:4]})
        for cand in candidates:
            r = cand['requirement']
            findings.append({
                'finding_id': stable_id('REQ', lead['change_id'], r['id']), 'kind': 'REQUIREMENT', 'change_id': lead['change_id'],
                'title': r['source_reference'], 'impact_class': 'Direct' if cand['assurance'] == 'EVIDENCE_BACKED' else 'Potential - requires verification',
                'severity': '', 'assurance': cand['assurance'],
                'verification_status': 'Recorded requirement (%s); the link to this change is a %s' % (
                    r['status'], 'reference or wording match' if cand['assurance'] == 'EVIDENCE_BACKED' else 'suggestion'),
                'payload': {'origin': 'RECORDED_REQUIREMENT', 'requirement_id': r['id'], 'reference': r['source_reference'], 'impact': impact,
                            'description': clip(r['description'], 600), 'document': cand['document_type'], 'change_ids': ids, 'change_count': len(ids),
                            'version': cand['version'], 'document_version_id': r['document_version_id'], 'match_basis': cand['basis'], 'score': cand['score']},
                'evidence': [{'ref': stable_id('EV', r['id'], 'req', length=8), 'role': 'recorded-requirement', 'document': cand['document_type'],
                              'document_version': cand['version'], 'anchor': r['anchor'], 'quote': clip(r['description'], 500),
                              'verified': True, 'requirement_id': r['id']}]})
    return findings, mapping


def _current_workbook_only(reader, tests):
    """(tests of the current workbook versions, ids of the superseded ones). A Knowledge source can have several test case workbook versions; only the
    latest counts as existing coverage (Core.test_case_scope). When the version records cannot be read, nothing is dropped and a note says so."""
    from Core import test_case_scope as scope
    rows = list(tests.values())
    note = getattr(reader, '_note', None)
    try:
        annotated = scope.annotate(rows, scope.records_for(reader, rows), True)
        ok = True
    except Exception:
        annotated, ok = scope.annotate(rows, [], False), False
    summary = scope.summarise(annotated, ok)
    if note and rows and (summary['superseded_rows'] or not ok or summary['unversioned_rows']):
        note('Test case workbook versions: ' + summary['note'])
    superseded = {r['id'] for r in annotated if r.get('scope_status') == scope.SUPERSEDED}
    return {tid: t for tid, t in tests.items() if tid not in superseded}, superseded


def build_test_impact(changes, mapping, systems, reader, documents, owner_ids, policy, unchanged_norms=None, source_ids=(), pool_text=''):
    """Return (test_findings, per_change_info). per_change_info drives gaps and risk."""
    limits = policy.get('limits', {})
    by_id = {c['change_id']: c for c in changes}
    versions = reader.knowledge_versions(owner_ids.get('knowledge_entity_id'))
    scope_versions = [v['document_version_id'] for v in versions]
    doc_versions = {d.get('document_version_id') for d in documents if d.get('document_version_id')}
    scope_tests = {t['id']: t for t in reader.test_cases(version_ids=scope_versions)} if scope_versions else {}
    source_rel = reader.tests_for_sources(source_ids)
    module_name = owner_ids.get('module') or (documents[0]['owner'].get('module') if documents else '')
    domain_name = owner_ids.get('domain') or (documents[0]['owner'].get('domain') if documents else '')
    if module_name:
        for t in reader.test_cases(module=module_name, domain=domain_name or None, limit=int(limits.get('max_tests_considered', 5000))):
            scope_tests.setdefault(t['id'], t)
    missing_ids = set(source_rel) - set(scope_tests)
    if missing_ids:
        scope_tests.update({t['id']: t for t in reader.test_cases(test_ids=sorted(missing_ids))})
    link_rows = reader.requirement_links([c['requirement']['id'] for cands in mapping.values() for c in cands])
    links_by_req = {}
    for link in link_rows:
        links_by_req.setdefault(link['requirement_id'], []).append(link)
    needed = {l['test_case_id'] for l in link_rows} - set(scope_tests)
    if needed:
        scope_tests.update({t['id']: t for t in reader.test_cases(test_ids=sorted(needed))})
    scope_tests, superseded_ids = _current_workbook_only(reader, scope_tests)
    review = reader.latest_review_state(list(scope_tests))
    findings, info, seen = [], {}, set()

    def relation_of(test):
        if test['id'] in source_rel or (test.get('document_version_id') in doc_versions and test.get('document_version_id')):
            return 'SOURCE'
        if test.get('document_version_id') in scope_versions:
            return 'KNOWLEDGE'
        return 'MODULE'

    def add(change, test, req_ref, req_id, link_status, classification, reason, action, assurance, evidence, extra=None):
        key = (change['change_id'], test['id'], classification)
        if key in seen:
            return
        seen.add(key)
        version = ('TC-V%s' % test['version_number']) if test.get('version_number') else 'Legacy (no revision)'
        payload = {'test_case_id': test['id'], 'tc_number': test.get('tc_number'), 'tc_id': test.get('tc_id'), 'title': _test_title(test),
                   'requirement_ref': req_ref, 'requirement_id': req_id, 'current_version': version,
                   'test_case_version_id': test.get('test_case_version_id'), 'document_version_id': test.get('document_version_id'),
                   'classification': classification, 'reason': reason, 'recommended_action': action, 'link_status': link_status,
                   'importance': test.get('importance'), 'test_type': test.get('test_type'), 'last_result': test.get('last_result') or 'Not Run',
                   'review_state': review.get(test['id'], 'Not reviewed'), 'domain': test.get('domain'), 'module': test.get('module')}
        payload.update(extra or {})
        findings.append({
            'finding_id': stable_id('TST', change['change_id'], test['id'], classification), 'kind': 'TEST', 'change_id': change['change_id'],
            'title': '%s %s' % (test.get('tc_number') or test['id'], _test_title(test)), 'impact_class': 'Regression', 'severity': '',
            'assurance': assurance, 'verification_status': {'CONFIRMED': 'Confirmed: related to the source document and its content matches the change',
                                                          'PROPOSED': 'Proposed link; not confirmed',
                                                          'LEXICAL': 'Possible match from wording evidence; not a confirmed relationship',
                                                          'KNOWLEDGE': 'Same Knowledge and strong content evidence; no direct source relationship',
                                                          'DEPENDENCY': 'Selected through a stated system dependency; needs verification',
                                                          'UNCHANGED_REQUIREMENT': 'Confirmed link to an unchanged requirement'}.get(link_status, 'Unverified'),
            'payload': payload, 'evidence': evidence})
        info.setdefault(change['change_id'], {'confirmed_tests': set(), 'tests': set(), 'firm_tests': set()})
        info[change['change_id']]['tests'].add(test['id'])
        if assurance in ('CONFIRMED', 'EVIDENCE_BACKED') and link_status in ('CONFIRMED', 'KNOWLEDGE'):
            info[change['change_id']]['firm_tests'].add(test['id'])
        if link_status == 'CONFIRMED' and assurance == 'CONFIRMED':
            info[change['change_id']]['confirmed_tests'].add(test['id'])

    functional = [c for c in changes if not c.get('administrative')]
    source_linked_unmatched = set()
    test_tokens = {tid: token_set(_test_text(t)) for tid, t in scope_tests.items()}
    for change in functional:
        terms = change_terms(change)
        info.setdefault(change['change_id'], {'confirmed_tests': set(), 'tests': set(), 'firm_tests': set()})
        for cand in mapping.get(change['change_id'], []):
            r = cand['requirement']
            for link in links_by_req.get(r['id'], []):
                test = scope_tests.get(link['test_case_id'])
                if not test:
                    continue
                confirmed = link['status'] == 'CONFIRMED'
                firm = cand['assurance'] == 'EVIDENCE_BACKED'
                assurance = 'CONFIRMED' if confirmed and firm else ('EVIDENCE_BACKED' if confirmed else 'SUGGESTED')
                link_status = 'CONFIRMED' if confirmed else 'PROPOSED'
                classification, reason, action = _classify(change, test, terms, confirmed and firm)
                add(change, test, r['source_reference'], r['id'], link_status, classification, reason, action, assurance,
                    [{'ref': stable_id('EV', r['id'], test['id'], length=8), 'role': 'recorded-link', 'document': cand['document_type'],
                      'document_version': cand['version'], 'quote': clip(link.get('rationale') or r['description'], 400), 'verified': True,
                      'requirement_id': r['id'], 'link_status': link['status']}] + change['evidence'][:1])
        # Content evidence: which test cases show the distinctive terms of this change
        subject, other = evidence_terms(change, pool_text)
        strong = subject | other
        scored = []
        for tid, test in scope_tests.items():
            if tid in info[change['change_id']]['tests']:
                continue
            tt = test_tokens[tid]
            shared_subject, shared_other = sorted(subject & tt), sorted(other & tt)
            relation = relation_of(test)
            hit = len(shared_subject) + len(shared_other)
            enough = (len(shared_subject) >= 1 and hit >= 2) or len(shared_subject) >= 2 or len(shared_other) >= 2
            if not enough:
                if relation == 'SOURCE':
                    source_linked_unmatched.add(tid)
                continue
            ratio = hit / float(len(strong) or 1)
            scored.append(({'SOURCE': 0, 'KNOWLEDGE': 1, 'MODULE': 2}[relation], -ratio, tid, relation, shared_subject + shared_other))
        scored.sort()
        for _, neg_ratio, tid, relation, shared in scored[:12]:
            test = scope_tests[tid]
            evidence_note = 'Shares %s with the changed requirement.' % ', '.join('"%s"' % w for w in shared[:6])
            if relation == 'SOURCE':
                link_status, assurance = 'CONFIRMED', 'CONFIRMED'
                note = 'The test case is related to the analysed document (source relationship). ' + evidence_note
            elif relation == 'KNOWLEDGE' and len(shared) >= 3:
                link_status, assurance = 'KNOWLEDGE', 'EVIDENCE_BACKED'
                note = 'The test case belongs to the same Knowledge. ' + evidence_note
            else:
                link_status, assurance = 'LEXICAL', 'SUGGESTED'
                note = 'Possible match only: no source relationship. ' + evidence_note
            classification, reason, action = _classify(change, test, terms, assurance in ('CONFIRMED', 'EVIDENCE_BACKED'))
            if link_status == 'LEXICAL':
                classification, action = 'Review required', 'Confirm whether this test covers the changed requirement before relying on it.'
                reason = note
            else:
                reason = note + ' ' + reason
            add(change, test, change['reference'], '', link_status, classification, reason, action, assurance,
                [{'ref': stable_id('EV', change['change_id'], test['id'], 'content', length=8), 'role': 'test-content',
                  'quote': clip(_test_text(test), 360), 'verified': True, 'matched_terms': shared[:8], 'relationship': relation}] + change['evidence'][-1:],
                {'overlap_terms': shared[:8], 'relationship': relation})
        # A removed requirement that no recorded link or distinctive term tied to a test: fall back to the plain wording overlap (the same rule Impact Analysis uses
        # for removed requirements), reported as a possible match to review, never as a confirmed link.
        if change['change_type'] == 'REMOVED' and not info[change['change_id']]['tests']:
            statement = {t for t in token_set(re.sub(r'^\s*[A-Za-z]+[-_ ]?\d+[:.)]?\s*', '', change.get('original_text') or ''))
                         if (len(t) >= 5 or any(ch.isdigit() for ch in t)) and t not in NOISE}
            fallback = []
            for tid, test in scope_tests.items():
                shared = sorted(statement & test_tokens[tid])
                if len(shared) >= 2 and len(shared) / float(len(statement) or 1) >= 0.4:
                    fallback.append((-len(shared) / float(len(statement)), tid, shared))
            for _, tid, shared in sorted(fallback)[:12]:
                test = scope_tests[tid]
                note = 'Possible match only for the removed requirement: the test shares %s with its wording; no recorded link.' % ', '.join('"%s"' % w for w in shared[:6])
                add(change, test, change['reference'], '', 'LEXICAL', 'Review required', note,
                    'Confirm whether this test covered the removed requirement, then retire or update it.', 'SUGGESTED',
                    [{'ref': stable_id('EV', change['change_id'], test['id'], 'removed', length=8), 'role': 'test-content', 'quote': clip(_test_text(test), 360), 'verified': True,
                      'matched_terms': shared[:8], 'relationship': relation_of(test)}] + change['evidence'][-1:],
                    {'overlap_terms': shared[:8], 'relationship': relation_of(test)})
    for cid, entry in info.items():
        entry['source_linked_unmatched'] = len(source_linked_unmatched)

    # Unchanged requirements with recorded links: reusable without changes (compare mode only).
    if unchanged_norms:
        unchanged = set(unchanged_norms)
        reqs = [r for v in versions for r in reader.requirements([v['document_version_id']])] if len(versions) < 40 else []
        for r in reqs:
            if normalize(r['description']) in unchanged:
                for link in reader.requirement_links([r['id']]):
                    if link['test_case_id'] in superseded_ids:
                        continue
                    test = scope_tests.get(link['test_case_id']) or (reader.test_cases(test_ids=[link['test_case_id']]) or [None])[0]
                    if test and link['status'] == 'CONFIRMED' and len(findings) < int(limits.get('max_tests_considered', 5000)):
                        scope_tests.setdefault(test['id'], test)
                        pseudo = {'change_id': '', 'change_kind': 'ACTUAL_DIFF', 'reference': r['source_reference'], 'evidence': []}
                        add(pseudo, test, r['source_reference'], r['id'], 'UNCHANGED_REQUIREMENT', 'Reuse without changes',
                            'The requirement wording is identical in both versions and the test is confirmed-linked to it.',
                            'Keep as is. Include in regression only if the module is in scope.', 'CONFIRMED',
                            [{'ref': stable_id('EV', r['id'], 'unchanged', length=8), 'role': 'recorded-requirement', 'quote': clip(r['description'], 300), 'verified': True}])

    # Cross-system: tests of systems the source text says are connected to the changed system.
    for system in systems:
        dep = (system.get('payload') or {}).get('dependency')
        if not dep or system['assurance'] not in ('EVIDENCE_BACKED', 'CONFIRMED'):
            continue
        target_module = (system['payload']['component'] or '').split(' › ')
        module_name = target_module[1] if len(target_module) > 1 else target_module[0]
        for cid in (system['payload'].get('change_ids') or [system['change_id']]):
            change = by_id.get(cid)
            if not change:
                continue
            tests = [t for t in reader.test_cases(module=module_name, limit=300) if t['id'] not in info[change['change_id']]['tests']]
            tests.sort(key=lambda t: ({'High': 0, 'Medium': 1}.get(t.get('importance'), 2), t['id']))
            for test in tests[:15]:
                add(change, test, change['reference'], '', 'DEPENDENCY', 'Review required',
                    'The source text states a relationship (%s) between the changed system and "%s", so this test of that system may be affected.'
                    % (dep['relationship'].replace('_', ' '), system['payload']['component']),
                    'Re-run or review this test for the connected system after the change.', 'EVIDENCE_BACKED' if system['assurance'] == 'EVIDENCE_BACKED' else 'CONFIRMED',
                    system['evidence'][:1], {'dependency_key': dep['key'], 'connected_system': system['payload']['component']})
    return findings, info


def _classify(change, test, terms, firm):
    text = ' ' + normalize(_test_text(test)) + ' '
    if change['change_type'] == 'REMOVED':
        return ('Obsolete candidate', 'The requirement no longer appears in the changed version. A reviewer must decide; nothing is retired automatically.',
                'Review this test in QA Engineering and retire it only if the removal is confirmed.')
    if change['change_kind'] == 'PROPOSED_CRF':
        return ('Review required', 'The change request proposes a change to this requirement; the final wording is not applied yet.',
                'Review this test against the proposed change and update it once the change is approved.')
    old_hits = [t for t in terms['old'] if len(t) >= 3 and (' ' + t + ' ') in text]
    ident_hits = [i for i in terms['identifiers'] if i.lower() in text]
    num_hits = [n for n in terms['numbers'] if (' ' + n + ' ') in text or n in text.split()]
    if change['change_type'] == 'MODIFIED' and (old_hits or num_hits) and firm and (num_hits or change['category'] in STRONG_CATEGORIES):
        shown = (num_hits or old_hits)[:3]
        return ('Update required', 'The test still refers to "%s", which the change replaces%s.' % (', '.join(shown), (' (now "%s")' % terms['new_text']) if terms['new_text'] else ''),
                'Update the expected result or steps to match the changed requirement.')
    if change['change_type'] == 'MODIFIED' and ident_hits and firm and change['category'] in STRONG_CATEGORIES:
        return ('Review required', 'The test uses "%s", which is part of this change.' % ident_hits[0], 'Review the steps and expected result for the changed rule.')
    if change['change_type'] == 'ADDED':
        return ('Review required', 'A new requirement was added next to this covered area.', 'Check whether this test already covers the new behaviour.')
    return ('Review required', 'The related requirement changed and the test wording does not show whether it is still correct.',
            'Review the steps and expected result against the new requirement text.')


def build_gaps(changes, mapping, test_info, owner, document, policy, scenarios_by_change=None):
    """Coverage gaps: one per logical requirement whose functional changes have no confirmed or evidence-backed existing test."""
    findings = []
    scenarios_by_change = scenarios_by_change or {}
    for key, group in requirement_groups(changes):
        group = [c for c in group if c['change_type'] != 'REMOVED']
        if not group:
            continue
        lead = group[0]
        infos = [test_info.get(c['change_id'], {}) for c in group]
        if any(i.get('confirmed_tests') or i.get('firm_tests') for i in infos):
            continue
        scenarios, types = SCENARIOS.get(lead['category'], SCENARIOS['Uncategorised'])
        specific = []
        for c in group:
            for sc in scenarios_by_change.get(c['change_id'], []):
                if sc['title'] not in specific:
                    specific.append(sc['title'])
        if specific:
            scenarios = specific
        refs = [lead['reference']] if lead['reference'] else []
        suggestions = len({t for i in infos for t in i.get('tests', ())})
        reason = ('No existing test case shows evidence for this requirement.' if not suggestions else
                  'No existing test case is confirmed for this requirement; %d possible match(es) need confirmation.' % suggestions)
        subject = ('%s - %s' % (lead['reference'], lead['requirement'])) if lead.get('reference') and lead.get('requirement') else (lead['reference'] or lead['change_id'])
        text = ' '.join(clip(c.get('modified_text') or c.get('original_text') or '', 600) for c in group)
        findings.append({
            'finding_id': stable_id('GAP', lead['change_id']), 'kind': 'GAP', 'change_id': lead['change_id'],
            'title': subject, 'impact_class': 'Regression', 'severity': '',
            'assurance': 'CONFIRMED' if lead['change_kind'] == 'ACTUAL_DIFF' else 'EVIDENCE_BACKED',
            'verification_status': 'Coverage gap: no confirmed existing test found, not proof that none exists',
            'payload': {'subject': subject, 'description': ' '.join(c['description'] for c in group), 'change_ids': [c['change_id'] for c in group], 'linked_tests': 0,
                        'suggested_tests': suggestions, 'missing_scenarios': scenarios, 'suggested_test_types': types, 'reason': reason,
                        'recommended_action': 'Design and review new test cases in QA Engineering using the evidence below. Nothing is created automatically.',
                        'handoff': {'source_id': document['source_id'], 'document_version_id': document['document_version_id'],
                                    'domain': owner['domain'], 'module': owner['module'], 'knowledge_name': owner['knowledge_name'],
                                    'document_type': owner['document_type'], 'version': owner['version'], 'requirement_refs': refs,
                                    'change_id': lead['change_id'], 'test_types': types, 'evidence_text': clip(text, 1200), 'scenarios': scenarios}},
            'evidence': [e for c in group for e in c['evidence'][-1:]][:3]})
    return findings
