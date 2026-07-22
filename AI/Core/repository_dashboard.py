from Core.repository_statistics import RepositoryStatistics
from Core.repository_health import RepositoryHealth
from Core.backup_manager import BackupManager


class RepositoryDashboard:

    def __init__(self):

        self.statistics = RepositoryStatistics()
        self.health = RepositoryHealth()
        self.backup = BackupManager()

    def summary(self):

        stats = self.statistics.summary()

        return {

            "total_files": stats["total_files"],

            "repository_size_mb": stats["total_size_mb"],

            "file_types": len(stats["extensions"]),

            "extensions": stats["extensions"],

            "health": self.health.check(),

            "backups": len(self.backup.list_backups()),

            "latest_backup": (
                self.backup.list_backups()[0]
                if self.backup.list_backups()
                else None
            )

        }