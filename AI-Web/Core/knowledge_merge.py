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

            # BUGFIX: this used to read item[14]/item[15] (raw positional
            # tuple indexing into "SELECT * FROM knowledge_items"). That
            # only worked by coincidence of the column order the schema
            # migrations happen to apply today — any future ALTER TABLE
            # inserted earlier in the column list, or any table-recreate
            # that reorders columns, would silently merge the WRONG
            # fields (e.g. platform into "summary") with no error at all.
            # item is a sqlite3.Row (row_factory set in
            # MetadataManager.get()), which supports name-based access
            # exactly like a dict — so index by column name instead.
            if item["summary"]:

                merged_summary.append(item["summary"])

            if item["tags"]:

                try:

                    import json

                    tags = json.loads(item["tags"])

                    merged_tags.update(tags)

                except Exception:

                    pass

        return {

            "count": len(items),

            "summary": "\n\n".join(merged_summary),

            "tags": sorted(merged_tags)

        }