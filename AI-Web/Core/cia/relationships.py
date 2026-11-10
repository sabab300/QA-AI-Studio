"""Persistent dependency model, separate from the Knowledge Hub hierarchy.

A relationship says: "this statement in the target document may depend on a change in the source document". It stores the
quotes and the fingerprints (sha256) of both documents. When a document changes, the relationship is flagged stale instead of
being trusted or silently deleted, so a person re-reviews it. QA decisions are kept here and copied from the review step.
"""
import sqlite3

from Core.change_impact.util import clip, digest, dumps, normalize, now

ENGINE = '2.0'


def rel_id(source_id, item_key):
    return 'REL-' + digest('%s|%s' % (source_id, item_key))[:12].upper()


class Relationships:

    def __init__(self, db):
        self.db = db

    def _conn(self):
        conn = self.db.get_connection()
        conn.row_factory = sqlite3.Row
        return conn

    def _shas(self, ids):
        ids = sorted({int(i) for i in ids if i})
        if not ids:
            return {}
        conn = self._conn()
        try:
            out = {}
            for start in range(0, len(ids), 400):
                part = ids[start:start + 400]
                for r in conn.execute("SELECT id,sha256,status FROM knowledge_items WHERE id IN (%s)" % ','.join('?' * len(part)), part):
                    if str(r['status'] or '').lower() not in ('deleted', 'retired', 'archived'):
                        out[r['id']] = r['sha256'] or ''
            return out
        finally:
            conn.close()

    # ------------------------------------------------------------------ write
    def record(self, run_id, source_row, rows_by_id, affected, functional, searched_ids, scope):
        """Upsert the relationships found by a run and flag the ones that were not found again. Returns counts."""
        counts = {'new': 0, 'confirmed_again': 0, 'needs_review': 0, 'stale': 0}
        refs = {c['change_id']: (c.get('reference') or '') for c in functional}
        stamp = now()
        conn = self._conn()
        try:
            seen = set()
            for item in affected:
                rid = rel_id(source_row['id'], item['key'])
                seen.add(rid)
                target = rows_by_id.get(item['hierarchy']['source_id']) or {}
                source_ref = ', '.join(sorted({refs[c] for c in item['change_ids'] if refs.get(c)})) or ''
                source_quote = ''
                for c in functional:
                    if c['change_id'] in item['change_ids']:
                        source_quote = clip(c.get('modified_text') or c.get('original_text') or '', 400)
                        break
                row = conn.execute('SELECT * FROM cia_relationships WHERE id=?', (rid,)).fetchone()
                evidence_json = dumps(item.get('evidence') or [])
                values = dict(source_sha=source_row.get('sha256') or '', target_sha=target.get('sha256') or '', source_ref=source_ref, source_quote=source_quote,
                              target_ref=item['reference'], target_quote=clip(item['quote'], 600), classification=item['classification'], origin=item['origin'])
                relationship = item.get('impact_kind') or ('API / integration' if 'Integration' in item['tags'] else 'Shared rule' if 'Business rule / validation' in item['tags'] else 'Requirement')
                if row is None:
                    conn.execute('INSERT INTO cia_relationships(id,source_id,source_sha,source_ref,source_quote,target_id,target_sha,target_ref,target_quote,relationship,classification,origin,'
                                 'first_run_id,last_run_id,last_verified_at,stale,engine_version,superseded,item_key,evidence_json) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,0,?,0,?,?)',
                                 (rid, source_row['id'], values['source_sha'], source_ref, source_quote, item['hierarchy']['source_id'], values['target_sha'], item['reference'],
                                  values['target_quote'], relationship, item['classification'], item['origin'], run_id, run_id, stamp, ENGINE, item['key'], evidence_json))
                    counts['new'] += 1
                    continue
                changed = (row['source_sha'] or '') != values['source_sha'] or (row['target_sha'] or '') != values['target_sha']
                stale, reason = 0, None
                if changed and row['human_status']:
                    stale, reason = 1, 'A document changed since this relationship was reviewed. Review it again.'
                    counts['needs_review'] += 1
                elif row['human_status']:
                    counts['confirmed_again'] += 1
                conn.execute('UPDATE cia_relationships SET source_sha=?,target_sha=?,source_ref=?,source_quote=?,target_ref=?,target_quote=?,relationship=?,classification=?,origin=?,'
                             'last_run_id=?,last_verified_at=?,stale=?,stale_reason=?,engine_version=?,item_key=?,evidence_json=? WHERE id=?',
                             (values['source_sha'], values['target_sha'], source_ref, source_quote, item['reference'], values['target_quote'], relationship, item['classification'],
                              item['origin'], run_id, stamp, stale, reason, ENGINE, item['key'], evidence_json, rid))
            searched = {int(i) for i in searched_ids}
            for row in conn.execute('SELECT id,target_id FROM cia_relationships WHERE source_id=? AND stale=0 AND superseded=0', (source_row['id'],)).fetchall():
                if row['id'] in seen:
                    continue
                if row['target_id'] in searched:
                    reason = 'Not found again in the latest analysis: the wording or the documents changed.'
                elif row['target_id'] not in rows_by_id and not scope:
                    reason = 'The target document is no longer active.'
                else:
                    continue
                conn.execute('UPDATE cia_relationships SET stale=1,stale_reason=?,last_run_id=? WHERE id=?', (reason, run_id, row['id']))
                counts['stale'] += 1
            conn.commit()
        finally:
            conn.close()
        return counts

    def previous(self, source_id, item_key):
        conn = self._conn()
        try:
            row = conn.execute('SELECT human_status,human_by,human_at FROM cia_relationships WHERE id=? AND stale=0 AND superseded=0 AND human_status IS NOT NULL', (rel_id(source_id, item_key),)).fetchone()
        finally:
            conn.close()
        return {'decision': row['human_status'], 'by': row['human_by'] or '', 'at': row['human_at'] or ''} if row else None

    def verified_keys(self, source_id):
        """Item keys of relationships a QA engineer confirmed under the current engine and that are still fresh (documents unchanged)."""
        conn = self._conn()
        try:
            return {r['item_key'] for r in conn.execute("SELECT item_key FROM cia_relationships WHERE source_id=? AND human_status='Confirmed' AND stale=0 AND superseded=0 "
                                                        "AND engine_version=? AND item_key IS NOT NULL", (int(source_id), ENGINE))}
        finally:
            conn.close()

    def earlier(self, source_id, target_id, quote):
        """A QA decision recorded on an engine 1.0 relationship for the same target statement. Shown for information, never used as evidence."""
        wanted = normalize(quote)[:300]
        if not wanted:
            return None
        conn = self._conn()
        try:
            for r in conn.execute('SELECT target_quote,human_status,human_by,human_at FROM cia_relationships WHERE source_id=? AND target_id=? AND superseded=1 AND human_status IS NOT NULL '
                                  'ORDER BY human_at DESC', (int(source_id), int(target_id))):
                have = normalize(r['target_quote'])[:300]
                if have and (have[:120] in wanted or wanted[:120] in have):
                    return {'decision': r['human_status'], 'by': r['human_by'] or '', 'at': r['human_at'] or '', 'engine': '1.0'}
        finally:
            conn.close()
        return None

    def set_human(self, source_id, item_key, decision, actor):
        rid = rel_id(source_id, item_key)
        conn = self._conn()
        try:
            row = conn.execute('SELECT source_id,target_id FROM cia_relationships WHERE id=?', (rid,)).fetchone()
            if not row:
                return False
            shas = self._shas([row['source_id'], row['target_id']])
            conn.execute('UPDATE cia_relationships SET human_status=?,human_by=?,human_at=?,source_sha=COALESCE(?,source_sha),target_sha=COALESCE(?,target_sha),stale=0,stale_reason=NULL WHERE id=?',
                         (decision, actor.get('username') or actor.get('full_name') or '', now(), shas.get(row['source_id']), shas.get(row['target_id']), rid))
            conn.commit()
            return True
        finally:
            conn.close()

    # ------------------------------------------------------------------ read / refresh
    def list(self, source_id=None, include_stale=True, limit=500):
        sql, values = 'SELECT * FROM cia_relationships', []
        clauses = []
        if source_id:
            clauses.append('source_id=?')
            values.append(int(source_id))
        if not include_stale:
            clauses.append('stale=0')
        if clauses:
            sql += ' WHERE ' + ' AND '.join(clauses)
        sql += ' ORDER BY stale DESC, last_verified_at DESC LIMIT ?'
        values.append(max(1, min(int(limit), 2000)))
        conn = self._conn()
        try:
            return [dict(r) for r in conn.execute(sql, values)]
        finally:
            conn.close()

    def refresh(self):
        """Flag every relationship whose source or target document changed or disappeared. Nothing is deleted."""
        conn = self._conn()
        marked = checked = 0
        try:
            rows = [dict(r) for r in conn.execute('SELECT id,source_id,source_sha,target_id,target_sha FROM cia_relationships WHERE stale=0 AND superseded=0')]
            shas = self._shas([x for r in rows for x in (r['source_id'], r['target_id'])])
            for r in rows:
                checked += 1
                reason = None
                for role, doc_id, stored in (('source', r['source_id'], r['source_sha']), ('target', r['target_id'], r['target_sha'])):
                    if doc_id not in shas:
                        reason = 'The %s document is no longer active.' % role
                    elif (stored or '') != shas[doc_id]:
                        reason = 'The %s document changed since the last analysis.' % role
                    if reason:
                        break
                if reason:
                    conn.execute('UPDATE cia_relationships SET stale=1,stale_reason=? WHERE id=?', (reason, r['id']))
                    marked += 1
            conn.commit()
        finally:
            conn.close()
        return {'checked': checked, 'marked_stale': marked}
