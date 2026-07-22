"""
QA AI Studio
Context Optimizer

Version: 3.0
"""

from Config import settings
from Core.logger import Logger


class ContextOptimizer:

    def __init__(self):

        self.logger = Logger.get_logger()

        self.max_chunks = getattr(
            settings,
            "MAX_CONTEXT_CHUNKS",
            3
        )

        self.max_characters = getattr(
            settings,
            "MAX_CONTEXT_CHARACTERS",
            7000
        )

    # --------------------------------------------------
    # Normalize Text
    # --------------------------------------------------

    def _normalize(self, text):

        if not isinstance(text, str):
            return ""

        text = text.replace("\r", "")

        lines = []
        previous = ""

        for line in text.split("\n"):

            line = " ".join(line.strip().split())

            if not line:
                continue

            if line == previous:
                continue

            previous = line

            lines.append(line)

        return "\n".join(lines).strip()

    # --------------------------------------------------
    # Optimize Context
    # --------------------------------------------------

    def optimize(

        self,

        documents,

        metadata=None,

        max_chunks=None,

        max_characters=None

    ):

        try:

            if not documents:
                return [], []

            if metadata is None:
                metadata = []

            if max_chunks is None:
                max_chunks = self.max_chunks

            if max_characters is None:
                max_characters = self.max_characters

            optimized_documents = []
            optimized_metadata = []

            seen = set()

            total_characters = 0

            for index, document in enumerate(documents):

                document = self._normalize(document)

                if not document:
                    continue

                fingerprint = document[:800].lower()

                if fingerprint in seen:
                    continue

                if len(optimized_documents) >= max_chunks:
                    break

                if total_characters + len(document) > max_characters:
                    break

                seen.add(fingerprint)

                optimized_documents.append(document)

                if index < len(metadata):
                    optimized_metadata.append(metadata[index])
                else:
                    optimized_metadata.append({})

                total_characters += len(document)

            self.logger.info(
                "Context optimized: %s → %s chunks (%s characters).",
                len(documents),
                len(optimized_documents),
                total_characters
            )

            return (
                optimized_documents,
                optimized_metadata
            )

        except Exception as error:

            self.logger.exception(
                "Context Optimizer failed: %s",
                error
            )

            return documents, metadata