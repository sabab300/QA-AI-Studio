from Core.metadata_manager import MetadataManager
from pathlib import Path


class OrphanMetadata:

    def __init__(self):

        self.metadata = MetadataManager()

    def scan(self):

        orphans = []

        for item in self.metadata.list_all():

            if not Path(item[8]).exists():

                orphans.append({

                    "id": item[0],
                    "knowledge_name": item[3],
                    "version": item[4]

                })

        return orphans