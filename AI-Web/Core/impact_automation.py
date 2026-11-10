"""Automation impact for AI Assistant Impact Analysis (Playwright, API, SQL), resolved per test case scenario.

Strictly read-only: nothing is executed, regenerated, repaired or overwritten, and no test case is created.

The links between a test case row and its automation come from ``Core.automation_links`` (shared with Change Impact), which
credits a row only with automation that belongs to that row: a Playwright asset of the same test id AND the row's own Knowledge
source, the API endpoint bound to the row, the row's own SQL script path. An API collection stored for a whole workbook version
is reported as version-level context and never makes a scenario "automated". A test id alone proves nothing, because the same id
repeats across workbook versions.

A finding is linked to automation only through the existing test case rows it already matched (``test_case_coverage.matches``).
"""
import re

from Core.automation_links import AutomationContext, KINDS, row_key
from Core.change_impact.automation import _hits, _snippet

# Statuses (per finding, and for the whole analysis)
NO_MATCHED_TESTS = 'NO_MATCHED_TESTS'          # no existing test matched, so there is nothing to look up
NOT_READ = 'AUTOMATION_NOT_READ'               # automation records could not be read (never reported as "none")
FOUND = 'AUTOMATION_FOUND'                     # matched scenarios have automation of their own
REVIEW = 'AUTOMATION_REVIEW_NEEDED'            # an asset's own text contains a term the change touches
DECLARED_ONLY = 'AUTOMATION_DECLARED_ONLY'     # the scenarios say they are automated, but no script/endpoint record backs it
NONE_LINKED = 'NO_AUTOMATION_LINKED'           # matched scenarios have no automation of their own
NOT_APPLICABLE = 'NOT_APPLICABLE'              # unchanged finding, test search not executed


def _numbers(text):
    return {n.replace(',', '') for n in re.findall(r'(?<![\w.])\d+(?:[.,]\d+)?(?![\w])', str(text or ''))}


def _terms(finding):
    """Distinctive search terms of a change: identifiers (snake_case, camelCase, paths, dotted) and numbers.

    Numbers are strong evidence only when they are what the change changed: the numbers that differ between the earlier and the current wording
    (the old value is what an existing script usually still holds). Without a version comparison only numbers of three or more digits are used,
    because one- and two-digit numbers (limits, ids, ports) appear in almost every script. Plain words are never used."""
    change = (finding or {}).get('source_change') or {}
    text = ' '.join(str(change.get(k) or '') for k in ('name', 'change'))
    identifiers = []
    for word in re.findall(r'[A-Za-z0-9_./-]{4,}', text):
        if re.search(r'[a-z][A-Z]|_|/|\.', word.strip('./-')) and not word.isdigit():
            identifiers.append(word.strip('./-'))
    status = (finding or {}).get('version_status') or {}
    prior, current = status.get('prior_quote'), status.get('current_quote')
    if prior and current:
        numbers = _numbers(prior) ^ _numbers(current)
    else:
        numbers = {n for n in _numbers(text) if len(n) >= 3}
    seen = set()
    identifiers = [i for i in identifiers if i and not (i.lower() in seen or seen.add(i.lower()))]
    return {'identifiers': identifiers[:12], 'numbers': sorted(numbers)[:8], 'old': []}


def _empty_summary(status, note):
    return {'status': status, 'note': note, 'read_only': True, 'tests_matched': 0, 'scenarios_matched': 0, 'scenarios_with_automation': 0, 'scenarios_linked': 0,
            'scenarios_declared_only': 0, 'scenarios_without_automation': 0, 'assets': {k: 0 for k in KINDS}, 'version_level_assets': 0,
            'findings_needing_review': 0, 'text_checked_assets': 0, 'not_text_checked_assets': 0, 'read_errors': []}


def _matched_rows(finding, rows_by_id, rows_by_key):
    """The test case rows a finding matched: exact rows when the match recorded them, otherwise (older results) every row of the identifier, flagged."""
    coverage = finding.get('test_case_coverage') or {}
    exact = [rows_by_id[m['row_id']] for m in coverage.get('matches') or [] if m.get('row_id') in rows_by_id]
    if exact:
        return exact, True
    rows = [r for ident in coverage.get('test_cases') or [] for r in rows_by_key.get(ident, [])]
    return rows, False


def analyse(findings, test_rows, reader, script_cap=200000, sql_text_reader=None):
    """Attach ``automation_impact`` to every finding and return the analysis-level summary."""
    rows_by_id, rows_by_key = {}, {}
    for row in test_rows or []:
        if row.get('scope_status') == 'SUPERSEDED':
            continue                                            # an older workbook version is never credited with, or checked for, automation
        if row.get('id') is not None:
            rows_by_id[row['id']] = row
        rows_by_key.setdefault(row_key(row), []).append(row)

    def mark(f, status, note, tests=None, exact=True):
        f['automation_impact'] = {'status': status, 'note': note, 'tests': tests or [], 'rows_exact': exact}

    searched = [f for f in findings if f.get('change_status') != 'UNCHANGED']
    for f in findings:
        if f.get('change_status') == 'UNCHANGED':
            mark(f, NOT_APPLICABLE, 'Unchanged from the earlier version; automation was not looked up.')

    matched = {}
    for f in searched:
        rows, _exact = _matched_rows(f, rows_by_id, rows_by_key)
        for r in rows:
            matched[r['id']] = r
    base = _empty_summary(NO_MATCHED_TESTS, 'No existing test matched a change, so there is no automation to review. This does not mean no automation exists.')
    if not matched:
        for f in searched:
            mark(f, NO_MATCHED_TESTS, 'No existing test matched this finding, so no linked automation was looked up.')
        return base

    if reader is None:
        context = None
        errors = ['automation reader unavailable']
    else:
        try:
            context = AutomationContext(reader, list(matched.values()), sql_text_reader=sql_text_reader, cap=script_cap)
            errors = list(context.errors)
        except Exception as exc:
            context, errors = None, ['%s' % type(exc).__name__]
    if context is None:
        base.update(status=NOT_READ, read_errors=errors, tests_matched=len({row_key(r) for r in matched.values()}), scenarios_matched=len(matched),
                    note='Automation records could not be read (%s), so automation impact is unknown, not "none".' % '; '.join(errors))
        for f in searched:
            if (f.get('test_case_coverage') or {}).get('test_cases'):
                mark(f, NOT_READ, 'Automation records could not be read; automation impact is unknown.')
            else:
                mark(f, NO_MATCHED_TESTS, 'No existing test matched this finding.')
        return base

    resolved = {rid: context.resolve(row) for rid, row in matched.items()}
    distinct = {}
    version_distinct = {}
    for res in resolved.values():
        for a in res['scenario']:
            distinct[(a['kind'], a['reference'])] = a
        for a in res['version']:
            version_distinct[(a['kind'], a['reference'])] = a
    with_auto = [rid for rid, res in resolved.items() if res['scenario']]
    linked = [rid for rid, res in resolved.items() if any(a['state'] == 'LINKED' for a in res['scenario'])]
    declared_only = [rid for rid in with_auto if rid not in linked]

    review_findings = 0
    for f in searched:
        rows, exact = _matched_rows(f, rows_by_id, rows_by_key)
        if not rows:
            mark(f, NO_MATCHED_TESTS, 'No existing test matched this finding, so no linked automation was looked up.')
            continue
        terms = _terms(f)
        entries, any_hits, any_linked, any_declared = [], False, False, False
        for row in rows:
            res = resolved[row['id']]
            scenario_assets = []
            for a in res['scenario']:
                hits = _hits(a['text'], terms) if a['text'] else []
                any_hits = any_hits or bool(hits)
                any_linked = any_linked or a['state'] == 'LINKED'
                any_declared = any_declared or a['state'] == 'DECLARED'
                scenario_assets.append({'kind': a['kind'], 'reference': a['reference'], 'label': a['label'], 'state': a['state'], 'basis': a['basis'], 'note': a['note'],
                                        'matched_terms': hits, 'text_checked': a['text_checked'], 'snippet': _snippet(a['text'], hits[0]) if hits else ''})
            version_assets = []
            for a in res['version']:
                hits = _hits(a['text'], terms) if a['text'] else []
                version_assets.append({'kind': a['kind'], 'reference': a['reference'], 'label': a['label'], 'basis': a['basis'], 'matched_terms': hits,
                                       'snippet': _snippet(a['text'], hits[0]) if hits else ''})
            entries.append({'test': row_key(row), 'row_id': row['id'], 'test_case_version_id': row.get('test_case_version_id'), 'scenario': str(row.get('scenario') or '')[:160],
                            'has_automation': bool(scenario_assets), 'assets': scenario_assets, 'version_assets': version_assets})
        if any_hits:
            status, note = REVIEW, 'An automation asset of a matched scenario contains a term this change touches. Review it; it was not run or changed.'
            review_findings += 1
        elif any_linked:
            status, note = FOUND, 'Matched scenarios have their own automation. No changed term was found in the asset text that could be read; assets not text-checked may still need review.'
        elif any_declared:
            status, note = DECLARED_ONLY, 'Matched scenarios say they are automated, but no script, endpoint or SQL file record backs it, so nothing could be text-checked.'
        else:
            status, note = NONE_LINKED, 'The matched scenarios have no automation of their own in the records read. A collection stored for the whole version is not counted; automation may exist elsewhere.'
        if not exact:
            note += ' This result predates scenario-level matching, so every row of each matched test id was considered.'
        if errors:
            note += ' Some automation records could not be read (%s), so this may be incomplete.' % '; '.join(errors)
        mark(f, status, note, entries, exact)

    summary = _empty_summary(NONE_LINKED, '')
    summary.update(
        tests_matched=len({row_key(r) for r in matched.values()}), scenarios_matched=len(matched), scenarios_with_automation=len(with_auto), scenarios_linked=len(linked),
        scenarios_declared_only=len(declared_only), scenarios_without_automation=len(matched) - len(with_auto),
        assets={k: sum(1 for (kind, _r) in distinct if kind == k) for k in KINDS}, version_level_assets=len(version_distinct),
        findings_needing_review=review_findings, text_checked_assets=sum(1 for a in distinct.values() if a['text_checked']),
        not_text_checked_assets=sum(1 for a in distinct.values() if not a['text_checked']), read_errors=errors)
    summary['status'] = REVIEW if review_findings else FOUND if linked else DECLARED_ONLY if declared_only else NONE_LINKED
    summary['note'] = {REVIEW: 'Automation assets that mention a changed term were found; review them. Nothing was executed or modified.',
                       FOUND: 'Matched scenarios have their own automation; no changed term was found in the text that could be read. Nothing was executed or modified.',
                       DECLARED_ONLY: 'Matched scenarios say they are automated but no asset record backs it. Nothing was executed or modified.',
                       NONE_LINKED: 'The matched scenarios have no automation of their own in the records read. Nothing was executed or modified.'}[summary['status']]
    if errors:
        summary['note'] += ' Some automation records could not be read (%s).' % '; '.join(errors)
    return summary
