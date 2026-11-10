"""Existing regression test cases and coverage (engine 2.0). Read-only: no test case is created, changed or duplicated.

Every test case shown here is a row that already exists in QA Engineering; its id is copied from the database, never generated.
A test is identified by its database row id AND its hierarchy (the same tc id can exist in several workbooks).

Evidence tiers, strongest first (a test is shown with the strongest tier it reaches):
  1  verified requirement link   QA Engineering links the test to a requirement of the changed document that carries the changed requirement's id,
                                 or the test was generated from the changed document and records that exact statement as its source evidence
  2  verified source link        the test was generated from / linked to the changed document, and its text also matches the changed requirement
  3  strong functional evidence  the test text names the same hard keys (requirement id, API path, header, field, rule value) AND the same behaviour
                                 (shown, but NOT counted as coverage: similar content is not a verified relationship)
  4  potential, needs QA review  an identifier matches without shared meaning, or the test is linked to the document and only part of the wording matches
Generic words never select a test.
"""
import re
import sqlite3

from Core.change_impact.util import clip
from Core.cia import match, terms as T

DIRECT, DEPENDENCY = 'Directly affected', 'Dependency-related'
ADDITIONAL = 'Additional recommended'      # engine 1.0 category: no longer produced, kept so earlier results still display
BASIS = {1: 'Verified requirement link', 2: 'Verified source link', 3: 'Strong functional evidence', 4: 'Potential (needs QA review)'}
VERIFIED, SUGGESTED = 'Verified', 'Suggested'
MAX_POTENTIAL_PER_CHANGE = 10
TEST_COLUMNS = ('id', 'tc_number', 'tc_id', 'domain', 'module', 'knowledge_name', 'version', 'scenario', 'importance', 'test_type', 'test_case',
                'pre_conditions', 'steps', 'expected_result', 'status', 'automation_type', 'last_result', 'last_run_date', 'document_version_id',
                'test_case_version_id', 'comments')
MAX_TESTS = 20000


def load_tests(reader):
    """(rows, ok, note). Only CURRENT workbook versions count as existing coverage (Core.test_case_scope)."""
    conn = reader._conn()
    try:
        tables = reader._tables(conn)
        if 'test_cases' not in tables:
            return [], False, 'There is no test case table, so existing test cases could not be searched.'
        columns = reader._columns(conn, 'test_cases')
        select = ','.join(('t.' + c) if c in columns else ('NULL AS ' + c) for c in TEST_COLUMNS)
        rows = [dict(r) for r in conn.execute('SELECT %s FROM test_cases t ORDER BY t.id LIMIT %d' % (select, MAX_TESTS))]
    finally:
        conn.close()
    note = ''
    try:
        from Core import test_case_scope as scope
        annotated = scope.annotate(rows, scope.records_for(reader, rows), True)
        rows = [r for r in annotated if r.get('scope_status') != scope.SUPERSEDED]
        dropped = len(annotated) - len(rows)
        if dropped:
            note = '%d test case row(s) belong to older workbook versions and were not counted.' % dropped
    except Exception:
        note = 'The test case workbook versions could not be checked, so rows of older versions may be included.'
    return rows, True, note


def test_text(row):
    return ' '.join(str(row.get(k) or '') for k in ('scenario', 'test_case', 'pre_conditions', 'steps', 'expected_result'))


_EVIDENCE_LINE = re.compile(r'^Evidence \((verified)\):\s*"?(.*?)"?\s*$', re.M)


def recorded_evidence(row):
    """The source sentence QA AI Studio recorded in the test's Comments when it generated the test (verified matches only)."""
    found = _EVIDENCE_LINE.search(str(row.get('comments') or ''))
    return found.group(2).strip() if found else ''


def quotes_statement(evidence, statement):
    """True when the recorded source sentence and the changed statement are the same text (one contains the other)."""
    a, b = re.sub(r'\W+', ' ', str(evidence).lower()).strip(), re.sub(r'\W+', ' ', str(statement).lower()).strip()
    return len(a) >= 25 and len(b) >= 25 and (a in b or b in a)


def display_id(row):
    return str(row.get('tc_id') or row.get('tc_number') or row.get('id'))


def execution_type(row):
    automation = str(row.get('automation_type') or 'None')
    manual = str(row.get('status') or 'Manual')
    return manual if automation in ('None', '', 'NONE') else '%s (%s automation)' % (manual, automation)


def _links(reader, rows, source_ids, requirement_version_ids):
    """Stored links: {test id: why}. test_case_sources = generated from / linked to a source document; qa_requirement_links = Task 5 requirement links."""
    ids = [r['id'] for r in rows]
    by_source = {}
    try:
        for tid, sources in reader.test_sources(ids).items():
            by_source[tid] = set(sources)
    except Exception:
        pass
    by_requirement = {}
    try:
        requirements = reader.requirements(sorted(requirement_version_ids))
        req_by_id = {r['id']: r for r in requirements}
        for link in reader.requirement_links(sorted(req_by_id)):
            by_requirement.setdefault(link['test_case_id'], []).append(req_by_id[link['requirement_id']])
    except Exception:
        pass
    return by_source, by_requirement



TEST_SID = '__tests__'
TOPIC_TEST_MIN, TOPIC_TEST_SHARE = 12, 0.25


class _TestTopicRepo:
    """The document statistics plus one more fact: a named term found in a large part of ALL existing tests (an organisation or project name) says nothing about one test."""

    def __init__(self, repo, test_texts):
        self._repo = repo
        texts = list(test_texts)
        counts = {}
        for text in texts:
            for key in T.hard_keys(text):
                counts[key] = counts.get(key, 0) + 1
        self._topic = {k for k, n in counts.items() if len(texts) >= TOPIC_TEST_MIN and n / len(texts) >= TOPIC_TEST_SHARE}

    def topic_term(self, key, source_id):
        if source_id == TEST_SID:
            return key in self._topic
        return self._repo.topic_term(key, source_id)

    def __getattr__(self, name):
        return getattr(self._repo, name)


class _NullRepo:
    """Statistics stand-in when no document repository is available (no key is treated as common)."""

    def doc_count(self, excluded=()):
        return 1

    def key_share(self, key, excluded=()):
        return 0.0

    def topic_term(self, key, source_id):
        return False


def _unit(text, bg, source_bg=frozenset(), same_knowledge=False, source_id=None):
    keys = T.hard_keys(text, same_knowledge)
    return {'text': text, 'ref': '', 'source_id': source_id, 'keys': keys, 'words': T.context_words(text, keys) - bg - source_bg, 'full': T.context_words(text) - bg - source_bg}


def _reason(tier, judged, linked_ref):
    keys = [k for k in (judged or {}).get('keys', []) if k['strength'] != T.WEAK]
    named = match.key_phrase(keys, 3) if keys else ''
    words = ', '.join(((judged or {}).get('context') or [])[:4])
    if tier == 1:
        if str(linked_ref).startswith('the statement'):
            return 'This test was generated from this exact statement of the changed document (its recorded source evidence quotes it).'
        return 'QA Engineering links this test to requirement %s of the changed document.' % linked_ref
    if tier == 2:
        return 'Linked to the changed document%s%s.' % ('; its text names ' + named if named else '', ('; same behaviour: ' + words) if words else '')
    if tier == 3:
        return 'Its text names %s%s.' % (named or 'the same requirement', ('; same behaviour: ' + words) if words else '')
    return ('Possibly relevant: %s%s. A QA engineer should confirm it.' % (('names ' + named) if named else 'wording partly matches', ('; shared wording: ' + words) if words else ''))


def find_tests(reader, db, changes, affected, source_row, rows_by_id, scope, version_ids_by_source, family_ids=None, repo=None, source_bg=frozenset()):
    """Returns (regression dict, coverage list, search info).

    changes:  [{'change_id','text','reference','category','level','title'}]  (changed requirements)
    affected: affected items from match.find_affected
    The impact scope does not limit this search: a test of the changed requirement is relevant wherever it lives. Dependency tests are searched
    only for the affected items that were listed.
    """
    rows, ok, note = load_tests(reader)
    info = {'executed': ok and bool(rows), 'tests_considered': len(rows), 'tests_total': len(rows), 'linked_to_document': 0, 'note': note}
    if not ok or not rows:
        if ok:
            info['note'] = (note + ' ' if note else '') + 'There are no existing test cases in the repository.'
        return {DIRECT: [], DEPENDENCY: []}, _coverage_unverified(changes, info), info
    repo = repo or _NullRepo()
    family_ids = set(family_ids or ()) | {source_row['id']}
    family_versions = set()
    for sid in family_ids:
        family_versions |= set(version_ids_by_source.get(sid, ()))
    affected_ids = {a['hierarchy']['source_id'] for a in affected}
    req_versions = set(family_versions)
    for sid in affected_ids:
        req_versions |= set(version_ids_by_source.get(sid, ()))
    by_source, by_requirement = _links(reader, rows, family_ids | affected_ids, req_versions)
    texts = {r['id']: test_text(r) for r in rows}
    test_bg = T.background_words(list(texts.values()))
    change_units = [(c, match.build_change_unit(c, source_bg, source_id=source_row['id'])) for c in changes]
    affected_units = [(a, _unit(a['quote'], frozenset(), source_bg)) for a in affected if a['classification'] in (match.CONFIRMED, match.POTENTIAL)]
    for _a, u in affected_units:
        u['change_id'] = _a['key']
        u['source_id'] = _a['hierarchy']['source_id']
    repo = _TestTopicRepo(repo, texts.values())
    selected = {}
    linked_family = set()

    def entry_for(row):
        key = row['id']
        if key not in selected:
            selected[key] = {'key': 'TC-%s' % row['id'], 'category': DEPENDENCY, 'database_id': row['id'], 'test_case_id': display_id(row), 'tc_number': row.get('tc_number') or '',
                             'title': clip(row.get('scenario') or row.get('test_case') or '', 200),
                             'hierarchy': {'domain': row['domain'], 'module': row['module'], 'knowledge_name': row['knowledge_name'], 'version': row['version']},
                             'related_requirement': '', 'reason': '', 'basis': '', 'tier': 9, 'evidence': [], 'importance': row.get('importance') or '',
                             'execution_type': execution_type(row), 'last_result': row.get('last_result') or '', 'change_ids': [], 'affected_keys': [], 'matches': []}
        return selected[key]

    def record(row, category, tier, judged, change_id, affected_key, linked_ref=''):
        e = entry_for(row)
        if category == DIRECT:
            e['category'] = DIRECT
        if change_id and change_id not in e['change_ids']:
            e['change_ids'].append(change_id)
        if affected_key and affected_key not in e['affected_keys']:
            e['affected_keys'].append(affected_key)
        if judged:
            e['matches'].append({'change_id': change_id or affected_key, 'keys': [k['surface'] or k['key'] for k in judged['keys']][:6], 'context': judged['context'][:6]})
        if tier < e['tier']:
            e['tier'], e['basis'], e['verified'] = tier, BASIS[tier], tier <= 2
            e['reason'] = _reason(tier, judged, linked_ref)
            e['evidence'] = [k['surface'] or k['key'] for k in (judged or {}).get('keys', [])][:6] or ([linked_ref] if linked_ref else [])
            if linked_ref:
                e['related_requirement'] = linked_ref

    for row in rows:
        same_k = (row['domain'], row['module'], row['knowledge_name']) == (source_row['domain'], source_row['module'], source_row['knowledge_name'])
        tu = _unit(texts[row['id']], test_bg, source_bg, same_k, TEST_SID)
        linked_sources = set(by_source.get(row['id'], ()))
        source_linked = bool(family_ids & linked_sources)
        if source_linked:
            linked_family.add(row['id'])
        req_links = [r for r in by_requirement.get(row['id'], []) if r.get('document_version_id') in family_versions]
        for change, cu in change_units:
            c_ids = {k for k, v in cu['keys'].items() if v['kind'] == 'requirement id'}
            ref_hit = next((r for r in req_links if T.norm_id(r.get('source_reference') or r.get('logical_key') or '-') in c_ids), None)
            judged = match.judge(cu, tu, repo, (), same_k, False, False)
            level = (judged or {}).get('level')
            statement_link = bool(source_linked and recorded_evidence(row) and quotes_statement(recorded_evidence(row), change['text']))
            if ref_hit:
                record(row, DIRECT, 1, judged, change['change_id'], None, T.norm_id(ref_hit.get('source_reference') or ref_hit.get('logical_key')))
            elif statement_link:
                record(row, DIRECT, 1, judged, change['change_id'], None, 'the statement it was generated from')
            elif source_linked and level in (match.CONFIRMED, match.POTENTIAL):
                record(row, DIRECT, 2, judged, change['change_id'], None)
            elif level == match.CONFIRMED:
                record(row, DIRECT, 3, judged, change['change_id'], None)
            elif level == match.POTENTIAL or (source_linked and level == match.INVESTIGATE):
                record(row, DIRECT, 4, judged, change['change_id'], None)
        if row['id'] in selected and selected[row['id']]['category'] == DIRECT:
            continue
        for item, au in affected_units:
            judged = match.judge(au, tu, repo, (), same_k, False, False)
            level = (judged or {}).get('level')
            target_linked = item['hierarchy']['source_id'] in linked_sources
            if item['classification'] != match.CONFIRMED:
                if level in (match.CONFIRMED, match.POTENTIAL):
                    record(row, DEPENDENCY, 4, judged, (item['change_ids'] or [''])[0], item['key'])      # a test reached only through a potential dependency is a suggestion, never verified
            elif target_linked and level in (match.CONFIRMED, match.POTENTIAL):
                record(row, DEPENDENCY, 2, judged, (item['change_ids'] or [''])[0], item['key'])
            elif level == match.CONFIRMED:
                record(row, DEPENDENCY, 3, judged, (item['change_ids'] or [''])[0], item['key'])
            elif level == match.POTENTIAL:
                record(row, DEPENDENCY, 4, judged, (item['change_ids'] or [''])[0], item['key'])
    info['linked_to_document'] = len(linked_family)
    result = {DIRECT: [], DEPENDENCY: []}
    for entry in selected.values():
        result[entry['category']].append(entry)
    _limit_potential(result)
    for category in (DIRECT, DEPENDENCY):
        for entry in result[category]:
            entry['priority'] = _priority(category, entry, changes)
        result[category].sort(key=lambda e: (e['priority'], e['tier'], str(e['hierarchy']['domain']), str(e['hierarchy']['module']), e['test_case_id'], e['database_id']))
    coverage = _coverage(changes, result, info, linked_family, texts, rows)
    return result, coverage, info


def _limit_potential(result):
    """Potential (tier 4) tests are suggestions for QA review; keep the best few per change so they cannot bury the verified ones."""
    seen = {}
    for category in (DIRECT, DEPENDENCY):
        keep = []
        for e in sorted(result[category], key=lambda x: (x['tier'], len(x['matches']) * -1, x['database_id'])):
            if e['tier'] == 4:
                owner = (e['change_ids'] or e['affected_keys'] or [''])[0]
                seen[owner] = seen.get(owner, 0) + 1
                if seen[owner] > MAX_POTENTIAL_PER_CHANGE:
                    continue
            keep.append(e)
        result[category] = keep


def _priority(category, entry, changes):
    levels = {c['change_id']: c.get('level', 'Low') for c in changes}
    order = ['Low', 'Medium', 'High', 'Critical']
    top = max([order.index(levels.get(cid, 'Low')) for cid in entry['change_ids']] or [0])
    if category == DIRECT:
        rank = 0 if top >= 2 else 1
    else:
        rank = 1 if top >= 2 else 2
    if entry.get('tier', 9) >= 4:
        rank = max(rank, 2)          # a potential match is a suggestion for QA review: never the first thing to run
    elif entry.get('tier', 9) == 3:
        rank = max(rank, 1) if rank == 0 and top < 3 else rank
    return 'P%d' % rank


def _coverage_unverified(changes, info):
    reason = info.get('note') or 'The test case search could not be completed.'
    return [{'key': 'COV-' + c['change_id'], 'subject': c.get('title') or c['text'][:120], 'change_id': c['change_id'], 'reference': c.get('reference', ''), 'kind': 'Changed requirement',
             'status': 'Coverage Not Verified', 'evidence': reason, 'gap': '', 'risk': c.get('level', 'Low'), 'scenario': c.get('scenario', ''), 'hierarchy': c.get('hierarchy', {}),
             'tests': []} for c in changes]


def _coverage(changes, result, info, linked_family, texts, rows):
    """Per changed requirement: Covered (a test with a VERIFIED requirement-to-test link exists), Coverage Gap (tests were searched, none relates) or Coverage Not Verified
    (only potential tests, or tests linked to the changed document that could not be matched to this requirement). A gap is never reported when a relevant test exists."""
    by_change = {}
    for t in result[DIRECT]:
        for cid in t['change_ids']:
            by_change.setdefault(cid, []).append(t)
    out = []
    for c in changes:
        tests = sorted(by_change.get(c['change_id'], []), key=lambda t: (t['tier'], t['database_id']))
        strong = [t for t in tests if t['tier'] <= 2]           # verified relationship only (requirement link, source link or recorded statement)
        content_only = [t for t in tests if t['tier'] == 3]
        ids = lambda group: ', '.join(t['test_case_id'] for t in group[:5]) + (' and %d more' % (len(group) - 5) if len(group) > 5 else '')
        if strong:
            status = 'Covered'
            evidence = '%d existing test(s): %s. Basis: %s%s.' % (len(strong), ids(strong), strong[0]['basis'].lower(), ('; ' + strong[0]['reason']) if strong[0].get('reason') else '')
            gap = ''
        elif content_only:
            status = 'Coverage Not Verified'
            evidence = '%d existing test(s) (%s) use the same identifiers and wording, but none is linked to this requirement or generated from this statement. Similar wording is not proof of coverage; confirm it in QA Engineering.' % (len(content_only), ids(content_only))
            gap = 'Matching content found; no verified requirement-to-test link.'
        elif tests:
            status = 'Coverage Not Verified'
            evidence = '%d existing test(s) may relate (%s) but only on potential evidence; a QA engineer should confirm.' % (len(tests), ids(tests))
            gap = 'Only potential tests were found.'
        elif linked_family:
            status = 'Coverage Not Verified'
            evidence = '%d existing test(s) are linked to the changed document, but none could be matched to this requirement. Check them in QA Engineering before writing a new test.' % len(linked_family)
            gap = 'Tests exist for the document; none matched this requirement.'
        else:
            status = 'Coverage Gap'
            evidence = 'No existing test case names this requirement\'s identifiers or describes its behaviour (%d test case(s) searched, none linked to the changed document).' % info['tests_considered']
            gap = 'No existing test appears to cover this changed requirement.'
        out.append({'key': 'COV-' + c['change_id'], 'subject': c.get('title') or c['text'][:120], 'change_id': c['change_id'], 'reference': c.get('reference', ''), 'kind': 'Changed requirement',
                    'status': status, 'evidence': evidence, 'gap': gap, 'risk': c.get('level', 'Low'), 'scenario': c.get('scenario', ''), 'hierarchy': c.get('hierarchy', {}),
                    'tests': [{'database_id': t['database_id'], 'test_case_id': t['test_case_id'], 'basis': t['basis'], 'tier': t['tier']} for t in tests[:12]]})
    return out
