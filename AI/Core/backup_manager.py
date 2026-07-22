from pathlib import Path
from datetime import datetime
import shutil


class BackupManager:

    def __init__(self, repository="Repository", backup_root="Backups"):

        self.repository = Path(repository)
        self.backup_root = Path(backup_root)

        self.backup_root.mkdir(
            exist_ok=True
        )

    def backup(self):

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

        destination = self.backup_root / f"Backup_{timestamp}"

        shutil.copytree(
            self.repository,
            destination
        )

        return destination

    def list_backups(self):

        if not self.backup_root.exists():

            return []

        backups = []

        for folder in sorted(
            self.backup_root.iterdir(),
            reverse=True
        ):

            if folder.is_dir():

                backups.append(folder.name)

        return backups