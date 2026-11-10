"""Excel report of one Change Impact Analysis: the five results, the QA review and the limitations."""
from io import BytesIO

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

HEAD = PatternFill('solid', fgColor='1F3A5F')
FONT = Font(bold=True, color='FFFFFF')


def _path(h):
    return ' / '.join(str(h.get(k) or '') for k in ('domain', 'module', 'knowledge_name', 'document_type', 'version') if h.get(k))


def _sheet(wb, title, header, rows, widths):
    ws = wb.create_sheet(title)
    ws.append(header)
    for cell in ws[1]:
        cell.fill, cell.font = HEAD, FONT
        cell.alignment = Alignment(vertical='center', wrap_text=True)
    for row in rows:
        ws.append([('' if v is None else v) for v in row])
    for i, width in enumerate(widths, 1):
        ws.column_dimensions[get_column_letter(i)].width = width
    for row in ws.iter_rows(min_row=2):
        for cell in row:
            cell.alignment = Alignment(vertical='top', wrap_text=True)
    ws.freeze_panes = 'A2'
    return ws


def build_xlsx(result, reviews):
    wb = Workbook()
    ws = wb.active
    ws.title = 'Summary'
    s = result['summary']
    src, base = result['source'], result.get('baseline')
    lines = [('Source document', '%s (%s)' % (src['file_name'], _path(src))),
             ('Compared with', ('%s (%s)' % (base['file_name'], _path(base))) if base else 'No earlier version: proposed changes only'),
             ('Impact scope', result['scope']['text']), ('Generated', result['generated_at']), ('Summary', s['headline']), ('Changes', s['changes']),
             ('Confirmed dependencies', s['affected'].get('Confirmed Dependency', 0)), ('Potential dependencies', s['affected'].get('Potential Dependency', 0)),
             ('Needs investigation', s['affected'].get('Needs Investigation', 0)), ('Risk: Critical / High / Medium / Low', ' / '.join(str(s['risk'][k]) for k in ('Critical', 'High', 'Medium', 'Low'))),
             ('Existing regression tests', s['tests_total']), ('Requirements covered', s.get('coverage_covered', '')), ('Coverage gaps', s['coverage_gaps']), ('Coverage not verified', s['coverage_unverified']),
             ('Analysis engine', result.get('engine_version', '1.0'))]
    for label, value in lines:
        ws.append([label, value])
        ws.cell(ws.max_row, 1).font = Font(bold=True)
    ws.column_dimensions['A'].width = 38
    ws.column_dimensions['B'].width = 110
    for row in ws.iter_rows():
        for cell in row:
            cell.alignment = Alignment(vertical='top', wrap_text=True)
    wc = result['what_changed']
    risk_by = {r['change_id']: r for r in result['risks']}
    cov_by = {c['change_id']: c for c in result['coverage']}
    _sheet(wb, '1 What Changed', ['Type', 'Reference', 'Requirement', 'Summary', 'Before', 'After / proposed', 'Precise identifiers', 'Category', 'Risk', 'Coverage', 'Document', 'Location', 'Quote'],
           [[c['label'], c['reference'], c['title'], c['summary'], c['original'] if wc['baseline_used'] else 'Not available (no baseline)', c['modified'], ', '.join(c.get('keys') or []), c.get('category', ''),
             (risk_by.get(c['key']) or {}).get('level', ''), (cov_by.get(c['key']) or {}).get('status', ''), c['document'], c['location'], c['quote']]
            for c in wc['changes']] + [['Set aside', '', '', o['reason'], '', o['text'], '', '', '', '', '', '', ''] for o in wc.get('other_differences', [])], [20, 14, 30, 50, 45, 45, 28, 18, 10, 20, 26, 20, 60])
    ref_of = {c['key']: (c['reference'] or c['title']) for c in wc['changes']}
    _sheet(wb, '2 What Is Affected', ['Classification', 'Kind', 'Domain', 'Module', 'Knowledge', 'Document type', 'Version', 'Document', 'Reference', 'Functionality', 'Why', 'Shared identifiers', 'Changed by', 'AI opinion', 'Evidence quote', 'Location', 'Same file also stored as', 'QA review', 'QA comment'],
           [[i['classification'], i['impact_kind'], i['hierarchy']['domain'], i['hierarchy']['module'], i['hierarchy']['knowledge_name'], i['hierarchy']['document_type'], i['hierarchy']['version'],
             i['hierarchy']['file_name'], i['reference'], i['functionality'], i['why'], ', '.join(i.get('shared_terms') or []), ', '.join(ref_of.get(c, c) for c in i['change_ids']), (('AI opinion (%s): %s' % (i.get('ai_view') or 'unclear', i['ai_note'])) if i.get('ai_note') else ''),
             i['evidence']['quote'], i['evidence']['location'], '; '.join('%s (%s / %s)' % (d['file_name'], d['domain'], d['module']) for d in i.get('duplicates') or []),
             (reviews.get(i['key']) or {}).get('decision', ''), (reviews.get(i['key']) or {}).get('comment', '')] for i in result['affected']['items']],
           [22, 16, 16, 16, 20, 14, 10, 24, 12, 40, 50, 30, 20, 40, 60, 18, 30, 18, 30])
    _sheet(wb, '3 Risk Assessment', ['Risk', 'Reference', 'Requirement', 'Reason', 'Coverage', 'Recommended QA action'],
           [[r['level'], r['reference'], r['subject'], r['reason'], r.get('coverage', ''), r['action']] for r in result['risks']], [10, 14, 40, 70, 20, 70])
    tests = []
    for category, entries in result['regression'].items():
        for t in entries:
            tests.append([category, t['test_case_id'], t['database_id'], t['title'], _path(t['hierarchy']), t['related_requirement'], ', '.join(ref_of.get(c, c) for c in t.get('change_ids') or []),
                          t['reason'], t['priority'], t['basis'], ', '.join(t.get('evidence') or []), t['execution_type'], t['last_result'],
                          (reviews.get(t['key']) or {}).get('decision', ''), (reviews.get(t['key']) or {}).get('comment', '')])
    _sheet(wb, '4 Regression Tests', ['Category', 'Test case ID', 'Database id', 'Title', 'Hierarchy', 'Linked requirement', 'For change', 'Reason', 'Priority', 'Basis', 'Matched on', 'Execution type', 'Last result', 'QA review', 'QA comment'],
           tests, [22, 14, 10, 50, 40, 18, 18, 60, 9, 26, 30, 20, 12, 18, 30])
    _sheet(wb, '5 Missing Coverage', ['Status', 'Reference', 'Subject', 'Evidence', 'Gap', 'Risk', 'Existing tests', 'Recommended scenario (not an existing test)', 'Hierarchy', 'QA review', 'QA comment'],
           [[c['status'], c.get('reference', ''), c['subject'], c['evidence'], c['gap'], c['risk'], ', '.join(t['test_case_id'] for t in c.get('tests') or []), c['scenario'], _path(c.get('hierarchy') or {}),
             (reviews.get(c['key']) or {}).get('decision', ''), (reviews.get(c['key']) or {}).get('comment', '')] for c in result['coverage']], [24, 14, 50, 60, 40, 10, 24, 60, 40, 18, 30])
    _sheet(wb, 'Limitations', ['Limitation'], [[x] for x in result['limitations']], [150])
    out = BytesIO()
    wb.save(out)
    return out.getvalue()
