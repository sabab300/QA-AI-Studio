"""Risk assessment (engine 2.0): one row per changed requirement, rule based, one clear explanation.

The level reflects, in this order of weight:
  1. functional / business impact of what changed (rules, validations, API contracts, data, permissions weigh more than wording or layout)
  2. strength of the dependency evidence: only CONFIRMED dependencies raise risk materially; potential ones add a little; similar wording adds nothing
  3. integration impact (an API or another system is involved)
  4. production consequence (security, payment, tax / duty, audit, credentials)
  5. existing coverage: a verified test lowers the risk, a gap raises it

A change that reaches another domain is not Critical because of that alone: crossing a domain only counts when a CONFIRMED dependency is there.
Risk is a prioritisation aid, not a probability. AI is never used here.
"""
import re

from Core.change_impact.util import clip
from Core.cia import terms as T

LEVELS = ('Low', 'Medium', 'High', 'Critical')
HEAVY = frozenset(('Business Rule', 'Validation', 'API Contract', 'Integration', 'Database Behavior', 'User Permission'))
MEDIUM = frozenset(('Functional Requirement', 'Workflow', 'Data Field', 'Error Handling', 'Non-Functional Requirement'))
PRODUCTION = re.compile(r'\b(token|authenticat\w*|authoris\w*|authoriz\w*|permission|credential\w*|security|encrypt\w*|signature|payment|duty|tax|levy|refund|audit|privacy|password|certificate|licen[cs]e)\b', re.I)
THRESHOLDS = ((7, 'Critical'), (5, 'High'), (3, 'Medium'))


def level_for(score):
    for limit, name in THRESHOLDS:
        if score >= limit:
            return name
    return 'Low'


def max_level(levels):
    return max((l for l in levels if l in LEVELS), key=LEVELS.index, default='Low')


def assess(changes, affected, coverage, regression, ignore_coverage=False):
    """changes: changed requirements. Returns [risk rows] sorted most severe first."""
    cov_by_change = {c['change_id']: c for c in coverage if c.get('kind') == 'Changed requirement'}
    direct_by_change = {}
    for t in regression.get('Directly affected', []):
        for cid in t['change_ids']:
            direct_by_change.setdefault(cid, []).append(t)
    rows = []
    for change in changes:
        cid = change['change_id']
        items = [a for a in affected if cid in a['change_ids']]
        confirmed = [a for a in items if a['classification'] == 'Confirmed Dependency']
        potential = [a for a in items if a['classification'] == 'Potential Dependency']
        investigate = [a for a in items if a['classification'] == 'Needs Investigation']
        drivers = []          # (weight, text): the explanation is built from the heaviest ones
        score = 0.0
        category = change.get('category') or 'Uncategorised'
        text = ' '.join(x for x in (change.get('modified_text'), change.get('original_text')) if x)
        if category in HEAVY:
            score += 2
            drivers.append((2, 'it changes %s' % _noun(category)))
        elif category in MEDIUM:
            score += 1
            drivers.append((1, 'it changes %s' % _noun(category)))
        if change.get('change_type') == 'REMOVED':
            score += 1
            drivers.append((1, 'behaviour was removed, so anything relying on it can break'))
        if 'Integration' in T.tags(text) or any('Integration' in a['tags'] for a in confirmed):
            score += 1
            drivers.append((1, 'an API or integration is involved'))
        if PRODUCTION.search(text):
            score += 1
            drivers.append((1, 'it touches a security, payment, duty or audit rule'))
        if confirmed:
            add = 1 if len(confirmed) == 1 else 2 if len(confirmed) < 5 else 3
            score += add
            drivers.append((add + 0.5, '%d confirmed dependency(ies) in %s' % (len(confirmed), _places(confirmed))))
            if any(a['cross']['domain'] for a in confirmed):
                score += 1
                drivers.append((1.5, 'a confirmed dependency is in another domain'))
        if potential:
            score += 0.5
            drivers.append((0.4, '%d potential dependency(ies) still need QA verification' % len(potential)))
        cov = cov_by_change.get(cid) or {}
        status = 'Skipped' if ignore_coverage else cov.get('status', 'Coverage Not Verified')
        if status == 'Coverage Gap':
            score += 1
            drivers.append((1.2, 'no existing test covers it'))
        elif status == 'Coverage Not Verified':
            score += 0.5
            drivers.append((0.3, 'existing coverage could not be verified'))
        elif status == 'Covered':
            score -= 1
            drivers.append((0.2, 'an existing test covers it, which lowers the risk'))
        score = max(0, score)
        level = level_for(score)
        top = [d for _w, d in sorted(drivers, key=lambda x: -x[0])[:3]]
        reason = ('%s risk: %s.' % (level, '; '.join(top))) if top else '%s risk: no dependency or coverage concern was found, so only the changed requirement itself needs retesting.' % level
        rows.append({'key': 'RISK-' + cid, 'change_id': cid, 'subject': change.get('title') or change.get('requirement') or clip(change.get('description') or text, 100),
                     'reference': change.get('reference') or '', 'level': level, 'score': round(score, 1), 'reason': reason, 'factors': [d for _w, d in sorted(drivers, key=lambda x: -x[0])],
                     'confirmed': len(confirmed), 'potential': len(potential), 'coverage': status if status != 'Skipped' else '',
                     'action': _action(change, level, confirmed, potential, investigate, direct_by_change.get(cid, []), status)})
    rows.sort(key=lambda r: (-LEVELS.index(r['level']), -r['score'], r['change_id']))
    return rows


def _noun(category):
    label = category if category.startswith('API') else category.lower()
    return ('an ' if label[:1].lower() in 'aeiou' else 'a ') + label


def _places(items):
    places = sorted({'%s / %s' % (a['hierarchy']['domain'], a['hierarchy']['module']) for a in items})
    return '; '.join(places[:2]) + (' and %d more' % (len(places) - 2) if len(places) > 2 else '')


def _action(change, level, confirmed, potential, investigate, direct_tests, coverage_status):
    steps = []
    strong = [t for t in direct_tests if t.get('tier', 9) <= 3]
    if strong:
        steps.append('Re-run %d existing test(s): %s' % (len(strong), ', '.join(t['test_case_id'] for t in strong[:5]) + (' and others' if len(strong) > 5 else '')))
    elif direct_tests:
        steps.append('Check whether %s covers it (potential match only)' % ', '.join(t['test_case_id'] for t in direct_tests[:3]))
    elif coverage_status == 'Coverage Gap':
        steps.append('Write a test for the changed requirement before release; no existing test was found for it')
    elif coverage_status == 'Coverage Not Verified':
        steps.append('Check in QA Engineering whether a test covers this requirement; the search could not verify it')
    if change.get('change_type') == 'REMOVED':
        steps.append('Confirm with the business owner that nothing still depends on the removed behaviour')
    if confirmed:
        steps.append('Review the confirmed dependency in %s with its requirement owner and retest that flow' % _places(confirmed))
    if potential:
        steps.append('Verify the %d potential dependency(ies) against their documents before deciding to test them' % len(potential))
    if level in ('Critical', 'High') and not steps:
        steps.append('Retest the changed requirement end to end')
    return '. '.join(steps) + '.' if steps else 'Retest the changed requirement once; no wider impact was found.'
