"""
QA AI Studio
Knowledge Manager

Version: 4.0

Provides a single interface for managing
Knowledge Items.
"""

from Core.metadata_manager import MetadataManager


class KnowledgeManager:

    def __init__(self):

        self.metadata = MetadataManager()

    # --------------------------------------------------

    def list_all(self):

        return self.metadata.list_all()

    # --------------------------------------------------

    def get(

        self,

        knowledge_id

    ):

        return self.metadata.get(
            knowledge_id
        )

    # --------------------------------------------------

    def delete(

        self,

        knowledge_id

    ):

        return self.metadata.delete(
            knowledge_id
        )

    # --------------------------------------------------

    def tree(self):

        return self.metadata.get_tree()
    
    # --------------------------------------------------

    def search(

        self,

        keyword

    ):

        return self.metadata.search(
            keyword
        )
    
    # --------------------------------------------------

    def versions(

        self,

        knowledge_name

    ):

        return self.metadata.get_versions(
            knowledge_name
        )