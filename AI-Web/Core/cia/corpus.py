"""Where the searchable text comes from.

1. The existing vector index (chunks written by Knowledge Hub upload) is used first, so documents are not re-read.
2. A document that has no indexed chunks is read directly from the managed repository (bounded, cached by file hash) and the
   limitation is reported. Nothing is invented for a document that cannot be read.
"""
import sqlite3
import threading

from Core.change_impact.util import ImpactError

_FALLBACK_CACHE = {}
_CACHE_LOCK = threading.Lock()
_CACHE_LIMIT = 300

ROW_SQL = (
    'SELECT k.id,k.file_name,k.source_type,k.document_version_id,k.domain,k.module,k.knowledge_name,k.document_type,k.version,k.sha256,k.status,'
    't.id AS document_type_id,t.knowledge_id AS knowledge_entity_id '
    'FROM knowledge_items k LEFT JOIN document_versions v ON v.id=k.document_version_id '
    'LEFT JOIN knowledge_document_types t ON t.id=v.document_type_id '
    "WHERE COALESCE(k.status,'') NOT IN ('Deleted','Retired','Archived')")

SCOPE_FIELDS = ('domain', 'module', 'knowledge_name', 'document_type', 'version')


def source_rows(db):
    """Every active source document with its canonical hierarchy (read-only)."""
    conn = db.get_connection()
    conn.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in conn.execute(ROW_SQL + ' ORDER BY k.domain,k.module,k.knowledge_name,k.document_type,k.version,k.file_name')]
    except sqlite3.OperationalError:
        # an older database without the hierarchy tables: fall back to the plain columns
        rows = conn.execute("SELECT id,file_name,source_type,document_version_id,domain,module,knowledge_name,document_type,version,sha256,status "
                            "FROM knowledge_items WHERE COALESCE(status,'') NOT IN ('Deleted','Retired','Archived')").fetchall()
        return [dict(dict(r), document_type_id=None, knowledge_entity_id=None) for r in rows]
    finally:
        conn.close()


def in_scope(row, scope):
    """A row is inside the scope when it matches every selected level (the selected node and its descendants)."""
    if not scope:
        return True
    if scope.get('source_id') and int(scope['source_id']) != int(row['id']):
        return False
    for field in SCOPE_FIELDS:
        wanted = scope.get(field)
        if wanted and str(row.get(field) or '') != str(wanted):
            return False
    return True


def split_text(text, size=1100):
    """Pseudo chunks for a document read directly: whole blocks, never cut inside a sentence line."""
    chunks, buffer = [], ''
    for block in str(text or '').split('\n\n'):
        block = block.strip()
        if not block:
            continue
        if buffer and len(buffer) + len(block) + 2 > size:
            chunks.append(buffer)
            buffer = ''
        buffer = (buffer + '\n\n' + block) if buffer else block
    if buffer:
        chunks.append(buffer)
    return chunks


class Corpus:
    """Chunks of the documents in the search scope."""

    def __init__(self, db, vector_store=None, loader=None, max_direct_documents=40, use_vector=True):
        self.db = db
        self._vector_store = vector_store
        self._use_vector = use_vector
        self.loader = loader
        self.max_direct = max_direct_documents
        self.notes = []
        self.stats = {'indexed_documents': 0, 'direct_documents': 0, 'unreadable_documents': 0, 'skipped_documents': 0, 'chunks': 0}

    def _note(self, text):
        if text not in self.notes:
            self.notes.append(text)

    # ------------------------------------------------------------------ vector index
    def _vector_chunks(self, wanted_ids, by_key):
        """{source_id: [chunk dicts]} from the vector index; {} when the index is unavailable."""
        store = self._vector_store
        if store is None and self._use_vector:
            try:
                from Core.vector_store import VectorStore
                store = VectorStore()
            except Exception as error:
                self._note('The document index is not available (%s); documents were read directly.' % type(error).__name__)
                return {}
        if store is None:
            return {}
        out = {}
        try:
            collection = store.collection
            offset, page = 0, 2000
            while True:
                data = collection.get(include=['documents', 'metadatas'], limit=page, offset=offset)
                ids = data.get('ids') or []
                if not ids:
                    break
                for text, meta in zip(data.get('documents') or [], data.get('metadatas') or []):
                    meta = meta or {}
                    source_id = meta.get('knowledge_id')
                    try:
                        source_id = int(source_id) if source_id not in (None, '') else None
                    except (TypeError, ValueError):
                        source_id = None
                    if source_id is None:
                        source_id = by_key.get((meta.get('domain'), meta.get('module'), meta.get('knowledge_name'), str(meta.get('version')), meta.get('file_name')))
                    if source_id in wanted_ids and text:
                        out.setdefault(source_id, []).append({'source_id': source_id, 'chunk_number': meta.get('chunk_number') or len(out.get(source_id, [])),
                                                              'page': meta.get('page') or meta.get('page_number') or '', 'text': str(text), 'via': 'index'})
                offset += len(ids)
                if len(ids) < page:
                    break
        except Exception as error:
            self._note('The document index could not be read completely (%s); documents were read directly where possible.' % type(error).__name__)
        return out

    # ------------------------------------------------------------------ direct read
    def _direct_chunks(self, row):
        key = (row['id'], row.get('sha256') or '')
        with _CACHE_LOCK:
            cached = _FALLBACK_CACHE.get(key)
        if cached is not None:
            return cached
        if self.loader is None:
            return None
        try:
            doc = self.loader.load(row['id'])
        except ImpactError:
            return None
        except Exception:
            return None
        chunks = [{'source_id': row['id'], 'chunk_number': i, 'page': '', 'text': t, 'via': 'file'} for i, t in enumerate(split_text(doc['text']), 1)]
        with _CACHE_LOCK:
            if len(_FALLBACK_CACHE) >= _CACHE_LIMIT:
                _FALLBACK_CACHE.pop(next(iter(_FALLBACK_CACHE)))
            _FALLBACK_CACHE[key] = chunks
        return chunks

    def load(self, rows):
        """Chunks for ``rows`` (source rows already limited to the scope). Returns a flat list."""
        wanted = {r['id'] for r in rows}
        by_key = {(r['domain'], r['module'], r['knowledge_name'], str(r['version']), r['file_name']): r['id'] for r in rows}
        indexed = self._vector_chunks(wanted, by_key) if wanted else {}
        chunks, direct_budget = [], self.max_direct
        for row in rows:
            got = indexed.get(row['id'])
            if got:
                self.stats['indexed_documents'] += 1
                chunks += got
                continue
            if direct_budget <= 0:
                self.stats['skipped_documents'] += 1
                continue
            direct = self._direct_chunks(row)
            if direct is None:
                self.stats['unreadable_documents'] += 1
                continue
            direct_budget -= 1
            self.stats['direct_documents'] += 1
            chunks += direct
        if self.stats['direct_documents']:
            self._note('%d document(s) had no indexed text and were read directly from the repository.' % self.stats['direct_documents'])
        if self.stats['unreadable_documents']:
            self._note('%d document(s) could not be read and were not searched.' % self.stats['unreadable_documents'])
        if self.stats['skipped_documents']:
            self._note('%d document(s) were not searched because they have no indexed text and the direct-read limit of %d was reached. Re-index them in Knowledge Hub.'
                       % (self.stats['skipped_documents'], self.max_direct))
        self.stats['chunks'] = len(chunks)
        return chunks


def dedupe_by_content(rows, prefer=()):
    """Documents stored twice (identical file hash) are searched once. Returns (rows, {kept id: [duplicate descriptions]}, {dropped ids}).
    Nothing is deleted or merged in the repository; the duplicate is only left out of this search and named in the result.
    ``prefer`` (ids inside the impact scope) are kept in preference to their copies."""
    prefer = set(prefer)
    kept, seen, also_in, dropped = [], {}, {}, set()
    for row in sorted(rows, key=lambda r: (r['id'] not in prefer, r['id'])):
        sha = str(row.get('sha256') or '')
        valid = len(sha) >= 32
        if valid and sha in seen:
            first = seen[sha]
            also_in.setdefault(first['id'], []).append({'source_id': row['id'], 'file_name': row['file_name'], 'domain': row['domain'], 'module': row['module'],
                                                        'knowledge_name': row['knowledge_name'], 'document_type': row['document_type'], 'version': row['version']})
            dropped.add(row['id'])
            continue
        if valid:
            seen[sha] = row
        kept.append(row)
    kept.sort(key=lambda r: r['id'])
    return kept, also_in, dropped
