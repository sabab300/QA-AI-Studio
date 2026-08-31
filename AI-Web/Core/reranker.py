"""
QA AI Studio
Cross Encoder Re-Ranker

Version: 4.0
Production Ready
"""

import time

from sentence_transformers import CrossEncoder

from Config import settings
from Core.logger import Logger


class ReRanker:

    _model = None
    _logger = None

    # --------------------------------------------------

    def __init__(self):

        if ReRanker._logger is None:
            ReRanker._logger = Logger.get_logger()

        self.logger = ReRanker._logger

        if ReRanker._model is None:

            self.logger.info(
                "Loading Cross Encoder model..."
            )

            start = time.perf_counter()

            ReRanker._model = CrossEncoder(
                settings.RERANK_MODEL
            )

            elapsed = time.perf_counter() - start

            self.logger.info(
                "Cross Encoder loaded in %.2f sec.",
                elapsed
            )

        self.model = ReRanker._model

    # --------------------------------------------------
    # Re-rank Documents
    # --------------------------------------------------

    def rerank(

        self,

        question,

        documents,

        top_k=5

    ):

        if not documents:
            return []

        try:

            start = time.perf_counter()

            pairs = [

                (question, document)

                for document in documents

            ]

            scores = self.model.predict(
                pairs
            )

            ranked = []

            for document, score in zip(
                documents,
                scores
            ):

                ranked.append(

                    {

                        "document": document,

                        "score": float(score)

                    }

                )

            ranked.sort(

                key=lambda item: item["score"],

                reverse=True

            )

            selected = []

            seen = set()

            for item in ranked:

                document = item["document"].strip()

                signature = document[:500].lower()

                if signature in seen:
                    continue

                seen.add(signature)

                selected.append(document)

                if len(selected) >= top_k:
                    break

            elapsed = time.perf_counter() - start

            self.logger.info(

                "Re-ranking completed in %.2f sec. (%s → %s)",

                elapsed,

                len(documents),

                len(selected)

            )

            return selected

        except Exception as error:

            self.logger.exception(

                "Re-ranking failed: %s",

                error

            )

            return documents[:top_k]