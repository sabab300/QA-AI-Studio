"""Change Impact Analysis orchestration.

Workflow: Select Change Source -> Analyze Changes -> Discover Dependencies ->
Map Impacted Requirements -> Identify Affected Tests -> Identify Automation ->
Select Regression Coverage -> Assess Risk -> Generate Report.

Reads Knowledge Hub, QA Engineering test case and QA Automation data; writes only ``ci_*`` rows.
"""
import json
import threading
import uuid
from pathlib import Path

from Core.change_impact import ai_support, automation, changes as change_engine, dependencies, graph, insight, mapping, regression, risk, semantics
from Core.change_impact.documents import SourceDocumentLoader
from Core.change_impact.repository import ChangeImpactRepository
from Core.change_impact.store import ScopeReader
from Core.change_impact.util import ImpactError, clip, digest, dumps, loads, now, stable_id

ENGINE_VERSION = '1.1'
STEPS = ('Select Change Source', 'Analyze Changes', 'Discover Dependencies', 'Map Impacted Requirements', 'Identify Affected Tests',
         'Identify Automation', 'Select Regression Coverage', 'Assess Risk', 'Generate Report')
MODES = ('VERSION_COMPARE', 'SINGLE_DOCUMENT')
REVIEW_DECISIONS = ('Accepted', 'Rejected', 'Investigate')
DEPENDENCY_DECISIONS = ('Confirmed', 'Rejected')


class Cancelled(Exception):
    pass


def unique_findings(findings, seen):
    """Guarantee one finding identifier per analysis (``seen`` is shared by every finding kind).

    A finding that repeats an earlier one exactly is dropped; a different finding that happens to derive the same
    identifier keeps its content and receives a deterministic new identifier. Nothing is lost silently.
    """
    out = []
    for finding in findings:
        content = digest([finding['kind'], finding.get('change_id', ''), finding.get('title', ''),
                          finding.get('payload'), finding.get('evidence')])
        fid = finding['finding_id']
        if fid in seen:
            if seen[fid] == content:
                continue
            number = 1
            while True:
                number += 1
                candidate = stable_id(fid.split('-')[0], fid, 'occurrence', number)
                if candidate not in seen:
                    break
            finding['finding_id'] = fid = candidate
        seen[fid] = content
        out.append(finding)
    return out


def load_policy(path=None):
    path = Path(path) if path else Path(__file__).resolve().parents[2] / 'Config' / 'change_impact_policy.json'
    policy = json.loads(path.read_text(encoding='utf-8'))
    if policy.get('version') != 1 or not isinstance(policy.get('limits'), dict):
        raise ImpactError('The Change Impact policy file is invalid.', 500, 'POLICY_INVALID')
    weights = policy.get('risk_weights') or {}
    if any((not isinstance(v, (int, float))) or v < 0 for v in weights.values()) or not any(v > 0 for v in weights.values()):
        raise ImpactError('Risk weights must be non-negative numbers with at least one above zero.', 500, 'POLICY_INVALID')
    return policy


class ChangeImpactService:

    def __init__(self, db=None, central=None, policy=None, ai=None, text_reader=None, run_inline=False,
                 access=None, repository=None, logger=None):
        if db is None:
            from Database.db_manager import DatabaseManager
            db = DatabaseManager()
        if central is None:
            try:
                from Shared.central_repository import CentralRepository
                central = CentralRepository()
            except Exception:
                central = None
        self.db, self.central = db, central
        self.policy = policy or load_policy()
        self.repo = repository or ChangeImpactRepository(db)
        self.ai = ai or ai_support.ImpactAi(timeout=int(self.policy['limits'].get('ai_timeout_seconds', 60)))
        self.text_reader = text_reader
        self.run_inline = run_inline
        self.access = access
        self.logger = logger
        self._flags = {}
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ request handling
    def _validate(self, request):
        if not isinstance(request, dict):
            raise ImpactError('Invalid request.', 400, 'INVALID_REQUEST')
        mode = request.get('mode')
        if mode not in MODES:
            raise ImpactError('Choose a change source mode: compare two versions, or analyse a single document.', 400, 'INVALID_MODE')
        try:
            changed = int(request.get('changed_source_id'))
        except (TypeError, ValueError):
            raise ImpactError('Select the changed source document.', 400, 'INVALID_SOURCE') from None
        baseline = None
        if mode == 'VERSION_COMPARE':
            try:
                baseline = int(request.get('baseline_source_id'))
            except (TypeError, ValueError):
                raise ImpactError('Select the baseline source document to compare against.', 400, 'INVALID_BASELINE') from None
            if baseline == changed:
                raise ImpactError('The baseline and the changed document must be different.', 400, 'SAME_DOCUMENT')
        elif request.get('baseline_source_id') not in (None, '', 0):
            raise ImpactError('A single-document analysis has no baseline.', 400, 'INVALID_BASELINE')
        if changed <= 0 or (baseline is not None and baseline <= 0):
            raise ImpactError('Invalid source document.', 400, 'INVALID_SOURCE')
        options = request.get('options') or {}
        if not isinstance(options, dict):
            raise ImpactError('Invalid options.', 400, 'INVALID_OPTIONS')
        depth = int(options.get('max_depth', self.policy['limits'].get('max_dependency_depth', 2)))
        if not 1 <= depth <= 3:
            raise ImpactError('Dependency depth must be between 1 and 3.', 400, 'INVALID_OPTIONS')
        title = str(request.get('title') or '').strip()
        if len(title) > 160:
            raise ImpactError('The title is longer than 160 characters.', 400, 'INVALID_TITLE')
        return {'mode': mode, 'changed_source_id': changed, 'baseline_source_id': baseline,
                'options': {'use_ai': bool(options.get('use_ai', False)), 'max_depth': depth}, 'title': title}

    def _loader(self):
        return SourceDocumentLoader(self.db, self.central, self.text_reader, self.policy['limits'])

    def start(self, request, actor):
        """Validate, create the record and start the run. Returns the header immediately."""
        req = self._validate(request)
        loader = self._loader()
        changed = loader.load(req['changed_source_id'])
        baseline = loader.load(req['baseline_source_id']) if req['baseline_source_id'] else None
        for doc in [d for d in (changed, baseline) if d]:
            if self.access and not self.access(actor, doc['owner']):
                raise ImpactError('You do not have access to the document "%s".' % doc['file_name'], 403, 'ACCESS_DENIED')
        if baseline:
            a, b = baseline['owner_ids'], changed['owner_ids']
            if a['document_type_id'] != b['document_type_id']:
                raise ImpactError('Both documents must be the same Document Type of the same Knowledge (for example SRS v1 and SRS v2).', 400, 'COMPARE_SCOPE_MISMATCH')
            if a['document_version_id'] == b['document_version_id']:
                raise ImpactError('Both documents belong to the same version. Pick two different versions.', 400, 'SAME_VERSION')
        analysis_id = 'CIA-' + uuid.uuid4().hex[:12].upper()
        title = req['title'] or ('%s: %s' % ('Version comparison' if baseline else 'Change request', changed['file_name']))
        config = {'options': req['options'], 'engine_version': ENGINE_VERSION, 'policy_hash': digest(self.policy)}
        self.repo.create_analysis({
            'id': analysis_id, 'title': title, 'mode': req['mode'], 'changed_source_id': changed['source_id'],
            'baseline_source_id': baseline['source_id'] if baseline else None,
            'changed_document_version_id': changed['document_version_id'],
            'baseline_document_version_id': baseline['document_version_id'] if baseline else None,
            'request_key': digest([actor.get('id'), req['mode'], req['changed_source_id'], req['baseline_source_id'], req['options']]),
            'config': config, 'ai_requested': req['options']['use_ai'], 'created_by': actor.get('id'),
            'created_by_name': actor.get('username') or actor.get('full_name') or ''})
        self.repo.log_event(analysis_id, 'ANALYSIS_STARTED', actor, {'mode': req['mode'], 'changed_source_id': changed['source_id'],
                                                                        'baseline_source_id': req['baseline_source_id'], 'options': req['options']})
        with self._lock:
            self._flags[analysis_id] = threading.Event()
        if self.run_inline:
            self._run(analysis_id, actor, req)
        else:
            threading.Thread(target=self._run, args=(analysis_id, actor, req), name='ci-' + analysis_id, daemon=True).start()
        return self.repo.header(analysis_id)

    def cancel(self, analysis_id, actor):
        header = self.repo.header(analysis_id)
        if header['status'] not in ('Queued', 'Running'):
            raise ImpactError('This analysis has already finished.', 409, 'NOT_RUNNING')
        with self._lock:
            flag = self._flags.get(analysis_id)
        if flag is None:
            raise ImpactError('This analysis is not running in this server process.', 409, 'NOT_RUNNING')
        flag.set()
        try:
            from Core.ai_platform.gateway import CANCELS
            CANCELS.cancel('ci-' + analysis_id)
        except Exception:
            pass
        self.repo.log_event(analysis_id, 'ANALYSIS_CANCEL_REQUESTED', actor, {})
        return {'status': 'cancelling'}

    # ------------------------------------------------------------------ pipeline
    def _run(self, analysis_id, actor, req):
        flag = self._flags.get(analysis_id) or threading.Event()
        progress = {'steps': [{'name': s, 'status': 'Pending', 'detail': ''} for s in STEPS], 'current': STEPS[0], 'percent': 0}

        def step(index, status, detail=''):
            progress['steps'][index].update(status=status, detail=detail)
            progress['current'] = STEPS[index]
            progress['percent'] = int(100 * sum(1 for s in progress['steps'] if s['status'] in ('Done', 'Skipped')) / len(STEPS))
            self.repo.set_progress(analysis_id, progress, 'Running')

        def check():
            if flag.is_set():
                raise Cancelled()

        try:
            result = self._pipeline(analysis_id, actor, req, step, check, progress)
            snapshot, rows_changes, rows_findings, ai_info = result
            progress['percent'] = 100
            progress['current'] = 'Completed'
            self.repo.set_ai_info(analysis_id, ai_info.get('provider'), ai_info.get('model'), ai_info.get('status'))
            self.repo.finalize(analysis_id, snapshot, rows_changes, rows_findings, progress)
            self.repo.log_event(analysis_id, 'ANALYSIS_COMPLETED', actor, {'changes': len(rows_changes), 'findings': len(rows_findings)})
        except Cancelled:
            self.repo.terminate(analysis_id, 'Cancelled', 'CANCELLED', 'Cancelled by the user.', progress)
            self.repo.log_event(analysis_id, 'ANALYSIS_CANCELLED', actor, {})
        except ImpactError as error:
            self.repo.terminate(analysis_id, 'Failed', error.code, error.message, progress)
            self.repo.log_event(analysis_id, 'ANALYSIS_FAILED', actor, {'code': error.code})
        except Exception as error:  # never expose a stack trace or internal path to the user
            if self.logger:
                self.logger.exception('Change impact analysis %s failed', analysis_id)
            self.repo.terminate(analysis_id, 'Failed', 'INTERNAL_ERROR',
                                'The analysis stopped because of an unexpected error (%s). Nothing was changed. Try again or contact an administrator.' % type(error).__name__, progress)
            self.repo.log_event(analysis_id, 'ANALYSIS_FAILED', actor, {'code': 'INTERNAL_ERROR', 'type': type(error).__name__})
        finally:
            with self._lock:
                self._flags.pop(analysis_id, None)

    def _pipeline(self, analysis_id, actor, req, step, check, progress):
        policy = dict(self.policy)
        policy['limits'] = dict(policy['limits'], max_dependency_depth=req['options']['max_depth'])
        request_id = 'ci-' + analysis_id
        reader = ScopeReader(self.db, policy['limits'])
        warnings = []

        step(0, 'Running')
        loader = self._loader()
        changed = loader.load(req['changed_source_id'])
        baseline = loader.load(req['baseline_source_id']) if req['baseline_source_id'] else None
        for doc in (changed, baseline):
            if doc and doc['truncated']:
                warnings.append('"%s" is longer than the analysis limit; only the first %d characters were analysed.' % (doc['file_name'], doc['characters']))
        step(0, 'Done', '%d document(s) loaded' % (2 if baseline else 1))
        check()

        step(1, 'Running')
        ai_stats = {'requested': req['options']['use_ai'], 'status': 'Not requested', 'provider': '', 'model': ''}
        if baseline:
            detection = change_engine.compare_versions(baseline, changed, policy)
        else:
            detection = change_engine.extract_crf_changes(changed, policy)
        found = detection['changes']
        if baseline:
            smaller = min(detection.get('baseline_clauses', 0), detection.get('changed_clauses', 0))
            if smaller >= 5 and detection.get('unchanged_clauses', 0) < 0.05 * smaller:
                warnings.append('The two documents share almost no identical text (%d of %d clauses). They may not be two versions of the same document; '
                                'check that the baseline is the right file before relying on this result.' % (detection.get('unchanged_clauses', 0), smaller))
        if req['options']['use_ai'] and not baseline:
            check()
            extra, stats = ai_support.ai_change_candidates(self.ai, changed, found, policy, request_id, actor.get('username', ''))
            found = found + extra
            ai_stats.update(candidates=stats, provider=self.ai.provider, model=self.ai.model,
                            status='Used' if not stats['error'] else 'Unavailable: ' + stats['error'])
            if stats['error']:
                warnings.append('AI change detection was not available (%s). The rule-based result is shown.' % stats['error'])
        elif req['options']['use_ai']:
            ai_stats['status'] = 'Not used for version comparison (the difference is computed directly); available for explanations'
        lex = semantics._lexicon(policy)
        found = [change_engine.complete_change(c, policy, lex) for c in found]
        found = change_engine.ensure_unique_change_ids(found)[:int(policy['limits']['max_changes'])]
        if detection['truncated']:
            warnings.append('More changes were detected than the limit of %d; the list is truncated.' % int(policy['limits']['max_changes']))
        functional = [c for c in found if c.get('functional')]
        step(1, 'Done', '%d unique change(s): %d functional, %d administrative' % (len(found), len(functional), len(found) - len(functional)))
        check()

        owner = changed['owner']
        owner_ids = changed['owner_ids']
        pool_text = changed['text'] + ('\n' + baseline['text'] if baseline else '')
        analysis_insight = insight.analyse_document(baseline, changed, found, policy, lex)
        questions = analysis_insight['questions']
        signals = analysis_insight['signals']
        step(2, 'Running')
        catalog = dependencies.SystemCatalog(self.db).systems()
        confirmed = dependencies.confirmed_dependencies(self.db)
        finding_ids = {}
        systems, edges = dependencies.discover(found, owner, catalog, confirmed, policy, analysis_insight, pool_text)
        systems = unique_findings(systems, finding_ids)
        potential = sum(1 for x in systems if x['payload'].get('certainty') == dependencies.POTENTIAL_IMPACT)
        step(2, 'Done', '%d impacted component(s) from %d detailed finding(s) (%d potential), %d stated dependency(ies)' % (len(systems), sum(x['payload'].get('merged_findings', 1) for x in systems), potential, len(edges)))
        check()

        step(3, 'Running')
        documents_loaded = [changed] + ([baseline] if baseline else [])
        req_findings, req_map = mapping.map_requirements(found, documents_loaded, reader, owner_ids, policy, analysis_insight)
        req_findings = unique_findings(req_findings, finding_ids)
        step(3, 'Done', '%d requirement row(s)' % len(req_findings))
        check()

        step(4, 'Running')
        source_ids = [d['source_id'] for d in documents_loaded]
        test_findings, test_info = mapping.build_test_impact(found, req_map, systems, reader, documents_loaded, owner_ids, policy,
                                                             detection.get('unchanged_norms') if baseline else None, source_ids, pool_text)
        test_findings = unique_findings(test_findings, finding_ids)
        step(4, 'Done', '%d test impact row(s)' % len(test_findings))
        check()

        step(5, 'Running')
        auto_findings, auto_summary = automation.analyse(test_findings, found, reader, self.central, policy)
        auto_findings = unique_findings(auto_findings, finding_ids)
        step(5, 'Done', '%d automation row(s)' % len(auto_findings))
        check()

        step(6, 'Running')
        conflict_ids = {}
        for q in questions:
            if q['type'] in ('CONTRADICTION', 'AMBIGUITY'):
                for cid in q.get('related_changes', []):
                    conflict_ids.setdefault(cid, []).append(q['id'])
        raw_scenarios, modules_by_change, scenarios_by_change = [], {}, {}
        for c in functional:
            modules = (signals.get(c['change_id']) or {}).get('modules') or []
            modules_by_change[c['change_id']] = modules
            own = insight.scenarios_for_change(c, modules, conflict_ids.get(c['change_id'], []))
            scenarios_by_change[c['change_id']] = own
            raw_scenarios += own
        scenarios = insight.merge_scenarios(raw_scenarios)
        gaps = unique_findings(mapping.build_gaps(found, req_map, test_info, owner, changed, policy, scenarios_by_change), finding_ids)
        risks = self._risk(found, systems, test_findings, auto_summary, reader, policy, signals, test_info, analysis_insight)
        for finding in systems + req_findings + test_findings + auto_findings + gaps:
            level = (risks.get(finding['change_id']) or {}).get('level')
            if finding['kind'] == 'REQUIREMENT' and finding['payload'].get('change_ids'):
                level = risk.max_level([(risks.get(cid) or {}).get('level') for cid in finding['payload']['change_ids']])
            if level:
                finding['severity'] = level
        history = reader.run_history(sorted({f['payload']['test_case_id'] for f in test_findings}))
        unmapped = sum(1 for c in functional if c['change_type'] != 'REMOVED' and not test_info.get(c['change_id'], {}).get('firm_tests')
                       and not test_info.get(c['change_id'], {}).get('confirmed_tests'))
        reg = regression.select(test_findings, auto_summary, risks, history, policy, unmapped, len(functional))
        unchanged_groups = insight.unchanged_requirement_groups(detection, found, changed) if baseline else []
        plan = regression.plan_scenarios(found, risks, scenarios, unchanged_groups, analysis_insight['facts'].get('dependencies', []),
                                         test_findings, auto_summary, gaps, modules_by_change)
        reg.update(scenarios=plan['scenarios'], smoke=plan['smoke'], automation_candidates=plan['automation_candidates'], categories=plan['categories'])
        step(6, 'Done', '%d existing test(s), %d recommended scenario(s), %d coverage gap(s)' % (len(reg['items']), len(plan['scenarios']) + len(plan['smoke']), len(gaps)))
        check()

        step(7, 'Running')
        findings = systems + req_findings + test_findings + auto_findings + gaps
        self._finalize_changes(found, systems, signals, conflict_ids)
        step(7, 'Done', '%d change(s) risk-rated' % len(risks))
        check()

        step(8, 'Running')
        notes = list(reader.notes)
        if not found:
            notes.append('No changes were detected between the selected documents.' if baseline else
                         'No change statements or requirement statements were found in the selected document.')
        if baseline and detection.get('raw_differences', 0) > len(found):
            notes.append('%d raw difference(s) were consolidated into %d unique change(s): repeated occurrences of the same change are reported once with every source reference.'
                         % (detection['raw_differences'], len(found)))
        documents = [self._doc_record(changed, 'changed' if baseline else 'proposed')]
        if baseline:
            documents.insert(0, self._doc_record(baseline, 'baseline'))
        unchanged_apis = insight.unchanged_api_details(detection) if baseline else []
        changed_text = ' '.join((c.get('original_text') or '') + ' ' + (c.get('modified_text') or '') for c in found).lower()
        unchanged_apis = [a for a in unchanged_apis if a['name'].lower() not in changed_text]
        snapshot = {
            'schema_version': 1, 'engine_version': ENGINE_VERSION,
            'analysis': {'id': analysis_id, 'mode': req['mode'], 'created_at': now(), 'created_by': actor.get('username') or '',
                         'options': req['options'], 'policy': policy, 'policy_hash': digest(self.policy)},
            'documents': documents, 'owner': owner, 'warnings': warnings, 'scope_notes': notes,
            'detection': {k: v for k, v in detection.items() if k not in ('changes', 'unchanged_norms', 'unchanged_clause_list')},
            'changes': found, 'findings': findings, 'dependencies': edges, 'risk': risks, 'regression': reg,
            'insight': {'questions': questions, 'facts': analysis_insight['facts'], 'statements_read': analysis_insight['statements'],
                        'no_evidence': {'apis': unchanged_apis,
                                        'requirements': [{'context': g['context'], 'quote': g['quote'], 'location': g['location']} for g in unchanged_groups[:40]]},
                        'summary': self._summary(found, risks, systems, test_findings, plan, gaps, questions, detection, baseline is not None)},
            'dashboard': graph.dashboard(found, findings, reg, risks, gaps, questions),
            'graph': graph.build_graph(found, findings, edges, risks, policy['limits'], plan),
            'ai': ai_stats,
            'limitations': self._limitations(baseline is not None, bool(reader.notes)),
        }
        step(8, 'Done', 'Snapshot frozen')
        rows_findings = findings
        return snapshot, found, rows_findings, {'provider': ai_stats.get('provider', ''), 'model': ai_stats.get('model', ''), 'status': ai_stats['status']}

    @staticmethod
    def _summary(found, risks, systems, test_findings, plan, gaps, questions, detection, compare):
        functional = [c for c in found if c.get('functional')]
        levels = lambda ls: {k: sum(1 for c in ls if (risks.get(c['change_id']) or {}).get('level') == k) for k in ('Critical', 'High', 'Medium', 'Low')}
        components = {x['payload']['component'] for x in systems if x['payload'].get('certainty') == dependencies.CONFIRMED_IMPACT}
        possible = {x['payload']['component'] for x in systems if x['payload'].get('certainty') == dependencies.POTENTIAL_IMPACT} - components
        existing = {f['payload']['test_case_id'] for f in test_findings if f['assurance'] in ('CONFIRMED', 'EVIDENCE_BACKED')}
        possible_tests = {f['payload']['test_case_id'] for f in test_findings if f['assurance'] == 'SUGGESTED'} - existing
        return {'raw_differences': detection.get('raw_differences', len(found)), 'unique_changes': len(found), 'functional_changes': len(functional),
                'administrative_changes': len(found) - len(functional), 'confirmed_systems': len(components), 'potential_systems': len(possible),
                'risk_functional': levels(functional), 'risk_all': levels(found), 'existing_impacted_tests': len(existing), 'possible_tests': len(possible_tests),
                'recommended_new_scenarios': len(plan['scenarios']), 'recommended_regression_scenarios': len(plan['scenarios']) + len(plan['smoke']),
                'coverage_gaps': len(gaps), 'open_questions': len(questions), 'conflicts': sum(1 for q in questions if q['type'] == 'CONTRADICTION'),
                'compare': compare}

    @staticmethod
    def _finalize_changes(found, systems, signals, conflict_ids):
        """Per-change system impact text (Confirmed / Potential, each separated) and the technical impact that names APIs, validations and interfaces."""
        for change in found:
            rows = dependencies.for_change(systems, change['change_id'])
            if change.get('administrative'):
                change['system_impact'] = []
                continue
            impact = []
            for f in rows:
                p = f['payload']
                certainty = p.get('certainty') or 'Potential Impact'
                impact.append({'component': p['component'], 'kind': p.get('component_kind', ''), 'certainty': certainty, 'finding_id': f['finding_id'],
                               'text': '%s: %s' % (p['component'], p.get('basis') or f.get('verification_status', '')),
                               'basis': 'SOURCE' if certainty == 'Confirmed Impact' else 'DERIVED'})
            change['system_impact'] = impact
            tech = list(change.get('technical_impact') or [])
            for f in rows:
                p = f['payload']
                if p.get('component_kind') in ('interface', 'api'):
                    tech.append({'text': '%s (%s): %s' % (p['component'], p['component_kind'], p.get('basis', '')), 'basis': 'SOURCE'})
            change['technical_impact'] = tech
            change['open_question_ids'] = conflict_ids.get(change['change_id'], [])
            change['signals'] = {k: v for k, v in (signals.get(change['change_id']) or {}).items() if k in ('modules', 'areas', 'conflicts', 'open_questions', 'blocking', 'integration')}

    @staticmethod
    def _doc_record(doc, role):
        return {'role': role, 'source_id': doc['source_id'], 'file_name': doc['file_name'], 'sha256': doc['sha256'],
                'document_version_id': doc['document_version_id'], 'owner': doc['owner'], 'characters': doc['characters'],
                'clauses': len(doc['clauses']), 'truncated': doc['truncated']}

    @staticmethod
    def _limitations(compare, notes):
        base = ['Impacts are possible, not assumed. Anything not labelled Confirmed needs verification by a QA engineer.',
                'Dependencies are reported only when the documents state them or a reviewer confirmed them earlier; co-mentions are hypotheses.',
                'A test case counts as impacted only with evidence: a source relationship plus matching content (Confirmed), strong content evidence in the same Knowledge (Evidence-backed), or wording overlap alone (Suggestion).',
                'Statements the engine derived (impact wording, scenarios, questions) are labelled as derived; conflicts between statements are reported for business clarification and are never resolved automatically.',
                'The regression list is a recommended subset and does not prove complete coverage.',
                'Risk scores are policy-weighted prioritisation aids, not probabilities.']
        if not compare:
            base.append('A single change request has no baseline, so original wording is not shown and nothing is compared.')
        return base

    def _risk(self, changes_list, systems, test_findings, auto_summary, reader, policy, signals=None, test_info=None, analysis_insight=None):
        history = reader.run_history(sorted({f['payload']['test_case_id'] for f in test_findings}))
        out = {}
        signals = signals or {}
        test_info = test_info or {}
        stated = len((analysis_insight or {}).get('facts', {}).get('dependencies', []))
        for change in changes_list:
            cid = change['change_id']
            sys_rows = dependencies.for_change(systems, cid)
            tests = {f['payload']['test_case_id'] for f in test_findings if f['change_id'] == cid}
            confirmed = {f['payload']['test_case_id'] for f in test_findings if f['change_id'] == cid and f['assurance'] == 'CONFIRMED' and f['payload']['link_status'] == 'CONFIRMED'}
            firm = (test_info.get(cid) or {}).get('firm_tests', set()) | confirmed
            runs = [r for t in tests for r in history.get(t, [])]
            ctx = {'dependencies': sum(1 for s in sys_rows if s['payload'].get('dependency') and s['assurance'] in ('EVIDENCE_BACKED', 'CONFIRMED')),
                   'indirect': sum(1 for s in sys_rows if s['impact_class'] == 'Indirect'), 'stated_dependencies': stated,
                   'hypotheses': sum(1 for s in sys_rows if s['assurance'] == 'HYPOTHESIS'),
                   'tests': len(tests), 'confirmed_tests': len(confirmed), 'firm_tests': len(firm),
                   'automated_tests': sum(1 for t in tests if auto_summary.get(t, {}).get('has_automation')),
                   'history_runs': len(runs), 'history_failed': sum(1 for r in runs if r.get('status') in ('Failed', 'Error')),
                   'signal': signals.get(cid) or {}}
            out[cid] = risk.assess(change, ctx, policy)
        return out

    # ------------------------------------------------------------------ reading results
    def result(self, analysis_id):
        header = self.repo.header(analysis_id)
        if header['status'] not in ('Completed',):
            return {'header': header, 'snapshot': None, 'review': None, 'explanations': []}
        snapshot = self.repo.snapshot(analysis_id)
        return {'header': header, 'snapshot': snapshot, 'review': self.review_overlay(analysis_id),
                'explanations': self.repo.explanations(analysis_id)}

    def review_overlay(self, analysis_id):
        events = self.repo.review_events(analysis_id)
        findings, deps, approvals, comments = {}, {}, [], []
        for e in events:
            rec = {'decision': e['decision'], 'comment': e['comment'], 'by': e['actor_name'], 'at': e['created_at'], 'event_id': e['id']}
            if e['event_type'] == 'FINDING_REVIEW':
                findings.setdefault(e['finding_id'], {'state': 'Pending', 'history': []})
                findings[e['finding_id']]['state'] = e['decision']
                findings[e['finding_id']].update(comment=e['comment'], by=e['actor_name'], at=e['created_at'])
                findings[e['finding_id']]['history'].append(rec)
            elif e['event_type'] == 'DEPENDENCY_CONFIRM':
                deps[e['finding_id']] = rec
            elif e['event_type'] == 'REGRESSION_APPROVAL':
                approvals.append(rec | {'test_case_ids': e['detail'].get('test_case_ids', []), 'scope': e['detail'].get('scope', '')})
            else:
                comments.append(rec | {'finding_id': e['finding_id']})
        latest = approvals[-1] if approvals else None
        return {'findings': findings, 'dependency_decisions': deps, 'regression_approval': latest, 'regression_approval_history': approvals,
                'comments': comments, 'all_events': events, 'pending_note': 'AI and rule-based findings stay Pending until a person reviews them.'}

    # ------------------------------------------------------------------ human review
    def _guard(self, analysis_id):
        header = self.repo.header(analysis_id)
        if header['status'] != 'Completed':
            raise ImpactError('Only a completed analysis can be reviewed.', 409, 'NOT_COMPLETED')
        return header

    @staticmethod
    def _comment(comment, required):
        comment = str(comment or '').strip()
        if len(comment) > 2000:
            raise ImpactError('The comment is longer than 2000 characters.', 400, 'INVALID_COMMENT')
        if required and len(comment) < 3:
            raise ImpactError('Add a short comment explaining the decision.', 400, 'COMMENT_REQUIRED')
        return comment

    def review_finding(self, analysis_id, finding_id, decision, comment, actor):
        self._guard(analysis_id)
        if decision not in REVIEW_DECISIONS:
            raise ImpactError('Decision must be Accepted, Rejected or Investigate.', 400, 'INVALID_DECISION')
        if finding_id not in self.repo.finding_ids(analysis_id):
            raise ImpactError('That finding does not belong to this analysis.', 404, 'FINDING_NOT_FOUND')
        comment = self._comment(comment, decision in ('Rejected', 'Investigate'))
        event_id = self.repo.add_review_event(analysis_id, 'FINDING_REVIEW', finding_id, decision, comment, actor)
        self.repo.log_event(analysis_id, 'FINDING_REVIEWED', actor, {'finding_id': finding_id, 'decision': decision})
        return {'event_id': event_id, 'finding_id': finding_id, 'decision': decision}

    def confirm_dependency(self, analysis_id, finding_id, decision, comment, actor):
        self._guard(analysis_id)
        if decision not in DEPENDENCY_DECISIONS:
            raise ImpactError('Decision must be Confirmed or Rejected.', 400, 'INVALID_DECISION')
        row = self.repo.finding_ids(analysis_id).get(finding_id)
        dependency = (loads(row['payload_json'], {}) or {}).get('dependency') if row else None
        if not row or row['kind'] != 'SYSTEM' or not dependency:
            raise ImpactError('Only a stated system dependency can be confirmed or rejected.', 400, 'NOT_A_DEPENDENCY')
        comment = self._comment(comment, True)
        event_id = self.repo.add_review_event(analysis_id, 'DEPENDENCY_CONFIRM', finding_id, decision, comment, actor, {'key': dependency['key']})
        self.repo.log_event(analysis_id, 'DEPENDENCY_' + decision.upper(), actor, {'finding_id': finding_id, 'key': dependency['key']})
        return {'event_id': event_id, 'finding_id': finding_id, 'decision': decision}

    def approve_regression(self, analysis_id, test_case_ids, comment, actor):
        self._guard(analysis_id)
        snapshot = self.repo.snapshot(analysis_id)
        valid = {i['test_case_id']: i for i in snapshot['regression']['items']}
        try:
            ids = sorted({int(x) for x in test_case_ids})
        except (TypeError, ValueError):
            raise ImpactError('Invalid test case selection.', 400, 'INVALID_SELECTION') from None
        if not ids:
            raise ImpactError('Select at least one regression test to approve.', 400, 'EMPTY_SELECTION')
        unknown = [i for i in ids if i not in valid]
        if unknown:
            raise ImpactError('Some selected tests are not part of this analysis\' recommendations.', 400, 'INVALID_SELECTION')
        obsolete = [i for i in ids if valid[i]['classification'] == 'Obsolete candidate']
        if obsolete:
            raise ImpactError('Obsolete candidates cannot be approved for regression; review them in QA Engineering instead.', 400, 'INVALID_SELECTION')
        comment = self._comment(comment, False)
        event_id = self.repo.add_review_event(analysis_id, 'REGRESSION_APPROVAL', '', 'Approved', comment, actor,
                                              {'test_case_ids': ids, 'count': len(ids), 'priorities': sorted({valid[i]['priority'] for i in ids})})
        self.repo.log_event(analysis_id, 'REGRESSION_APPROVED', actor, {'count': len(ids)})
        return {'event_id': event_id, 'approved': len(ids)}

    def add_comment(self, analysis_id, finding_id, comment, actor):
        self._guard(analysis_id)
        if finding_id and finding_id not in self.repo.finding_ids(analysis_id):
            raise ImpactError('That finding does not belong to this analysis.', 404, 'FINDING_NOT_FOUND')
        comment = self._comment(comment, True)
        return {'event_id': self.repo.add_review_event(analysis_id, 'COMMENT', finding_id, '', comment, actor)}

    # ------------------------------------------------------------------ explanations
    def explain(self, analysis_id, subject_type, subject_id, actor, refresh=False):
        self._guard(analysis_id)
        if subject_type not in ('finding', 'change', 'risk'):
            raise ImpactError('Choose a finding, a change or a risk to explain.', 400, 'INVALID_SUBJECT')
        existing = [e for e in self.repo.explanations(analysis_id) if e['subject_type'] == subject_type and e['subject_id'] == subject_id]
        if existing and not refresh:
            return existing[-1]
        snapshot = self.repo.snapshot(analysis_id)
        finding = change = assessment = None
        if subject_type == 'finding':
            finding = next((f for f in snapshot['findings'] if f['finding_id'] == subject_id), None)
            if not finding:
                raise ImpactError('That finding does not belong to this analysis.', 404, 'FINDING_NOT_FOUND')
            change = next((c for c in snapshot['changes'] if c['change_id'] == finding['change_id']), None)
            assessment = snapshot['risk'].get(finding['change_id'])
        else:
            change = next((c for c in snapshot['changes'] if c['change_id'] == subject_id), None)
            if not change:
                raise ImpactError('That change does not belong to this analysis.', 404, 'CHANGE_NOT_FOUND')
            assessment = snapshot['risk'].get(subject_id)
        # Only facts from this frozen analysis are used: no new retrieval, so source permissions cannot be bypassed.
        outcome = ai_support.explain(self.ai, 'risk' if subject_type == 'risk' else subject_type, finding, change, assessment,
                                     request_id='ci-explain-' + analysis_id, username=actor.get('username', ''))
        record = {'id': 'EXP-' + uuid.uuid4().hex[:12].upper(), 'analysis_id': analysis_id, 'subject_type': subject_type, 'subject_id': subject_id,
                  'mode': outcome['mode'], 'text': outcome['text'] + ((' Recommended action: ' + outcome['recommended_action']) if outcome.get('recommended_action') else ''),
                  'source_refs': outcome['source_refs'], 'insufficient_evidence': bool(outcome.get('insufficient')),
                  'provider': outcome.get('provider', ''), 'model': outcome.get('model', ''), 'created_by': actor.get('id')}
        self.repo.add_explanation(record)
        self.repo.log_event(analysis_id, 'EXPLANATION_GENERATED', actor, {'subject': subject_type, 'subject_id': subject_id, 'mode': outcome['mode']})
        return self.repo.explanations(analysis_id)[-1] | {'missing_evidence': outcome.get('missing_evidence', []), 'note': outcome.get('note', '')}

    # ------------------------------------------------------------------ reporting
    def report(self, analysis_id):
        from Core.change_impact import reports
        header = self.repo.header(analysis_id)
        if header['status'] != 'Completed':
            raise ImpactError('Only a completed analysis has a report.', 409, 'NOT_COMPLETED')
        snapshot = self.repo.snapshot(analysis_id)
        return reports.build_report(header, snapshot, self.review_overlay(analysis_id), self.repo.explanations(analysis_id))

    def export(self, analysis_id, fmt, actor):
        from Core.change_impact import reports
        report = self.report(analysis_id)
        if fmt == 'xlsx':
            data, name, mime = reports.to_excel(report), 'Change_Impact_%s.xlsx' % analysis_id, 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        elif fmt == 'pdf':
            data, name, mime = reports.to_pdf(report), 'Change_Impact_%s.pdf' % analysis_id, 'application/pdf'
        else:
            raise ImpactError('Export format must be xlsx or pdf.', 400, 'INVALID_FORMAT')
        self.repo.log_event(analysis_id, 'EXPORT_' + fmt.upper(), actor, {'bytes': len(data)})
        return data, name, mime
