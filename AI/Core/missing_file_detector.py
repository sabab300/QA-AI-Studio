from pathlib import Path

from Core.metadata_manager import MetadataManager


class MissingFileDetector:

    def __init__(self):

        self.metadata = MetadataManager()

    def scan(self):

        missing = []

        for item in self.metadata.list_all():

            file_path = item[8]

            if not Path(file_path).exists():

                missing.append({

                    "id": item[0],

                    "knowledge_name": item[3],

                    "version": item[4],

                    "file": file_path

                })

        return missing