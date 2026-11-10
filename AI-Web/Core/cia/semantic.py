"""Similar-wording discovery from the existing document index (engine 2.0, optional).

The deterministic matcher in ``match.py`` finds dependencies that share an identifier or specific wording. Two requirements can also describe the same
behaviour in different words ("payload must be signed" / "request carries a digital signature"). The vector index that Knowledge Hub already fills
(ChromaDB, one collection) is used here ONLY to *find* such candidates. Similarity is never proof:

* every item found this way is "Needs Investigation" at most, with the similarity score shown, never Potential or Confirmed;
* items already found by the rules are not repeated, and nothing found here can raise a risk level or select a test;
* when the index or the embedding model is not available, the analysis is unchanged and a note says so.
"""
import math

from Core.cia import terms as T

HITS_PER_CHANGE = 15        # index chunks fetched per changed requirement
UNITS_PER_CHANGE = 120      # requirement statements of those chunks that are compared
SIMILARITY_RELATED = 0.72   # cosine similarity needed inside a document the rules already linked to this change (or of the same Knowledge)
SIMILARITY_OTHER = 0.82     # ... and in any other document: similar wording alone must not open a new, unrelated document
MAX_PER_CHANGE = 2          # similar items listed per changed requirement
MIN_WORDS = 7               # shorter statements are labels, cells or list numbers, not requirements


class DefaultEmbedder:
    """The embedding model Knowledge Hub already uses (loaded on first use)."""

    def __init__(self):
        from Core.embedding_engine import EmbeddingEngine
        self._engine = EmbeddingEngine()

    def embed(self, texts):
        vectors = self._engine.generate_embeddings(list(texts))
        if vectors is None:
            raise RuntimeError('The embedding model could not encode the text.')
        return [list(map(float, v)) for v in vectors]


def cosine(a, b):
    dot = sum(x * y for x, y in zip(a, b))
    na, nb = math.sqrt(sum(x * x for x in a)), math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


class Semantic:
    """``embedder.embed(list of text) -> list of vectors``; ``store.search(vector, limit) -> Chroma-style result`` (both injectable for tests)."""

    def __init__(self, embedder=None, store=None):
        self._embedder, self._store = embedder, store
        self.status, self.found, self.compared, self._related = 'Not run', 0, 0, set()

    def _parts(self):
        if self._embedder is None:
            self._embedder = DefaultEmbedder()
        if self._store is None:
            from Core.vector_store import VectorStore
            self._store = VectorStore()
        return self._embedder, self._store

    def discover(self, changes, repo, items, source_row, excluded, target_ids, merge, also_in):
        """Adds Needs-Investigation items to ``items`` through ``merge``. Never raises: any failure only sets ``status``."""
        try:
            embedder, store = self._parts()
        except Exception as error:
            self.status = 'Unavailable: %s' % type(error).__name__
            return
        by_key = {(r.get('domain'), r.get('module'), r.get('knowledge_name'), str(r.get('version')), r.get('file_name')): sid for sid, r in repo.rows.items()}
        related = {sid for item in items.values() for sid in [item['hierarchy']['source_id']] if any(e['level'] in ('Potential Dependency', 'Confirmed Dependency') for e in item['evidence'])}
        related |= {sid for sid, row in repo.rows.items() if source_row.get('knowledge_entity_id') and row.get('knowledge_entity_id') == source_row.get('knowledge_entity_id')}
        self._related = related
        by_doc = {}
        for pos, unit in enumerate(repo.units):
            by_doc.setdefault(unit['source_id'], []).append(pos)
        try:
            vectors = embedder.embed([c['text'] for c in changes]) if changes else []
            for change, vector in zip(changes, vectors):
                self._one(change, vector, embedder, store, repo, items, source_row, set(excluded), target_ids, merge, also_in, by_key, by_doc)
            self.status = 'Used: %d similar-wording item(s) found among %d statement(s) compared' % (self.found, self.compared)
        except Exception as error:
            self.status = 'Partly used: %s' % type(error).__name__

    def _one(self, change, vector, embedder, store, repo, items, source_row, excluded, target_ids, merge, also_in, by_key, by_doc):
        from Core.cia import match
        result = store.search(vector, limit=HITS_PER_CHANGE)
        if not result or not (result.get('ids') or [[]])[0]:
            return
        documents, metas = (result.get('documents') or [[]])[0], (result.get('metadatas') or [[]])[0]
        positions = []
        for text, meta in zip(documents, metas):
            meta = meta or {}
            sid = self._source_id(meta, by_key)
            if sid is None or sid in excluded or sid not in repo.doc_ids:
                continue
            chunk = meta.get('chunk_number')
            found = [p for p in by_doc.get(sid, ()) if chunk is not None and str(repo.units[p].get('chunk_number')) == str(chunk)]
            if not found and text:
                found = [p for p in by_doc.get(sid, ()) if repo.units[p]['text'] and repo.units[p]['text'] in text]
            positions += [p for p in found if p not in positions]
        candidates = []
        for pos in positions:
            unit = repo.units[pos]
            if len(unit['text'].split()) < MIN_WORDS or T.noise_kind(unit['text']):
                continue
            key = match.item_key(source_row['id'], unit['source_id'], unit['ref'], unit['text'])
            if key in items and change['change_id'] in items[key]['change_ids']:
                continue            # the rules already judged this pair
            candidates.append((pos, key))
        candidates = candidates[:UNITS_PER_CHANGE]
        if not candidates:
            return
        self.compared += len(candidates)
        scored = []
        for (pos, key), vec in zip(candidates, embedder.embed([repo.units[p]['text'] for p, _ in candidates])):
            score = cosine(vector, vec)
            if score >= (SIMILARITY_RELATED if repo.units[pos]['source_id'] in self._related else SIMILARITY_OTHER):
                scored.append((score, pos, key))
        scored.sort(key=lambda t: (-t[0], t[1]))
        for score, pos, key in scored[:MAX_PER_CHANGE]:
            if target_ids is not None and repo.units[pos]['source_id'] not in target_ids:
                continue            # outside the impact scope: counted for the comparison, not listed
            judged = {'level': match.INVESTIGATE, 'keys': [], 'context': [], 'defines': False, 'document_named': False, 'verified': False, 'similar': round(score, 2)}
            merge(items, repo, pos, key, judged, change, source_row, also_in)
            self.found += 1

    @staticmethod
    def _source_id(meta, by_key):
        value = meta.get('knowledge_id')
        try:
            if value not in (None, ''):
                return int(value)
        except (TypeError, ValueError):
            pass
        return by_key.get((meta.get('domain'), meta.get('module'), meta.get('knowledge_name'), str(meta.get('version')), meta.get('file_name')))
