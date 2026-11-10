"""Change identification: Baseline -> Changed comparison and single-CRF extraction.

Every change carries a stable ID and verbatim source evidence. For a standalone
CRF the original wording is *not available* and is never invented.
"""
import difflib
import re
from collections import defaultdict

from Core.change_impact import semantics
from Core.change_impact.util import clip, jaccard, normalize, stable_id, token_set, tokens, digest

CATEGORY_ORDER = ['User Permission', 'API Contract', 'Integration', 'Database Behavior', 'Validation',
                  'Error Handling', 'Workflow', 'Data Field', 'Non-Functional Requirement', 'Business Rule',
                  'Functional Requirement', 'Administrative']
NUMBER = re.compile(r'\b\d+(?:[.,]\d+)?\b')
ORIGINAL_NOT_AVAILABLE = 'Not available: a standalone change request does not contain the baseline wording.'


def categorize(text, policy):
    """Rule-based category with the matched keywords as evidence (transparent, no AI).

    Keywords match whole words (a stem written with a trailing * matches any continuation); the earlier plain
    substring test reported "rest" inside "request" and "api" inside "capital".
    """
    hits = semantics.policy_categories(text, policy)
    if not hits:
        return 'Uncategorised', [], []
    ranked = sorted(hits, key=lambda c: (-len(hits[c]), CATEGORY_ORDER.index(c) if c in CATEGORY_ORDER else 99))
    # 'Functional Requirement' (generic modal verbs) only wins when nothing more specific matched.
    specific = [c for c in ranked if c != 'Functional Requirement']
    primary = specific[0] if specific else ranked[0]
    secondary = [c for c in ranked if c != primary][:2]
    return primary, hits[primary][:6], secondary


def _evidence(role, document, clause, ref_index, quote=None):
    """Verbatim evidence. ``quote`` narrows a long clause to the sentence that matters; the clause stays available as context."""
    text = quote if quote else clause['text']
    item = {
        'ref': stable_id('EV', document['source_id'], clause['anchor'], digest(normalize(text)), length=8),
        'role': role, 'document': document['file_name'], 'source_id': document['source_id'],
        'document_version': document['owner']['version'], 'document_version_id': document['document_version_id'],
        'clause_ref': clause['ref'], 'section': clause['section'], 'anchor': clause['anchor'],
        'quote': clip(text, 700), 'quote_hash': clause['quote_hash'], 'verified': True,
    }
    for key in ('location', 'label', 'context'):
        if clause.get(key):
            item[key] = clause[key]
    if quote and clause['text'] != quote:
        item['clause_text'] = clip(clause['text'], 900)
    return item


def verify_quote(document, quote):
    """True only when the quote really occurs in the loaded document text (whitespace-insensitive)."""
    q = normalize(quote)
    return len(q) >= 12 and q in normalize(document['text'])


def word_diff(old, new):
    old_t, new_t = old.split(), new.split()
    matcher = difflib.SequenceMatcher(a=old_t, b=new_t, autojunk=False)
    removed, added = [], []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag in ('replace', 'delete'):
            removed.append(' '.join(old_t[i1:i2]))
        if tag in ('replace', 'insert'):
            added.append(' '.join(new_t[j1:j2]))
    return {'removed': [clip(x, 160) for x in removed[:6]], 'added': [clip(x, 160) for x in added[:6]],
            'numbers_changed': sorted(set(NUMBER.findall(old)) ^ set(NUMBER.findall(new)))[:10]}


def _label_key(clause):
    """Short "Label | value" rows (form fields) pair by their label, so a changed value is a modification and not a removal plus an addition."""
    label, text = clause.get('label') or '', clause['text']
    if label and text.startswith(label + ' | ') and len(text) < 260:
        return normalize(label)
    return ''


def compare_versions(baseline, changed, policy):
    """Align clauses (by reference, exact text, label, then bounded similarity), split changed clauses into sentences,
    and report each logical change once with every place it occurs."""
    limits = policy.get('limits', {})
    threshold = float(limits.get('similarity_match_threshold', 0.62))
    lex = semantics._lexicon(policy)
    b_clauses, c_clauses = baseline['clauses'], changed['clauses']
    b_by_ref = defaultdict(list)
    for clause in b_clauses:
        if clause['ref']:
            b_by_ref[clause['ref']].append(clause)
    pairs, used_b, used_c = [], set(), set()

    # 1. exact text anywhere = unchanged (a moved clause is not a change)
    b_by_hash = defaultdict(list)
    for clause in b_clauses:
        b_by_hash[clause['quote_hash']].append(clause)
    unchanged_pairs = []
    for clause in c_clauses:
        for candidate in b_by_hash.get(clause['quote_hash'], []):
            if candidate['index'] not in used_b:
                used_b.add(candidate['index']); used_c.add(clause['index']); unchanged_pairs.append(clause); break
    unchanged = len(used_c)

    # 2. same reference
    for clause in c_clauses:
        if clause['index'] in used_c or not clause['ref']:
            continue
        for candidate in b_by_ref.get(clause['ref'], []):
            if candidate['index'] not in used_b:
                pairs.append((candidate, clause, 'reference')); used_b.add(candidate['index']); used_c.add(clause['index']); break

    # 2b. same form-field label
    b_by_label = defaultdict(list)
    for clause in b_clauses:
        key = _label_key(clause)
        if key and clause['index'] not in used_b:
            b_by_label[key].append(clause)
    for clause in c_clauses:
        key = _label_key(clause)
        if clause['index'] in used_c or not key:
            continue
        candidates = [x for x in b_by_label.get(key, []) if x['index'] not in used_b]
        candidates.sort(key=lambda x: (x.get('location') != clause.get('location'), x['index']))
        if candidates:
            pairs.append((candidates[0], clause, 'label')); used_b.add(candidates[0]['index']); used_c.add(clause['index'])

    # 3. bounded similarity through an inverted token index
    index = defaultdict(set)
    b_tokens = {}
    for clause in b_clauses:
        if clause['index'] in used_b:
            continue
        b_tokens[clause['index']] = token_set(clause['text'])
        for token in b_tokens[clause['index']]:
            index[token].add(clause['index'])
    for clause in c_clauses:
        if clause['index'] in used_c:
            continue
        c_set = token_set(clause['text'])
        votes = defaultdict(int)
        for token in c_set:
            posting = index.get(token)
            if posting and len(posting) <= 60:
                for i in posting:
                    if i not in used_b:
                        votes[i] += 1
        best, best_score = None, 0.0
        for i, _ in sorted(votes.items(), key=lambda kv: -kv[1])[:6]:
            score = jaccard(c_set, b_tokens[i])
            if score > best_score:
                best, best_score = i, score
        if best is not None and best_score >= threshold:
            pairs.append((b_clauses[best], clause, 'similarity:%.2f' % best_score)); used_b.add(best); used_c.add(clause['index'])

    raw = []
    for old, new, how in pairs:
        if old['quote_hash'] == new['quote_hash']:
            unchanged += 1
            continue
        units_pairs, only_old, only_new, equal = semantics.align_units(semantics.split_units(old['text']), semantics.split_units(new['text']))
        unchanged += equal
        for old_unit, new_unit, similarity in units_pairs:
            if normalize(old_unit) == normalize(new_unit):
                continue
            raw.append(_logical(policy, lex, baseline, changed, 'MODIFIED', old, new, old_unit, new_unit,
                                how if how != 'similarity' else 'similarity', similarity))
        for unit in only_new:
            raw.append(_logical(policy, lex, baseline, changed, 'ADDED', None, new, None, unit, 'new sentence in a changed clause', 0.0))
        for unit in only_old:
            raw.append(_logical(policy, lex, baseline, changed, 'REMOVED', old, None, unit, None, 'sentence no longer in the changed clause', 0.0))
    for clause in c_clauses:
        if clause['index'] not in used_c:
            raw.append(_logical(policy, lex, baseline, changed, 'ADDED', None, clause, None, clause['text'], 'unmatched', 0.0))
    for clause in b_clauses:
        if clause['index'] not in used_b:
            raw.append(_logical(policy, lex, baseline, changed, 'REMOVED', clause, None, clause['text'], None, 'unmatched', 0.0))
    detected = len(raw)
    changes = consolidate(raw)
    changes.sort(key=lambda c: (c['evidence'][0]['anchor'] if c['evidence'] else 0, c['change_id']))
    ensure_unique_change_ids(changes)
    cap = int(limits.get('max_changes', 300))
    changed_norms = {c['norm'] for c in c_clauses}
    unchanged_norms = [c['norm'] for c in b_clauses if c['index'] in used_b and c['norm'] in changed_norms]
    return {'changes': changes[:cap], 'truncated': len(changes) > cap, 'total_detected': len(changes), 'unchanged_clauses': unchanged,
            'raw_differences': detected, 'consolidated_into': len(changes),
            'baseline_clauses': len(b_clauses), 'changed_clauses': len(c_clauses), 'unchanged_norms': unchanged_norms[:4000],
            'unchanged_clause_list': [{'text': c['text'], 'ref': c['ref'], 'context': c.get('context', ''), 'label': c.get('label', ''),
                                       'location': c.get('location', ''), 'index': c['index'], 'anchor': c['anchor'], 'section': c.get('section', ''),
                                       'quote_hash': c['quote_hash']} for c in unchanged_pairs[:3000]]}


def ensure_unique_change_ids(changes):
    """Give every change in one analysis its own identifier.

    A change ID is derived from the clause wording (plus reference and change type), so a document that repeats the
    same wording (boilerplate rows, repeated notes, table lines) produced the same ID twice and the result could not
    be saved. The first occurrence keeps its ID unchanged, so existing analyses and the IDs of ordinary changes are
    not affected; later occurrences get a deterministic ID derived from the original ID and their position in the list.
    Idempotent: a list that is already unique is returned untouched.
    """
    seen = set(c['change_id'] for c in changes)
    used, counts = set(), {}
    for change in changes:
        base = change['change_id']
        if base not in used:
            used.add(base)
            counts[base] = 1
            continue
        number = counts[base]
        while True:
            number += 1
            candidate = stable_id('CHG', base, 'occurrence', number)
            if candidate not in used and candidate not in seen:
                break
        counts[base] = number
        used.add(candidate)
        change['change_id'] = candidate
        change['occurrence'] = number
        change['description'] = '%s (same wording as an earlier clause: occurrence %d)' % (change['description'], number)
    return changes


def _clause_label(clause):
    ref = (clause or {}).get('ref') or ''
    if ref and not str(ref).upper().startswith('CRF'):
        return ref
    return (clause or {}).get('context') or ''


def _logical(policy, lex, baseline, changed, change_type, old_clause, new_clause, old_unit, new_unit, how, similarity):
    """One logical change: a changed (or added / removed) sentence, with its rule-level meaning."""
    old_unit, new_unit = (old_unit or None), (new_unit or None)
    if change_type == 'MODIFIED':
        analysis = semantics.analyse_pair(old_unit, new_unit, lex)
        deltas = analysis['deltas']
    else:
        analysis = semantics.describe_statement(new_unit or old_unit)
        analysis.update({'deltas': [], 'administrative': False, 'condition_old': '', 'condition_new': analysis.get('condition', ''),
                         'outcome_old': '', 'outcome_new': analysis.get('outcome', '')})
        deltas = []
    clause = new_clause or old_clause
    text = ' '.join(x for x in (old_unit, new_unit) if x)
    category, keywords, secondary = categorize(text, policy)
    administrative = semantics.is_administrative(clause['text'], clause.get('label', ''), analysis, lex)
    if change_type != 'MODIFIED' and not administrative:
        # an added or removed form field (label | value) with no rule words is administrative too
        administrative = bool(lex.get('admin_labels')) and semantics.is_administrative(clause['text'], clause.get('label', ''), None, lex)
    category = semantics.semantic_category(deltas, text, category, administrative)
    subject = '' if administrative else analysis.get('subject', '')
    ref = _clause_label(clause)
    sig = semantics.signature(change_type, old_unit, new_unit)
    evidence = []
    if old_clause:
        evidence.append(_evidence('baseline', baseline, old_clause, None, old_unit))
    if new_clause:
        evidence.append(_evidence('changed', changed, new_clause, None, new_unit))
    occurrence = {'location': (new_clause or old_clause).get('location', ''), 'label': (new_clause or old_clause).get('label', ''),
                  'context': (new_clause or old_clause).get('context', ''), 'section': (new_clause or old_clause).get('section', ''),
                  'clause_ref': (new_clause or old_clause).get('ref', ''), 'anchor': (new_clause or old_clause)['anchor'],
                  'evidence_refs': [e['ref'] for e in evidence]}
    change = {
        'change_id': stable_id('CHG', 'diff', sig), 'category': category, 'secondary_categories': secondary, 'category_keywords': keywords,
        'change_kind': 'ACTUAL_DIFF', 'change_type': change_type, 'reference': ref,
        'original_text': old_unit, 'modified_text': new_unit,
        'original_clause': clip(old_clause['text'], 900) if old_clause and old_clause['text'] != old_unit else None,
        'modified_clause': clip(new_clause['text'], 900) if new_clause and new_clause['text'] != new_unit else None,
        'source_document': changed['file_name'], 'source_version': changed['owner']['version'],
        'baseline_document': baseline['file_name'], 'baseline_version': baseline['owner']['version'],
        'diff': dict(word_diff(old_unit or '', new_unit or ''), deltas=[d['summary'] for d in deltas]),
        'match_method': how, 'evidence': evidence, 'status': 'Detected: actual version difference',
        'assurance': 'CONFIRMED', 'signature': sig,
        'administrative': administrative, 'functional': not administrative,
        'logical_kind': 'Administrative' if administrative else 'Functional',
        'subject': subject, 'deltas': deltas, 'context': (clause.get('context') or ''),
        'condition_old': analysis.get('condition_old', ''), 'condition_new': analysis.get('condition_new', ''),
        'outcome_old': analysis.get('outcome_old', ''), 'outcome_new': analysis.get('outcome_new', ''),
        'statement': {k: analysis.get(k) for k in ('numbers', 'identifiers', 'method_ids', 'api', 'predicates')} if change_type != 'MODIFIED' else None,
        'occurrences': [occurrence], 'occurrence_count': 1,
        'alignment': {'method': how, 'similarity': similarity},
    }
    change['requirement'] = semantics.requirement_title(subject, category, lex.get('facets')) or (clip(new_unit or old_unit or '', 60) if not administrative else _admin_title(clause))
    change['requirement_key'] = '%s|%s' % (change['context'] or change['reference'] or '', normalize(subject or change['requirement']))
    change['description'] = describe(change)
    change['old_behavior'] = old_unit
    change['new_behavior'] = new_unit
    change.update(_impact_fields(change))
    return change


def _admin_title(clause):
    head = str(clause.get('label') or clause['text'].split(' | ')[0])
    return clip(head, 60)


def _impact_fields(change):
    text = semantics.impact_text(change)
    return {'business_impact': text['business'], 'technical_impact': text['technical'], 'qa_impact': text['qa'],
            'confidence': {'CONFIRMED': 'Confirmed', 'EVIDENCE_BACKED': 'Evidence-backed', 'SUGGESTED': 'Suggested'}.get(change.get('assurance'), 'Unknown')}


def describe(change):
    """One readable sentence per logical change, built from its rule deltas (never "a clause now also says not")."""
    ref = change.get('reference')
    where = ('%s - ' % ref) if ref else ''
    subject = change.get('subject')
    kind = change['change_type']
    deltas = [d for d in change.get('deltas', []) if d['kind'] != 'wording'] or change.get('deltas', [])
    lead = ('%s: ' % (subject[:1].upper() + subject[1:])) if subject and not change.get('administrative') else ''
    if kind == 'MODIFIED':
        if not deltas:
            return '%s%sWording was revised without a detectable rule change.' % (where, lead)
        text = '; '.join(d['summary'].rstrip('.') for d in deltas[:3])
        extra = len(deltas) - 3
        return '%s%s%s%s.' % (where, lead, text, (' (+%d more difference(s))' % extra) if extra > 0 else '')
    if kind == 'ADDED':
        return '%s%sNew statement: %s' % (where, lead, clip(change.get('modified_text') or '', 200))
    if kind == 'REMOVED':
        return '%s%sStatement no longer appears: %s' % (where, lead, clip(change.get('original_text') or '', 200))
    return where + clip(change.get('modified_text') or '', 200)


def consolidate(changes):
    """Merge repeated occurrences of the same logical change (same old and new wording) into one record.

    The record keeps every source reference. Two different requirements are never merged just because their wording is
    similar: the merge key is the exact normalised old and new sentence, so only the same difference merges.
    """
    merged, order = {}, []
    for change in changes:
        key = change['signature']
        if key not in merged:
            merged[key] = change
            order.append(key)
            continue
        base = merged[key]
        known = {e['ref'] for e in base['evidence']}
        for evidence in change['evidence']:
            if evidence['ref'] not in known:
                base['evidence'].append(evidence)
                known.add(evidence['ref'])
        if not any(o['anchor'] == change['occurrences'][0]['anchor'] and o['location'] == change['occurrences'][0]['location'] for o in base['occurrences']):
            base['occurrences'].append(change['occurrences'][0])
        base['occurrence_count'] = len(base['occurrences'])
    for key in order:
        change = merged[key]
        if change['occurrence_count'] > 1:
            places = [o['location'] or ('position %d' % o['anchor']) for o in change['occurrences']]
            change['occurrence_note'] = 'The same difference occurs %d times in the document (%s); it is reported once.' % (change['occurrence_count'], '; '.join(places[:8]))
    return [merged[k] for k in order]


CRF_SENTENCE = re.compile(r'(?<=[.!?;])\s+(?=[A-Z(\[])')
CRF_MODAL = re.compile(r'\b(shall|must|should|will|required|needs? to|has to|is to)\b', re.I)
CRF_VERBS = (('PROPOSED_REMOVAL', re.compile(r'\b(remove|removed|delete|deleted|drop|retire|discontinue|disable)\b', re.I)),
             ('PROPOSED_ADDITION', re.compile(r'\b(add|added|introduce|introduced|include|create|new)\b', re.I)),
             ('PROPOSED_MODIFICATION', re.compile(r'\b(change|changed|update|updated|modify|modified|replace|replaced|amend|increase|decrease|extend|reduce|revise|rename|allow|restrict)\b', re.I)))


def _crf_units(clause):
    """A change request clause may hold several sentences; each is judged on its own so one
    paragraph does not hide separate proposed changes. Quotes stay verbatim substrings of the clause."""
    from Core.change_impact.documents import INLINE_REF, normalize_ref
    parts = [p.strip() for p in CRF_SENTENCE.split(clause['text']) if p.strip()]
    if len(parts) <= 1:
        return [clause]
    units = []
    for part in parts:
        if len(tokens(part)) < 5 and not INLINE_REF.search(part):
            continue
        inline = INLINE_REF.search(part)
        unit = dict(clause, text=part, norm=normalize(part), quote_hash=digest(normalize(part)))
        unit['ref'] = normalize_ref(inline.group(1)) if inline else (clause['ref'] if part == parts[0] else '')
        units.append(unit)
    return units or [clause]


def extract_crf_changes(document, policy):
    """Standalone change request: report *proposed* changes with evidence; never invent originals."""
    markers = [m.lower() for m in policy.get('crf_change_markers', [])]
    limits = policy.get('limits', {})
    lex = semantics._lexicon(policy)
    results, by_hash = [], {}
    for whole in document['clauses']:
        for clause in _crf_units(whole):
            lowered = ' ' + clause['text'].lower() + ' '
            marker = next((m for m in markers if m in lowered), None)
            modal = bool(CRF_MODAL.search(clause['text']))
            verb = None
            if modal or marker:
                for kind, pattern in CRF_VERBS:
                    hit = pattern.search(clause['text'])
                    if hit:
                        verb = (kind, hit.group(1).lower())
                        break
            is_requirement = bool((clause['ref'] and not str(clause['ref']).upper().startswith('CRF')) or modal)
            if not marker and not is_requirement:
                continue
            # a bare title such as "Change Request CRF-77" is a heading, not a proposed change
            if marker and not modal and not verb and len(tokens(clause['text'])) < 8:
                continue
            if clause['quote_hash'] in by_hash:      # the same statement repeated: one change, every place kept
                _add_occurrence(by_hash[clause['quote_hash']], document, clause)
                continue
            ref = '' if str(clause['ref']).upper().startswith('CRF') else clause['ref']   # the CRF's own number is not a requirement reference
            change_type = 'PROPOSED_UNSPECIFIED'
            how = 'requirement-statement'
            if marker:
                how = 'marker:' + marker
                change_type = 'PROPOSED_MODIFICATION'
                if any(w in marker for w in ('remove', 'delet')):
                    change_type = 'PROPOSED_REMOVAL'
                elif any(w in marker for w in ('add', 'new')):
                    change_type = 'PROPOSED_ADDITION'
            if verb and (not marker or marker == 'change request'):
                change_type, how = verb[0], 'verb:' + verb[1]
            stated = bool(marker or verb)
            category, keywords, secondary = categorize(clause['text'], policy)
            analysis = semantics.describe_statement(clause['text'])
            administrative = semantics.is_administrative(whole['text'], whole.get('label', ''), None, lex)
            category = semantics.semantic_category([], clause['text'], category, administrative)
            subject = '' if administrative else analysis.get('subject', '')
            label = ref or whole.get('context') or ''
            evidence = [_evidence('proposed', document, clause, None)]
            change = {
                'change_id': stable_id('CHG', 'crf', ref or clause['quote_hash'], clause['quote_hash']),
                'category': category, 'secondary_categories': secondary, 'category_keywords': keywords,
                'change_kind': 'PROPOSED_CRF', 'change_type': change_type, 'reference': label,
                'description': ('Proposed (not yet applied): ' + clip(clause['text'], 220)),
                'original_text': None, 'original_note': ORIGINAL_NOT_AVAILABLE, 'modified_text': clause['text'],
                'source_document': document['file_name'], 'source_version': document['owner']['version'],
                'diff': None, 'match_method': how, 'evidence': evidence,
                'status': 'Proposed change request (original wording not available)' if stated else 'Requirement statement in the change request (not confirmed as a change)',
                'assurance': 'EVIDENCE_BACKED' if stated else 'HYPOTHESIS',
                'administrative': administrative, 'functional': not administrative,
                'logical_kind': 'Administrative' if administrative else 'Functional', 'subject': subject,
                'deltas': [], 'context': clause.get('context') or '', 'condition_old': '', 'condition_new': analysis.get('condition', ''),
                'outcome_old': '', 'outcome_new': analysis.get('outcome', ''), 'old_behavior': None, 'new_behavior': clause['text'],
                'statement': {k: analysis.get(k) for k in ('numbers', 'identifiers', 'method_ids', 'api', 'predicates')},
                'occurrences': [{'location': clause.get('location', ''), 'label': clause.get('label', ''), 'context': clause.get('context', ''),
                                 'section': clause.get('section', ''), 'clause_ref': clause.get('ref', ''), 'anchor': clause['anchor'],
                                 'evidence_refs': [e['ref'] for e in evidence]}],
                'occurrence_count': 1, 'signature': clause['quote_hash'],
            }
            change['requirement'] = semantics.requirement_title(subject, category, lex.get('facets')) or (clip(clause['text'], 60) if not administrative else _admin_title(clause))
            change['requirement_key'] = '%s|%s' % (change['context'] or change['reference'] or '', normalize(subject or change['requirement']))
            change.update(_impact_fields(change))
            # statement rules read as "new behaviour" for the impact text
            change['deltas'] = [{'kind': 'statement_rule', 'axis': p['axis'], 'axis_name': semantics.AXIS_NAME.get(p['axis'], p['axis']), 'role': p['role'],
                                 'subject': p['subject'] or subject, 'old': '', 'new': semantics.label_for(p['axis'], p['value']),
                                 'new_surface': p['surface'], 'new_phrase': p['phrase'],
                                 'summary': '%s stated: %s%s.' % ('Condition' if p['role'] == 'condition' else 'Rule', (p['subject'] + ' ') if p['subject'] else '', p['surface'])}
                                for p in analysis.get('predicates', [])]
            by_hash[clause['quote_hash']] = change
            results.append(change)
    for change in results:
        if change['occurrence_count'] > 1:
            places = [o['location'] or ('position %d' % o['anchor']) for o in change['occurrences']]
            change['occurrence_note'] = 'The same statement occurs %d times in the document (%s); it is reported once.' % (change['occurrence_count'], '; '.join(places[:8]))
    ensure_unique_change_ids(results)
    cap = int(limits.get('max_changes', 300))
    return {'changes': results[:cap], 'truncated': len(results) > cap, 'total_detected': len(results), 'unchanged_clauses': 0,
            'baseline_clauses': 0, 'changed_clauses': len(document['clauses'])}


def complete_change(change, policy, lex):
    """Give a change from another source (an AI candidate) the same business fields as a rule-detected one."""
    if 'functional' in change and 'deltas' in change:
        return change
    text = change.get('modified_text') or change.get('original_text') or ''
    analysis = semantics.describe_statement(text)
    administrative = semantics.is_administrative(text, '', None, lex)
    subject = '' if administrative else analysis.get('subject', '')
    change.setdefault('deltas', [])
    change.update({'administrative': administrative, 'functional': not administrative, 'logical_kind': 'Administrative' if administrative else 'Functional',
                   'subject': subject, 'context': change.get('context', ''), 'condition_old': '', 'condition_new': analysis.get('condition', ''),
                   'outcome_old': '', 'outcome_new': analysis.get('outcome', ''), 'old_behavior': change.get('original_text'), 'new_behavior': change.get('modified_text'),
                   'statement': {k: analysis.get(k) for k in ('numbers', 'identifiers', 'method_ids', 'api', 'predicates')},
                   'occurrences': change.get('occurrences') or [{'location': '', 'anchor': (change['evidence'] or [{}])[0].get('anchor', 0)}],
                   'occurrence_count': change.get('occurrence_count', 1), 'signature': change.get('signature') or change['change_id']})
    change['requirement'] = semantics.requirement_title(subject, change['category'], lex.get('facets')) or (clip(text, 60) if not administrative else clip(text, 60))
    change['requirement_key'] = '%s|%s' % (change['reference'] or '', normalize(subject or change['requirement']))
    change.update(_impact_fields(change))
    return change


def _add_occurrence(change, document, clause):
    evidence = _evidence('proposed', document, clause, None)
    if all(e['ref'] != evidence['ref'] for e in change['evidence']):
        change['evidence'].append(evidence)
    change['occurrences'].append({'location': clause.get('location', ''), 'label': clause.get('label', ''), 'context': clause.get('context', ''),
                                  'section': clause.get('section', ''), 'clause_ref': clause.get('ref', ''), 'anchor': clause['anchor'],
                                  'evidence_refs': [evidence['ref']]})
    change['occurrence_count'] = len(change['occurrences'])
