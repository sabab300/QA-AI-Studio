"""Scenario-level automation links (Playwright, API, SQL), shared by Change Impact and AI Assistant Impact Analysis.

A "scenario" is one test case row (one id). The same tc_id / tc_number repeats across the versions of a test case workbook
and across Knowledge sources, so an identifier alone never proves that a row is automated. Links are resolved from what the
records themselves say:

* Playwright: an asset record is linked to a row only when the tc_id matches AND the asset's Knowledge source is one of the
  row's own sources. An asset with no source scope, or a row with no recorded source, is linked with the weaker basis stated.
  The text checked is the script of the asset's ACTIVE source (AUTO -> automation_script, UPLOADED -> uploaded_script,
  MANUAL -> recorded_script, as in QA Automation's own lifecycle code), not just any stored variant.
* API: the endpoint bound to the row (bound_api_endpoint_id), the row's own request configuration, or an explicit asset link.
* SQL: the row's own sql_script_path, a script whose file name is this row's test id, or an explicit asset link.
* Anything stored for the whole test case VERSION (an API collection, other SQL scripts) is version-level context. It is
  reported, may be text-checked, but never makes a scenario "automated".
* A type recorded on the row (Playwright / API / SQL) with no asset record behind it is DECLARED: reported, not text-checked.

Read-only. Nothing is executed, regenerated or written.
"""
import json
from pathlib import Path

KINDS = ('Playwright', 'API', 'SQL')
SCENARIO, VERSION = 'SCENARIO', 'VERSION'
LINKED, DECLARED, VERSION_CONTEXT = 'LINKED', 'DECLARED', 'VERSION_LEVEL'

# QA Automation's own mapping of a script source to its stored field (Core/playwright_script_lifecycle.py).
SCRIPT_FIELD = {'AUTO': 'automation_script', 'UPLOADED': 'uploaded_script', 'MANUAL': 'recorded_script', 'RECORDED': 'recorded_script'}
ALL_FIELDS = ('automation_script', 'uploaded_script', 'recorded_script')


def row_key(row):
    return row.get('tc_id') or row.get('tc_number') or 'row-%s' % row.get('id')


def active_script(asset, cap=200000):
    """(text, field_used, note). The active source's script; another stored variant only when the active one is empty, and then it says so."""
    source = (asset.get('active_script_source') or 'AUTO').upper()
    field = SCRIPT_FIELD.get(source, 'automation_script')
    text = asset.get(field) or ''
    if text:
        return text[:cap], field, ''
    for other in ALL_FIELDS:
        if asset.get(other):
            return asset[other][:cap], other, 'The active %s script is empty; the stored %s was checked instead.' % (source, other.replace('_script', ''))
    return '', field, 'No script text is stored for this asset.'


class AutomationContext:
    """Prefetched, read-only data for a set of test case rows."""

    def __init__(self, reader, rows, sql_text_reader=None, cap=200000):
        self.rows = list(rows or [])
        self.cap = cap
        self.sql_text_reader = sql_text_reader
        self.errors = []
        ids = [r['id'] for r in self.rows if r.get('id') is not None]
        keys = sorted({row_key(r) for r in self.rows if r.get('tc_id') or r.get('tc_number')})
        versions = sorted({r['test_case_version_id'] for r in self.rows if r.get('test_case_version_id')})
        self.links = self._get(reader, 'asset_links', ids) or {}
        self.sources = self._get(reader, 'test_sources', ids) or {}
        all_sources = sorted({s for v in self.sources.values() for s in v})
        self.playwright = self._get(reader, 'playwright_assets', keys, all_sources) or []
        self.endpoints = self._get(reader, 'api_endpoints', versions) or []
        self.sql_scripts = self._get(reader, 'sql_scripts', versions) or []

    def _get(self, reader, name, *args):
        try:
            return getattr(reader, name)(*args)
        except Exception as exc:                               # reported as "not read", never as "none"
            self.errors.append('%s: %s' % (name, type(exc).__name__))
            return None

    # ---------------------------------------------------------------------------------------------
    def resolve(self, row):
        """{'scenario': [asset...], 'version': [asset...]} for one row. Each asset: kind, level, state, reference, label, basis, text, text_checked, note."""
        scenario, version = [], []
        key = row_key(row)
        row_sources = set(self.sources.get(row.get('id'), []))

        # ---- Playwright
        for asset in self.playwright:
            if asset.get('tc_id') != (row.get('tc_id') or row.get('tc_number')):
                continue
            scope = asset.get('source_knowledge_id')
            if scope is not None and row_sources and scope not in row_sources:
                continue                                        # an asset of another Knowledge source is not this scenario's script
            basis = ('test id %s and source %s' % (key, scope)) if scope is not None and row_sources else (
                'test id %s (the asset has no source scope)' % key if scope is None else 'test id %s (this row records no source, so the scope is unverified)' % key)
            text, field, note = active_script(asset, self.cap)
            stale = self._predates_workbook(asset, row)
            if stale:
                note = (note + ' ' + stale).strip()
            scenario.append({'kind': 'Playwright', 'level': SCENARIO, 'state': LINKED, 'reference': 'playwright:%s' % asset['id'],
                             'label': 'Playwright script (%s, %s)' % (asset.get('active_script_source'), asset.get('lifecycle_status')),
                             'basis': basis, 'text': text, 'text_checked': bool(text), 'note': note})

        # ---- API: bound endpoint / own request configuration
        by_endpoint = {e['endpoint_id']: e for e in self.endpoints}
        bound = row.get('bound_api_endpoint_id')
        config = row.get('api_request_config_json') or ''
        if bound:
            e = by_endpoint.get(bound)
            blob = ' '.join(str(e.get(k) or '') for k in ('name', 'method', 'url_raw', 'headers_json', 'query_params_json', 'body_raw')) if e else ''
            scenario.append({'kind': 'API', 'level': SCENARIO, 'state': LINKED, 'reference': 'endpoint:%s' % bound,
                             'label': ('API %s %s' % (e['method'], e['url_raw'])) if e else 'API endpoint %s (bound to this scenario)' % bound,
                             'basis': 'endpoint %s is bound to this test case row' % bound, 'text': (blob + ' ' + config)[:self.cap], 'text_checked': bool(blob or config),
                             'note': '' if e else 'The bound endpoint was not found among the active collections read.'})
        elif config.strip():
            scenario.append({'kind': 'API', 'level': SCENARIO, 'state': LINKED, 'reference': 'apiconfig:%s' % row.get('id'), 'label': 'API request configuration of this scenario',
                             'basis': 'the row stores its own API request configuration', 'text': config[:self.cap], 'text_checked': True, 'note': ''})

        # ---- SQL: own path / script named for this row
        path = (row.get('sql_script_path') or '').strip()
        if path:
            text, note = self._sql_text(path)
            scenario.append({'kind': 'SQL', 'level': SCENARIO, 'state': LINKED, 'reference': 'sqlpath:%s' % Path(path.replace('\\', '/')).name, 'label': 'SQL script %s' % Path(path.replace('\\', '/')).name,
                             'basis': 'the row stores this script path', 'text': text, 'text_checked': bool(text), 'note': note})
        for s in self.sql_scripts:
            if s['test_case_version_id'] != row.get('test_case_version_id'):
                continue
            names = [str(s.get(k) or '') for k in ('display_name', 'original_filename', 'repository_path')]
            stems = {Path(n.replace('\\', '/')).stem.lower() for n in names if n}
            named_for_row = str(key).lower() in stems and not any(a['reference'] == 'sql:%s' % s['id'] for a in scenario)
            text, note = ('', '')
            entry = {'kind': 'SQL', 'reference': 'sql:%s' % s['id'], 'label': 'SQL script %s' % (s.get('display_name') or s.get('original_filename'))}
            if named_for_row:
                text, note = self._sql_text(s.get('repository_path'))
                scenario.append(dict(entry, level=SCENARIO, state=LINKED, basis='the script file name is this row\'s test id', text=text, text_checked=bool(text), note=note))
            else:
                version.append(dict(entry, level=VERSION, state=VERSION_CONTEXT, basis='stored for the whole test case version, not for this scenario', text='', text_checked=False, note=''))

        # ---- explicit QA asset links (authoritative for kind)
        for link in self.links.get(row.get('id'), []):
            kind = link.get('kind')
            if kind in KINDS and not any(a['kind'] == kind for a in scenario):
                scenario.append({'kind': kind, 'level': SCENARIO, 'state': LINKED, 'reference': link.get('asset_reference') or '', 'label': '%s asset %s' % (kind, link.get('asset_reference')),
                                 'basis': 'explicit QA asset link', 'text': '', 'text_checked': False, 'note': ''})

        # ---- type recorded on the row with no record behind it
        declared = row.get('automation_type')
        if declared in KINDS and not any(a['kind'] == declared for a in scenario):
            scenario.append({'kind': declared, 'level': SCENARIO, 'state': DECLARED, 'reference': 'declared:%s' % row.get('id'), 'label': 'Automation type recorded on the test case (%s)' % declared,
                             'basis': 'the row says %s but no matching asset record or script was found' % declared, 'text': '', 'text_checked': False,
                             'note': 'Declared only: the script or endpoint could not be found, so it was not text-checked.'})

        # ---- version-level API collection (context only)
        version_endpoints = [e for e in self.endpoints if e['test_case_version_id'] == row.get('test_case_version_id')]
        if version_endpoints:
            blob = ' '.join(' '.join(str(e.get(k) or '') for k in ('name', 'method', 'url_raw', 'headers_json', 'query_params_json', 'body_raw')) for e in version_endpoints)
            bound_ids = {a['reference'] for a in scenario if a['kind'] == 'API'}
            version.append({'kind': 'API', 'level': VERSION, 'state': VERSION_CONTEXT, 'reference': 'collection:%s' % version_endpoints[0].get('collection_id'),
                            'label': 'API collection %s (%d endpoint(s), version level)' % (version_endpoints[0].get('collection') or '', len(version_endpoints)),
                            'basis': 'stored for the whole test case version, not for this scenario', 'text': blob[:self.cap], 'text_checked': True,
                            'note': 'Not counted as this scenario\'s automation%s.' % ('' if bound_ids else '; the scenario is not bound to an endpoint')})
        return {'scenario': scenario, 'version': version}

    @staticmethod
    def _predates_workbook(asset, row):
        """A Playwright asset belongs to its source and test id, not to one workbook version. When it was last modified before the row's workbook
        version was created, it may describe an earlier wording of the scenario; it stays linked, and the note says so."""
        modified, created = str(asset.get('modified_date') or ''), str(row.get('workbook_created_date') or '')
        if modified and created and modified < created:
            return 'The script was last modified (%s) before this workbook version was created (%s), so it may describe an earlier wording of the scenario.' % (modified[:10], created[:10])
        return ''

    def _sql_text(self, path):
        if not path or self.sql_text_reader is None:
            return '', 'The script file body was not opened.' if path else ''
        try:
            text = self.sql_text_reader(path) or ''
            return text[:self.cap], '' if text else 'The script file could not be read.'
        except Exception:
            return '', 'The script file could not be read.'
