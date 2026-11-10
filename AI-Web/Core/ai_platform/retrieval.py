"""
Ranked retrieval for the AI Assistant (read-only on the vector store).

Fixes over the previous RetrievalEngine / RAGEngine path:

* Candidate pool: Chroma is asked for ``candidate_pool`` (default 24) chunks
  instead of 5, so the cross-encoder has something to choose between.
* Correct similarity: the collection uses Chroma's default L2 space; stored
  vectors are unit-normalised, so cosine = 1 - d^2/2 (Chroma returns squared
  L2). The old ``1 - distance`` collapsed to 0 for relevant chunks.
* Score-aligned reranking: cross-encoder scores stay attached to their chunk
  (the old code re-matched by text and then re-sorted by keyword score, which
  discarded the cross-encoder order).
* Reciprocal-rank fusion of vector rank, cross-encoder rank and lexical rank
  plus an exact requirement-reference bonus.
* Per-document chunk cap so one long document cannot crowd out others
  (multi-document reasoning).
* Cache key includes the knowledge token, so any ingestion / delete / re-index
  invalidates it.

Nothing here writes to Chroma or to metadata.db.
"""

import math
import os
import re
import threading
import time

_STOP = frozenset("""a an the of to in on for and or is are was were be been being what which who
whom this that these those with as at by from it its into about how why when where do does did can
could should would will shall may might must not no yes if then than so such there their them they
you your we our us i me my tell give show explain describe please list""".split())

_TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_\-./]*[A-Za-z0-9]|[A-Za-z0-9]")
_REF = re.compile(
    r"\b[A-Za-z]{2,8}(?:[-_][A-Za-z0-9]{1,8})*[-_]\d{1,6}(?:[-_.]\d+)*\b|\b\d{1,2}(?:\.\d{1,3}){1,4}\b")


def tokenize(text):
    return [t.lower() for t in _TOKEN.findall(text or "")]


def content_terms(text):
    return [t for t in tokenize(text) if t not in _STOP and len(t) > 1]


# Strict identifiers (CRF-AD-0017, FR-12, TC_POR_0002): letters + digits. Section numbers
# (4.2.1) are references too but are not unique enough for an exact-match lookup.
_STRICT_ID = re.compile(r"\b[A-Za-z]{2,8}(?:[-_][A-Za-z0-9]{1,8})*[-_]\d{1,6}\b")


def strict_refs(text):
    from .routing import _PUBLIC_STD    # SHA-256, UTF-8, ISO-27001 ... are not enterprise ids
    return {m.group(0).lower() for m in _STRICT_ID.finditer(text or "")
            if re.split(r"[-_]", m.group(0))[0].upper() not in _PUBLIC_STD}


def extract_refs(text):
    """Requirement / section style references: FR-12, REQ_004, 4.2.1 ..."""
    return {m.group(0).lower() for m in _REF.finditer(text or "")}


def distance_to_similarity(distance, space="l2"):
    """Convert a Chroma distance into a 0..1 cosine similarity."""

    d = float(distance)
    if space == "l2":
        # Chroma reports squared L2; embeddings are unit vectors.
        return max(0.0, min(1.0, 1.0 - d / 2.0))
    if space in ("cosine", "ip"):
        return max(0.0, min(1.0, 1.0 - d))
    return max(0.0, min(1.0, 1.0 - d))


def sigmoid(x):
    try:
        return 1.0 / (1.0 + math.exp(-float(x)))
    except OverflowError:
        return 0.0 if x < 0 else 1.0


def lexical_scores(question, docs):
    """IDF-weighted query-term coverage within the candidate pool (0..1)."""

    q_terms = list(dict.fromkeys(content_terms(question)))
    if not q_terms or not docs:
        return [0.0] * len(docs)

    doc_sets = [set(tokenize(d)) for d in docs]
    n = len(docs)
    idf = {}
    for t in q_terms:
        df = sum(1 for s in doc_sets if t in s)
        idf[t] = math.log(1 + (n - df + 0.5) / (df + 0.5))

    total = sum(idf.values()) or 1.0
    phrase = " ".join(content_terms(question))
    out = []
    for d, s in zip(docs, doc_sets):
        score = sum(idf[t] for t in q_terms if t in s) / total
        if len(q_terms) >= 2 and phrase and phrase in " ".join(content_terms(d)):
            score = min(1.0, score + 0.15)
        out.append(score)
    return out


def _newer(a, b):
    """True if metadata ``a`` belongs to a newer document version than ``b``."""
    try:
        return int(a.get("document_version_id")) > int(b.get("document_version_id"))
    except (TypeError, ValueError):
        return False


def _doc_key(meta):
    return str(meta.get("document_version_id") or meta.get("document_id")
               or meta.get("file_name") or "")


def knowledge_token(collection=None, chroma_dir=None):
    """Cheap fingerprint that changes whenever the knowledge store changes.

    Vector count + size/mtime of Chroma's sqlite file (and WAL). Used in every
    cache key so ingestion, deletion or re-indexing invalidates caches.
    """

    parts = []
    try:
        if collection is not None:
            parts.append(str(collection.count()))
    except Exception:
        parts.append("?")

    if chroma_dir:
        for name in ("chroma.sqlite3", "chroma.sqlite3-wal"):
            p = os.path.join(str(chroma_dir), name)
            try:
                st = os.stat(p)
                parts.append(f"{int(st.st_mtime)}:{st.st_size}")
            except OSError:
                parts.append("-")

    return "|".join(parts)


_TIMING = threading.local()


def _instrument_embedding(searcher):
    """Record how long query embedding takes (separately from the vector search).

    Wraps ``searcher.embedding.generate_embedding`` once; the duration of the
    last call on this thread is stored in a thread-local, so concurrent requests
    cannot mix up their timings."""

    emb = getattr(searcher, "embedding", None)
    fn = getattr(emb, "generate_embedding", None)
    if emb is None or fn is None or getattr(fn, "_t4_timed", False):
        return

    def timed(*a, **k):
        t = time.perf_counter()
        try:
            return fn(*a, **k)
        finally:
            _TIMING.embed_ms = int((time.perf_counter() - t) * 1000)

    timed._t4_timed = True
    try:
        emb.generate_embedding = timed
    except Exception:
        pass


def _scope_violations(chunks, filters):
    """Defence in depth: a chunk whose metadata contradicts the selected scope
    must never reach the prompt or a citation, whatever the store returned."""

    keys = {"domain": "domain", "module": "module", "knowledge_name": "knowledge_name",
            "version": "version", "document_type": "document_type",
            "document_version_id": "document_version_id"}
    keep, bad = [], []
    for c in chunks:
        meta = c.get("metadata") or {}
        ok = True
        for fk, mk in keys.items():
            want = filters.get(fk)
            if want in (None, ""):
                continue
            if str(meta.get(mk)) != str(want):
                ok = False
                break
        files = filters.get("source_file_names")
        if ok and files and meta.get("file_name") not in set(files):
            ok = False
        (keep if ok else bad).append(c)
    return keep, bad


class Retriever:
    """Read-only ranked retrieval with diagnostics."""

    def __init__(self, searcher=None, reranker=None, space="l2",
                 token_fn=None, clock=time.time):
        self._searcher = searcher
        self._reranker = reranker
        self._reranker_failed = False
        self.space = space
        self._token_fn = token_fn
        self._cache = {}
        self._lock = threading.Lock()
        self._clock = clock

    # ------------------------------------------------------------ defaults

    @classmethod
    def default(cls):
        inst = cls()
        inst._searcher = None   # built lazily so import never loads models
        return inst

    def _get_searcher(self):
        if self._searcher is None:
            from Core.search import KnowledgeSearch
            self._searcher = KnowledgeSearch()
            try:
                coll = self._searcher.store.collection
                meta = getattr(coll, "metadata", None) or {}
                space = meta.get("hnsw:space")
                if not space:
                    # newer Chroma versions keep the distance space in the
                    # collection configuration instead of the metadata
                    try:
                        space = ((getattr(coll, "configuration", None) or {})
                                 .get("hnsw") or {}).get("space")
                    except Exception:
                        space = None
                self.space = space or "l2"
                if self._token_fn is None:
                    try:
                        from Core.config_manager import ConfigManager
                        chroma_dir = ConfigManager().chroma_db
                    except Exception:
                        chroma_dir = None
                    self._token_fn = lambda: knowledge_token(coll, chroma_dir)
            except Exception:
                pass
        return self._searcher

    def _get_reranker(self):
        if self._reranker is None and not self._reranker_failed:
            try:
                from Core.reranker import ReRanker
                self._reranker = ReRanker()
            except Exception:
                self._reranker_failed = True
        return self._reranker

    def token(self):
        try:
            self._get_searcher()
            return self._token_fn() if self._token_fn else ""
        except Exception:
            return ""

    # ------------------------------------------------------------ retrieve

    def retrieve(self, question, rag, filters=None, query_text=None):
        """Return ranked chunks.

        ``rag`` is the RAG settings section. ``filters`` may contain domain,
        module, knowledge_name, version, document_type, document_version_id,
        source_file_names. ``query_text`` is the (possibly follow-up
        expanded) text used for embedding; ``question`` is used for rerank.
        """

        t0 = self._clock()
        filters = {k: v for k, v in (filters or {}).items() if v not in (None, "", [])}
        qtext = query_text or question

        token = self.token()
        ckey = repr((qtext, question, sorted(filters.items(), key=lambda x: x[0]),
                     token, rag.get("candidate_pool"), rag.get("top_k"),
                     rag.get("similarity_threshold"), rag.get("rerank_enabled"),
                     rag.get("max_chunks_per_document")))

        with self._lock:
            hit = self._cache.get(ckey)
            if hit and self._clock() - hit[0] < 120:
                out = dict(hit[1])
                out["cache_hit"] = True
                return out

        pool = int(rag.get("candidate_pool", 24))
        _TIMING.embed_ms = None

        # Phase 5: the ONE documented recoverable failure of the enhanced
        # retriever - the vector store / embedder cannot be reached. It happens
        # before any LLM or tool call, so a visible legacy fallback is safe.
        try:
            searcher = self._get_searcher()
            _instrument_embedding(searcher)
            raw = searcher.search_knowledge(
                query=qtext, top_k=pool,
                domain=filters.get("domain"), module=filters.get("module"),
                knowledge_name=filters.get("knowledge_name"),
                version=filters.get("version"),
                source_file_names=filters.get("source_file_names"),
                document_type=filters.get("document_type"),
                document_version_id=filters.get("document_version_id"))
        except Exception as exc:  # noqa: BLE001
            from .fallback import RecoverableEnhancedError
            raise RecoverableEnhancedError(
                "Vector search failed (%s)." % type(exc).__name__,
                "vector_store_unavailable") from exc

        t_search = self._clock()
        embed_ms = getattr(_TIMING, "embed_ms", None)

        if not isinstance(raw, dict):
            return self._result([], [], {"error": "vector search unavailable",
                                         "pool": 0}, t0, token, filters)

        def first(key):
            v = raw.get(key) or []
            return v[0] if v else []

        ids, docs = first("ids"), first("documents")
        metas, dists = first("metadatas"), first("distances")

        cands, seen = [], {}
        collapsed = 0
        for i, (doc, meta, dist) in enumerate(zip(docs, metas, dists)):
            if not doc or not doc.strip():
                continue
            sig = " ".join(doc.split())[:400].lower()
            meta = dict(meta or {})
            if sig in seen:
                # Identical text stored under several document versions (the
                # real store has many). Keep ONE copy, prefer the newest
                # document version, and remember the others so the citation
                # can say the passage is unchanged in those versions.
                kept = seen[sig]
                collapsed += 1
                kept.setdefault("same_text_in", []).append(
                    {"version": meta.get("version"),
                     "document_version_id": meta.get("document_version_id")})
                if _newer(meta, kept["metadata"]):
                    kept["same_text_in"].append(
                        {"version": kept["metadata"].get("version"),
                         "document_version_id": kept["metadata"].get("document_version_id")})
                    kept["same_text_in"] = [
                        x for x in kept["same_text_in"]
                        if x["document_version_id"] != meta.get("document_version_id")]
                    kept["chunk_id"] = ids[i] if i < len(ids) else kept["chunk_id"]
                    kept["metadata"] = meta
                continue
            c = {
                "chunk_id": ids[i] if i < len(ids) else f"c{i}",
                "text": doc.strip(), "metadata": meta,
                "cosine": distance_to_similarity(dist, self.space),
                "vector_rank": len(cands) + 1,
            }
            seen[sig] = c
            cands.append(c)

        # ---- exact reference lookup --------------------------------------
        # A passage that names the requirement the user typed (CRF-AD-0017) may not
        # be among the 24 nearest vectors (an id is a poor embedding signal). Ask the
        # store for it directly, inside the SAME scope filter, then rank it normally.
        asked = strict_refs(question)
        have = set()
        for c in cands:
            have |= strict_refs(c["text"])
        exact_added = 0
        looked_up = sorted(r for r in asked if r not in have)[:3]
        if looked_up:
            try:
                coll = self._get_searcher().store.collection
                where = self.build_where(filters)
                for ref in looked_up:
                    variants = list(dict.fromkeys(
                        [m.group(0) for m in _STRICT_ID.finditer(question)
                         if m.group(0).lower() == ref] + [ref.upper()]))
                    for v in variants:
                        kw = {"where_document": {"$contains": v},
                              "include": ["documents", "metadatas"], "limit": 6}
                        if where:
                            kw["where"] = where
                        got = coll.get(**kw)
                        for i, doc, meta in zip(got.get("ids") or [], got.get("documents") or [],
                                                got.get("metadatas") or []):
                            sig = " ".join((doc or "").split())[:400].lower()
                            if not doc or sig in seen:
                                continue
                            c = {"chunk_id": i, "text": doc.strip(), "metadata": dict(meta or {}),
                                 "cosine": 0.0, "vector_rank": None, "exact_ref": True}
                            seen[sig] = c
                            cands.append(c)
                            exact_added += 1
                        if exact_added:
                            break
            except Exception:  # noqa: BLE001 - lookup is an optional improvement
                pass
        found_refs = set()
        for c in cands:
            refs_in = strict_refs(c["text"])
            found_refs |= refs_in
            if asked & refs_in:
                c["exact_ref"] = True      # names the item the user typed: never dropped by the floor

        diag = {"pool": len(docs), "after_dedupe": len(cands),
                "refs_requested": sorted(asked),
                "refs_found": sorted(asked & found_refs),
                "exact_reference_passages_added": exact_added,
                "duplicates_collapsed": collapsed,
                "space": self.space,
                "similarity_threshold": rag.get("similarity_threshold")}

        thr = float(rag.get("similarity_threshold", 0.0))
        kept = [c for c in cands if c["cosine"] >= thr or c.get("exact_ref")]
        for c in cands:
            c["below_threshold"] = c["cosine"] < thr
        diag["after_threshold"] = len(kept)

        # ---- rerank (cross-encoder), score-aligned --------------------
        diag["rerank_used"] = False
        t_rr0 = self._clock()
        if kept and rag.get("rerank_enabled", True):
            rr = self._get_reranker()
            if rr is not None:
                try:
                    pairs = [(question, c["text"][:2000]) for c in kept[:40]]
                    scores = rr.model.predict(pairs)
                    for c, s in zip(kept[:40], scores):
                        c["rerank_raw"] = float(s)
                        c["rerank_prob"] = sigmoid(s)
                    diag["rerank_used"] = True
                except Exception as exc:  # noqa: BLE001
                    diag["rerank_error"] = type(exc).__name__

        t_rr1 = self._clock()

        # ---- lexical + reference signals ------------------------------
        lex = lexical_scores(question, [c["text"] for c in kept])
        q_refs = extract_refs(question)
        for c, l in zip(kept, lex):
            c["lexical"] = l
            c["ref_match"] = bool(q_refs and (q_refs & extract_refs(c["text"])))

        self._fuse(kept, diag["rerank_used"])

        kept.sort(key=lambda c: c["fused"], reverse=True)

        # ---- per-document cap + top_k ---------------------------------
        cap = int(rag.get("max_chunks_per_document", 3))
        top_k = int(rag.get("top_k", 6))
        per_doc, final = {}, []
        for c in kept:
            k = _doc_key(c["metadata"])
            if per_doc.get(k, 0) >= cap:
                c["drop_reason"] = "per_document_cap"
                continue
            if len(final) >= top_k:
                c["drop_reason"] = "beyond_top_k"
                continue
            per_doc[k] = per_doc.get(k, 0) + 1
            c["selected"] = True
            final.append(c)

        final, bad = _scope_violations(final, filters)
        if bad:
            diag["scope_violations_dropped"] = len(bad)
            for c in bad:
                c["selected"] = False
                c["drop_reason"] = "outside_selected_scope"

        diag["returned"] = len(final)
        diag["documents_covered"] = len({_doc_key(c["metadata"]) for c in final})
        search_ms = int((t_search - t0) * 1000)
        diag["timing_ms"] = {
            # embedding is part of "search"; reported separately where measurable
            "embedding": embed_ms,
            "vector_search": (max(search_ms - embed_ms, 0) if embed_ms is not None else None),
            "search": search_ms,
            "rerank": int((t_rr1 - t_rr0) * 1000),
            "rank_fusion": int((self._clock() - t_rr1) * 1000),
            "total": int((self._clock() - t0) * 1000)}

        res = self._result(final, cands, diag, t0, token, filters)
        with self._lock:
            if len(self._cache) > 200:
                self._cache.clear()
            self._cache[ckey] = (self._clock(), res)
        return res

    # ------------------------------------------------------ scope listing

    @staticmethod
    def build_where(filters):
        """Chroma ``where`` for the same filters ``KnowledgeSearch`` applies."""
        parts = []
        for key in ("domain", "module", "knowledge_name", "version", "document_type"):
            if filters.get(key):
                parts.append({key: filters[key]})
        if filters.get("document_version_id") is not None:
            try:
                parts.append({"document_version_id": int(filters["document_version_id"])})
            except (TypeError, ValueError):
                pass
        if filters.get("source_file_names"):
            parts.append({"file_name": {"$in": list(filters["source_file_names"])}})
        if not parts:
            return None
        return parts[0] if len(parts) == 1 else {"$and": parts}

    def scope_chunks(self, filters, max_chunks=400):
        """Every chunk of the selected scope in document order (read-only).

        Used for broad questions ("list all requirements") where ranking by
        similarity cannot tell which passages matter. Identical-text duplicates
        stored under several document versions are collapsed like in retrieve().
        Returns ``{"chunks": [...], "total": n, "truncated": bool}`` or None when
        the store cannot be read (the caller then keeps the normal ranked path).
        """

        filters = {k: v for k, v in (filters or {}).items() if v not in (None, "", [])}
        where = self.build_where(filters)
        if where is None:
            return None            # no scope selected: listing "everything" is meaningless
        try:
            searcher = self._get_searcher()
            coll = searcher.store.collection
            got = coll.get(where=where, include=["documents", "metadatas"], limit=max_chunks + 1)
        except Exception:
            return None

        ids, docs, metas = got.get("ids") or [], got.get("documents") or [], got.get("metadatas") or []
        truncated = len(ids) > max_chunks
        rows, seen = [], {}
        for i, doc, meta in zip(ids[:max_chunks], docs[:max_chunks], metas[:max_chunks]):
            if not doc or not doc.strip():
                continue
            meta = dict(meta or {})
            sig = " ".join(doc.split())[:400].lower()
            if sig in seen:
                kept = seen[sig]
                kept.setdefault("same_text_in", []).append(
                    {"version": meta.get("version"),
                     "document_version_id": meta.get("document_version_id")})
                if _newer(meta, kept["metadata"]):
                    kept["metadata"], kept["chunk_id"] = meta, i
                continue
            c = {"chunk_id": i, "text": doc.strip(), "metadata": meta, "cosine": 0.0,
                 "scope_listing": True}
            seen[sig] = c
            rows.append(c)

        def order(c):
            m = c["metadata"]
            try:
                n = int(m.get("chunk_number"))
            except (TypeError, ValueError):
                n = 0
            return (str(m.get("file_name") or ""), n)

        rows.sort(key=order)
        rows, bad = _scope_violations(rows, filters)
        return {"chunks": rows, "total": len(rows), "truncated": truncated,
                "violations_dropped": len(bad)}

    # ------------------------------------------------------------- helpers

    @staticmethod
    def _fuse(cands, use_rerank, k=60):
        """Reciprocal-rank fusion: vector, cross-encoder and lexical ranks."""

        def ranks(key, reverse=True):
            order = sorted(cands, key=lambda c: c.get(key, 0.0), reverse=reverse)
            return {id(c): i + 1 for i, c in enumerate(order)}

        r_vec = ranks("cosine")
        r_lex = ranks("lexical")
        r_cross = ranks("rerank_prob") if use_rerank else None

        for c in cands:
            score = 0.8 / (k + r_vec[id(c)]) + 0.5 / (k + r_lex[id(c)])
            if r_cross is not None:
                score += 1.0 / (k + r_cross[id(c)])
            if c.get("ref_match"):
                score += 0.03    # exact requirement reference named in the question
            c["fused"] = score

    @staticmethod
    def _result(final, all_cands, diag, t0, token, filters):

        candidates_view = []
        for c in all_cands[:30]:
            candidates_view.append({
                "chunk_id": c["chunk_id"],
                "file_name": c["metadata"].get("file_name", ""),
                "chunk_number": c["metadata"].get("chunk_number"),
                "cosine": round(c["cosine"], 4),
                "rerank_prob": round(c["rerank_prob"], 4) if "rerank_prob" in c else None,
                "lexical": round(c.get("lexical", 0.0), 4),
                "fused": round(c.get("fused", 0.0), 5),
                "selected": bool(c.get("selected")),
                "drop_reason": ("below_threshold" if c.get("below_threshold")
                                else c.get("drop_reason", "")),
            })

        return {"success": True, "chunks": final, "diagnostics": diag,
                "candidates": candidates_view, "knowledge_token": token,
                "filters": filters, "cache_hit": False}
