"""Impact graph (Change -> Requirement -> System -> Test Case -> Automation) and dashboard totals.

The graph is built from findings only, so every node and edge carries the same
assurance and evidence as the table it summarises. Traversal uses a visited set,
so dependency cycles (A -> B -> A) cannot loop.
"""
import hashlib

from Core.change_impact.util import clip

SEVERITY_RANK = {'Low': 0, 'Medium': 1, 'High': 2, 'Critical': 3}
SOFT = frozenset(('SUGGESTED', 'HYPOTHESIS'))


def _ev_brief(evidence, limit=2, quote=160):
    seen, out = set(), []
    for e in evidence or []:
        if e.get('ref') and e['ref'] not in seen:
            seen.add(e['ref'])
            out.append({'ref': e.get('ref'), 'quote': clip(e.get('quote', ''), quote), 'where': e.get('location') or e.get('document', '')})
    return out[:limit]


def _norm(text):
    return ' '.join(str(text or '').casefold().split())


def build_graph(changes, findings, edges, risk, limits, plan=None):
    """Change -> Requirement -> System / Module -> Test Case -> Automation.

    One node per logical thing: several differences of one requirement are several changes that share one requirement node,
    administrative changes collapse into one node, a system or module is one node however many changes touch it, and
    system links hang from the requirement (not from every change) so lines do not pile up.
    """
    max_nodes = int(limits.get('max_graph_nodes', 250))
    nodes, links, order = {}, [], []

    def node(node_id, kind, label, force=False, **extra):
        if node_id not in nodes:
            if len(nodes) >= max_nodes and not force:
                return None
            nodes[node_id] = dict({'id': node_id, 'kind': kind, 'label': clip(label, 160)}, **extra)
            order.append(node_id)
        return node_id

    def link(a, b, label, assurance, finding_id='', evidence=None, basis='', **extra):
        if a in nodes and b in nodes and a != b and not any(l['source'] == a and l['target'] == b and l['label'] == label for l in links):
            links.append(dict({'source': a, 'target': b, 'label': label, 'assurance': assurance, 'soft': assurance in SOFT, 'finding_id': finding_id,
                               'evidence': _ev_brief(evidence), 'basis': basis}, **extra))

    system_by_name = {}

    def system_node(label, **extra):
        """One node per system or module, however its name is capitalised or spaced."""
        key = _norm(label)
        if key in system_by_name:
            return system_by_name[key]
        nid = node('S-' + label, 'system', label, **extra)
        if nid:
            system_by_name[key] = nid
        return nid

    functional = [c for c in changes if not c.get('administrative')]
    admin = [c for c in changes if c.get('administrative')]
    change_node = {}
    for change in functional:
        level = (risk.get(change['change_id']) or {}).get('level', 'Low')
        short = (change.get('deltas') or [{}])[0].get('summary') or change['description'].split(' - ', 1)[-1]
        label = '%s \u00b7 %s' % (change.get('reference') or change['change_id'], clip(short.rstrip('.'), 110))
        nid = node(change['change_id'], 'change', label, severity=level, assurance=change['assurance'], detail=change['description'],
                   change_id=change['change_id'], evidence=_ev_brief(change['evidence'][-1:]))
        if nid:   # None once the node limit is reached; a missing node must never be used as a parent later
            change_node[change['change_id']] = nid
    if admin:
        node('CHG-ADMIN', 'change', 'Administrative changes (%d)' % len(admin), severity='Low', assurance='CONFIRMED',
             detail='; '.join(clip(c['description'], 90) for c in admin[:6]), change_id='')
    req_nodes, req_by_change = {}, {}
    for f in findings:
        if f['kind'] != 'REQUIREMENT':
            continue
        p = f['payload']
        recorded = p.get('origin') != 'SOURCE_CLAUSE'
        ids = p.get('change_ids') or [f['change_id']]
        level = max_level_of([(risk.get(cid) or {}).get('level', 'Low') for cid in ids])
        nid = node('N-' + f['finding_id'], 'requirement', f['title'] + (' \u00b7 recorded' if recorded else ''), assurance=f['assurance'], finding_id=f['finding_id'],
                   severity=level, detail=p.get('description', ''), evidence=_ev_brief(f.get('evidence')))
        if not nid:
            continue
        for cid in ids:
            if cid in change_node:
                link(cid, nid, 'changes' if not recorded else 'maps to', f['assurance'], f['finding_id'], f.get('evidence'), 'The change is a difference inside this requirement.')
            if not recorded:
                req_by_change.setdefault(cid, []).append(nid)
        req_nodes[f['finding_id']] = (nid, f, ids)
    sys_ids = {}
    for f in findings:
        if f['kind'] != 'SYSTEM':
            continue
        p = f['payload']
        label = p.get('component') or f['title']
        certainty = p.get('certainty') or ('Potential Impact' if f['assurance'] == 'HYPOTHESIS' else 'Confirmed Impact')
        ids = p.get('change_ids') or [f['change_id']]
        nid = system_node(label, assurance=f['assurance'], finding_id=f['finding_id'], certainty=certainty,
                   detail='%s. %s%s' % (certainty, p.get('basis') or f.get('verification_status', ''), (' Requirements: %s.' % ', '.join(p['requirement_refs'])) if p.get('requirement_refs') else ''),
                   severity=max_level_of([(risk.get(cid) or {}).get('level', 'Low') for cid in ids]), evidence=_ev_brief(f.get('evidence')))
        if not nid:
            continue
        if certainty == 'Confirmed Impact' and nodes[nid].get('certainty') != 'Confirmed Impact':
            nodes[nid].update(certainty='Confirmed Impact', assurance=f['assurance'], detail='%s. %s' % (certainty, p.get('basis') or ''))
        sys_ids[label] = nid
        dep = p.get('dependency')
        soft = certainty != 'Confirmed Impact'
        rel = ('part of' if p.get('component_kind') == 'owner' else ((dep or {}).get('relationship', '').replace('_', ' ') or ('stated scope' if not soft else 'may affect')))
        for cid in ids:
            sources = req_by_change.get(cid) or ([change_node[cid]] if cid in change_node else [])
            for src in sources[:1]:
                link(src, nid, rel, f['assurance'], f['finding_id'], f.get('evidence'), p.get('basis') or f.get('verification_status', ''))
    adjacency = {}
    edge_support = {}
    for f in findings:
        dep = (f.get('payload') or {}).get('dependency') if f['kind'] == 'SYSTEM' else None
        for d in ([dep] if dep else []) + [m.get('dependency') for m in (f.get('payload') or {}).get('per_requirement', []) if isinstance(m, dict) and m.get('dependency')]:
            if d and d.get('key') and d['key'] not in edge_support:
                edge_support[d['key']] = (f.get('evidence'), (f['payload'].get('basis') or ''))
    for e in edges:
        a, b = system_node(e['source'], assurance=e['assurance']), system_node(e['target'], assurance=e['assurance'])
        if not (a and b):
            continue
        support = edge_support.get(e.get('key'), (None, ''))
        link(a, b, e['relationship'].replace('_', ' '), e['assurance'], evidence=support[0], basis=support[1] or 'Stated in the source text or confirmed by a reviewer.')
        adjacency.setdefault(a, []).append(b)
    cycle = _has_cycle(adjacency)
    test_nodes = {}
    for f in findings:
        if f['kind'] != 'TEST':
            continue
        p = f['payload']
        nid = node('T-%s' % p['test_case_id'], 'test', '%s' % (p['tc_number'] or p['test_case_id']), assurance=f['assurance'], detail=p['title'],
                   severity=(risk.get(f['change_id']) or {}).get('level', 'Low'), finding_id=f['finding_id'], test_case_id=p['test_case_id'],
                   evidence=_ev_brief(f.get('evidence')))
        if not nid:
            continue
        test_nodes[p['test_case_id']] = nid
        source = None
        for fid, (rid, rf, ids) in req_nodes.items():
            if f['change_id'] in ids and rf['payload'].get('origin') == 'SOURCE_CLAUSE':
                source = rid
            if rf['payload'].get('requirement_id') and rf['payload']['requirement_id'] == p.get('requirement_id'):
                source = rid
                break
        confirmed = f['assurance'] == 'CONFIRMED' and p['link_status'] == 'CONFIRMED'
        target = source or change_node.get(f['change_id'])
        if target:
            link(target, nid, 'covered by' if confirmed else ('may be covered by' if f['assurance'] != 'EVIDENCE_BACKED' else 'likely covered by'), f['assurance'], f['finding_id'], f.get('evidence'), p.get('reason', ''))
    for f in findings:
        if f['kind'] != 'AUTOMATION' or f['payload'].get('asset_kind') == 'None':
            continue
        p = f['payload']
        nid = node('A-' + p['asset_reference'], 'automation', p['asset_label'], assurance=f['assurance'], detail=p['reason'],
                   finding_id=f['finding_id'], severity=(risk.get(f['change_id']) or {}).get('level', 'Low'), evidence=_ev_brief(f.get('evidence')))
        if nid and p['test_case_id'] in test_nodes:
            link(test_nodes[p['test_case_id']], nid, 'automated by' if p['matched_terms'] else 'automation to review', f['assurance'], f['finding_id'], f.get('evidence'), p['reason'])
    recommendations = _recommendations(plan, node, link, nodes, req_by_change, change_node, limits)
    seen, unique = set(), []
    for l in links:
        k = (l['source'], l['target'], l['label'])
        if k not in seen:
            seen.add(k)
            unique.append(l)
    return {'nodes': [nodes[i] for i in order], 'links': unique, 'cycle_detected': cycle,
            'truncated': len(nodes) - recommendations['nodes'] >= max_nodes, 'max_nodes': max_nodes, 'recommendations': recommendations,
            'legend': {'solid': 'Confirmed or evidence-backed', 'dashed': 'Potential or suggested; needs verification',
                       'new_test': 'Recommended scenarios are suggestions, separate from existing test cases; none is created automatically'}}


RECOMMENDATION_GROUPS = (
    ('new_scenarios', 'New test scenarios', 'new_test', 'Recommended new test scenarios derived from the changed wording. They are suggestions, not existing test cases, and none is created automatically.'),
    ('integration', 'Integration regression', 'reg_test', 'Regression of a stated integration that the change request says must change. These are suggestions, not existing test cases.'),
    ('existing_regression', 'Existing-behaviour regression', 'reg_test', 'Unchanged requirements in the same module as a changed requirement; their behaviour must not break. These are suggestions, not existing test cases.'),
    ('other_regression', 'Other regression recommendations', 'reg_test', 'Other recommended regression checks. These are suggestions, not existing test cases.'))


def _group_key(category, is_new):
    c = (category or '').lower()
    if is_new:
        return 'new_scenarios'
    return 'integration' if c.startswith('integration') else ('existing_regression' if c.startswith('existing') else 'other_regression')


def _recommendations(plan, node, link, nodes, req_by_change, change_node, limits):
    """Every recommended regression item becomes its own node, inside an expandable group per kind.

    Items are de-duplicated by title within a group, never invented and never created as test cases. A group's count is the number of
    distinct items; a scenario that covers two requirements is one node with two relationships.
    """
    summary = {'total': 0, 'new_scenarios': 0, 'integration': 0, 'existing_regression': 0, 'other_regression': 0, 'duplicates_merged': 0, 'shown': 0, 'omitted': 0, 'nodes': 0}
    if not plan:
        return summary
    cap = int(limits.get('max_graph_scenarios', 200))
    grouped = {k[0]: {} for k in RECOMMENDATION_GROUPS}
    for is_new, items in ((True, plan.get('scenarios', [])), (False, plan.get('smoke', []))):
        for sc in items:
            bucket = grouped[_group_key(sc.get('category'), is_new)]
            if sc['title'] in bucket:
                summary['duplicates_merged'] += 1
                bucket[sc['title']]['change_ids'] = sorted(set(bucket[sc['title']]['change_ids']) | set(sc['change_ids']))
            else:
                bucket[sc['title']] = dict(sc)
    systems = [(n['id'], _norm(n['label'])) for n in nodes.values() if n['kind'] == 'system']
    before = len(nodes)
    for key, title, kind, about in RECOMMENDATION_GROUPS:
        items = list(grouped[key].values())
        summary[key] = len(items)
        summary['total'] += len(items)
        if not items:
            continue
        gid = node('G-' + key, 'rec_group', '%s (%d)' % (title, len(items)), force=True, assurance='SUGGESTED', category=key, detail=about, members=[], collapsed=True,
                   severity=max_level_of([i.get('risk') for i in items]), counts={})
        parents_of = {}
        for sc in items:
            nid = 'SC-' + hashlib.sha1(('%s|%s' % (key, sc['title'])).encode('utf-8')).hexdigest()[:10].upper()
            if summary['shown'] >= cap:
                summary['omitted'] += 1
                continue
            parents = []
            if key == 'new_scenarios':
                for cid in sc['change_ids']:
                    parents += [r for r in req_by_change.get(cid, []) if r not in parents]
                relation, why = 'needs', ('Derived from the changed wording of this requirement. ' + ('The document states the expected result.' if sc.get('basis') == 'SOURCE' else 'The document does not state the expected result; pending BA confirmation.'))
            elif key == 'integration':
                name = _norm(sc['title'].split(' integration')[0])
                parents = [i for i, label in systems if label == name] or [i for i, label in systems if name and name in label]
                relation, why = 'regression of', 'The change request states that this integration needs a change; the integration is regressed as a whole.'
            else:
                mods = [_norm(m) for m in sc.get('modules', [])]
                parents = [i for i, label in systems if any(m and (label == m or label.endswith(m) or m in label) for m in mods)]
                relation, why = 'regression of', 'This unchanged requirement belongs to the same module as a changed requirement and must not break.'
            if not parents:
                parents = [change_node[c] for c in sc['change_ids'] if c in change_node][:1]
            parents = [r for r in parents if r in nodes]   # drop parents that were never created (node limit)
            reqs = [{'id': r, 'label': nodes[r]['label']} for r in parents if nodes[r]['kind'] == 'requirement']
            scenario = {'title': sc['title'], 'category': sc.get('category', ''), 'priority': sc.get('priority', ''), 'risk': sc.get('risk', ''), 'execution': sc.get('execution', []),
                        'change_ids': sc['change_ids'], 'references': sc.get('related_references', []), 'reason': sc.get('reason', ''), 'expected': sc.get('expected', ''),
                        'basis': sc.get('basis', ''), 'modules': sc.get('modules', []), 'open_questions': sc.get('open_questions', []),
                        'evidence': _ev_brief(sc.get('evidence'), 3, 260)}
            made = node(nid, kind, sc['title'], force=True, assurance='SUGGESTED', severity=sc.get('risk') or 'Low', category=key, priority=sc.get('priority', ''),
                        execution=sc.get('execution', []), change_ids=sc['change_ids'], requirements=reqs, group=gid, scenario=scenario, detail=sc.get('reason', ''),
                        evidence=scenario['evidence'])
            nodes[gid]['members'].append(made)
            nodes[gid]['counts'][sc.get('priority', '')] = nodes[gid]['counts'].get(sc.get('priority', ''), 0) + 1
            summary['shown'] += 1
            for p in parents:
                link(p, made, relation, 'SUGGESTED', evidence=sc.get('evidence'), basis=why + ' Not an existing test case.', group=gid)
                parents_of.setdefault(p, []).append(sc)
        for p, scs in parents_of.items():
            link(p, gid, '%s (%d)' % ('needs' if key == 'new_scenarios' else 'regression of', len(scs)), 'SUGGESTED', evidence=[e for sc in scs[:2] for e in sc.get('evidence', [])[:1]],
                 basis='%d recommended item(s) from this node. Expand the group to see each one. Not existing test cases.' % len(scs), group=gid, aggregate=True)
    summary['nodes'] = len(nodes) - before
    return summary


def max_level_of(levels):
    ranked = [SEVERITY_RANK[l] for l in levels if l in SEVERITY_RANK]
    return [k for k, v in SEVERITY_RANK.items() if v == max(ranked)][0] if ranked else 'Low'


def _has_cycle(adjacency):
    state = {}

    def visit(n):
        state[n] = 1
        for m in adjacency.get(n, []):
            if state.get(m) == 1:
                return True
            if m not in state and visit(m):
                return True
        state[n] = 2
        return False
    return any(n not in state and visit(n) for n in list(adjacency))


def dashboard(changes, findings, regression, risk, gaps, questions=None):
    sys_rows = [f for f in findings if f['kind'] == 'SYSTEM']
    certain = lambda f: f['payload'].get('certainty') or ('Potential Impact' if f['assurance'] == 'HYPOTHESIS' else 'Confirmed Impact')
    firm_systems = {f['payload']['component'] for f in sys_rows if certain(f) == 'Confirmed Impact'}
    possible = {f['payload']['component'] for f in sys_rows if certain(f) != 'Confirmed Impact'} - firm_systems
    reqs = {f['finding_id'] for f in findings if f['kind'] == 'REQUIREMENT' and f['payload'].get('origin', 'SOURCE_CLAUSE') == 'SOURCE_CLAUSE'}
    test_rows = [f for f in findings if f['kind'] == 'TEST']
    tests = {f['payload']['test_case_id'] for f in test_rows if f['assurance'] in ('CONFIRMED', 'EVIDENCE_BACKED')}
    possible_tests = {f['payload']['test_case_id'] for f in test_rows if f['assurance'] == 'SUGGESTED'} - tests
    assets = {f['payload']['asset_reference'] for f in findings if f['kind'] == 'AUTOMATION' and f['payload'].get('asset_kind') != 'None'}
    functional = [c for c in changes if not c.get('administrative')]
    high = [c for c in functional if (risk.get(c['change_id']) or {}).get('level') in ('High', 'Critical')]
    by = lambda key, rows: {k: sum(1 for r in rows if key(r) == k) for k in sorted({key(r) for r in rows})}
    categories = (regression or {}).get('categories') or {}
    return {
        'totals': {'changes': len(changes), 'functional_changes': len(functional), 'administrative_changes': len(changes) - len(functional),
                   'impacted_systems': len(firm_systems), 'possible_systems_unverified': len(possible),
                   'unique_system_components': len(firm_systems) + len(possible), 'system_detail_findings': sum(f['payload'].get('merged_findings', 1) for f in sys_rows),
                   'requirements': len(reqs), 'test_cases': len(tests), 'existing_impacted_tests': len(tests), 'possible_tests': len(possible_tests),
                   'automation_assets': len(assets), 'coverage_gaps': len(gaps), 'high_risk_impacts': len(high),
                   'recommended_regression_tests': regression['recommended_total'], 'recommended_new_scenarios': categories.get('new_scenarios', 0),
                   'recommended_regression_scenarios': categories.get('new_scenarios', 0) + categories.get('existing_regression', 0) + categories.get('integration', 0),
                   'open_questions': len(questions or [])},
        'by_category': by(lambda c: c['category'], changes), 'by_change_type': by(lambda c: c['change_type'], changes),
        'by_risk': by(lambda c: (risk.get(c['change_id']) or {}).get('level', 'Low'), changes),
        'by_priority': regression['counts'], 'by_assurance': by(lambda f: f['assurance'], findings),
        'by_classification': by(lambda f: f['payload']['classification'], test_rows),
        'note': 'Existing impacted tests, recommended new scenarios and coverage gaps are separate measurements. No percentage is shown because completeness of coverage cannot be demonstrated from the available records.'}
