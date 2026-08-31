"""
QA AI Studio
Hybrid Search

Version: 2.0
Production Ready
"""

import re

from Core.logger import Logger


class HybridSearch:

    def __init__(self):

        self.logger = Logger.get_logger()

    # --------------------------------------------------
    # Normalize
    # --------------------------------------------------

    def _normalize(

        self,

        text

    ):

        if not isinstance(text, str):

            return ""

        text = text.lower()

        text = re.sub(
            r"\s+",
            " ",
            text
        )

        return text.strip()

    # --------------------------------------------------
    # Keyword Score
    # --------------------------------------------------

    def _keyword_score(

        self,

        question,

        document

    ):

        query_words = {

            word

            for word in self._normalize(question).split()

            if len(word) > 2

        }

        if not query_words:

            return 0

        text = self._normalize(document)

        score = 0

        for word in query_words:

            if word in text:

                score += 1

        if self._normalize(question) in text:

            score += 5

        return score

    # --------------------------------------------------
    # Metadata Score
    # --------------------------------------------------

    def _metadata_score(

        self,

        metadata,

        question

    ):

        if not isinstance(metadata, dict):

            return 0

        score = 0

        query_words = {

            word

            for word in self._normalize(question).split()

            if len(word) > 2

        }

        fields = [

            "title",

            "file_name",

            "module",

            "platform",

            "category",

            "business_process"

        ]

        for field in fields:

            value = self._normalize(

                metadata.get(

                    field,

                    ""

                )

            )

            if not value:

                continue

            for word in query_words:

                if word in value:

                    score += 2

        return score

    # --------------------------------------------------
    # Hybrid Re-rank
    # --------------------------------------------------

    def rerank(

        self,

        question,

        documents,

        metadata

    ):

        if not documents:

            return [], []

        ranked = []

        seen = set()

        for document, meta in zip(

            documents,

            metadata

        ):

            if not document:

                continue

            signature = document[:600].lower()

            if signature in seen:

                continue

            seen.add(signature)

            score = (

                self._keyword_score(

                    question,

                    document

                )

                +

                self._metadata_score(

                    meta,

                    question

                )

            )

            ranked.append(

                (

                    score,

                    document,

                    meta

                )

            )

        ranked.sort(

            key=lambda item: item[0],

            reverse=True

        )

        final_documents = [

            item[1]

            for item in ranked

        ]

        final_metadata = [

            item[2]

            for item in ranked

        ]

        self.logger.info(

            "Hybrid Search ranked %s chunks.",

            len(final_documents)

        )

        return (

            final_documents,

            final_metadata

        )