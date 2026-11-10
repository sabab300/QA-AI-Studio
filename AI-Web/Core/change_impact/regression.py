"""Intelligent regression selection (P0-P3) with an explicit statement of what it cannot prove."""
from Core.change_impact.risk import LEVEL_ORDER, max_level
from Core.change_impact.util import clip

CLASS_RANK = {'Update required': 0, 'Review required': 1, 'New test required': 2, 'Obsolete candidate': 3, 'Reuse without changes': 4}
LINK_RANK = {'CONFIRMED': 0, 'UNCHANGED_REQUIREMENT': 1, 'KNOWLEDGE': 2, 'DEPENDENCY': 2, 'PROPOSED': 3, 'LEXICAL': 4}


def select(test_findings, automation_summary, change_risk, history, policy, unmapped_changes=0, total_changes=0):
    limits = policy.get('limits', {})
    groups = {}
    for f in test_findings:
        groups.setdefault(f['payload']['test_case_id'], []).append(f)
    items = []
    for test_id, fs in groups.items():
        fs.sort(key=lambda f: (CLASS_RANK.get(f['payload']['classification'], 9), LINK_RANK.get(f['payload']['link_status'], 9)))
        top = fs[0]
        p = top['payload']
        classification = p['classification']
        link = min((f['payload']['link_status'] for f in fs), key=lambda l: LINK_RANK.get(l, 9))
        related = sorted({f['change_id'] for f in fs if f['change_id']})
        risk = max_level([change_risk.get(c, {}).get('level', 'Low') for c in related]) if related else 'Low'
        hist = history.get(test_id, [])
        failed_before = bool(hist and hist[0].get('status') in ('Failed', 'Error')) or str(p.get('last_result') or '').lower() in ('fail', 'failed')
        firm = any(f['assurance'] in ('CONFIRMED', 'EVIDENCE_BACKED') for f in fs)
        priority, why = _priority(classification, link, risk, failed_before, firm)
        auto = automation_summary.get(test_id, {})
        kinds = auto.get('kinds', [])
        reasons = [p['reason']]
        if why:
            reasons.append(why)
        if failed_before:
            reasons.append('Its previous recorded result was a failure.')
        items.append({
            'test_case_id': test_id, 'tc_number': p['tc_number'], 'tc_id': p.get('tc_id'), 'title': p['title'], 'priority': priority,
            'risk': risk, 'related_changes': related, 'requirement_refs': sorted({f['payload'].get('requirement_ref') for f in fs if f['payload'].get('requirement_ref')}),
            'classification': classification, 'link_status': link, 'selection_reason': ' '.join(reasons),
            'execution_type': 'Automated' if kinds else 'Manual', 'automation': ', '.join(kinds) if kinds else 'None',
            'previous_result': p.get('last_result') or 'Not Run', 'review_state': p.get('review_state'), 'current_version': p['current_version'],
            'assurance': 'CONFIRMED' if link == 'CONFIRMED' and firm else ('EVIDENCE_BACKED' if firm else 'SUGGESTED'),
            'recommended': priority in ('P0', 'P1', 'P2') and classification != 'Obsolete candidate',
            'finding_ids': [f['finding_id'] for f in fs], 'module': p.get('module'), 'importance': p.get('importance')})
    items.sort(key=lambda i: (i['priority'], -LEVEL_ORDER.index(i['risk']) if i['risk'] in LEVEL_ORDER else 0, i['tc_number'] or '', i['test_case_id']))
    cap = int(limits.get('max_regression_items', 500))
    truncated = len(items) > cap
    items = items[:cap]
    counts = {p: sum(1 for i in items if i['priority'] == p) for p in ('P0', 'P1', 'P2', 'P3')}
    statement = ('This is a recommended subset built from the source relationships and content evidence available. It does not prove complete coverage. '
                 '%d of %d functional change(s) have no existing test with confirmed evidence; the recommended regression scenarios below do not depend on existing tests. '
                 'Dependencies the documents do not state and knowledge outside the analysed scope are not considered.' % (unmapped_changes, total_changes))
    return {'items': items, 'counts': counts, 'truncated': truncated, 'coverage_statement': statement,
            'rules': policy.get('priority_rules', {}), 'recommended_total': sum(1 for i in items if i['recommended'])}


def _priority(classification, link, risk, failed_before, firm):
    if classification == 'Obsolete candidate':
        return 'P3', 'Obsolete candidates are not recommended for execution; a reviewer must decide.'
    if classification == 'Update required' and link == 'CONFIRMED':
        return 'P0', 'It is confirmed-linked to a requirement this change affects and must be updated before it is relied on.'
    if link == 'CONFIRMED' and risk in ('Critical', 'High'):
        return 'P0', 'It is confirmed-linked to a %s-risk change.' % risk.lower()
    if link in ('CONFIRMED', 'UNCHANGED_REQUIREMENT') and failed_before and classification != 'Reuse without changes':
        return 'P0', 'It covers a changed requirement and failed last time.'
    if classification == 'Update required' or (link == 'CONFIRMED' and risk == 'Medium'):
        return 'P1', 'It is linked to a medium-risk change or needs updating.'
    if link == 'CONFIRMED' and classification == 'Review required':
        return 'P1', 'It is confirmed-linked to a changed requirement and needs review.'
    if link == 'KNOWLEDGE' and firm and risk in ('Critical', 'High'):
        return 'P1', 'It belongs to the same Knowledge, shows strong content evidence and the change is %s risk.' % risk.lower()
    if link == 'DEPENDENCY' and firm:
        return 'P2', 'It belongs to a system the source text says is connected to the changed system.'
    if classification in ('Review required', 'Reuse without changes'):
        return ('P3', 'It is only a possible match; confirm it covers the change before relying on it.') if link in ('LEXICAL', 'PROPOSED') else ('P2', '')
    return 'P3', ''


PRIORITY_BY_RISK = {'Critical': ('P0', 'P0'), 'High': ('P0', 'P1'), 'Medium': ('P1', 'P2'), 'Low': ('P2', 'P3')}


def scenario_priority(risk_level, primary):
    pair = PRIORITY_BY_RISK.get(risk_level, ('P2', 'P3'))
    return pair[0] if primary else pair[1]


def plan_scenarios(changes, risks, scenarios, unchanged_groups, dependencies, test_findings, auto_summary, gaps, modules_by_change):
    """Recommended regression scenarios, grouped by what they are.

    Returns {'scenarios': [...], 'smoke': [...], 'automation_candidates': [...], 'categories': {...counts}}.
    Every scenario carries Scenario | Reason | Related Change | Priority | Suggested Execution Type | Evidence, and a ``basis``:
    SOURCE (the expected behaviour is stated by the document) or PENDING_BA (the document does not state it).
    """
    by_id = {c['change_id']: c for c in changes}
    out = []
    for sc in scenarios:
        level = max((risks.get(cid, {}).get('level', 'Low') for cid in sc['change_ids']), key=lambda l: LEVEL_ORDER.index(l) if l in LEVEL_ORDER else 0)
        item = dict(sc)
        item['category'] = 'New regression scenario'
        item['risk'] = level
        item['priority'] = scenario_priority(level, sc.get('primary'))
        item['related_changes'] = sc['change_ids']
        item['related_references'] = sorted({by_id[c]['reference'] for c in sc['change_ids'] if c in by_id and by_id[c].get('reference')})
        out.append(item)
    # Existing, unchanged requirements in the same modules: regression of behaviour that must not break.
    smoke = []
    changed_modules = {m for mods in modules_by_change.values() for m in mods}
    functional = [c for c in changes if not c.get('administrative')]
    for group in unchanged_groups[:40]:
        text = group['quote']
        mentioned = [m for m in sorted(changed_modules) if m.lower() in (group['quote'] + ' ' + group.get('context', '')).lower()]
        scope = group.get('scope_modules') or mentioned
        overlap = sorted(set(scope) & changed_modules)
        if not overlap:
            continue
        related = [c['change_id'] for c in functional if set(modules_by_change.get(c['change_id'], [])) & set(overlap)]
        if not related:
            continue
        level = max((risks.get(cid, {}).get('level', 'Low') for cid in related), key=lambda l: LEVEL_ORDER.index(l) if l in LEVEL_ORDER else 0)
        smoke.append({'title': 'Existing behaviour still works (%s): %s' % (group.get('context') or 'unchanged requirement', group['quote'][:70]), 'category': 'Existing regression (unchanged requirement)',
                      'reason': 'This requirement is unchanged but belongs to the same module (%s) as the changed requirement(s); it must not be broken by the change.' % ', '.join(overlap),
                      'expected': 'Behaviour is identical to the unchanged requirement: "%s"' % group['quote'][:200], 'basis': 'SOURCE', 'primary': False,
                      'change_ids': related, 'related_changes': related, 'related_references': [], 'execution': ['Manual', 'Playwright'], 'modules': overlap,
                      'risk': level, 'priority': 'P2' if level in ('Critical', 'High') else 'P3', 'open_questions': [],
                      'evidence': [group['evidence']] if group.get('evidence') else []})
    for dep in dependencies:
        related = [c['change_id'] for c in functional]
        if not related:
            continue
        level = max((risks.get(cid, {}).get('level', 'Low') for cid in related), key=lambda l: LEVEL_ORDER.index(l) if l in LEVEL_ORDER else 0)
        smoke.append({'title': '%s integration: end-to-end submission still succeeds' % dep['system'], 'category': 'Integration regression',
                      'reason': 'The change request states that %s needs a change (%s). It does not say which requirement drives it, so the integration is regressed as a whole.' % (dep['system'], dep['answer']),
                      'expected': 'Submission through the integration succeeds; the contract with %s is not broken. Specific expectations await the %s change request.' % (dep['system'], dep['system']),
                      'basis': 'PENDING_BA', 'primary': False, 'change_ids': related, 'related_changes': related, 'related_references': [],
                      'execution': ['API', 'Manual'], 'modules': [], 'risk': level, 'priority': 'P1' if level in ('Critical', 'High') else 'P2', 'open_questions': [],
                      'evidence': [dep['evidence']]})
    # Automation candidates: scenarios an automated asset type can run, and existing tests that have no automation.
    candidates = []
    for sc in out + smoke:
        kinds = [k for k in sc['execution'] if k in ('API', 'Playwright', 'SQL')]
        if kinds:
            candidates.append({'title': sc['title'], 'kinds': kinds, 'related_changes': sc['related_changes'], 'priority': sc['priority'],
                               'reason': 'Repeatable check; suggested type: %s.' % ', '.join(kinds)})
    for f in test_findings:
        p = f['payload']
        if f['assurance'] in ('CONFIRMED', 'EVIDENCE_BACKED') and not auto_summary.get(p['test_case_id'], {}).get('has_automation'):
            candidates.append({'title': '%s %s' % (p.get('tc_number') or p['test_case_id'], p['title']), 'kinds': [], 'related_changes': [f['change_id']] if f['change_id'] else [],
                               'priority': 'P2', 'reason': 'Existing impacted test case without an automation asset.'})
    out.sort(key=lambda i: (i['priority'], not i.get('primary'), i['title']))
    smoke.sort(key=lambda i: (i['priority'], i['title']))
    return {'scenarios': out, 'smoke': smoke, 'automation_candidates': candidates[:200],
            'categories': {'new_scenarios': len(out), 'existing_regression': len([s for s in smoke if s['category'].startswith('Existing')]),
                           'integration': len([s for s in smoke if s['category'].startswith('Integration')]), 'automation_candidates': len(candidates),
                           'coverage_gaps': len(gaps)}}
