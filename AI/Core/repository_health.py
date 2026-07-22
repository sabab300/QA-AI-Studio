from pathlib import Path


class RepositoryHealth:

    def __init__(self, repository="Repository"):

        self.repository = Path(repository)

    def check(self):

        issues = []

        if not self.repository.exists():

            issues.append("Repository folder does not exist.")

            return issues

        empty_folders = 0

        for folder in self.repository.rglob("*"):

            if folder.is_dir():

                if not any(folder.iterdir()):

                    empty_folders += 1

        if empty_folders:

            issues.append(f"Empty folders : {empty_folders}")

        empty_files = 0

        for file in self.repository.rglob("*"):

            if file.is_file():

                if file.stat().st_size == 0:

                    empty_files += 1

        if empty_files:

            issues.append(f"Empty files : {empty_files}")

        if not issues:

            issues.append("Repository Healthy")

        return issues