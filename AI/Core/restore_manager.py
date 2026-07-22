from pathlib import Path
import shutil


class RestoreManager:

    def __init__(
        self,
        archive="Repository_Archive"
    ):

        self.archive = Path(archive)

    def restore(
        self,
        file_name,
        destination
    ):

        source = self.archive / file_name

        if not source.exists():

            return False

        destination = Path(destination)

        destination.mkdir(
            parents=True,
            exist_ok=True
        )

        restored = destination / file_name

        shutil.copy2(
            source,
            restored
        )

        return restored