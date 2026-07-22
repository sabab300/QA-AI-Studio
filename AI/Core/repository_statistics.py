from pathlib import Path


class RepositoryStatistics:

    def __init__(self, repository="Repository"):

        self.repository = Path(repository)

    def summary(self):

        total_files = 0
        total_size = 0

        extensions = {}

        for file in self.repository.rglob("*"):

            if not file.is_file():
                continue

            total_files += 1

            total_size += file.stat().st_size

            ext = file.suffix.lower()

            extensions[ext] = extensions.get(ext, 0) + 1

        return {

            "total_files": total_files,

            "total_size_mb": round(
                total_size / (1024 * 1024),
                2
            ),

            "extensions": dict(
                sorted(extensions.items())
            )

        }