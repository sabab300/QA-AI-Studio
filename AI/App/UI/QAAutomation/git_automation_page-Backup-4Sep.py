# Create: App/UI/QAAutomation/git_automation_page.py

"""
==========================================================
QA AI Studio

QA Automation

Git Automation

Version : 1.0

Buttons (per spec):
    Upload Files, Pull, Push, Merge, Compare,
    Resolve Simple Conflicts, Generate Commit Messages using AI

Safety note: "Resolve Simple Conflicts" does not guess how to merge
conflicting content. It detects conflicted files and lets you choose,
per file, to keep your version, keep the incoming version, or open
it in your normal editor. See Core/git_service.py for details.
==========================================================
"""

import os
import platform
import subprocess

from pathlib import Path

from PySide6.QtCore import Qt, QThread

from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QGridLayout,
    QLabel,
    QPushButton,
    QLineEdit,
    QGroupBox,
    QTextEdit,
    QPlainTextEdit,
    QMessageBox,
    QScrollArea,
    QSizePolicy,
    QListWidget,
    QListWidgetItem,
    QFileDialog,
    QCheckBox,
)

from Core.git_config_manager import GitConfigManager

from UI.QAAutomation.git_automation_worker import GitOperationWorker


class GitAutomationPage(QWidget):

    def __init__(self):

        super().__init__()

        self.config_manager = GitConfigManager()

        self.thread = None

        self.worker = None

        self.build_ui()

        self.load_config_into_form()


    # ======================================================
    # UI
    # ======================================================

    def build_ui(self):

        outer = QVBoxLayout(self)

        outer.setContentsMargins(0, 0, 0, 0)

        outer.setSpacing(0)

        scroll = QScrollArea()

        scroll.setWidgetResizable(True)

        scroll.setFrameShape(QScrollArea.NoFrame)

        outer.addWidget(scroll)

        content = QWidget()

        scroll.setWidget(content)

        layout = QVBoxLayout(content)

        layout.setContentsMargins(20, 20, 20, 20)

        layout.setSpacing(15)


        title = QLabel("Git Automation")

        title.setObjectName("SectionTitle")

        layout.addWidget(title)


        note = QLabel(
            "Your repository path, URL and token are stored only on "
            "this PC (Config/git_config.json). They are never sent "
            "anywhere except to your own Git server."
        )

        note.setWordWrap(True)

        layout.addWidget(note)


        # ==================================================
        # Configuration
        # ==================================================

        config_group = QGroupBox("Repository Configuration")

        config_layout = QGridLayout(config_group)

        config_layout.setContentsMargins(15, 20, 15, 15)

        config_layout.setHorizontalSpacing(12)

        config_layout.setVerticalSpacing(10)

        config_layout.setColumnStretch(1, 1)


        self.repo_path = QLineEdit()

        self.browse_btn = QPushButton("Browse")

        self.remote_url = QLineEdit()

        self.remote_url.setPlaceholderText(
            "https://dev.azure.com/your-org/your-project/_git/your-repo"
        )

        self.branch = QLineEdit("main")

        self.username = QLineEdit()

        self.token = QLineEdit()

        self.token.setEchoMode(QLineEdit.Password)

        self.save_config_btn = QPushButton("Save Configuration")


        config_layout.addWidget(QLabel("Local Folder"), 0, 0)

        config_layout.addWidget(self.repo_path, 0, 1)

        config_layout.addWidget(self.browse_btn, 0, 2)

        config_layout.addWidget(QLabel("Remote URL"), 1, 0)

        config_layout.addWidget(self.remote_url, 1, 1, 1, 2)

        config_layout.addWidget(QLabel("Branch"), 2, 0)

        config_layout.addWidget(self.branch, 2, 1, 1, 2)

        config_layout.addWidget(QLabel("Username"), 3, 0)

        config_layout.addWidget(self.username, 3, 1, 1, 2)

        config_layout.addWidget(
            QLabel("Access Token"), 4, 0
        )

        config_layout.addWidget(self.token, 4, 1, 1, 2)

        config_layout.addWidget(self.save_config_btn, 5, 2)


        layout.addWidget(config_group)


        # ==================================================
        # Upload Files (add + commit)
        # ==================================================

        upload_group = QGroupBox("Upload Files")

        upload_layout = QVBoxLayout(upload_group)

        upload_layout.setContentsMargins(15, 20, 15, 15)

        upload_layout.setSpacing(8)


        add_row = QHBoxLayout()

        self.add_files_btn = QPushButton("Add Files...")

        self.add_folder_btn = QPushButton("Add Folder...")

        add_row.addWidget(self.add_files_btn)

        add_row.addWidget(self.add_folder_btn)

        upload_layout.addLayout(add_row)

        add_hint = QLabel(
            "Use these to copy test case documents from anywhere "
            "on your PC into this repository, ready to commit below."
        )

        add_hint.setWordWrap(True)

        upload_layout.addWidget(add_hint)


        self.refresh_status_btn = QPushButton("Refresh Changed Files")

        upload_layout.addWidget(self.refresh_status_btn)


        self.changed_files_list = QListWidget()

        self.changed_files_list.setMinimumHeight(120)

        self.changed_files_list.setSizePolicy(
            QSizePolicy.Expanding, QSizePolicy.Fixed
        )

        upload_layout.addWidget(self.changed_files_list)


        message_row = QHBoxLayout()

        self.commit_message = QLineEdit()

        self.commit_message.setPlaceholderText(
            "Commit message..."
        )

        self.ai_message_btn = QPushButton(
            "Generate Commit Message (AI)"
        )

        message_row.addWidget(self.commit_message)

        message_row.addWidget(self.ai_message_btn)

        upload_layout.addLayout(message_row)


        self.push_after_commit = QCheckBox(
            "Push immediately after commit"
        )

        self.push_after_commit.setChecked(True)

        upload_layout.addWidget(self.push_after_commit)


        self.upload_btn = QPushButton("Upload Files")

        self.upload_btn.setMinimumHeight(36)

        upload_layout.addWidget(self.upload_btn)


        layout.addWidget(upload_group)


        # ==================================================
        # Pull / Push / Merge
        # ==================================================

        sync_group = QGroupBox("Sync")

        sync_layout = QGridLayout(sync_group)

        sync_layout.setContentsMargins(15, 20, 15, 15)

        sync_layout.setHorizontalSpacing(12)

        sync_layout.setVerticalSpacing(10)


        self.pull_btn = QPushButton("Pull")

        self.push_btn = QPushButton("Push")

        self.merge_source_branch = QLineEdit()

        self.merge_source_branch.setPlaceholderText(
            "branch to merge in, e.g. feature/cess-waiver"
        )

        self.merge_btn = QPushButton("Merge")

        self.resolve_conflicts_btn = QPushButton(
            "Resolve Simple Conflicts"
        )


        sync_layout.addWidget(self.pull_btn, 0, 0)

        sync_layout.addWidget(self.push_btn, 0, 1)

        sync_layout.addWidget(QLabel("Merge From"), 1, 0)

        sync_layout.addWidget(self.merge_source_branch, 1, 1)

        sync_layout.addWidget(self.merge_btn, 1, 2)

        sync_layout.addWidget(self.resolve_conflicts_btn, 2, 0, 1, 3)


        layout.addWidget(sync_group)


        # ==================================================
        # Conflict Resolution (shown only when conflicts exist)
        # ==================================================

        self.conflict_group = QGroupBox("Conflicted Files")

        self.conflict_layout = QVBoxLayout(self.conflict_group)

        self.conflict_layout.setContentsMargins(15, 20, 15, 15)

        self.conflict_group.setVisible(False)

        layout.addWidget(self.conflict_group)


        # ==================================================
        # Compare
        # ==================================================

        compare_group = QGroupBox("Compare")

        compare_layout = QVBoxLayout(compare_group)

        compare_layout.setContentsMargins(15, 20, 15, 15)


        compare_inputs = QHBoxLayout()

        self.compare_ref_a = QLineEdit()

        self.compare_ref_a.setPlaceholderText(
            "e.g. main  (or leave Ref B empty to compare "
            "against your working directory)"
        )

        self.compare_ref_b = QLineEdit()

        self.compare_ref_b.setPlaceholderText(
            "e.g. feature/cess-waiver (optional)"
        )

        self.compare_btn = QPushButton("Compare")

        compare_inputs.addWidget(self.compare_ref_a)

        compare_inputs.addWidget(self.compare_ref_b)

        compare_inputs.addWidget(self.compare_btn)

        compare_layout.addLayout(compare_inputs)


        self.compare_output = QPlainTextEdit()

        self.compare_output.setReadOnly(True)

        self.compare_output.setMinimumHeight(160)

        self.compare_output.setSizePolicy(
            QSizePolicy.Expanding, QSizePolicy.Fixed
        )

        compare_layout.addWidget(self.compare_output)


        layout.addWidget(compare_group)


        # ==================================================
        # Log
        # ==================================================

        log_group = QGroupBox("Log")

        log_layout = QVBoxLayout(log_group)

        log_layout.setContentsMargins(15, 20, 15, 15)


        self.log = QTextEdit()

        self.log.setReadOnly(True)

        self.log.setMinimumHeight(120)

        self.log.setSizePolicy(
            QSizePolicy.Expanding, QSizePolicy.Fixed
        )

        log_layout.addWidget(self.log)


        layout.addWidget(log_group)


        # ==================================================
        # Events
        # ==================================================

        self.browse_btn.clicked.connect(self.browse_repo_path)

        self.add_files_btn.clicked.connect(self.add_files_to_repo)

        self.add_folder_btn.clicked.connect(self.add_folder_to_repo)

        self.save_config_btn.clicked.connect(self.save_config)

        self.refresh_status_btn.clicked.connect(self.refresh_status)

        self.ai_message_btn.clicked.connect(
            self.generate_commit_message
        )

        self.upload_btn.clicked.connect(self.upload_files)

        self.pull_btn.clicked.connect(self.pull)

        self.push_btn.clicked.connect(self.push)

        self.merge_btn.clicked.connect(self.merge)

        self.resolve_conflicts_btn.clicked.connect(
            self.check_for_conflicts
        )

        self.compare_btn.clicked.connect(self.compare)


    # ======================================================
    # Config
    # ======================================================

    def load_config_into_form(self):

        data = self.config_manager.load()

        self.repo_path.setText(data.get("repo_path", ""))

        self.remote_url.setText(data.get("remote_url", ""))

        self.branch.setText(data.get("branch", "main") or "main")

        self.username.setText(data.get("username", ""))

        self.token.setText(data.get("token", ""))


    def current_config(self):

        return {
            "repo_path": self.repo_path.text().strip(),
            "remote_url": self.remote_url.text().strip(),
            "branch": self.branch.text().strip() or "main",
            "username": self.username.text().strip(),
            "token": self.token.text().strip(),
        }


    def browse_repo_path(self):

        folder = QFileDialog.getExistingDirectory(
            self, "Select or Create Local Repository Folder"
        )

        if folder:

            self.repo_path.setText(folder)


    def add_files_to_repo(self):

        repo_path = self.repo_path.text().strip()

        if not repo_path:

            QMessageBox.warning(
                self,
                "QA AI Studio",
                "Set and save your Local Folder first."
            )

            return

        files, _ = QFileDialog.getOpenFileNames(
            self, "Select Test Case Documents to Add"
        )

        if not files:

            return

        import shutil

        copied = 0

        for file in files:

            try:

                dest = Path(repo_path) / Path(file).name

                shutil.copy2(file, dest)

                copied += 1

            except Exception as ex:

                self.log.append(
                    f"Could not copy {file}: {ex}"
                )

        self.log.append(
            f"Copied {copied} file(s) into the repository."
        )

        self.refresh_status()


    def add_folder_to_repo(self):

        repo_path = self.repo_path.text().strip()

        if not repo_path:

            QMessageBox.warning(
                self,
                "QA AI Studio",
                "Set and save your Local Folder first."
            )

            return

        source_folder = QFileDialog.getExistingDirectory(
            self, "Select Folder of Test Case Documents to Add"
        )

        if not source_folder:

            return

        import shutil

        dest_folder = Path(repo_path) / Path(source_folder).name

        try:

            shutil.copytree(
                source_folder,
                dest_folder,
                dirs_exist_ok=True
            )

            self.log.append(
                f"Copied folder '{Path(source_folder).name}' into "
                f"the repository."
            )

        except Exception as ex:

            QMessageBox.critical(
                self,
                "QA AI Studio",
                f"Could not copy the folder.\n\n{ex}"
            )

            return

        self.refresh_status()


    def save_config(self):

        config = self.current_config()

        if not config["repo_path"]:

            QMessageBox.warning(
                self, "QA AI Studio", "Please choose a local folder."
            )

            return

        self.config_manager.save(**config)

        self.log.append("Git configuration saved.")

        self.run_operation("clone_or_open")


    # ======================================================
    # Background operation runner (shared by every button)
    # ======================================================

    def run_operation(self, operation, on_success=None, **kwargs):

        if self.thread is not None:

            QMessageBox.information(
                self,
                "QA AI Studio",
                "Please wait for the current Git operation to finish."
            )

            return

        config = self.current_config()

        if not config["repo_path"]:

            QMessageBox.warning(
                self, "QA AI Studio", "Please configure a local folder first."
            )

            return

        self.set_buttons_enabled(False)

        self.thread = QThread()

        self.worker = GitOperationWorker(config, operation, **kwargs)

        self.worker.moveToThread(self.thread)

        self.thread.started.connect(self.worker.run)

        self.worker.progress.connect(self.log.append)

        self.worker.finished.connect(
            lambda result: self.on_operation_finished(
                operation, result, on_success
            )
        )

        self.worker.error.connect(self.on_operation_error)

        self.worker.finished.connect(self.thread.quit)

        self.worker.error.connect(self.thread.quit)

        self.thread.finished.connect(self.cleanup_thread)

        self.thread.start()


    def set_buttons_enabled(self, enabled):

        for btn in (
            self.upload_btn,
            self.pull_btn,
            self.push_btn,
            self.merge_btn,
            self.resolve_conflicts_btn,
            self.compare_btn,
            self.ai_message_btn,
            self.save_config_btn,
        ):

            btn.setEnabled(enabled)


    def on_operation_finished(self, operation, result, on_success):

        self.set_buttons_enabled(True)

        if not result.get("success"):

            if result.get("conflict"):

                self.log.append(result.get("error", "Conflict detected."))

                self.show_conflicts(
                    result.get("conflicted_files", [])
                )

                QMessageBox.warning(
                    self,
                    "Merge Conflict",
                    "This operation caused conflicts. Scroll down to "
                    "'Conflicted Files' to resolve them, then commit."
                )

            else:

                self.log.append(
                    f"Failed: {result.get('error', 'Unknown error')}"
                )

                QMessageBox.critical(
                    self, "Git Operation Failed",
                    result.get("error", "Unknown error")
                )

            return

        self.log.append(f"{operation} completed successfully.")

        if on_success:

            on_success(result)


    def on_operation_error(self, message):

        self.set_buttons_enabled(True)

        self.log.append(f"Error: {message}")

        QMessageBox.critical(self, "Git Operation Failed", message)


    def cleanup_thread(self):

        if self.thread:

            self.thread.deleteLater()

        self.thread = None

        self.worker = None


    # ======================================================
    # Status / Upload Files
    # ======================================================

    def refresh_status(self):

        config = self.current_config()

        if not config["repo_path"] or not Path(config["repo_path"], ".git").exists():

            QMessageBox.information(
                self,
                "QA AI Studio",
                "Save your configuration first (this will open or "
                "clone the repository)."
            )

            return

        try:

            from Core.git_service import GitService

            service = GitService(**config)

            status = service.status()

            self.changed_files_list.clear()

            for path in status["modified"] + status["untracked"]:

                item = QListWidgetItem(path)

                item.setFlags(item.flags() | Qt.ItemIsUserCheckable)

                item.setCheckState(Qt.Checked)

                self.changed_files_list.addItem(item)

            self.log.append(
                f"Branch: {status['branch']} | "
                f"{len(status['modified'])} modified, "
                f"{len(status['untracked'])} untracked."
            )

        except Exception as ex:

            QMessageBox.critical(self, "QA AI Studio", str(ex))


    def get_checked_files(self):

        files = []

        for i in range(self.changed_files_list.count()):

            item = self.changed_files_list.item(i)

            if item.checkState() == Qt.Checked:

                files.append(item.text())

        return files


    def upload_files(self):

        files = self.get_checked_files()

        message = self.commit_message.text().strip()

        if not files:

            QMessageBox.warning(
                self,
                "QA AI Studio",
                "No files selected. Click 'Refresh Changed Files' first."
            )

            return

        if not message:

            QMessageBox.warning(
                self, "QA AI Studio", "Please enter a commit message."
            )

            return

        def after_commit(result):

            self.commit_message.clear()

            self.changed_files_list.clear()

            if self.push_after_commit.isChecked():

                self.run_operation(
                    "push",
                    branch=self.current_config()["branch"],
                )

        self.run_operation(
            "commit",
            on_success=after_commit,
            files=files,
            message=message,
        )


    # ======================================================
    # Pull / Push / Merge
    # ======================================================

    def pull(self):

        self.run_operation(
            "pull", branch=self.current_config()["branch"]
        )


    def push(self):

        self.run_operation(
            "push", branch=self.current_config()["branch"]
        )


    def merge(self):

        source = self.merge_source_branch.text().strip()

        if not source:

            QMessageBox.warning(
                self, "QA AI Studio", "Enter a branch name to merge."
            )

            return

        self.run_operation(
            "merge", source_branch=source
        )


    # ======================================================
    # Conflicts
    # ======================================================

    def check_for_conflicts(self):

        try:

            from Core.git_service import GitService

            service = GitService(**self.current_config())

            conflicts = service.list_conflicts()

            if not conflicts:

                QMessageBox.information(
                    self, "QA AI Studio", "No conflicts found."
                )

                self.conflict_group.setVisible(False)

                return

            self.show_conflicts(conflicts)

        except Exception as ex:

            QMessageBox.critical(self, "QA AI Studio", str(ex))


    def show_conflicts(self, conflicted_files):

        # Clear old rows.
        while self.conflict_layout.count():

            child = self.conflict_layout.takeAt(0)

            if child.widget():

                child.widget().deleteLater()

        if not conflicted_files:

            self.conflict_group.setVisible(False)

            return

        for file_path in conflicted_files:

            row = QHBoxLayout()

            row.addWidget(QLabel(file_path))

            keep_mine_btn = QPushButton("Keep Mine")

            keep_theirs_btn = QPushButton("Keep Theirs")

            open_btn = QPushButton("Open in Editor")

            keep_mine_btn.clicked.connect(
                lambda _, f=file_path: self.resolve_conflict(f, "ours")
            )

            keep_theirs_btn.clicked.connect(
                lambda _, f=file_path: self.resolve_conflict(f, "theirs")
            )

            open_btn.clicked.connect(
                lambda _, f=file_path: self.open_in_editor(f)
            )

            row.addWidget(keep_mine_btn)

            row.addWidget(keep_theirs_btn)

            row.addWidget(open_btn)

            self.conflict_layout.addLayout(row)

        reminder = QLabel(
            "After resolving all files above, use 'Upload Files' to "
            "commit and complete the merge."
        )

        reminder.setWordWrap(True)

        self.conflict_layout.addWidget(reminder)

        self.conflict_group.setVisible(True)


    def resolve_conflict(self, file_path, strategy):

        try:

            from Core.git_service import GitService

            service = GitService(**self.current_config())

            service.resolve_conflict(file_path, strategy)

            self.log.append(
                f"{file_path}: kept {'your' if strategy == 'ours' else 'incoming'} "
                f"version."
            )

            self.check_for_conflicts()

        except Exception as ex:

            QMessageBox.critical(self, "QA AI Studio", str(ex))


    def open_in_editor(self, file_path):

        full_path = str(
            Path(self.current_config()["repo_path"]) / file_path
        )

        try:

            system = platform.system()

            if system == "Windows":

                os.startfile(full_path)

            elif system == "Darwin":

                subprocess.Popen(["open", full_path])

            else:

                subprocess.Popen(["xdg-open", full_path])

        except Exception as ex:

            QMessageBox.warning(
                self,
                "QA AI Studio",
                f"Could not open the file automatically.\n\n"
                f"{full_path}\n\n{ex}"
            )


    # ======================================================
    # Compare
    # ======================================================

    def compare(self):

        ref_a = self.compare_ref_a.text().strip()

        ref_b = self.compare_ref_b.text().strip()

        if not ref_a:

            QMessageBox.warning(
                self, "QA AI Studio", "Enter at least Ref A to compare."
            )

            return

        try:

            from Core.git_service import GitService

            service = GitService(**self.current_config())

            diff_text = service.compare(ref_a, ref_b)

            self.compare_output.setPlainText(diff_text)

        except Exception as ex:

            QMessageBox.critical(self, "QA AI Studio", str(ex))


    # ======================================================
    # AI Commit Message
    # ======================================================

    def generate_commit_message(self):

        def on_success(result):

            message = result.get("message", "")

            if message:

                self.commit_message.setText(message)

        self.run_operation(
            "commit_message",
            on_success=on_success,
        )