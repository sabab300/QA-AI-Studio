"""
QA AI Studio
Production Context Builder

Version: 5.0
"""

from Core.logger import Logger
from Core.context_optimizer import ContextOptimizer
from Core.citation_engine import CitationEngine


class ContextBuilder:

    DEFAULT_MAX_CHARACTERS = 12000


    def __init__(self):

        self.logger = Logger.get_logger()

        self.optimizer = ContextOptimizer()

        self.citation = CitationEngine()


    # --------------------------------------------------
    # Normalize Document
    # --------------------------------------------------

    def _normalize(self, text):

        if not isinstance(text, str):

            return ""


        text = text.replace(
            "\r\n",
            "\n"
        )

        text = text.replace(
            "\r",
            "\n"
        )


        lines = []

        previous = None


        for line in text.split("\n"):

            stripped = line.rstrip()


            if not stripped:

                lines.append("")

                continue


            # Preserve technical formatting
            # JSON, SQL, API examples, tables

            if stripped == previous:

                continue


            previous = stripped

            lines.append(stripped)



        result = "\n".join(lines)


        # Reduce excessive blank lines

        while "\n\n\n" in result:

            result = result.replace(
                "\n\n\n",
                "\n\n"
            )


        return result.strip()



    # --------------------------------------------------
    # Metadata Header
    # --------------------------------------------------

    def _build_header(

        self,

        metadata,

        index

    ):


        if not metadata:

            return ""


        if index >= len(metadata):

            return ""


        meta = metadata[index]


        if not isinstance(meta, dict):

            return ""



        file_name = meta.get(
            "file_name",
            "Unknown"
        )


        platform = meta.get(
            "platform",
            ""
        )


        category = meta.get(
            "category",
            ""
        )


        module = meta.get(
            "module",
            ""
        )


        chunk = meta.get(
            "chunk_number",
            "-"
        )


        total = meta.get(
            "total_chunks",
            "-"
        )


        return (

            f"[Source={file_name}"

            f" | Platform={platform}"

            f" | Module={module}"

            f" | Category={category}"

            f" | Chunk={chunk}/{total}]"

        )



    # --------------------------------------------------
    # Build Context
    # --------------------------------------------------

    def build(

        self,

        documents,

        metadata=None,

        max_characters=None

    ):


        try:


            if not documents:

                self.logger.info(
                    "No documents available."
                )

                return ""



            if metadata is None:

                metadata = []



            if max_characters is None:

                max_characters = self.DEFAULT_MAX_CHARACTERS



            self.logger.info(

                f"Building context from {len(documents)} documents."

            )



            # ------------------------------------------
            # Context Optimization
            # ------------------------------------------

            documents, metadata = self.optimizer.optimize(

                documents=documents,

                metadata=metadata

            )



            context_parts = []

            seen = set()

            total_size = 0

            included = 0



            for index, document in enumerate(documents):


                document = self._normalize(document)



                if not document:

                    continue



                # duplicate detection

                fingerprint = hash(
                    document[:1000]
                )


                if fingerprint in seen:

                    continue



                seen.add(fingerprint)



                header = self._build_header(

                    metadata,

                    index

                )



                block = document


                if header:

                    block = (

                        header

                        + "\n"

                        + document

                    )



                block_size = len(block)



                if (

                    total_size + block_size

                    >

                    max_characters

                ):

                    self.logger.info(

                        "Context size limit reached."

                    )

                    break



                context_parts.append(block)



                total_size += block_size

                included += 1



            context = "\n\n".join(

                context_parts

            ).strip()



            if not context:

                return ""



            # ------------------------------------------
            # Add citations
            # ------------------------------------------

            references = self.citation.format(

                metadata

            )



            if references:

                context += (

                    "\n\n"

                    + references

                )



            self.logger.info(

                "Context completed | "

                f"Chunks={included} | "

                f"Characters={len(context)}"

            )


            return context



        except Exception as error:


            self.logger.exception(

                f"Context Builder failed: {error}"

            )


            return ""