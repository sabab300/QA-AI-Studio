from pathlib import Path
import shutil


class ArchiveManager:

    def __init__(
        self,
        repository="Repository",
        archive="Repository_Archive"
    ):

        self.repository = Path(repository)

        self.archive = Path(archive)

        self.archive.mkdir(
            exist_ok=True
        )

    def archive_file(self, file_path):

        file_path = Path(file_path)

        if not file_path.exists():

            return False

        destination = self.archive / file_path.name

        shutil.copy2(
            file_path,
            destination
        )

        return destination