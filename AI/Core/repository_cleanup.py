from Core.metadata_manager import MetadataManager


class RepositoryCleanup:

    def __init__(self):

        self.metadata = MetadataManager()

    def remove_missing_records(self):

        removed = 0

        for item in self.metadata.list_all():

            knowledge_id = item[0]

            file_path = item[8]

            from pathlib import Path

            if not Path(file_path).exists():

                self.metadata.delete(knowledge_id)

                removed += 1

        return removed