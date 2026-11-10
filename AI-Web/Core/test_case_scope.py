"""Which test case rows are current, and which belong to which Knowledge source and workbook version.

A Knowledge source can have several test case workbook versions (test_case_versions: 1, 2, 3 ...). Each later version is a revision of the
same source's test cases, and the application itself treats the highest version_number of a source as the current one. The same tc_id / scenario
therefore repeats across versions, and an older workbook version must never be reported as the existing coverage of a newer change.

Row states:
  CURRENT     the row belongs to the latest workbook version of its source
  SUPERSEDED  the row belongs to an older workbook version of its source (listed for transparency, never counted)
  UNVERSIONED the row has no workbook version record (legacy rows); counted, and labelled as such
  UNKNOWN     the version records could not be read; counted, and the result says the version scope is unverified

Read-only. Nothing is written or changed.
"""
import json

CURRENT, SUPERSEDED, UNVERSIONED, UNKNOWN = 'CURRENT', 'SUPERSEDED', 'UNVERSIONED', 'UNKNOWN'


def parse_source_ids(value):
    """The Knowledge source ids a row records (a JSON list such as '[83]', a list, or a single id)."""
    if value in (None, ''):
        return []
    if isinstance(value, (list, tuple, set)):
        raw = list(value)
    else:
        try:
            parsed = json.loads(value) if isinstance(value, str) else value
        except ValueError:
            parsed = [p for p in str(value).replace(';', ',').split(',') if p.strip()]
        raw = parsed if isinstance(parsed, list) else [parsed]
    out = []
    for item in raw:
        try:
            out.append(int(item))
        except (TypeError, ValueError):
            continue
    return sorted(set(out))


def collect_ids(rows):
    """(version ids, source ids) mentioned by the rows, for one read of the version records."""
    versions = sorted({r['test_case_version_id'] for r in rows or [] if r.get('test_case_version_id')})
    sources = sorted({s for r in rows or [] for s in parse_source_ids(r.get('source_knowledge_ids'))})
    return versions, sources


def annotate(rows, version_records, ok=True):
    """Copies of the rows with scope fields added. ``version_records``: rows of test_case_versions
    (id, source_knowledge_id, version_number, document_version_id, created_date) for the rows' versions AND every version of their sources."""
    version_records = version_records or []
    by_id = {v['id']: v for v in version_records}
    latest = {}
    for v in version_records:
        sid, number = v.get('source_knowledge_id'), v.get('version_number')
        if sid is not None and number is not None and number > latest.get(sid, -1):
            latest[sid] = number
    out = []
    for row in rows or []:
        r = dict(row)
        record = by_id.get(row.get('test_case_version_id'))
        sources = [record['source_knowledge_id']] if record and record.get('source_knowledge_id') is not None else parse_source_ids(row.get('source_knowledge_ids'))
        r['source_knowledge_ids'] = sources
        r['workbook_version'] = record.get('version_number') if record else None
        r['workbook_created_date'] = record.get('created_date') if record else None
        best = max((latest[s] for s in sources if s in latest), default=None)
        r['latest_workbook_version'] = best
        if not ok:
            r['scope_status'] = UNKNOWN
            r['scope_note'] = 'The workbook version records could not be read, so it is not known whether this row belongs to the current version.'
        elif record is None or r['workbook_version'] is None or best is None:
            r['scope_status'] = UNVERSIONED
            r['scope_note'] = 'This row has no workbook version record (legacy), so its version could not be checked.'
        elif r['workbook_version'] < best:
            r['scope_status'] = SUPERSEDED
            r['scope_note'] = 'Belongs to workbook version %s; the current version of this source is %s.' % (r['workbook_version'], best)
        else:
            r['scope_status'] = CURRENT
            r['scope_note'] = ''
        out.append(r)
    return out


def summarise(annotated, ok=True):
    """Counts for the result: how many rows were current, superseded, unversioned or unknown, and the versions per source."""
    counts = {CURRENT: 0, SUPERSEDED: 0, UNVERSIONED: 0, UNKNOWN: 0}
    sources = {}
    for r in annotated or []:
        counts[r.get('scope_status') or UNKNOWN] += 1
        for s in r.get('source_knowledge_ids') or []:
            entry = sources.setdefault(s, {'latest': r.get('latest_workbook_version'), 'versions_seen': set()})
            if r.get('workbook_version') is not None:
                entry['versions_seen'].add(r['workbook_version'])
    for entry in sources.values():
        entry['versions_seen'] = sorted(entry['versions_seen'])
    if not ok:
        status = 'UNVERIFIED'
    elif counts[SUPERSEDED] or counts[CURRENT]:
        status = 'VERIFIED' if not counts[UNVERSIONED] else 'PARTLY_VERIFIED'
    else:
        status = 'NOT_APPLICABLE' if not annotated else 'PARTLY_VERIFIED'
    note = {'VERIFIED': 'Only the latest workbook version of each source counts as existing coverage; %d row(s) of older versions were excluded.' % counts[SUPERSEDED],
            'PARTLY_VERIFIED': '%d row(s) have no workbook version record and are counted as they are; %d row(s) of older versions were excluded.' % (counts[UNVERSIONED], counts[SUPERSEDED]),
            'UNVERIFIED': 'The workbook version records could not be read, so rows of older versions may be included.',
            'NOT_APPLICABLE': 'There were no test case rows to check.'}[status]
    return {'status': status, 'note': note, 'current_rows': counts[CURRENT], 'superseded_rows': counts[SUPERSEDED],
            'unversioned_rows': counts[UNVERSIONED], 'unknown_rows': counts[UNKNOWN], 'sources': {str(k): v for k, v in sorted(sources.items())}}


def records_for(reader, rows):
    """The version records needed to annotate ``rows``: their own versions and every version of their sources. Raises when they cannot be read."""
    version_ids, source_ids = collect_ids(rows)
    if not version_ids:
        return []
    first = reader.test_case_versions(version_ids=version_ids)
    sources = set(source_ids) | {v['source_knowledge_id'] for v in first if v.get('source_knowledge_id') is not None}
    return reader.test_case_versions(source_ids=sorted(sources), version_ids=version_ids)
