"""
QA AI Studio
Production RAG Engine

Version: 6.0
"""

import time

from Core.logger import Logger
from Core.retrieval_engine import RetrievalEngine
from Core.context_builder import ContextBuilder
from Core.citation_engine import CitationEngine
from Core.retrieval_cache import RetrievalCache


class RAGEngine:


    def __init__(self):

        self.logger = Logger.get_logger()

        self.retrieval = RetrievalEngine()

        self.builder = ContextBuilder()

        self.citation = CitationEngine()

        self.cache = RetrievalCache()



    # --------------------------------------------------
    # Cache Key
    # --------------------------------------------------

    def _cache_key(

        self,

        question,

        top_k,

        domain,

        module,

        knowledge_name,

        version,
        source_file_names=None,

    ):

        return "|".join([

            str(question).lower().strip(),

            str(top_k),

            str(domain),

            str(module),

            str(knowledge_name),

            str(version),
            ",".join(sorted(source_file_names or []))

        ])



    # --------------------------------------------------
    # Retrieve Context
    # --------------------------------------------------

    def get_context(

        self,

        question,

        top_k=5,

        domain=None,

        module=None,

        knowledge_name=None,

        version=None,
        source_file_names=None,

    ):


        if not question:

            return self._empty_result(
                "Question is empty."
            )


        start = time.perf_counter()


        try:


            cache_key = self._cache_key(

                question,

                top_k,

                domain,

                module,

                knowledge_name,

                version,
                source_file_names,

            )


            cached = self.cache.get(
                cache_key
            )


            if cached:


                self.logger.info(
                    "RAG Cache Hit"
                )


                return cached



            self.logger.info(

                f"RAG Query: {question}"

            )



            retrieved = self.retrieval.retrieve(

                question=question,

                top_k=top_k,

                domain=domain,

                module=module,

                knowledge_name=knowledge_name,

                version=version,
                source_file_names=source_file_names,

            )



            if not isinstance(
                retrieved,
                dict
            ):

                return self._empty_result(

                    "Invalid retrieval response."

                )



            if not retrieved.get(
                "success"
            ):

                return self._empty_result(

                    retrieved.get(
                        "error",
                        "No retrieval result."
                    )

                )



            documents = retrieved.get(

                "documents",

                []

            )


            metadata = retrieved.get(

                "metadata",

                []

            )

            scores = retrieved.get(

                "scores",

                []

            )

            if source_file_names:
                allowed_sources = {str(name) for name in source_file_names if name}
                selected = [
                    index for index, item in enumerate(metadata)
                    if str((item or {}).get("file_name") or "") in allowed_sources
                ]
                documents = [documents[index] for index in selected]
                metadata = [metadata[index] for index in selected]
                scores = [scores[index] for index in selected if index < len(scores)]

            if not documents:


                return self._empty_result(

                    "No relevant knowledge found."

                )



            context = self.builder.build(

                documents=documents,

                metadata=metadata

            )



            citations = self.citation.build(

                metadata

            )



            confidence = self.calculate_confidence(

                documents,

                metadata,

                scores

            )



            elapsed = round(

                time.perf_counter()
                -
                start,

                2

            )



            result = {


                "success": True,


                "context": context,


                "documents": documents,


                "references": metadata,


                "citations": citations,


                "confidence": confidence,


                "total_chunks": len(documents),


                "domain": domain,


                "module": module,


                "knowledge_name": knowledge_name,


                "version": version,


                "execution_time": elapsed

            }



            self.cache.save(

                cache_key,

                result

            )


            self.logger.info(

                f"RAG completed | Chunks={len(documents)} | Time={elapsed}s"

            )


            return result



        except Exception as ex:


            self.logger.exception(

                "RAG Engine failed."

            )


            return self._empty_result(

                str(ex)

            )
        
    # --------------------------------------------------
    # Backward Compatibility
    # --------------------------------------------------

    def search(

        self,

        question,

        top_k=5,

        domain=None,

        module=None,

        knowledge_name=None,

        version=None

    ):

        """
        Backward compatibility wrapper.

        Existing modules/tests may still call:

            rag.search(...)

        Internally this redirects to:

            get_context(...)
        """

        return self.get_context(

            question=question,

            top_k=top_k,

            domain=domain,

            module=module,

            knowledge_name=knowledge_name,

            version=version

        )



    # --------------------------------------------------
    # Confidence Calculation
    # --------------------------------------------------

    def calculate_confidence(

        self,

        documents,

        metadata,

        scores=None

    ):


        if not documents:

            return 0



        score = 0



        count = len(documents)



        if count >= 1:

            score += 30


        if count >= 3:

            score += 25


        if count >= 5:

            score += 15



        if metadata:

            score += 15



        valid_meta = sum(

            1

            for item in metadata

            if isinstance(item, dict)

            and (

                item.get("domain")

                or item.get("module")

                or item.get("knowledge_name")

            )

        )



        if valid_meta:

            score += 10



        if scores:


            try:

                avg_score = sum(scores) / len(scores)


                if avg_score > 0.8:

                    score += 5


            except Exception:

                pass



        return min(

            score,

            100

        )



    # --------------------------------------------------
    # Empty Result
    # --------------------------------------------------

    def _empty_result(

        self,

        error=None

    ):


        return {


            "success": False,


            "error": error,


            "context": "",


            "documents": [],


            "references": [],


            "citations": [],


            "confidence": 0,


            "total_chunks": 0

        }
