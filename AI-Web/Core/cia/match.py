"""Evidence rules (engine 2.0): which existing requirements may be affected by a change, and how sure we are.

The unit of comparison is a REQUIREMENT (a table row, a numbered statement, a sentence), never a loose chunk of text.

Every pair (changed requirement, existing requirement) is judged on three kinds of evidence:

  hard keys      a requirement id, API path or header name (strong); a field, rule value with its unit or a named business object (medium)
  context        content words the two statements share once platform terms, boilerplate and the *document's own background words* are removed
  relationship   the existing statement DEFINES the shared key, the other document names this change request, or QA already confirmed the link

An exact identifier is evidence, not proof: it must be backed by shared meaning (context) before a dependency is Confirmed.

  Confirmed Dependency   two strong keys; or a strong key plus shared meaning or a second key; or two medium keys that agree in context;
                         or one medium key plus three shared words; or a relationship QA already confirmed for the same pair.
  Potential Dependency   a strong key whose meaning is not shown to match; or a medium key plus two shared words.
  Needs Investigation    a single medium key, or only ordinary wording. Similar wording is never a dependency by itself.

The classification of a pair depends only on the two statements and on repository-wide statistics. It never depends on the impact
scope the user picked: the scope only decides which documents are listed.
AI and similarity search (Core.cia.discovery) may suggest candidates but can never create a Confirmed Dependency.
"""
import math
import re
from collections import defaultdict

from Core.change_impact.util import clip, stable_id
from Core.cia import terms as T

CONFIRMED, POTENTIAL, INVESTIGATE = 'Confirmed Dependency', 'Potential Dependency', 'Needs Investigation'
RANK = {CONFIRMED: 3, POTENTIAL: 2, INVESTIGATE: 1}
ORDER = {T.STRONG: 3, T.MEDIUM: 2, T.WEAK: 1}
MAX_PER_CHANGE, MAX_INVESTIGATE_PER_CHANGE = 10, 3


TOPIC_MIN_UNITS = 12
TOPIC_SHARE = 0.25


class Repository:
    """Requirement units of every searchable document plus repository-wide statistics (built once per analysis).

    Statistics cover the whole repository, not only the impact scope, so a pair is classified the same way whatever scope is chosen.
    """

    def __init__(self, chunks, rows_by_id):
        self.rows = rows_by_id
        self.units = []
        per_doc = defaultdict(list)
        for chunk in chunks:
            for u in T.requirement_units(chunk['text']):
                if T.json_fragment(u['text']) or T.form_field_row(u['text']) or T.administrative_closing(u['text']):
                    continue                  # a sample body line or a form field row is never the other side of a dependency
                unit = {'source_id': chunk['source_id'], 'chunk_number': chunk.get('chunk_number'), 'page': chunk.get('page') or '', 'via': chunk.get('via', 'index'),
                        'text': u['text'], 'ref': u['ref'], 'keys': T.hard_keys(u['text'])}
                per_doc[chunk['source_id']].append(len(self.units))
                self.units.append(unit)
        self.background = {}
        for sid, positions in per_doc.items():
            self.background[sid] = T.background_words([self.units[p]['text'] for p in positions])
        self.key_docs = defaultdict(set)
        self.word_docs = defaultdict(set)
        self.by_key = defaultdict(list)
        self.by_word = defaultdict(list)
        for pos, unit in enumerate(self.units):
            unit['words'] = T.context_words(unit['text'], unit['keys']) - self.background.get(unit['source_id'], frozenset())
            unit['full'] = T.context_words(unit['text']) - self.background.get(unit['source_id'], frozenset())
            for key in unit['keys']:
                self.key_docs[key].add(unit['source_id'])
                self.by_key[key].append(pos)
            for word in unit['words']:
                self.word_docs[word].add(unit['source_id'])
                self.by_word[word].append(pos)
        self.doc_ids = set(per_doc)
        self.subject_words = {sid: self._subject(row) for sid, row in rows_by_id.items()}
        self.unit_counts = {sid: len(pos) for sid, pos in per_doc.items()}
        self.key_counts = defaultdict(lambda: defaultdict(int))
        for unit in self.units:
            for key in unit['keys']:
                self.key_counts[unit['source_id']][key] += 1
        self.document_names = defaultdict(set)      # source_id -> CRF / document numbers written anywhere in the document
        for chunk in chunks:
            for number in T.document_numbers(chunk['text']):
                self.document_names[chunk['source_id']].add(number)
        self.chunk_text = {}
        for chunk in chunks:
            self.chunk_text.setdefault((chunk['source_id'], chunk.get('chunk_number')), chunk['text'])

    def doc_count(self, excluded=()):
        return max(1, len(self.doc_ids - set(excluded)))

    @staticmethod
    def _subject(row):
        """Words of the names that label the whole document (file name, knowledge name, module): the organisation or project it is about."""
        text = ' '.join(str(row.get(k) or '') for k in ('file_name', 'knowledge_name', 'module'))
        return {w.lower() for w in re.findall(r'[A-Za-z]{3,}', text)}

    def topic_term(self, key, source_id):
        """True when a named term names the whole document, not one requirement: it is part of the document's own name/knowledge/module (an organisation or project),
        or it is found in a large part of the document's statements."""
        words = re.findall(r'[a-z]{3,}', str(key).lower())
        if words and set(words) <= self.subject_words.get(source_id, set()):
            return True
        n = self.unit_counts.get(source_id, 0)
        return n >= TOPIC_MIN_UNITS and self.key_counts.get(source_id, {}).get(key, 0) / n >= TOPIC_SHARE

    def key_share(self, key, excluded):
        return len(self.key_docs.get(key, set()) - set(excluded)) / self.doc_count(excluded)


def build_change_unit(change, source_bg=frozenset(), same_knowledge=False, source_id=None):
    text = change['text']
    keys = T.hard_keys(text, same_knowledge)
    ref = change.get('reference') or ''
    ids = T.hard_keys(ref, same_knowledge)
    for k, v in ids.items():
        keys.setdefault(k, v)
    return {'change_id': change['change_id'], 'text': text, 'keys': keys, 'words': T.context_words(text, keys) - source_bg, 'full': T.context_words(text) - source_bg, 'source_id': source_id}


EXAMPLE_VALUE = re.compile(r'\b\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?Z?)?')


def item_key(source_id, target_id, ref, text):
    """One key per requirement. A field row repeated in several API tables with different example values (dates, timestamps) is one requirement."""
    return 'AFF-' + stable_id('A2', source_id, target_id, ref or T.normalize(EXAMPLE_VALUE.sub(' ', str(text or ''))))[3:]


def _strength(key, info_change, info_target, repo, excluded, same_knowledge, unit_sids=()):
    level = min(ORDER[info_change['strength']], ORDER[info_target['strength']])
    if info_change['kind'] in ('business object', 'quoted name') and any(sid is not None and repo.topic_term(key, sid) for sid in unit_sids):
        level = 1                           # the subject of the whole document (e.g. the organisation's name) is not evidence about one requirement
    if info_change['kind'] == 'requirement id' and same_knowledge and level == ORDER[T.MEDIUM] and not T._is_namespaced(key):
        level = ORDER[T.STRONG]
    if repo.doc_count(excluded) >= 6 and repo.key_share(key, excluded) > 0.5:
        level = max(1, level - 1)           # a key found in most documents (a common header, a generic term) says little
    return {3: T.STRONG, 2: T.MEDIUM, 1: T.WEAK}[level]


def judge(change_unit, target, repo, excluded, same_knowledge, doc_named, verified):
    """The ladder for one pair. Returns None or {'level','keys','context','defines','reasons'}."""
    shared = {}
    for key, info in change_unit['keys'].items():
        other = target['keys'].get(key)
        if other:
            shared[key] = dict(kind=info['kind'], surface=info['surface'], strength=_strength(key, info, other, repo, excluded, same_knowledge, (change_unit.get('source_id'), target.get('source_id'))))
    # words shared by the two statements. A statement's own named phrases (e.g. "date range exceeds") are not meaning words when they are ONLY its own keys,
    # but they are when the other statement uses the same words; only the words of keys that both statements share are left out (they are counted as keys).
    full_c, full_t = change_unit.get('full'), target.get('full')
    if full_c is not None and full_t is not None:
        shared_words = T.context_words(' '.join(v['surface'] or k for k, v in shared.items())) if shared else set()
        context = sorted((full_c & full_t) - shared_words)
    else:
        context = sorted(change_unit['words'] & target['words'])
    strong = [k for k, v in shared.items() if v['strength'] == T.STRONG]
    medium = [k for k, v in shared.items() if v['strength'] == T.MEDIUM]
    weak = [k for k, v in shared.items() if v['strength'] == T.WEAK]
    rule_only = bool(medium) and all(shared[k]['kind'] == 'rule value' for k in medium) and not strong
    lead = target['text'].split(' | ', 1)[0] if ' | ' in target['text'] else ''
    lead = lead if len(lead.split()) <= 6 else ''
    defines = any(shared[k]['surface'] and shared[k]['surface'].lower() in lead.lower() for k in shared if shared[k]['kind'] != 'rule value') or bool(target['ref'] and target['ref'] in shared)
    c = len(context) + (1 if defines else 0) + (1 if doc_named else 0)
    medium_kinds = {shared[k]['kind'] for k in medium}
    level = None
    if verified and (strong or medium):
        level = CONFIRMED
    elif len(strong) >= 2 or (strong and (c >= 2 or medium)):
        level = CONFIRMED
    elif len(medium) >= 2 and (c >= 1 or len(medium_kinds) >= 2) and not rule_only:
        level = CONFIRMED
    elif medium and c >= 3:
        level = CONFIRMED
    elif strong:
        level = POTENTIAL
    elif (medium and c >= 2) or (medium and defines and not rule_only):
        level = POTENTIAL
    elif doc_named and len(context) >= 3:
        level = POTENTIAL
    elif (medium and not rule_only) or (weak and c >= 2) or _wording_only(context, change_unit, repo, excluded):
        level = INVESTIGATE
    if level is None:
        return None
    return {'level': level, 'keys': [dict(key=k, **v) for k, v in sorted(shared.items(), key=lambda kv: (-ORDER[kv[1]['strength']], kv[0]))],
            'context': context[:8], 'defines': bool(defines), 'document_named': bool(doc_named), 'verified': bool(verified)}


def _wording_only(context, change_unit, repo, excluded):
    """Wording without any shared key is worth a look only when several words are shared and a fair part of the change's own meaning words, or when the shared words are rare."""
    if len(context) >= 4 and len(context) >= 0.2 * max(1, len(change_unit['words'])):
        return True
    return len(context) >= 3 and _distinctive(context, repo, excluded) >= 3


def _distinctive(words, repo, excluded):
    """How many of the shared words are rare in the repository (found in at most a fifth of the documents)."""
    total = repo.doc_count(excluded)
    return sum(1 for w in words if len(repo.word_docs.get(w, set()) - set(excluded)) <= max(1, 0.2 * total))


def _candidates(unit, repo, excluded, allowed_ids):
    """Positions of existing units worth judging: those sharing a non-weak hard key, plus a few wording-only neighbours."""
    found = {}
    for key, info in unit['keys'].items():
        if info['strength'] == T.WEAK:
            continue
        for pos in repo.by_key.get(key, ()):
            found[pos] = found.get(pos, 0) + 3
    votes = defaultdict(float)
    doc_total = repo.doc_count(excluded)
    for word in unit['words']:
        docs = len(repo.word_docs.get(word, set()) - set(excluded))
        if docs == 0 or (doc_total >= 5 and docs / doc_total > 0.4):
            continue
        weight = math.log(1 + doc_total / docs)
        for pos in repo.by_word.get(word, ()):
            votes[pos] += weight
    for pos, score in sorted(votes.items(), key=lambda kv: -kv[1])[:30]:
        found.setdefault(pos, 0)
        found[pos] += score * 0.1
    return [pos for pos, _ in sorted(found.items(), key=lambda kv: -kv[1])]


def find_affected(changes, repo, source_row, source_doc_numbers, excluded_ids, source_bg=frozenset(), target_ids=None, verified_keys=frozenset(), also_in=None, semantic=None):
    """changes: [{'change_id','text','reference'}]. ``target_ids``: documents that may be LISTED (the impact scope); None = every document.
    Returns affected items, one per existing requirement, each carrying the evidence of every change it depends on."""
    items, excluded = {}, set(excluded_ids)
    also_in = also_in or {}
    for change in changes:
        cu = build_change_unit(change, source_bg, source_id=source_row['id'])
        found, investigate = [], []
        for pos in _candidates(cu, repo, excluded, target_ids):
            target = repo.units[pos]
            sid = target['source_id']
            if sid in excluded:
                continue
            row = repo.rows.get(sid)
            if not row:
                continue
            same_k = bool(source_row.get('knowledge_entity_id') and row.get('knowledge_entity_id') == source_row.get('knowledge_entity_id'))
            doc_named = bool(repo.document_names.get(sid, set()) & source_doc_numbers)
            key = item_key(source_row['id'], sid, target['ref'], target['text'])
            judged = judge(cu, target, repo, excluded, same_k, doc_named, key in verified_keys)
            if judged is None:
                continue
            (investigate if judged['level'] == INVESTIGATE else found).append((pos, key, judged))
        found.sort(key=lambda t: (-RANK[t[2]['level']], -sum(1 for k in t[2]['keys'] if k['strength'] == T.STRONG), -len(t[2]['context']), t[0]))
        investigate.sort(key=lambda t: (-len(t[2]['context']), t[0]))
        for pos, key, judged in found[:MAX_PER_CHANGE * 3] + investigate[:MAX_INVESTIGATE_PER_CHANGE]:
            if target_ids is not None and repo.units[pos]['source_id'] not in target_ids:
                continue
            _merge(items, repo, pos, key, judged, change, source_row, also_in)
    if semantic is not None:
        semantic.discover([{'change_id': c['change_id'], 'text': c['text']} for c in changes], repo, items, source_row, excluded, target_ids, _merge, also_in)
    out = []
    for item in items.values():
        if len(item['change_ids']) and item['evidence']:
            best = max(item['evidence'], key=lambda e: (RANK[e['level']], len(e['keys']), len(e['context']), e.get('similar') or 0))
            item['classification'] = best['level']
            item['shared_terms'] = {k['surface'] or k['key']: k['kind'] for e in item['evidence'] for k in e['keys']}
            item['shared_words'] = sorted({w for e in item['evidence'] for w in e['context']})[:8]
            item['doc_reference'] = any(e['document_named'] for e in item['evidence'])
            if all(e.get('similar') and not e['keys'] for e in item['evidence']):
                item['origin'] = 'SIMILAR'
            item['why'] = why_text(item, best)
            item['occurrences'] = max(1, len(item.pop('_positions', ()) or ()))
            if item['occurrences'] > 1:
                item['why'] += ' The same requirement is stated in %d rows of this document; it is reported once.' % item['occurrences']
            item['cross'] = cross_flags(item['hierarchy'], source_row)
            out.append(item)
    out.sort(key=lambda i: (-RANK[i['classification']], i['hierarchy']['domain'], i['hierarchy']['module'], i['key']))
    return out


def ordered_duplicates(copies):
    """The other stored copies of a file, in a stable order (they are dicts, which cannot be sorted directly)."""
    return sorted(copies or [], key=lambda d: (str(d.get('file_name', '')), str(d.get('domain', '')), str(d.get('module', '')),
                                               str(d.get('knowledge_name', '')), str(d.get('version', '')), int(d.get('source_id') or 0)))


def _merge(items, repo, pos, key, judged, change, source_row, also_in):
    target = repo.units[pos]
    sid = target['source_id']
    item = items.get(key)
    if item is None:
        row = repo.rows[sid]
        item = items[key] = {
            'key': key, 'classification': judged['level'], 'origin': 'RULE', 'engine': '2.0',
            'hierarchy': {'domain': row['domain'], 'module': row['module'], 'knowledge_name': row['knowledge_name'], 'document_type': row['document_type'],
                          'version': row['version'], 'file_name': row['file_name'], 'source_id': sid},
            'reference': target['ref'], 'functionality': clip(target['text'], 220), 'quote': target['text'],
            'location': ('chunk %s' % target.get('chunk_number')) + ((', page %s' % target['page']) if target.get('page') else ''),
            'change_ids': [], 'evidence': [], 'shared_terms': {}, 'shared_words': [], 'doc_reference': False, 'same_knowledge_reference': False,
            'tags': T.tags(target['text']), 'via': target.get('via', 'index'), 'duplicates': ordered_duplicates(also_in.get(sid, [])), '_positions': set()}
    item['_positions'].add(pos)
    if change['change_id'] not in item['change_ids']:
        item['change_ids'].append(change['change_id'])
    entry = {'change_id': change['change_id'], 'level': judged['level'], 'keys': judged['keys'], 'context': judged['context'], 'defines': judged['defines'],
             'document_named': judged['document_named'], 'verified': judged['verified'], 'quote': target['text'], 'similar': judged.get('similar')}
    for old in item['evidence']:
        if old['change_id'] == change['change_id']:
            if RANK[entry['level']] > RANK[old['level']]:
                old.update(entry)
            return
    item['evidence'].append(entry)


def cross_flags(h, source_row):
    return {'domain': h['domain'] != source_row['domain'],
            'module': (h['domain'], h['module']) != (source_row['domain'], source_row['module']),
            'knowledge': h['knowledge_name'] != source_row['knowledge_name'] or h['module'] != source_row['module']}


def key_phrase(keys, limit=4):
    parts = ['%s "%s"' % (k['kind'], k['surface'] or k['key']) for k in keys[:limit]]
    return ', '.join(parts)


def why_text(item, best):
    keys = [k for k in best['keys'] if k['strength'] != T.WEAK]
    context = best['context'][:5]
    bits = []
    if keys:
        bits.append('both statements name ' + key_phrase(keys))
    if best.get('defines'):
        bits.append('the existing requirement defines it')
    if context:
        bits.append('both describe: ' + ', '.join(context))
    if best.get('document_named'):
        bits.append('the document names this change request')
    if best.get('verified'):
        bits.append('QA already confirmed this link')
    level = item['classification']
    if best.get('similar') and not keys and not context:
        return ('Different wording, similar meaning (similarity %.2f, found through the document index). Similarity is a search aid, not proof of a dependency: '
                'check the statement and decide.' % best['similar'])
    text = ('; '.join(bits)[:1].upper() + '; '.join(bits)[1:] + '.') if bits else ''
    if level == POTENTIAL:
        text += (' The identifier matches, but the meaning of the two statements is not shown to match closely, so a QA engineer should verify it.' if any(k['strength'] == T.STRONG for k in keys)
                 else ' One specific term and some shared wording point to a link; a QA engineer should verify it.')
    elif level == INVESTIGATE:
        text = ('Only ordinary wording or a single weak term is shared (%s). Similar wording alone does not show a dependency.' % (', '.join(context) or 'no shared words')) if not keys else \
            (text + ' Not enough shared meaning to call it a dependency; check it only if time allows.')
    elif level == CONFIRMED and not keys and best.get('verified'):
        text = 'QA already confirmed this link.'
    return text.strip()
