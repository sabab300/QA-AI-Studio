"""Management decision and effort layer shared by Change Impact and AI Assistant Impact Analysis.

Pure functions over a stored result. Nothing is written to any database, nothing is created, and nothing is invented:

* the decision-readiness state follows fixed, visible rules and always lists the reasons for it;
* the effort comparison separates what was MEASURED (processing time, counts) from what is ESTIMATED or ILLUSTRATIVE (the manual-effort
  baseline and the human-review allowance). A monetary figure is produced only when PSW has configured an hourly cost, and savings are
  never described as prevented defects.

Both modules call the same functions, so the dashboard, the Excel workbook, the PDF and the history all show the same management
summary for one result. The baseline lives in Config/impact_effort_baseline.json and can be changed without a code change.
"""
import json
from datetime import datetime
from pathlib import Path

READY = 'READY FOR QA PLANNING'
CLARIFY = 'NEEDS REQUIREMENT CLARIFICATION'
VERIFY = 'NEEDS TECHNICAL VERIFICATION'
INSUFFICIENT = 'INSUFFICIENT EVIDENCE'
STATES = (READY, CLARIFY, VERIFY, INSUFFICIENT)

MEASURED, ESTIMATED, ILLUSTRATIVE = 'MEASURED', 'ESTIMATED', 'ILLUSTRATIVE'

DEFAULT_BASELINE = {
    'version': 1,
    'status': 'ILLUSTRATIVE',
    'note': 'Placeholder manual-effort assumptions. Replace them with PSW\'s agreed figures and set status to AGREED; until then every saving is labelled illustrative.',
    'manual_minutes': {'requirements_assessed': 6, 'changes_compared': 12, 'systems_identified': 10, 'regression_items': 8, 'test_search': 30, 'report': 45},
    'review_minutes_per_finding': 2,
    'hourly_cost': None,
    'currency': None,
}
UNIT_LABELS = (('requirements_assessed', 'Requirement statements assessed'), ('changes_compared', 'Changes compared'), ('systems_identified', 'Systems / modules identified'),
               ('regression_items', 'Regression items recommended'), ('test_search', 'Existing-test search'), ('report', 'Report preparation'))
_CONFIG_PATH = Path(__file__).resolve().parents[1] / 'Config' / 'impact_effort_baseline.json'


def load_baseline(path=None):
    """The agreed baseline when the file is present and valid; otherwise the illustrative default (never a failure)."""
    try:
        data = json.loads(Path(path or _CONFIG_PATH).read_text(encoding='utf-8'))
        merged = dict(DEFAULT_BASELINE)
        merged.update({k: v for k, v in data.items() if k in DEFAULT_BASELINE})
        minutes = dict(DEFAULT_BASELINE['manual_minutes'])
        minutes.update({k: float(v) for k, v in (data.get('manual_minutes') or {}).items() if k in minutes and isinstance(v, (int, float)) and v >= 0})
        merged['manual_minutes'] = minutes
        if str(merged.get('status')).upper() not in ('AGREED', 'ILLUSTRATIVE'):
            merged['status'] = 'ILLUSTRATIVE'
        merged['status'] = str(merged['status']).upper()
        return merged
    except Exception:
        return dict(DEFAULT_BASELINE)


def _int(value):
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


# ---------------------------------------------------------------------------------------------------- facts
def facts_from_impact_analysis(result):
    """Normalised facts from one stored AI Assistant Impact Analysis result."""
    result = result or {}
    summary, coverage = result.get('summary') or {}, result.get('coverage') or {}
    findings = result.get('findings') or []
    sreq = coverage.get('source_requirements') or {}
    processing = coverage.get('target_processing') or {}
    tests = coverage.get('test_cases') or {}
    warnings = coverage.get('warning_details') or []
    codes = {w.get('code') for w in warnings if isinstance(w, dict)}
    detected = _int(sreq.get('detected')) or len(findings)
    assessed = _int(sreq.get('assessed'))
    if not sreq:
        assessed = len(findings)
    not_assessed = max(0, detected - assessed) + len(coverage.get('changes_not_assessed') or []) + len(coverage.get('target_statements_not_assessed') or [])
    failed = _int(processing.get('failed'))
    incomplete = bool(processing) and (processing.get('status') not in (None, 'COMPLETED') or failed > 0)
    failure_codes = sorted(c for c in codes if c and any(t in c for t in ('FAIL', 'UNAVAILABLE', 'PARTIAL', 'NOT_ASSESSED', 'NOT_EXAMINED')))
    doc_changes = (summary.get('document_changes') or {})
    confirmed, potential = _int(summary.get('confirmed')), _int(summary.get('potential'))
    insufficient = _int(summary.get('insufficient_evidence'))
    matched = len({x for f in findings for x in ((f.get('test_case_coverage') or {}).get('test_cases') or [])})      # distinct existing tests, not findings
    automation = coverage.get('automation') or {}
    assets = automation.get('assets') or {}
    asset_total = sum(_int(v) for v in assets.values()) if isinstance(assets, dict) else 0
    executed = tests.get('status') in ('MATCHES_FOUND', 'NO_MATCHING_TESTS')
    impacted = [f for f in findings if f.get('classification') in ('CONFIRMED IMPACT', 'POTENTIAL IMPACT')]
    gaps = sum(1 for f in impacted if (f.get('test_case_coverage') or {}).get('classification') == 'NO MATCHING COVERAGE FOUND') if executed else 0
    regression = len({t for f in impacted for t in ((f.get('test_case_coverage') or {}).get('test_cases') or [])})
    # Results stored by an earlier engine carry no engine version or summary, so their completeness cannot be established.
    legacy = not (result.get('engine_version') and result.get('summary'))
    diff = result.get('version_difference') or {}
    removed = _int((diff.get('counts') or {}).get('removed'))
    counts = diff.get('counts') or {}
    other_differences = _int(counts.get('other_changed')) + _int(counts.get('other_added')) + _int(counts.get('other_removed'))
    removed_tests = len({t for r in (result.get('removed_requirements') or []) for t in ((r.get('test_case_coverage') or {}).get('test_cases') or [])})
    return {
        'module': 'IMPACT_ANALYSIS', 'legacy_result': legacy,
        'changes_total': _int(doc_changes.get('confirmed_changes')) + _int(doc_changes.get('new_requirements')) + removed,
        'functional_changes': _int(doc_changes.get('confirmed_changes')) + _int(doc_changes.get('new_requirements')) + removed,
        'removed_requirements': removed, 'removed_tests': removed_tests, 'other_differences': other_differences,
        'unassessed_differences': _int(diff.get('unassessed_count')), 'comparison_incomplete': bool(diff) and diff.get('complete') is False,
        'requirements_detected': detected, 'requirements_assessed': assessed, 'not_assessed': not_assessed,
        'confirmed_systems': confirmed, 'potential_systems': potential, 'insufficient': insufficient,
        'already_covered': _int(summary.get('already_covered')), 'no_documented_impact': _int(summary.get('no_documented_impact')),
        'open_questions': 0, 'contradictions': 0, 'pending_business_confirmation': 0,
        'incomplete_processing': incomplete, 'failure_codes': failure_codes,
        'evidence_limited': bool(findings) and insufficient >= max(1, len(findings)),
        'test_status': tests.get('status') or ('SEARCH_EXECUTED' if tests.get('complete') else None), 'test_total': tests.get('total'), 'tests_matched_findings': matched,
        'regression_items': regression, 'coverage_gaps': gaps, 'automation_assets': asset_total, 'high_risk': 0, 'findings_total': len(findings),
        'raw_differences': None, 'ai_seconds': result.get('execution_time') if isinstance(result.get('execution_time'), (int, float)) else None,
        'versions_compared': bool((result.get('analysis_context') or {}).get('prior_version_compared')),
    }


def _seconds_between(start, end):
    try:
        a = datetime.fromisoformat(str(start)[:19])
        b = datetime.fromisoformat(str(end)[:19])
        seconds = (b - a).total_seconds()
        return seconds if seconds >= 0 else None
    except Exception:
        return None


def facts_from_change_impact(snapshot, header=None):
    """Normalised facts from one frozen Change Impact snapshot."""
    snapshot = snapshot or {}
    dash = (snapshot.get('dashboard') or {}).get('totals') or {}
    insight = snapshot.get('insight') or {}
    summary = insight.get('summary') or {}
    questions = insight.get('questions') or []
    findings = snapshot.get('findings') or []
    changes = snapshot.get('changes') or []
    functional = [c for c in changes if c.get('functional', True)]
    risk = snapshot.get('risk') or {}
    high = sum(1 for c in functional if (risk.get(c.get('change_id')) or {}).get('level') in ('Critical', 'High'))
    removed_ids = {c.get('change_id') for c in functional if c.get('change_type') == 'REMOVED'}
    removed_tests = len({(f.get('payload') or {}).get('test_case_id') for f in findings if f.get('kind') == 'TEST' and f.get('change_id') in removed_ids})
    scenarios = (snapshot.get('regression') or {}).get('scenarios') or []
    pending_ba = sum(1 for s in scenarios if isinstance(s, dict) and s.get('basis') == 'PENDING_BA')
    statements = _int(insight.get('statements_read')) if not isinstance(insight.get('statements_read'), (list, dict)) else len(insight.get('statements_read'))
    warnings = [str(w) for w in (snapshot.get('warnings') or [])]
    seconds = _seconds_between((header or {}).get('created_at'), (header or {}).get('completed_at')) if header else None
    return {
        'module': 'CHANGE_IMPACT',
        'changes_total': len(changes), 'functional_changes': len(functional), 'removed_requirements': len(removed_ids), 'removed_tests': removed_tests,
        'unassessed_differences': 0, 'comparison_incomplete': False,
        'requirements_detected': _int(dash.get('requirements')), 'requirements_assessed': _int(dash.get('requirements')), 'not_assessed': 0,
        'confirmed_systems': _int(dash.get('impacted_systems')), 'potential_systems': _int(dash.get('possible_systems_unverified')), 'insufficient': 0,
        'already_covered': 0, 'no_documented_impact': 0,
        'open_questions': len(questions), 'contradictions': _int(summary.get('conflicts')), 'pending_business_confirmation': pending_ba,
        'incomplete_processing': False, 'failure_codes': [], 'warnings': warnings,
        'evidence_limited': False,
        'test_status': 'MATCHES_FOUND' if _int(dash.get('existing_impacted_tests')) else None, 'test_total': _int(dash.get('existing_impacted_tests')),
        'tests_matched_findings': _int(dash.get('existing_impacted_tests')),
        'regression_items': _int(dash.get('recommended_regression_tests')) + _int(dash.get('recommended_regression_scenarios')), 'coverage_gaps': _int(dash.get('coverage_gaps')),
        'automation_assets': _int(dash.get('automation_assets')), 'high_risk': high or _int(dash.get('high_risk_impacts')), 'findings_total': len(findings),
        'raw_differences': summary.get('raw_differences'), 'ai_seconds': seconds, 'statements_read': statements,
        'versions_compared': (snapshot.get('analysis') or {}).get('mode') == 'VERSION_COMPARE',
    }


# ---------------------------------------------------------------------------------------------------- decision
def decision_readiness(facts):
    """One decision state plus every reason that applies. Precedence: insufficient evidence, requirement clarification, technical verification, ready."""
    reasons = {INSUFFICIENT: [], CLARIFY: [], VERIFY: []}
    if facts['module'] == 'IMPACT_ANALYSIS':
        if facts.get('legacy_result'):
            reasons[INSUFFICIENT].append('This analysis was stored by an earlier engine version that did not record completeness, evidence or classification totals, so no decision can be based on it. Re-run it to get a decision-grade assessment.')
        if facts['requirements_detected'] == 0 and facts['findings_total'] == 0:
            reasons[INSUFFICIENT].append('No requirement statements were found in the source document, so nothing could be assessed.')
        if facts['requirements_assessed'] == 0 and facts['requirements_detected'] > 0:
            reasons[INSUFFICIENT].append('None of the %d requirement statement(s) was assessed against the target.' % facts['requirements_detected'])
        if facts['incomplete_processing']:
            reasons[INSUFFICIENT].append('Target processing did not complete (retrieval or model failure, or partial processing); the outcome is not a full assessment.')
        if facts['evidence_limited'] and facts['requirements_assessed']:
            reasons[INSUFFICIENT].append('Every assessed requirement ended as Insufficient Evidence: no independent target evidence was found, so no software impact can be judged.')
        if facts['not_assessed']:
            reasons[VERIFY].append('%d requirement statement(s) or difference(s) were not assessed and are listed in the report as unassessed.' % facts['not_assessed'])
    else:
        if facts['changes_total'] == 0 and not facts.get('statements_read'):
            reasons[INSUFFICIENT].append('No change or requirement statements were read from the selected document(s).')
    if facts.get('unassessed_differences'):
        reasons[VERIFY].append('%d functional difference(s) between the versions were found by comparing the documents but were not assessed against the target (listed under Version Comparison).' % facts['unassessed_differences'])
    if facts.get('comparison_incomplete'):
        reasons[VERIFY].append('The comparison of the two versions is incomplete, so some differences, especially removed requirements, may be missing.')
    if facts.get('removed_requirements'):
        if facts.get('removed_tests'):
            reasons[VERIFY].append('%d requirement(s) were removed; the %d existing test(s) that matched them must be reviewed for retirement or update (nothing is retired automatically).' % (facts['removed_requirements'], facts['removed_tests']))
        else:
            reasons[VERIFY].append('%d requirement(s) were removed; no existing test matched them, so confirm that no test or automation still depends on them.' % facts['removed_requirements'])
    if facts['open_questions']:
        reasons[CLARIFY].append('%d open question(s) need business clarification%s.' % (facts['open_questions'], (', including %d possible contradiction(s)' % facts['contradictions']) if facts['contradictions'] else ''))
    if facts['pending_business_confirmation']:
        reasons[CLARIFY].append('%d recommended scenario(s) have an expected result that the document does not state (awaiting business confirmation).' % facts['pending_business_confirmation'])
    if facts['potential_systems']:
        reasons[VERIFY].append('%d potential (unverified) system, module or interface impact(s) need technical confirmation.' % facts['potential_systems'])
    if facts['test_status'] in ('REPOSITORY_UNAVAILABLE', 'SEARCH_FAILED', 'SEARCH_NOT_EXECUTED', 'SCOPE_EMPTY_REPOSITORY_COUNT_UNKNOWN'):
        reasons[VERIFY].append('The existing-test search could not be completed (%s); test coverage is unknown, not zero.' % facts['test_status'])
    for state in (INSUFFICIENT, CLARIFY, VERIFY):
        if reasons[state]:
            state_value, why = state, reasons[state]
            break
    else:
        state_value = READY
        why = ['Changes are identified, system impacts are backed by evidence, and nothing needs clarification or technical confirmation.'] if (facts['changes_total'] or facts['requirements_assessed']) else ['No documented impact was found in the examined scope.']
    also = [s for s in (INSUFFICIENT, CLARIFY, VERIFY) if s != state_value and reasons[s]]
    return {'state': state_value, 'reasons': why, 'also_needed': also, 'also_reasons': [r for s in also for r in reasons[s]]}


def _next_steps(facts, decision):
    steps = []
    state = decision['state']
    if INSUFFICIENT in [state] + decision['also_needed']:
        steps.append('Add independent target evidence (target SRS, module or integration document) to the Knowledge Hub, or fix the failed retrieval or provider step, then re-run the analysis.')
    if CLARIFY in [state] + decision['also_needed']:
        steps.append('Ask the business analyst to answer the open questions and state the expected results marked "awaiting business confirmation".')
    if VERIFY in [state] + decision['also_needed']:
        steps.append('Ask the technical owner to confirm or reject each potential system impact before relying on it.')
    if facts['functional_changes'] or facts['requirements_assessed']:
        steps.append('Plan QA: rerun the identified existing tests and review the recommended scenarios; nothing is created without approval.')
    if facts.get('removed_requirements'):
        steps.append('Review the tests and automation that covered the %d removed requirement(s); decide whether to retire or update them.' % facts['removed_requirements'])
    if facts['coverage_gaps']:
        steps.append('Decide whether to create tests for the %d coverage gap(s).' % facts['coverage_gaps'])
    return steps


def management_questions(facts, decision):
    """The standard questions management asks, answered only from counted facts."""
    changed = ('%d change(s), %d functional' % (facts['changes_total'], facts['functional_changes'])) if facts['changes_total'] else 'No confirmed document change was identified.'
    if facts['module'] == 'IMPACT_ANALYSIS':
        changed += '; %d requirement statement(s) examined, %d assessed.' % (facts['requirements_detected'], facts['requirements_assessed'])
    if facts.get('removed_requirements'):
        changed += ' %d removed requirement(s) are included in the count.' % facts['removed_requirements']
    if facts['module'] == 'CHANGE_IMPACT':
        systems = '%d system(s) owned or named by the changed document (document-stated, not independently verified against another target) and %d potential (unverified) system impact(s).' % (facts['confirmed_systems'], facts['potential_systems'])
    else:
        systems = '%d confirmed (shown by independent target evidence) and %d potential (unverified) system impact(s).' % (facts['confirmed_systems'], facts['potential_systems'])
    if facts['module'] == 'IMPACT_ANALYSIS' and facts['insufficient']:
        systems += ' %d requirement(s) have insufficient evidence to judge impact.' % facts['insufficient']
    risk = ('%d high or critical functional change(s).' % facts['high_risk']) if facts['module'] == 'CHANGE_IMPACT' else 'Risk levels are assessed per change in Change Impact; Impact Analysis reports evidence strength, not a risk score.'
    testing = '%s existing impacted test(s) with evidence; %d recommended regression item(s); %d coverage gap(s); %d automation asset(s) identified.' % (
        facts['tests_matched_findings'] if facts['test_status'] else 'Existing-test impact not established:', facts['regression_items'], facts['coverage_gaps'], facts['automation_assets'])
    if facts.get('removed_tests'):
        testing += ' %d existing test(s) tied to removed requirements need review.' % facts['removed_tests']
    if not facts['test_status']:
        testing = 'Existing-test impact is not established (search not executed or no data); %d recommended regression item(s); %d coverage gap(s).' % (facts['regression_items'], facts['coverage_gaps'])
    unknown = list(decision['reasons']) if decision['state'] != READY else []
    unknown += decision['also_reasons']
    if facts.get('other_differences'):
        unknown.append('%d other text difference(s) (statements without requirement wording) are listed in the report but were not judged; review them if they describe behaviour.' % facts['other_differences'])
    approve = []
    if facts['regression_items']:
        approve.append('Approve the regression selection for execution (suggestions only until approved).')
    if facts['potential_systems']:
        approve.append('Nominate a technical owner to verify the potential system impacts.')
    if facts['open_questions'] or facts['pending_business_confirmation']:
        approve.append('Assign a business analyst to resolve the open questions.')
    return [('What changed?', changed), ('What systems may be affected?', systems), ('What is the business risk?', risk), ('What testing is required?', testing),
            ('What is unknown?', ' '.join(unknown) if unknown else 'Nothing material is outstanding in the examined scope.'),
            ('What should management approve?', ' '.join(approve) if approve else 'No approval is requested by this analysis.'),
            ('What should happen next?', ' '.join(_next_steps(facts, decision)) or 'No action is required.')]


# ---------------------------------------------------------------------------------------------------- effort
def effort_comparison(facts, baseline=None, review_findings=None):
    """Manual versus AI-assisted effort. Returns hours, never an invented currency amount."""
    baseline = baseline or load_baseline()
    minutes = baseline['manual_minutes']
    units = {
        'requirements_assessed': facts['requirements_assessed'] if facts['module'] == 'IMPACT_ANALYSIS' else 0,
        'changes_compared': facts['changes_total'] if facts['module'] == 'CHANGE_IMPACT' else 0,
        'systems_identified': facts['confirmed_systems'] + facts['potential_systems'],
        'regression_items': facts['regression_items'],
        'test_search': 1 if facts['test_status'] else 0,
        'report': 1 if (facts['findings_total'] or facts['changes_total']) else 0,
    }
    rows, manual_minutes = [], 0.0
    for key, label in UNIT_LABELS:
        mins = units[key] * minutes[key]
        manual_minutes += mins
        rows.append({'key': key, 'label': label, 'units': units[key], 'minutes_per_unit': minutes[key], 'manual_minutes': round(mins, 1)})
    findings = facts['findings_total'] if review_findings is None else review_findings
    review_minutes = findings * float(baseline['review_minutes_per_finding'])
    ai_seconds = facts.get('ai_seconds')
    ai_minutes = (ai_seconds / 60.0) if isinstance(ai_seconds, (int, float)) else None
    assisted = (ai_minutes or 0.0) + review_minutes
    basis = ESTIMATED if baseline['status'] == 'AGREED' else ILLUSTRATIVE
    saving_minutes = manual_minutes - assisted
    rate, currency = baseline.get('hourly_cost'), baseline.get('currency')
    money = None
    if isinstance(rate, (int, float)) and rate > 0 and currency:
        money = {'amount': round(saving_minutes / 60.0 * rate, 2), 'currency': currency, 'label': basis}
    measured = {
        'processing_time': {'value_minutes': round(ai_minutes, 2) if ai_minutes is not None else None, 'label': MEASURED if ai_minutes is not None else 'NOT MEASURED'},
        'requirements_assessed': {'value': facts['requirements_assessed'], 'label': MEASURED},
        'existing_tests_reused': {'value': facts['tests_matched_findings'], 'label': MEASURED},
        'automation_assets_identified': {'value': facts['automation_assets'], 'label': MEASURED},
        'regression_items_recommended': {'value': facts['regression_items'], 'label': MEASURED},
        'duplicate_differences_consolidated': {'value': max(0, _int(facts['raw_differences']) - facts['changes_total']) if facts.get('raw_differences') is not None else None, 'label': MEASURED if facts.get('raw_differences') is not None else 'NOT MEASURED'},
    }
    return {
        'basis': basis, 'baseline_status': baseline['status'], 'baseline_note': baseline.get('note', ''),
        'manual': {'rows': rows, 'minutes': round(manual_minutes, 1), 'hours': round(manual_minutes / 60.0, 2), 'label': basis},
        'ai_assisted': {'ai_processing_minutes': round(ai_minutes, 2) if ai_minutes is not None else None, 'ai_processing_label': MEASURED if ai_minutes is not None else 'NOT MEASURED',
                        'human_review_minutes': round(review_minutes, 1), 'human_review_label': ESTIMATED if baseline['status'] == 'AGREED' else ILLUSTRATIVE,
                        'review_findings': findings, 'total_minutes': round(assisted, 1), 'total_hours': round(assisted / 60.0, 2)},
        'saving': {'minutes': round(saving_minutes, 1), 'hours': round(saving_minutes / 60.0, 2), 'percent': round(100.0 * saving_minutes / manual_minutes, 1) if manual_minutes else None, 'label': basis},
        'cost': money if money else {'amount': None, 'currency': None, 'label': 'NOT CALCULATED', 'note': 'No agreed hourly cost is configured, so no monetary saving is stated.'},
        'measured': measured,
        'not_measured': ['Correction time after human review', 'Actual manual analysis time for this change', 'Defects prevented (not claimed: no evidence is collected for it)'],
        'formula': 'Saving = manual baseline effort - (AI processing time + human review time). Human review is part of the AI-assisted effort.',
    }


def management_block(result, kind, header=None, baseline=None):
    """The complete management block for one stored result. kind is 'impact_analysis' or 'change_impact'."""
    facts = facts_from_impact_analysis(result) if kind == 'impact_analysis' else facts_from_change_impact(result, header)
    decision = decision_readiness(facts)
    return {'decision': decision, 'questions': management_questions(facts, decision), 'effort': effort_comparison(facts, baseline), 'facts': facts}


def _fmt_minutes(value):
    if value is None:
        return 'not measured'
    return '%.1f hours' % (value / 60.0) if value >= 90 else '%.1f minutes' % value


def management_lines(block):
    """Plain-text lines (used by the Impact Analysis report and the Change Impact report paragraphs)."""
    d, e = block['decision'], block['effort']
    lines = ['Decision readiness: %s' % d['state']]
    lines += ['  - %s' % r for r in d['reasons']]
    if d['also_needed']:
        lines.append('  Also outstanding: %s' % '; '.join(d['also_needed']))
    lines.append('')
    for q, a in block['questions']:
        lines.append('%s %s' % (q, a))
    lines += ['', 'Effort comparison (%s):' % e['basis'],
              '  Manual baseline: %s (%s; baseline status %s)' % (_fmt_minutes(e['manual']['minutes']), e['manual']['label'], e['baseline_status']),
              '  AI processing: %s (%s)' % (_fmt_minutes(e['ai_assisted']['ai_processing_minutes']), e['ai_assisted']['ai_processing_label']),
              '  Human review allowance: %s (%s, %d finding(s))' % (_fmt_minutes(e['ai_assisted']['human_review_minutes']), e['ai_assisted']['human_review_label'], e['ai_assisted']['review_findings']),
              '  AI-assisted total: %s' % _fmt_minutes(e['ai_assisted']['total_minutes']),
              '  Time saved: %s%s (%s)' % (_fmt_minutes(e['saving']['minutes']), (', %.0f%%' % e['saving']['percent']) if e['saving']['percent'] is not None else '', e['saving']['label'])]
    if e['cost'].get('amount') is not None:
        lines.append('  Cost saved: %s %.2f (%s)' % (e['cost']['currency'], e['cost']['amount'], e['cost']['label']))
    else:
        lines.append('  Cost saved: not calculated - %s' % e['cost']['note'])
    lines.append('  Measured: existing tests reused %s, automation assets identified %s, regression items %s.' % (
        e['measured']['existing_tests_reused']['value'], e['measured']['automation_assets_identified']['value'], e['measured']['regression_items_recommended']['value']))
    lines.append('  Not measured: ' + '; '.join(e['not_measured']) + '.')
    lines.append('  ' + e['formula'])
    if e['baseline_status'] != 'AGREED':
        lines.append('  ' + e['baseline_note'])
    return lines
