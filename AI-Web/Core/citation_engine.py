"""
QA AI Studio
Citation Engine

Version: 2.0
Production Ready
"""

from Core.logger import Logger


class CitationEngine:

    def __init__(self):

        self.logger = Logger.get_logger()

    # --------------------------------------------------
    # Safe Metadata Value
    # --------------------------------------------------

    def _value(

        self,

        metadata,

        *keys,

        default=""

    ):

        if not isinstance(metadata, dict):
            return default

        for key in keys:

            value = metadata.get(key)

            if value not in (None, ""):
                return str(value)

        return default

    # --------------------------------------------------
    # Build Citation List
    # --------------------------------------------------

    def build(

        self,

        metadata_list

    ):

        if not metadata_list:
            return []

        citations = []

        seen = set()

        for metadata in metadata_list:

            file_name = self._value(

                metadata,

                "file_name",
                "filename",
                "document",
                "source",
                "title",

                default="Unknown"

            )

            module = self._value(
                metadata,
                "module"
            )

            page = self._value(
                metadata,
                "page",
                "page_number"
            )

            chunk = self._value(
                metadata,
                "chunk_number"
            )

            parts = [file_name]

            if module:
                parts.append(f"Module={module}")

            if page:
                parts.append(f"Page={page}")

            if chunk:
                parts.append(f"Chunk={chunk}")

            citation = " | ".join(parts)

            if citation in seen:
                continue

            seen.add(citation)

            citations.append(citation)

        return citations

    # --------------------------------------------------
    # Format Citation Block
    # --------------------------------------------------

    def format(

        self,

        metadata_list

    ):

        citations = self.build(
            metadata_list
        )

        if not citations:
            return ""

        lines = [

            "",

            "---",

            "References:"

        ]

        for index, citation in enumerate(

            citations,

            start=1

        ):

            lines.append(

                f"{index}. {citation}"

            )

        return "\n".join(lines)

    # --------------------------------------------------
    # Append References
    # --------------------------------------------------

    def append(

        self,

        response,

        metadata_list

    ):

        if not response:
            return ""

        references = self.format(
            metadata_list
        )

        if not references:
            return response.strip()

        return (

            response.strip()

            + "\n"

            + references

        )

    # --------------------------------------------------
    # Alias
    # --------------------------------------------------

    def build_reference_block(

        self,

        metadata_list

    ):

        return self.format(
            metadata_list
        )