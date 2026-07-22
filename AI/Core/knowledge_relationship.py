"""
QA AI Studio
Knowledge Relationship Manager

Version: 1.0
"""

from Core.metadata_manager import MetadataManager


class KnowledgeRelationship:

    def __init__(self):

        self.metadata = MetadataManager()

    # --------------------------------------------------

    def related(

        self,

        knowledge_id

    ):

        item = self.metadata.get(knowledge_id)

        if not item:

            return []

        domain = item[1]
        module = item[2]

        related = []

        seen = set()

        for row in self.metadata.list_all():

            if row[0] == knowledge_id:

                continue

            if row[1] != domain or row[2] != module:

                continue

            key = (

                row[3],   # knowledge_name
                row[4]    # version

            )

            if key in seen:

                continue

            seen.add(key)

            related.append({

                "id": row[0],

                "knowledge_name": row[3],

                "version": row[4]

            })

            return related