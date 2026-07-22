from pathlib import Path
import shutil


class RepositoryExport:

    def __init__(self, repository="Repository"):

        self.repository = Path(repository)

    def export(self, destination):

        destination = Path(destination)

        if destination.exists():

            shutil.rmtree(destination)

        shutil.copytree(
            self.repository,
            destination
        )

        return destination