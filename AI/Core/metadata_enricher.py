"""
QA AI Studio
Metadata Enricher

Version: 1.0
"""

from Core.metadata_manager import MetadataManager


class MetadataEnricher:

    def __init__(self):

        self.metadata = MetadataManager()

    # --------------------------------------------------
    # Enrich Search Results
    # --------------------------------------------------

    def enrich(self, results):

        if not results:

            return []

        enriched = []

        for item in results:

            record = self._find_metadata(item)

            if record:

                item["domain"] = record[1]
                item["module"] = record[2]
                item["knowledge_name"] = record[3]
                item["version"] = record[4]
                item["platform"] = record[21]
                item["category"] = record[22]
                item["business_process"] = record[23]
                item["document_type"] = record[24]

            enriched.append(item)

        return enriched

    # --------------------------------------------------
    # Find Metadata
    # --------------------------------------------------

    def _find_metadata(self, item):

        file_name = item.get("file_name")

        if not file_name:

            return None

        records = self.metadata.search(file_name)

        if not records:

            return None

        for record in records:

            if record[6] == file_name:

                return record

        return records[0]