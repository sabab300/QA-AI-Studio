"""Explainable, configurable risk scoring.

The score is a transparent *prioritisation heuristic*: a weighted average of
factors, each shown with its value, weight and reason. It is not a probability of
failure and never carries an "AI confidence" figure.
"""
from Core.change_impact.util import clip

LEVEL_ORDER = ['Low', 'Medium', 'High', 'Critical']


def level_for(score, policy):
    levels = sorted((policy.get('risk_levels') or {}).items(), key=lambda kv: -kv[1])
    for name, floor in levels:
        if score >= floor:
            return name
    return 'Low'


def max_level(levels):
    ranked = [LEVEL_ORDER.index(l) for l in levels if l in LEVEL_ORDER]
    return LEVEL_ORDER[max(ranked)] if ranked else 'Low'


ADMIN_NOTE = 'Administrative change (reference, date or sign-off metadata): no business rule, validation or interface changed.'


def _mandatory_flip(change):
    return [d for d in change.get('deltas', []) if d['kind'] == 'flip' and d['axis'] == 'deontic']


def assess(change, ctx, policy):
    """Business-based risk. Each factor carries its value, weight and the evidence-supported reason; admin changes are Low."""
    weights = policy.get('risk_weights', {})
    limits = policy.get('limits', {})
    signal = ctx.get('signal') or {}
    if change.get('administrative'):
        row = {'key': 'administrative', 'label': 'Administrative change', 'value': 0.05, 'weight': 1.0, 'applied': True, 'contribution': 0.05, 'note': ADMIN_NOTE}
        return {'score': 0.05, 'level': 'Low', 'factors': [row], 'summary': 'Rated Low: ' + ADMIN_NOTE, 'reasons': [{'factor': 'administrative', 'text': ADMIN_NOTE, 'quote': ''}],
                'mitigation': ['Confirm the new value is correct; no functional regression is needed for this difference alone.'],
                'disclaimer': 'A prioritisation score from policy-weighted factors. It is not a probability of failure.'}
    criticality = int((policy.get('category_criticality') or {}).get(change['category'], 1))
    flips = _mandatory_flip(change)
    conditions = [d for d in change.get('deltas', []) if d.get('role') == 'condition']
    base = criticality / 3.0
    crit_notes = ['%s changes are rated %d of 3 in the policy.' % (change['category'], criticality)]
    value = base
    if flips:
        value = max(value, 0.9)
        crit_notes.append('A mandatory / optional duty changed (%s).' % '; '.join(d['summary'].rstrip('.') for d in flips[:2]))
    if signal.get('blocking'):
        value = max(value, 0.85)
        crit_notes.append('The statement governs validation or submission, so a wrong implementation can block users.')
    regulatory = signal.get('regulatory') or []
    financial = signal.get('financial') or []
    reg_value = 0.0
    reg_notes = []
    if regulatory:
        direct = any(r['via'] == 'change' for r in regulatory)
        reg_value = 0.7 if direct else 0.5
        reg_notes.append('Regulatory wording (%s) %s: "%s".' % (', '.join(regulatory[0]['terms']), 'in the changed text' if direct else 'in a statement about the same subject', clip(regulatory[0]['quote'], 160)))
    if financial:
        direct = any(r['via'] == 'change' for r in financial)
        reg_value = max(reg_value, 0.7 if direct else 0.4)
        reg_notes.append('Financial wording (%s) %s.' % (', '.join(financial[0]['terms']), 'in the changed text' if direct else 'in a statement about the same subject'))
    user_value = 0.9 if signal.get('blocking') else (0.4 if signal.get('ui') else 0.1)
    integration = 1.0 if signal.get('integration') else (0.5 if ctx.get('dependencies', 0) or ctx.get('stated_dependencies', 0) else 0.0)
    n_modules = len(signal.get('modules') or [])
    breadth = min(1.0, 0.25 * n_modules + 0.1 * len(signal.get('areas') or []))
    complexity = min(1.0, 0.25 * (signal.get('deltas', 0) or 1) + 0.2 * signal.get('conditions', 0) + 0.15 * signal.get('inversions', 0)
                     + (0.1 if change['change_type'] in ('ADDED', 'REMOVED') else 0.0))
    firm = ctx.get('firm_tests', ctx.get('confirmed_tests', 0))
    coverage = {0: 1.0, 1: 0.5, 2: 0.25}.get(firm, 0.0)
    uncertainty = min(1.0, (1.0 if signal.get('conflicts') else 0.0) + 0.3 * max(0, signal.get('open_questions', 0) - signal.get('conflicts', 0)))
    runs, failed = ctx.get('history_runs', 0), ctx.get('history_failed', 0)
    min_runs = int(limits.get('minimum_history_runs', 3))
    history_ok = runs >= min_runs

    factors = [
        ('business_criticality', 'Business criticality', value, True, ' '.join(crit_notes)),
        ('regulatory_financial', 'Regulatory and financial exposure', reg_value, True, ' '.join(reg_notes) or 'No regulatory or financial wording relates to this change.'),
        ('user_impact', 'Customer / user impact', user_value, True,
         'Users can be blocked from submitting.' if signal.get('blocking') else ('The change is visible to users on screen.' if signal.get('ui') else 'No user-facing effect is stated.')),
        ('integration_impact', 'Integration impact', integration, True,
         'The change alters an API detail.' if signal.get('integration') else ('The change request states a dependency on another system.' if integration else 'No integration is stated for this change.')),
        ('module_breadth', 'Affected modules', breadth, True, '%d module(s) and %d interface(s) named by the document.' % (n_modules, len(signal.get('areas') or []))),
        ('validation_complexity', 'Validation complexity', complexity, True,
         '%d rule difference(s), %d condition(s) changed, %d reversed meaning(s).' % (signal.get('deltas', 0), signal.get('conditions', 0), signal.get('inversions', 0))),
        ('coverage_gap', 'Existing test coverage', coverage, True,
         '%d existing test(s) with confirmed evidence. None means the change may go untested.' % firm),
        ('uncertainty', 'Uncertainty / contradiction', uncertainty, True,
         '%d open question(s), %d of them conflicting statements to be clarified by the business analyst.' % (signal.get('open_questions', 0), signal.get('conflicts', 0))),
        ('historical_failures', 'Historical failures', (failed / float(runs)) if history_ok else 0.0, history_ok,
         ('%d of %d earlier finished runs failed.' % (failed, runs)) if history_ok else
         'Only %d earlier finished run(s) are recorded (minimum %d), so history is not used.' % (runs, min_runs)),
    ]
    applicable = sum(float(weights.get(k, 1)) for k, _, _, ok, _ in factors if ok)
    rows, total = [], 0.0
    for key, label, val, ok, note in factors:
        weight = float(weights.get(key, 1))
        contribution = (weight * val / applicable) if ok and applicable else 0.0
        total += contribution
        rows.append({'key': key, 'label': label, 'value': round(val, 3), 'weight': weight, 'applied': ok, 'contribution': round(contribution, 3), 'note': note})
    score = round(total, 3)
    level = level_for(score, policy)
    top = sorted([r for r in rows if r['applied'] and r['value'] >= 0.5], key=lambda r: -r['contribution'])[:4]
    reasons = [{'factor': r['key'], 'text': r['note'], 'quote': ''} for r in top]
    if regulatory and reasons:
        for r in reasons:
            if r['factor'] == 'regulatory_financial':
                r['quote'] = regulatory[0]['quote']
    mitigation = []
    if signal.get('conflicts'):
        mitigation.append('Resolve the conflicting statements with the business analyst before finalising test design.')
    if signal.get('blocking'):
        mitigation.append('Test the blocked and the allowed submission path for every affected module.')
    if firm == 0:
        mitigation.append('Design new regression scenarios; no existing test shows evidence for this change.')
    if signal.get('integration') or integration:
        mitigation.append('Verify the integration contract with the provider before release.')
    if conditions:
        mitigation.append('Exercise every changed condition both ways (true and false).')
    return {'score': score, 'level': level, 'factors': rows, 'reasons': reasons, 'mitigation': mitigation,
            'summary': 'Rated %s because of: %s.' % (level, '; '.join(clip(r['label'] + ' (' + r['note'] + ')', 150) for r in top) or 'its limited business exposure'),
            'disclaimer': 'A prioritisation score from policy-weighted factors. It is not a probability of failure.'}
