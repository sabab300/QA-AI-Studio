from pathlib import Path
import shutil


class RepositoryImport:

    def import_repository(
        self,
        source,
        destination="Repository_Imported"
    ):

        source = Path(source)
        destination = Path(destination)

        if destination.exists():

            shutil.rmtree(destination)

        shutil.copytree(
            source,
            destination
        )

        return destination