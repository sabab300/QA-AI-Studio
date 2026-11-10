"""
QA AI Studio
Production Retrieval Engine

Version: 5.0
"""

import time
import json

from Core.search import KnowledgeSearch
from Core.embedding_engine import EmbeddingEngine
from Core.reranker import ReRanker
from Core.logger import Logger
from Core.hybrid_search import HybridSearch
from Core.retrieval_cache import RetrievalCache
from Core.metadata_filter import MetadataFilter


class RetrievalEngine:


    def __init__(self):

        self.logger = Logger.get_logger()

        self.embedding = EmbeddingEngine()

        self.reranker = ReRanker()

        self.hybrid = HybridSearch()

        self.cache = RetrievalCache()

        self.filter = MetadataFilter()

        self.search = KnowledgeSearch()



    # --------------------------------------------------
    # Query Expansion
    # --------------------------------------------------

    def expand_query(self, question):

        if not question:

            return ""


        lower = question.lower()


        expansions = {

            "test case": [
                "requirements",
                "business rules",
                "validation rules",
                "workflow",
                "test scenarios"
            ],

            "test cases": [
                "requirements",
                "business rules",
                "validation rules",
                "workflow",
                "test scenarios"
            ],

            "sd warehousing": [
                "single declaration warehousing",
                "in bond",
                "ex bond",
                "warehouse",
                "NOC"
            ]

        }


        expanded = [question]


        for key, values in expansions.items():

            if key in lower:

                expanded.extend(values)


        return " ".join(expanded)



    # --------------------------------------------------
    # Cache Key
    # --------------------------------------------------

    def _cache_key(

        self,

        question,

        domain,

        module,

        knowledge_name,

        version,
        source_file_names=None,
        document_type=None,
        document_version_id=None,

    ):

        return json.dumps([

            str(question),

            str(domain),

            str(module),

            str(knowledge_name),

            str(version),
            sorted(source_file_names or []), str(document_type), document_version_id,

        ])




    # --------------------------------------------------
    # Enhanced retrieval (Task 4)
    # --------------------------------------------------

    _ranked = None

    def _retrieve_enhanced(
        self, question, domain, module, knowledge_name, version,
        source_file_names, top_k, rerank_top, min_score,
        document_type, document_version_id, start,
    ):

        try:

            from Core.ai_platform import runtime
            from Core.ai_platform.retrieval import Retriever

            from Core.ai_platform import fallback as _fb

            if _fb.env_disabled():

                return None

            rag = dict(runtime.get_store().get_section("rag"))

            if not rag.get("enhanced_pipeline", True):

                return None

            if RetrievalEngine._ranked is None:

                RetrievalEngine._ranked = Retriever.default()

            final = int(rerank_top or 5)

            rag.update({
                "candidate_pool": max(int(top_k or 20), int(rag.get("candidate_pool", 24))),
                "top_k": final,
                "max_chunks_per_document": final,
                "similarity_threshold": float(min_score or 0.0),
            })

            filters = {
                "domain": domain, "module": module,
                "knowledge_name": knowledge_name, "version": version,
                "document_type": document_type,
                "document_version_id": document_version_id,
                "source_file_names": source_file_names,
            }

            result = RetrievalEngine._ranked.retrieve(
                question, rag, filters,
                query_text=self.expand_query(question),
            )

            chunks = result.get("chunks") or []

            if not chunks:

                return self._empty("No relevant knowledge found.")

            return {
                "success": True,
                "documents": [c["text"] for c in chunks],
                "metadata": [c["metadata"] for c in chunks],
                "scores": [c["cosine"] for c in chunks],
                "execution_time": round(time.perf_counter() - start, 2),
            }

        except Exception as exc:

            # QA-AI-STUDIO-TASK4 (Phase 5): only the documented recoverable
            # failure (vector store / embedder unavailable) may use the
            # legacy retrieval, and it is logged + audited. Anything else
            # (permission, privacy, configuration, security, unexpected) is
            # returned as an error - never silently retried.
            try:

                from Core.ai_platform import fallback as fb

            except ImportError:

                # enhanced components missing: documented recoverable case
                self.logger.warning(
                    "Enhanced retrieval components unavailable; "
                    "using legacy retrieval."
                )

                return None

            info = fb.classify(exc)

            if isinstance(exc, ImportError):

                info = {"recoverable": True,
                        "category": "enhanced_component_unavailable",
                        "message": str(exc)}

            if info["recoverable"]:

                self.logger.warning(
                    "Enhanced retrieval: recoverable failure (%s); "
                    "using legacy retrieval.", info["category"],
                )

                fb.audit(None, info)

                return None

            self.logger.exception(
                "Enhanced retrieval failed (%s); NOT falling back.",
                info["category"],
            )

            return self._empty(info["message"])

    # --------------------------------------------------
    # Retrieve
    # --------------------------------------------------

    def retrieve(

        self,

        question,

        domain=None,

        module=None,

        knowledge_name=None,

        version=None,
        source_file_names=None,

        top_k=20,

        rerank_top=5,

        min_score=None,
        document_type=None,
        document_version_id=None,

    ):


        start = time.perf_counter()

        # QA-AI-STUDIO-TASK4: delegate to the ranked retriever (candidate
        # pool, correct L2->cosine similarity, score-aligned cross-encoder
        # reranking, reciprocal-rank fusion). Same return shape, so RAGEngine
        # and Impact Analysis benefit unchanged. Set rag.enhanced_pipeline to
        # false in Settings -> LLM -> Knowledge / RAG to use the legacy path.
        enhanced = self._retrieve_enhanced(
            question, domain, module, knowledge_name, version,
            source_file_names, top_k, rerank_top, min_score,
            document_type, document_version_id, start,
        )

        if enhanced is not None:

            return enhanced


        try:


            cache_key = self._cache_key(

                question,

                domain,

                module,

                knowledge_name,

                version,
                source_file_names,
                document_type,
                document_version_id,

            )


            cached = self.cache.get(
                cache_key
            )


            if cached:

                self.logger.info(
                    "Retrieval Cache Hit"
                )

                return cached



            enhanced_question = self.expand_query(
                question
            )


            self.logger.info(
                f"Retrieval query: {enhanced_question}"
            )


            self.filter.build(

                domain=domain,

                module=module,

                knowledge_name=knowledge_name,

                version=version,
                document_type=document_type,
                document_version_id=document_version_id,

            )


            result = self.search.search_knowledge(

                query=enhanced_question,

                top_k=top_k,

                domain=domain,

                module=module,

                knowledge_name=knowledge_name,

                version=version,
                source_file_names=source_file_names,
                document_type=document_type,
                document_version_id=document_version_id,

            )



            if not isinstance(result, dict):

                return self._empty()



            documents = result.get(
                "documents",
                []
            )


            metadata = result.get(
                "metadatas",
                []
            )


            distances = result.get(
                "distances",
                []
            )


            documents = documents[0] if documents else []

            metadata = metadata[0] if metadata else []

            distances = distances[0] if distances else []



            candidates = []


            seen = set()


            for doc, meta, distance in zip(

                documents,

                metadata,

                distances

            ):


                if not doc:

                    continue


                similarity = max(

                    0,

                    1 - float(distance)

                )


                if min_score and similarity < min_score:

                    continue


                key = doc.strip()


                if key in seen:

                    continue


                seen.add(key)


                candidates.append({

                    "document": key,

                    "metadata": meta or {},

                    "score": similarity

                })



            if not candidates:


                return self._empty(
                    "No relevant knowledge found."
                )



            reranked = self.reranker.rerank(

                question,

                [

                    item["document"]

                    for item in candidates

                ],

                top_k=rerank_top

            )



            final_docs = []

            final_meta = []

            final_scores = []



            for doc in reranked:


                for item in candidates:


                    if item["document"] == doc:


                        final_docs.append(doc)

                        final_meta.append(
                            item["metadata"]
                        )

                        final_scores.append(
                            item["score"]
                        )

                        break



            if not final_docs:


                final_docs = [

                    item["document"]

                    for item in candidates[:rerank_top]

                ]

                final_meta = [

                    item["metadata"]

                    for item in candidates[:rerank_top]

                ]



            final_docs, final_meta = self.hybrid.rerank(

                question,

                final_docs,

                final_meta

            )



            result = {

                "success": True,

                "documents": final_docs,

                "metadata": final_meta,

                "scores": final_scores,

                "execution_time":
                    round(
                        time.perf_counter()-start,
                        2
                    )

            }



            self.cache.save(

                cache_key,

                result

            )


            self.logger.info(

                f"Retrieved {len(final_docs)} chunks"

            )


            return result



        except Exception as ex:


            self.logger.exception(
                "Retrieval Engine failed"
            )


            return self._empty(
                str(ex)
            )



    # --------------------------------------------------

    def _empty(self,error=None):

        return {

            "success": False,

            "error": error,

            "documents": [],

            "metadata": [],

            "scores": []

        }
