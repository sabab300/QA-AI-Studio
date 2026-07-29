# Create: App/UI/QAAutomation/git_automation_worker.py

"""
==========================================================
QA AI Studio

Git Automation Worker

Version : 1.0

Runs Git operations (pull, push, merge, commit-message
generation) off the UI thread — pull/push depend on network
speed and shouldn't freeze the app.
==========================================================
"""

from PySide6.QtCore import QObject, Signal

from Core.git_service import GitService


class GitOperationWorker(QObject):

    started = Signal()

    progress = Signal(str)

    finished = Signal(dict)

    error = Signal(str)

    def __init__(self, config, operation, **kwargs):
        """
        config: dict with repo_path, remote_url, username, token
        operation: "pull" | "push" | "merge" | "commit" |
                   "commit_message" | "clone_or_open"
        kwargs: operation-specific arguments
        """

        super().__init__()

        self.config = config

        self.operation = operation

        self.kwargs = kwargs

    def run(self):

        try:

            self.started.emit()

            service = GitService(
                repo_path=self.config.get("repo_path", ""),
                remote_url=self.config.get("remote_url", ""),
                username=self.config.get("username", ""),
                token=self.config.get("token", ""),
            )

            if self.operation == "clone_or_open":

                self.progress.emit("Opening / cloning repository...")

                service.open_or_clone()

                result = {"success": True}

            elif self.operation == "pull":

                self.progress.emit("Pulling from remote...")

                result = service.pull(
                    self.kwargs.get("branch")
                )

            elif self.operation == "push":

                self.progress.emit("Pushing to remote...")

                result = service.push(
                    self.kwargs.get("branch")
                )

            elif self.operation == "commit":

                self.progress.emit("Committing changes...")

                result = service.add_and_commit(
                    self.kwargs.get("files"),
                    self.kwargs.get("message"),
                )

            elif self.operation == "merge":

                self.progress.emit(
                    f"Merging '{self.kwargs.get('source_branch')}'..."
                )

                result = service.merge(
                    self.kwargs.get("source_branch")
                )

            elif self.operation == "commit_message":

                self.progress.emit(
                    "Asking AI to write a commit message..."
                )

                result = service.generate_commit_message()

            else:

                raise ValueError(
                    f"Unknown Git operation: {self.operation}"
                )

            self.finished.emit(result)

        except Exception as ex:

            self.error.emit(str(ex))