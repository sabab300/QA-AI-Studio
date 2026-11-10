"""Report building and Excel / PDF export.

One report dictionary is built from the frozen snapshot plus the current review
overlay. The screen, the Excel workbook and the PDF all render that same dictionary,
so what a person exports always matches what they see, with evidence references kept.
"""
import io
from datetime import datetime, timezone

from Core import impact_management
from Core.change_impact.util import clip

ASSURANCE_LABEL = {'CONFIRMED': 'Confirmed', 'EVIDENCE_BACKED': 'Evidence-backed', 'SUGGESTED': 'Suggested', 'HYPOTHESIS': 'Suggested (unverified)'}
SECTION_TITLES = ('Executive Summary', 'Management Summary', 'Change Overview', 'Source Documents', 'Impacted Systems', 'Impacted Requirements', 'Affected Test Cases',
                  'Impacted Automation', 'Coverage Gaps', 'Regression Recommendations', 'Recommended Scenarios', 'Risk Assessment', 'Open Questions',
                  'Traceability', 'Evidence and References', 'Recommended QA Actions', 'Review Log')


def _review_state(overlay, finding_id):
    rec = (overlay or {}).get('findings', {}).get(finding_id)
    dep = (overlay or {}).get('dependency_decisions', {}).get(finding_id)
    parts = [rec['state']] if rec else ['Pending']
    if dep:
        parts.append('Dependency %s' % dep['decision'].lower())
    return '; '.join(parts)


def _ev(evidence):
    return ', '.join(e['ref'] for e in (evidence or []) if e.get('ref')) or 'None'


def _texts(items):
    """Derived impact statements are marked so they are never mistaken for quotations."""
    out = []
    for item in items or []:
        if isinstance(item, dict):
            out.append(('[Derived] ' if item.get('basis') == 'DERIVED' else '') + str(item.get('text', '')))
        else:
            out.append(str(item))
    return '\n'.join(out)


def _certainty(finding):
    return (finding.get('payload') or {}).get('certainty') or ('Potential Impact' if finding.get('assurance') == 'HYPOTHESIS' else 'Confirmed Impact')


def _confidence(change):
    return change.get('confidence') or {'CONFIRMED': 'Confirmed', 'EVIDENCE_BACKED': 'Evidence-backed', 'SUGGESTED': 'Suggested'}.get(change.get('assurance'), 'Unknown')


def build_report(header, snapshot, overlay, explanations, events=None):
    f = snapshot['findings']
    by_kind = lambda k: [x for x in f if x['kind'] == k]
    risk = snapshot['risk']
    dash = snapshot['dashboard']['totals']
    reg = snapshot['regression']
    insight = snapshot.get('insight') or {}
    summary = insight.get('summary') or {}
    questions = insight.get('questions') or []
    approval = (overlay or {}).get('regression_approval')
    approved = set(approval['test_case_ids']) if approval else set()
    changes = snapshot['changes']
    functional = [c for c in changes if c.get('functional', True)]
    change_by_id = {c['change_id']: c for c in changes}
    sections = []

    # A  Executive summary
    mode = 'compared two document versions' if snapshot['analysis']['mode'] == 'VERSION_COMPARE' else 'analysed a single change request'
    levels = summary.get('risk_functional') or {k: sum(1 for c in functional if (risk.get(c['change_id']) or {}).get('level') == k) for k in ('Critical', 'High', 'Medium', 'Low')}
    docs = '; '.join('%s: %s (v%s)' % (d['role'].title(), d['file_name'], d['owner']['version']) for d in snapshot['documents'])
    consolidated = summary.get('raw_differences')
    paragraphs = [
        'Documents: %s.' % docs,
        'This analysis %s and found %d unique change(s): %d functional and %d administrative (reference, date or sign-off information)%s.'
        % (mode, len(changes), len(functional), len(changes) - len(functional),
           ('; %d raw difference(s) were consolidated' % consolidated) if consolidated and consolidated > len(changes) else ''),
        'System impact: %d confirmed (owned or named by the changed document) and %d potential (unverified) system, module or interface(s).' % (dash.get('impacted_systems', 0), dash.get('possible_systems_unverified', 0)),
        'Risk of the functional changes: %d Critical, %d High, %d Medium, %d Low.' % (levels.get('Critical', 0), levels.get('High', 0), levels.get('Medium', 0), levels.get('Low', 0)),
        'QA impact: %d existing impacted test case(s) with evidence, %d possible match(es), %d recommended new regression scenario(s) and %d coverage gap(s). These are separate measurements.'
        % (dash.get('existing_impacted_tests', dash.get('test_cases', 0)), dash.get('possible_tests', 0), dash.get('recommended_new_scenarios', 0), dash.get('coverage_gaps', 0)),
        '%d open question(s) need business clarification%s.' % (len(questions), (' (%d possible contradiction(s))' % summary['conflicts']) if summary.get('conflicts') else '')]
    top = sorted(functional, key=lambda c: -risk.get(c['change_id'], {}).get('score', 0))[:3]
    if top:
        paragraphs.append('Highest-risk changes: ' + '; '.join('%s - %s (%s)' % (c.get('reference') or c['change_id'], c.get('requirement') or c['category'], risk[c['change_id']]['level']) for c in top) + '.')
    paragraphs.append('AI use: %s.' % (snapshot['ai'].get('status') or 'Not requested'))
    paragraphs += snapshot.get('warnings', [])
    paragraphs += snapshot.get('scope_notes', [])
    paragraphs.append('Limitations: ' + ' '.join(snapshot['limitations']))
    paragraphs.append('Review status: %d finding(s) reviewed by a person; regression %s.' % (
        len((overlay or {}).get('findings', {})), ('approved by %s on %s (%d tests)' % (approval['by'], approval['at'][:19], len(approved))) if approval else 'not yet approved'))
    sections.append({'key': 'executive_summary', 'part': 'A', 'heading': 'A. Executive Summary', 'title': 'Executive Summary', 'paragraphs': paragraphs, 'columns': [], 'rows': []})

    # A2  Management summary and effort (derived from the frozen snapshot; nothing is stored)
    try:
        block = impact_management.management_block(snapshot, 'change_impact', header)
        effort = block['effort']
        sections.append({'key': 'management_summary', 'part': 'A', 'heading': 'Management Summary and Effort', 'title': 'Management Summary',
                         'paragraphs': [line for line in impact_management.management_lines(block) if line.strip()],
                         'columns': ['Manual activity (baseline)', 'Units', 'Minutes per unit', 'Manual minutes'],
                         'rows': [[r['label'], str(r['units']), '%g' % r['minutes_per_unit'], '%g' % r['manual_minutes']] for r in effort['manual']['rows']]})
    except Exception:
        pass

    # B  Detailed change comparison
    rows = []
    for c in changes:
        a = risk.get(c['change_id'], {})
        occurrences = c.get('occurrence_count', 1)
        rows.append([c['change_id'], c.get('logical_kind') or ('Functional' if c.get('functional', True) else 'Administrative'), c['change_type'].replace('_', ' ').title(),
                     c.get('requirement') or '', c.get('reference') or '', c['category'], c['description'],
                     c.get('original_text') or c.get('original_note') or 'Not available', c.get('modified_text') or '(removed)',
                     _texts(c.get('business_impact')), _texts(c.get('system_impact')), _texts(c.get('technical_impact')), _texts(c.get('qa_impact')),
                     a.get('level', ''), '; '.join(r['text'] for r in a.get('reasons', [])) or a.get('summary', ''), _confidence(c),
                     ('%d: %s' % (occurrences, '; '.join(o.get('location') or '' for o in c.get('occurrences', [])[:8]))) if occurrences > 1 else '1',
                     c['source_document'], c['source_version'], _ev(c['evidence'])])
    sections.append({'key': 'change_overview', 'part': 'B', 'heading': 'B. Detailed Change Comparison', 'title': 'Change Overview',
                     'paragraphs': ['Each row is one unique logical change. Text marked [Derived] is the engine\'s interpretation, not a quotation; everything else is quoted from the documents.'],
                     'rows': rows,
                     'columns': ['Change ID', 'Classification', 'Type', 'Requirement', 'Reference', 'Category', 'Description', 'Old behaviour', 'New behaviour', 'Business impact',
                                 'System impact', 'Technical impact', 'QA impact', 'Risk', 'Risk reason', 'Confidence', 'Occurrences', 'Source document', 'Version', 'Evidence']})

    rows = [[d['role'].title(), d['file_name'], ' > '.join([d['owner']['domain'], d['owner']['module'], d['owner']['knowledge_name']]), d['owner']['document_type'],
             d['owner']['version'], d['sha256'][:16], str(d['characters'])] for d in snapshot['documents']]
    sections.append({'key': 'source_documents', 'part': '', 'heading': 'Source Documents', 'title': 'Source Documents', 'paragraphs': [], 'rows': rows,
                     'columns': ['Role', 'File', 'Domain > Module > Knowledge', 'Document type', 'Version', 'SHA-256 (start)', 'Characters analysed']})

    # C  System and integration impact
    order = {'Confirmed Impact': 0, 'Potential Impact': 1}
    systems = sorted(by_kind('SYSTEM'), key=lambda x: (order.get(_certainty(x), 2), x['payload']['component']))
    rows = [[_certainty(x), x['payload'].get('source', ''), x['payload']['component'], x['payload'].get('component_kind', ''), ', '.join(x['payload']['impact_types']),
             x['payload'].get('basis', ''), _ev(x['evidence']), ASSURANCE_LABEL.get(x['assurance'], x['assurance']) + ' - ' + x['verification_status'], _review_state(overlay, x['finding_id']),
             x['payload'].get('identity', ''), str(x['payload'].get('merged_findings', 1)),
             '; '.join('%s [%s]: %s' % (l['requirement'], l['certainty'] or '', clip(l['basis'], 140)) for l in (x['payload'].get('per_requirement') or []))]
            for x in systems]
    for item in (insight.get('no_evidence') or {}).get('apis', []):
        rows.append(['No Evidence of Impact', '', item['name'], 'API / interface detail', 'Unchanged', 'Appears identically in both versions and is not part of any change; it is not reported as modified.',
                     'None', 'Context: %s - "%s"' % (item.get('location') or item.get('context') or 'document', clip(item.get('quote', ''), 160)), '', 'Described in the document', '0', ''])
    sections.append({'key': 'impacted_systems', 'part': 'C', 'heading': 'C. System and Integration Impact', 'title': 'Impacted Systems',
                     'paragraphs': ['Confirmed Impact: the changed text or the document states it. Potential Impact: derived or mentioned, needs verification. No Evidence of Impact: present but unchanged. '
                                    'A system named in a document is not proof that it must change. Each row is one unique component; "Detailed findings" counts how many per-change findings were merged into it, and the per-requirement column keeps each one.'],
                     'rows': rows,
                     'columns': ['Certainty', 'Source', 'Impacted component', 'Kind', 'Impact type', 'Basis', 'Evidence', 'Status', 'Review', 'Identity (Knowledge Hub check)', 'Detailed findings', 'Per-requirement detail']})

    rows = [[x['payload'].get('requirement') or x['title'], ', '.join(x['payload'].get('change_ids') or [x['change_id']]), x['payload'].get('reference') or '', x['payload']['description'],
             x['payload'].get('previous_description') or '', x['payload']['impact'], x['payload']['match_basis'], ASSURANCE_LABEL.get(x['assurance'], x['assurance']), _ev(x['evidence'])]
            for x in by_kind('REQUIREMENT')]
    sections.append({'key': 'impacted_requirements', 'part': 'C', 'heading': 'Impacted Requirements', 'title': 'Impacted Requirements', 'paragraphs': [], 'rows': rows,
                     'columns': ['Requirement', 'Changes', 'Reference', 'Requirement (new wording)', 'Previous wording', 'Impact', 'Match basis', 'Confidence', 'Evidence']})

    # D  QA impact
    rows = [[x['payload']['tc_number'] or str(x['payload']['test_case_id']), x['payload'].get('requirement_ref') or '', x['payload']['title'], x['payload']['current_version'],
             x['payload']['classification'], x['payload'].get('relationship') or x['payload']['link_status'], x['payload']['reason'], x['payload']['recommended_action'],
             x['verification_status'], _ev(x['evidence']), _review_state(overlay, x['finding_id'])] for x in by_kind('TEST')]
    sections.append({'key': 'affected_test_cases', 'part': 'D', 'heading': 'D. QA Impact - existing test cases', 'title': 'Affected Test Cases',
                     'paragraphs': ['Nothing was changed, retired or deleted. A test case is listed only with evidence; possible matches are not confirmed relationships.'], 'rows': rows,
                     'columns': ['Test Case ID', 'Requirement', 'Title', 'Current version', 'Classification', 'Relationship', 'Reason', 'Recommended action', 'Traceability', 'Evidence', 'Review']})

    rows = [[x['payload']['tc_number'] or '', x['payload']['asset_kind'], x['payload']['asset_label'], x['payload']['impact'], ', '.join(x['payload']['matched_terms']),
             x['payload']['reason'], x['payload']['recommended_action'], _ev(x['evidence'])] for x in by_kind('AUTOMATION')]
    sections.append({'key': 'impacted_automation', 'part': 'D', 'heading': 'Impacted Automation', 'title': 'Impacted Automation', 'paragraphs': ['No script was executed or modified.'], 'rows': rows,
                     'columns': ['Test', 'Kind', 'Asset', 'Impact', 'Matched terms', 'Reason', 'Recommended action', 'Evidence']})

    rows = [[x['payload']['subject'], str(x['payload']['linked_tests']), str(x['payload'].get('suggested_tests', 0)), '; '.join(x['payload']['missing_scenarios']),
             ', '.join(x['payload']['suggested_test_types']), x['severity'] or risk.get(x['change_id'], {}).get('level', ''), x['payload']['reason'], x['payload']['recommended_action'],
             _review_state(overlay, x['finding_id'])] for x in by_kind('GAP')]
    sections.append({'key': 'coverage_gaps', 'part': 'D', 'heading': 'Coverage Gaps', 'title': 'Coverage Gaps', 'paragraphs': [], 'rows': rows,
                     'columns': ['Requirement / change', 'Confirmed linked tests', 'Possible tests', 'Missing scenarios', 'Suggested test types', 'Risk', 'Reason', 'Recommended action', 'Review']})

    rows = [[i['priority'], i['tc_number'] or str(i['test_case_id']), i['title'], ', '.join(i['related_changes']), i['risk'], i['selection_reason'], i['execution_type'],
             i['automation'], i['previous_result'], 'Yes' if i['recommended'] else 'No', 'Yes' if i['test_case_id'] in approved else 'No'] for i in reg['items']]
    sections.append({'key': 'regression_recommendations', 'part': 'D', 'heading': 'Regression - existing test cases', 'title': 'Regression Recommendations', 'paragraphs': [reg['coverage_statement']], 'rows': rows,
                     'columns': ['Priority', 'Test ID', 'Title', 'Related changes', 'Risk', 'Selection reason', 'Execution', 'Automation', 'Previous result', 'Recommended', 'Approved']})

    scenario_rows = []
    for sc in (reg.get('scenarios') or []) + (reg.get('smoke') or []):
        scenario_rows.append([sc['category'], sc['priority'], sc['title'], sc['reason'], sc.get('expected', ''),
                              'Supported by the document' if sc['basis'] == 'SOURCE' else 'Awaiting business clarification',
                              ', '.join('%s (%s)' % (change_by_id[c].get('reference') or c, c) if c in change_by_id else c for c in sc['related_changes']),
                              ', '.join(sc.get('modules') or []), ', '.join(sc['execution']), ', '.join(sc.get('open_questions') or []), _ev(sc.get('evidence'))])
    sections.append({'key': 'recommended_scenarios', 'part': 'D', 'heading': 'Recommended regression scenarios (derived)', 'title': 'Recommended Scenarios',
                     'paragraphs': ['Derived from the changed wording; no test case is created automatically. "Awaiting business clarification" means the document does not state the expected behaviour.'],
                     'rows': scenario_rows,
                     'columns': ['Category', 'Priority', 'Scenario', 'Reason', 'Expected behaviour', 'Basis', 'Related change', 'Modules', 'Suggested execution', 'Open question', 'Evidence']})

    # E  Risk assessment
    rows = []
    for c in changes:
        a = risk.get(c['change_id'])
        if a:
            rows.append([c['change_id'], c.get('requirement') or c.get('reference') or '', a['level'], '%.2f' % a['score'], a['summary'], '\n'.join(r['text'] for r in a.get('reasons', [])),
                         '\n'.join(a.get('mitigation', [])), '; '.join('%s %.2f x %g%s' % (x['label'], x['value'], x['weight'], '' if x['applied'] else ' (not applied)') for x in a['factors'])])
    sections.append({'key': 'risk_assessment', 'part': 'E', 'heading': 'E. Risk Assessment', 'title': 'Risk Assessment',
                     'paragraphs': ['A prioritisation score from policy-weighted business factors. It is not a probability of failure and carries no AI confidence figure. Administrative changes are rated Low.'],
                     'rows': rows, 'columns': ['Change', 'Requirement', 'Level', 'Score (0-1)', 'Why', 'Evidence-supported reasons', 'Mitigation', 'Factors (value x weight)']})

    # F  Open questions
    rows = []
    for q in questions:
        rows.append([q['id'], q['type'].replace('_', ' ').title(), q['severity'], q['title'], q['question'], q['basis'],
                     '\n'.join('%s: "%s"' % (st.get('where') or '', st['quote']) for st in q['statements']), ', '.join(q.get('related_changes', [])),
                     _ev([st.get('evidence') for st in q['statements'] if st.get('evidence')])])
    sections.append({'key': 'open_questions', 'part': 'F', 'heading': 'F. Open Questions for business clarification', 'title': 'Open Questions',
                     'paragraphs': ['Conflicts between statements are reported, never resolved by the tool. Each question quotes the statements that raised it.'], 'rows': rows,
                     'columns': ['ID', 'Type', 'Severity', 'Title', 'Question', 'Why it was raised', 'Statements', 'Related changes', 'Evidence']})

    # G  Traceability
    rows = []
    for c in functional:
        cid = c['change_id']
        sys_rows = [x for x in systems if cid in (x['payload'].get('change_ids') or [x['change_id']])]
        tests = [x for x in by_kind('TEST') if x['change_id'] == cid]
        firm = [x for x in tests if x['assurance'] in ('CONFIRMED', 'EVIDENCE_BACKED')]
        soft = [x for x in tests if x['assurance'] not in ('CONFIRMED', 'EVIDENCE_BACKED')]
        auto = [x for x in by_kind('AUTOMATION') if x['change_id'] == cid and x['payload'].get('asset_kind') != 'None']
        scs = [sc['title'] for sc in (reg.get('scenarios') or []) if cid in sc['change_ids']]
        gap = any(g['change_id'] == cid or cid in (g['payload'].get('change_ids') or []) for g in by_kind('GAP'))
        rows.append([c.get('reference') or '', c.get('requirement') or '', cid, '; '.join(x['payload']['component'] for x in sys_rows if _certainty(x) == 'Confirmed Impact'),
                     '; '.join(x['payload']['component'] for x in sys_rows if _certainty(x) != 'Confirmed Impact'),
                     '; '.join('%s %s' % (x['payload']['tc_number'] or x['payload']['test_case_id'], x['payload']['title']) for x in firm) or 'None',
                     '; '.join('%s %s' % (x['payload']['tc_number'] or x['payload']['test_case_id'], x['payload']['title']) for x in soft) or 'None',
                     '; '.join(x['payload']['asset_label'] for x in auto) or 'None', str(len(scs)), 'Gap: no confirmed existing test' if gap else 'Covered by existing test(s)'])
    sections.append({'key': 'traceability', 'part': 'G', 'heading': 'G. Traceability (Knowledge Hub > QA Engineering > QA Automation)', 'title': 'Traceability',
                     'paragraphs': ['Existing impacted tests, possible matches, recommended new scenarios and coverage gaps are separate measurements.'], 'rows': rows,
                     'columns': ['Reference', 'Requirement', 'Change', 'Confirmed systems', 'Potential systems', 'Existing impacted tests', 'Possible tests', 'Automation', 'Recommended new scenarios', 'Coverage']})

    # Evidence
    seen, rows = set(), []
    for item in changes + f:
        for e in item.get('evidence', []):
            if e.get('ref') and e['ref'] not in seen:
                seen.add(e['ref'])
                rows.append([e['ref'], e.get('role', ''), e.get('document', ''), str(e.get('document_version', '')), e.get('location') or e.get('clause_ref') or '', str(e.get('anchor', '')), clip(e.get('quote', ''), 500)])
    for x in explanations or []:
        rows.append(['AI-%s' % x['id'], 'ai-explanation (%s)' % x['mode'], x.get('provider') or 'rule-based', x.get('model') or '', '', '', clip(x['text'], 500) + ' [cites: %s]' % ', '.join(x['source_refs'])])
    sections.append({'key': 'evidence_references', 'part': '', 'heading': 'Evidence and References', 'title': 'Evidence and References',
                     'paragraphs': ['Every quotation is copied from the stored document text. AI explanations are listed as written and still need human review.'], 'rows': rows,
                     'columns': ['Reference', 'Role', 'Document', 'Version', 'Location', 'Anchor', 'Quotation']})

    # Actions
    unconfirmed = sum(1 for x in by_kind('SYSTEM') if x['payload'].get('dependency') and x['assurance'] != 'CONFIRMED')
    update = sum(1 for x in by_kind('TEST') if x['payload']['classification'] == 'Update required')
    obsolete = sum(1 for x in by_kind('TEST') if x['payload']['classification'] == 'Obsolete candidate')
    actions = []
    if questions:
        actions.append(['High', 'Resolve %d open question(s) with the business analyst before finalising test design.' % len(questions), 'Open Questions'])
    if update:
        actions.append(['High', 'Update %d test case(s) marked "Update required" in QA Engineering.' % update, 'Affected Test Cases'])
    if dash.get('coverage_gaps'):
        actions.append(['High', 'Design and review new tests for %d coverage gap(s) in QA Engineering. Nothing is created automatically.' % dash['coverage_gaps'], 'Coverage Gaps'])
    if scenario_rows:
        actions.append(['Medium', 'Review the %d recommended regression scenario(s) and turn the accepted ones into test cases.' % len(scenario_rows), 'Recommended Scenarios'])
    if unconfirmed:
        actions.append(['Medium', 'Confirm or reject %d stated dependency(ies) so they become verified knowledge.' % unconfirmed, 'Impacted Systems'])
    if dash.get('possible_systems_unverified'):
        actions.append(['Medium', 'Investigate %d potential system impact(s); no change is confirmed for them.' % dash['possible_systems_unverified'], 'Impacted Systems'])
    if obsolete:
        actions.append(['Medium', 'Decide whether %d obsolete candidate test(s) should be retired in QA Engineering.' % obsolete, 'Affected Test Cases'])
    if reg['items']:
        actions.append(['Medium', 'Review and approve the regression selection (%d recommended).' % reg['recommended_total'], 'Regression Recommendations'])
    actions.append(['Low', 'Link confirmed test cases to their source documents in QA Engineering to improve traceability for the next analysis.', 'Affected Test Cases'])
    sections.append({'key': 'recommended_actions', 'part': '', 'heading': 'Recommended QA Actions', 'title': 'Recommended QA Actions', 'paragraphs': [], 'rows': actions, 'columns': ['Priority', 'Action', 'See section']})

    rows = [[e['created_at'][:19], e['actor_name'], e['event_type'].replace('_', ' ').title(), e['finding_id'], e['decision'], e['comment']]
            for e in sorted((overlay or {}).get('all_events', []), key=lambda e: e['id'])]
    sections.append({'key': 'review_log', 'part': '', 'heading': 'Review Log', 'title': 'Review Log', 'paragraphs': ['Every decision is appended and never edited. AI findings are never approved automatically.'], 'rows': rows,
                     'columns': ['When (UTC)', 'Reviewer', 'Event', 'Finding', 'Decision', 'Comment']})
    return {'title': header['title'], 'analysis_id': header['id'], 'status': header['status'], 'generated_at': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
            'created_at': header['created_at'], 'created_by': header['created_by_name'], 'snapshot_hash': header['snapshot_hash'],
            'ai': {'provider': header['ai_provider'], 'model': header['ai_model'], 'status': header['ai_status']}, 'sections': sections}


# ---------------------------------------------------------------------- Excel
def _safe_cell(value):
    text = '' if value is None else str(value)
    text = ''.join(ch for ch in text if ch in '\n\t' or ord(ch) >= 32)
    if text[:1] in ('=', '+', '-', '@'):
        text = "'" + text   # never let document text run as a formula
    return text[:32000]


def to_excel(report):
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    wb = Workbook()
    summary = wb.active
    summary.title = 'Summary'
    summary.append(['Change Impact Analysis'])
    summary['A1'].font = Font(bold=True, size=14)
    for label, value in (('Title', report['title']), ('Analysis ID', report['analysis_id']), ('Status', report['status']), ('Created by', report['created_by']),
                         ('Created at', report['created_at']), ('Exported at', report['generated_at']), ('Snapshot hash', report['snapshot_hash']),
                         ('AI provider / model', '%s %s' % (report['ai']['provider'] or '-', report['ai']['model'] or '')), ('AI status', report['ai']['status'])):
        summary.append([label, _safe_cell(value)])
    exec_section = report['sections'][0]
    summary.append([])
    for p in exec_section['paragraphs']:
        summary.append([_safe_cell(p)])
        summary.cell(row=summary.max_row, column=1).alignment = Alignment(wrap_text=True, vertical='top')
    summary.column_dimensions['A'].width = 120
    head_fill = PatternFill('solid', fgColor='1F3A5F')
    for section in report['sections'][1:]:
        ws = wb.create_sheet(title=section['title'][:31])
        if section['paragraphs']:
            for p in section['paragraphs']:
                ws.append([_safe_cell(p)])
            ws.append([])
        start = ws.max_row + 1 if section['paragraphs'] else 1   # no blank first row
        ws.append(section['columns'])
        for cell in ws[start]:
            cell.font = Font(bold=True, color='FFFFFF')
            cell.fill = head_fill
            cell.alignment = Alignment(wrap_text=True, vertical='top')
        for row in section['rows']:
            ws.append([_safe_cell(v) for v in row])
        for r in ws.iter_rows(min_row=start + 1):
            for cell in r:
                cell.alignment = Alignment(wrap_text=True, vertical='top')
        for index, name in enumerate(section['columns'], 1):
            longest = max([len(str(name))] + [min(len(str(r[index - 1])), 60) for r in section['rows'] if index - 1 < len(r)] or [10])
            ws.column_dimensions[ws.cell(row=start, column=index).column_letter].width = max(12, min(longest + 2, 62))
        ws.freeze_panes = ws.cell(row=start + 1, column=1)
    buffer = io.BytesIO()
    wb.save(buffer)
    return buffer.getvalue()


# ---------------------------------------------------------------------- PDF
def to_pdf(report):
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import cm
    from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
    from xml.sax.saxutils import escape
    styles = getSampleStyleSheet()
    cell = ParagraphStyle('ciCell', parent=styles['Normal'], fontSize=6.8, leading=8.2)
    head = ParagraphStyle('ciHead', parent=cell, textColor=colors.white, fontName='Helvetica-Bold')
    body = ParagraphStyle('ciBody', parent=styles['Normal'], fontSize=9, leading=12)
    buffer = io.BytesIO()

    def footer(canvas, doc):
        canvas.saveState()
        canvas.setFont('Helvetica', 7)
        canvas.drawString(1.2 * cm, 0.8 * cm, 'QA AI Studio - Change Impact Analysis %s - snapshot %s' % (report['analysis_id'], (report['snapshot_hash'] or '')[:12]))
        canvas.drawRightString(landscape(A4)[0] - 1.2 * cm, 0.8 * cm, 'Page %d' % doc.page)
        canvas.restoreState()

    doc = SimpleDocTemplate(buffer, pagesize=landscape(A4), leftMargin=1.2 * cm, rightMargin=1.2 * cm, topMargin=1.2 * cm, bottomMargin=1.5 * cm,
                            title=report['title'], author='QA AI Studio')
    width = landscape(A4)[0] - 2.4 * cm
    flow = [Paragraph(escape(report['title']), styles['Title']),
            Paragraph(escape('Analysis %s - %s - created by %s on %s - exported %s' % (report['analysis_id'], report['status'], report['created_by'], report['created_at'][:19], report['generated_at'])), body),
            Spacer(1, 6)]
    for index, section in enumerate(report['sections']):
        flow.append(Paragraph(escape(section.get('heading') or section['title']), styles['Heading2']))
        for p in section['paragraphs']:
            flow.append(Paragraph(escape(p), body))
            flow.append(Spacer(1, 3))
        if section['columns']:
            if not section['rows']:
                flow.append(Paragraph('No rows for this section.', body))
            else:
                n = len(section['columns'])
                grid = [('GRID', (0, 0), (-1, -1), 0.25, colors.HexColor('#9AA5B1')), ('VALIGN', (0, 0), (-1, -1), 'TOP')]
                if n > 8:
                    # Wide sections are printed as one label/value block per record so that no row can exceed the page height.
                    label = ParagraphStyle('ciLabel', parent=cell, fontName='Helvetica-Bold')
                    for number, row in enumerate(section['rows'], 1):
                        data = [[Paragraph(escape(str(c)), label), Paragraph(escape(clip(str(v), 1500)).replace('\n', '<br/>'), cell)]
                                for c, v in zip(section['columns'], row) if str(v).strip()]
                        if not data:
                            continue
                        block = Table(data, colWidths=[width * 0.2, width * 0.8])
                        block.setStyle(TableStyle(grid + [('BACKGROUND', (0, 0), (0, -1), colors.HexColor('#E6ECF3'))]))
                        flow.append(Paragraph('Record %d' % number, styles['Heading4']))
                        flow.append(block)
                        flow.append(Spacer(1, 6))
                else:
                    widths = [width / n] * n
                    data = [[Paragraph(escape(c), head) for c in section['columns']]]
                    for row in section['rows']:
                        data.append([Paragraph(escape(clip(str(v), 700)).replace('\n', '<br/>'), cell) for v in row])
                    table = Table(data, colWidths=widths, repeatRows=1)
                    table.setStyle(TableStyle(grid + [('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#1F3A5F')),
                                                      ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#F3F6FA')])]))
                    flow.append(table)
        flow.append(Spacer(1, 10))
    doc.build(flow, onFirstPage=footer, onLaterPages=footer)
    return buffer.getvalue()
