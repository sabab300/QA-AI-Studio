"""
QA AI Studio
Knowledge Merge

Version: 1.0
"""

from Core.metadata_manager import MetadataManager


class KnowledgeMerge:

    def __init__(self):

        self.metadata = MetadataManager()

    # --------------------------------------------------

    def merge(

        self,

        knowledge_ids

    ):

        merged_summary = []

        merged_tags = set()

        items = []

        for knowledge_id in knowledge_ids:

            item = self.metadata.get(knowledge_id)

            if not item:

                continue

            items.append(item)

            if item[14]:

                merged_summary.append(item[14])

            if item[15]:

                try:

                    import json

                    tags = json.loads(item[15])

                    merged_tags.update(tags)

                except Exception:

                    pass

        return {

            "count": len(items),

            "summary": "\n\n".join(merged_summary),

            "tags": sorted(merged_tags)

        }