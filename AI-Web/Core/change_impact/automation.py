"""Automation impact (Playwright, API, SQL). Read-only: nothing is executed, regenerated or overwritten."""
import re
from pathlib import Path

from Core.automation_links import AutomationContext
from Core.change_impact.mapping import change_terms
from Core.change_impact.util import clip, stable_id

KINDS = ('Playwright', 'API', 'SQL')


def _snippet(text, term, width=70):
    index = text.lower().find(term.lower())
    if index < 0:
        return ''
    return clip(text[max(0, index - width): index + len(term) + width].replace('\n', ' '), 2 * width + len(term) + 3)


def _hits(text, terms):
    """Distinctive terms only: identifiers/paths/numbers/quoted labels and old-wording keywords."""
    low = text.lower()
    hits = []
    for t in terms['identifiers']:
        if t.lower() in low:
            hits.append(t)
    for n in terms['numbers']:
        if re.search(r'(?<![\w.])' + re.escape(n) + r'(?![\w])', low):
            hits.append(n)
    for k in terms['old']:
        if len(k) >= 5 and k in low and k not in (h.lower() for h in hits):
            hits.append(k)
    seen, out = set(), []
    for h in hits:
        if h.lower() not in seen:
            seen.add(h.lower())
            out.append(h)
    return out[:8]


def _sql_text_reader(central, cap):
    """Reads a managed SQL script (read-only). Paths outside the managed Central Repository are not opened."""
    def read(path):
        if central is None:
            return ''
        resolved = central.resolve_managed(path, '')
        resolved.relative_to(Path(central.root).resolve())
        if resolved.is_file() and resolved.stat().st_size <= cap * 4:
            return resolved.read_text(encoding='utf-8', errors='replace')[:cap]
        return ''
    return read


def analyse(test_findings, changes, reader, central, policy, sql_reader=None):
    """Return (automation_findings, per_test_summary).

    per_test_summary[test_id] = {'assets': [...], 'has_automation': bool, 'impacted': bool}
    """
    limits = policy.get('limits', {})
    cap = int(limits.get('max_script_characters', 200000))
    by_id = {c['change_id']: c for c in changes}
    tests = {}
    for f in test_findings:
        p = f['payload']
        tests.setdefault(p['test_case_id'], []).append(f)
    test_ids = list(tests)
    inline = {t['id']: t for t in reader.test_cases(test_ids=test_ids)} if test_ids else {}
    rows = []
    for test_id, fs in tests.items():
        first = fs[0]['payload']
        base = dict(inline.get(test_id) or {})
        base.setdefault('id', test_id)
        base['id'] = test_id
        base.setdefault('tc_id', first.get('tc_id'))
        base.setdefault('tc_number', first.get('tc_number'))
        if not base.get('tc_id'):
            base['tc_id'] = first.get('tc_id')
        if not base.get('test_case_version_id'):
            base['test_case_version_id'] = first.get('test_case_version_id')
        rows.append(base)
    # Scenario-level links (Core/automation_links.py): a version-level API collection or another source's script never makes a scenario "automated".
    context = AutomationContext(reader, rows, sql_text_reader=_sql_text_reader(central, cap), cap=cap)
    resolved = {r['id']: context.resolve(r) for r in rows}

    findings, summary = [], {}
    for test_id, fs in tests.items():
        first = fs[0]['payload']
        assets = list(resolved[test_id]['scenario'])
        version_assets = list(resolved[test_id]['version'])
        has_automation = any(a['kind'] in KINDS for a in assets)
        impacted = False
        for fnd in fs:
            change = by_id.get(fnd['change_id'])
            if not change:
                continue
            terms = change_terms(change)
            for asset in assets:
                hits = _hits(asset['text'], terms) if asset['text'] else []
                needs_review = fnd['payload']['classification'] in ('Update required', 'Review required', 'Obsolete candidate')
                if not hits and not needs_review:
                    continue
                impacted = impacted or bool(hits) or needs_review
                if hits:
                    state, assurance = 'Impacted: review or maintenance needed', 'EVIDENCE_BACKED'
                    reason = 'The asset text contains %s, which this change touches.' % ', '.join('"%s"' % h for h in hits[:4])
                    evidence = [{'ref': stable_id('EV', asset['reference'], hits[0], length=8), 'role': 'automation-asset', 'document': asset['label'],
                                 'quote': _snippet(asset['text'], hits[0]), 'verified': True}]
                else:
                    state, assurance = 'Review (linked test needs review)', 'SUGGESTED'
                    reason = ('The linked test is "%s", so its %s asset may need maintenance. No changed term was found in the asset text%s.'
                              % (fnd['payload']['classification'], asset['kind'], ' (the script file could not be read)' if asset.get('unreadable') else ''))
                    evidence = []
                findings.append({
                    'finding_id': stable_id('AUT', fnd['finding_id'], asset['reference']), 'kind': 'AUTOMATION', 'change_id': fnd['change_id'],
                    'title': '%s - %s' % (asset['kind'], asset['label']), 'impact_class': 'Regression', 'severity': '', 'assurance': assurance,
                    'verification_status': 'Text match; execute nothing, review manually' if hits else 'Inferred from the linked test; needs verification',
                    'payload': {'test_case_id': test_id, 'tc_number': first['tc_number'], 'asset_kind': asset['kind'], 'asset_reference': asset['reference'],
                                'asset_label': asset['label'], 'asset_level': asset['level'], 'link_state': asset['state'], 'link_basis': asset['basis'],
                                'impact': state, 'matched_terms': hits, 'reason': reason,
                                'recommended_action': 'Review and, if needed, update this %s asset. It was not run or changed.' % asset['kind']},
                    'evidence': evidence})
        # Version-level context (an API collection or script stored for the whole test case version): reported only when its text contains a changed
        # term, as a suggestion. It is not this scenario's own automation and does not make the scenario "automated".
        for fnd in fs:
            change = by_id.get(fnd['change_id'])
            if not change:
                continue
            terms = change_terms(change)
            for asset in version_assets:
                hits = _hits(asset['text'], terms) if asset['text'] else []
                if not hits:
                    continue
                findings.append({
                    'finding_id': stable_id('AUT', fnd['finding_id'], asset['reference']), 'kind': 'AUTOMATION', 'change_id': fnd['change_id'],
                    'title': '%s - %s' % (asset['kind'], asset['label']), 'impact_class': 'Regression', 'severity': '', 'assurance': 'SUGGESTED',
                    'verification_status': 'Version-level asset mentions a changed term; not linked to this scenario',
                    'payload': {'test_case_id': test_id, 'tc_number': first['tc_number'], 'asset_kind': asset['kind'], 'asset_reference': asset['reference'],
                                'asset_label': asset['label'], 'asset_level': asset['level'], 'link_state': asset['state'], 'link_basis': asset['basis'],
                                'impact': 'Possibly related: version-level asset', 'matched_terms': hits,
                                'reason': 'The %s stored for this test case version contains %s, which this change touches. It is not linked to this scenario.' % (asset['kind'], ', '.join('"%s"' % h for h in hits[:4])),
                                'recommended_action': 'Check whether this scenario relies on that asset. It was not run or changed.'},
                    'evidence': [{'ref': stable_id('EV', asset['reference'], hits[0], length=8), 'role': 'automation-asset', 'document': asset['label'],
                                  'quote': _snippet(asset['text'], hits[0]), 'verified': True}]})
        if not has_automation:
            needs = any(f['payload']['classification'] in ('Update required', 'Review required', 'New test required') for f in fs)
            if needs:
                findings.append({
                    'finding_id': stable_id('AUT', 'missing', test_id), 'kind': 'AUTOMATION', 'change_id': fs[0]['change_id'],
                    'title': 'No automation for %s' % (first['tc_number'] or test_id), 'impact_class': 'Regression', 'severity': '', 'assurance': 'EVIDENCE_BACKED',
                    'verification_status': 'No linked asset found in the records read; automation may exist elsewhere',
                    'payload': {'test_case_id': test_id, 'tc_number': first['tc_number'], 'asset_kind': 'None', 'asset_reference': '', 'asset_label': 'No automation',
                                'impact': 'Missing automation', 'matched_terms': [], 'reason': 'This impacted test has no Playwright, API or SQL asset linked to it.',
                                'recommended_action': 'Run it manually for this regression, and consider automating it.'},
                    'evidence': []})
        summary[test_id] = {'assets': [{'kind': a['kind'], 'reference': a['reference'], 'label': a['label'], 'level': a['level'], 'state': a['state'], 'basis': a['basis']} for a in assets],
                            'version_assets': [{'kind': a['kind'], 'reference': a['reference'], 'label': a['label']} for a in version_assets],
                            'has_automation': has_automation, 'impacted': impacted,
                            'declared_only': bool(assets) and all(a['state'] == 'DECLARED' for a in assets),
                            'kinds': sorted({a['kind'] for a in assets if a['kind'] in KINDS})}
    return findings, summary
