# Create: AI/Core/git_service.py

"""
QA AI Studio
Git Service

Version: 1.0

Wraps GitPython for the Git Automation screen.

Safety note on "Resolve Simple Conflicts": this does NOT try to
guess how to merge conflicting content — that's genuinely risky and
can silently corrupt code. Instead it detects conflicted files after
a pull/merge, and lets the QA engineer choose per file: keep their
own version ("ours"), keep the incoming version ("theirs"), or open
it in their normal editor to resolve by hand. That matches "simple"
honestly — the automation is in detecting and applying a clear
choice, not in inventing one.

Requires: GitPython (add "GitPython" to AI/requirements.txt)
"""

from pathlib import Path

import git
from git import Repo, GitCommandError, InvalidGitRepositoryError, NoSuchPathError

from Core.logger import Logger
from Core.llm_engine import LLMEngine


class GitService:

    def __init__(self, repo_path, remote_url="", username="", token=""):

        self.logger = Logger.get_logger()

        self.repo_path = str(Path(repo_path).resolve())

        self.remote_url = remote_url

        self.username = username

        self.token = token

        self.llm = LLMEngine()

    # --------------------------------------------------
    # Open / Init / Clone
    # --------------------------------------------------

    def open_or_clone(self):
        """
        If repo_path already has a .git folder, open it.
        Otherwise, if a remote_url is configured, clone it there.
        Otherwise, initialize a brand new empty repo at repo_path.
        """

        path = Path(self.repo_path)

        try:

            repo = Repo(self.repo_path)

            self.logger.info(
                f"Opened existing repo at {self.repo_path}"
            )

            return repo

        except (InvalidGitRepositoryError, NoSuchPathError):

            pass

        if self.remote_url:

            path.mkdir(parents=True, exist_ok=True)

            auth_url = self._authenticated_url(self.remote_url)

            self.logger.info(
                f"Cloning {self.remote_url} into {self.repo_path}"
            )

            repo = Repo.clone_from(auth_url, self.repo_path)

            # Don't leave the token sitting in .git/config on disk.
            repo.remotes.origin.set_url(self.remote_url)

            return repo

        path.mkdir(parents=True, exist_ok=True)

        self.logger.info(
            f"No existing repo and no remote configured — "
            f"initializing a new empty repo at {self.repo_path}"
        )

        return Repo.init(self.repo_path)


    def get_repo(self):

        return Repo(self.repo_path)


    # --------------------------------------------------
    # Auth helper
    # --------------------------------------------------

    def _authenticated_url(self, url):

        if not self.token:

            return url

        if url.startswith("https://"):

            rest = url[len("https://"):]

            if self.username:

                return f"https://{self.username}:{self.token}@{rest}"

            return f"https://{self.token}@{rest}"

        # SSH URLs (git@...) rely on your OS SSH key setup, not a
        # token — return unchanged.
        return url


    def _with_auth_url(self, repo, action):
        """
        Temporarily points 'origin' at an authenticated URL for the
        duration of `action(repo)`, then restores the clean URL —
        so the token doesn't stay written to .git/config afterward.
        """

        origin = repo.remotes.origin

        clean_url = self.remote_url or next(origin.urls, "")

        auth_url = self._authenticated_url(clean_url)

        try:

            origin.set_url(auth_url)

            return action(repo)

        finally:

            if clean_url:

                origin.set_url(clean_url)


    # --------------------------------------------------
    # Status
    # --------------------------------------------------

    def status(self):

        repo = self.get_repo()

        return {
            "branch": repo.active_branch.name if not repo.head.is_detached else "(detached)",
            "modified": [item.a_path for item in repo.index.diff(None)],
            "staged": [item.a_path for item in repo.index.diff("HEAD")],
            "untracked": repo.untracked_files,
            "is_dirty": repo.is_dirty(untracked_files=True),
        }


    # --------------------------------------------------
    # Upload Files (add + commit)
    # --------------------------------------------------

    def add_and_commit(self, files, message):
        """
        files: list of file paths (relative to repo root), or
        None/["."] to stage everything.
        """

        repo = self.get_repo()

        if not files or files == ["."]:

            repo.git.add(A=True)

        else:

            repo.index.add(files)

        merge_in_progress = (
            Path(repo.git_dir) / "MERGE_HEAD"
        ).exists()

        if (
            not merge_in_progress
            and not repo.is_dirty(untracked_files=False)
            and not repo.index.diff("HEAD")
        ):

            return {
                "success": False,
                "error": "Nothing to commit — no staged changes."
            }

        commit = repo.index.commit(message)

        self.logger.info(
            f"Committed {commit.hexsha[:8]}: {message}"
        )

        return {
            "success": True,
            "commit_hash": commit.hexsha,
        }


    # --------------------------------------------------
    # Pull
    # --------------------------------------------------

    def pull(self, branch=None):

        repo = self.get_repo()

        branch = branch or repo.active_branch.name

        try:

            def do_pull(r):

                return r.remotes.origin.pull(
                    branch,
                    no_rebase=True
                )

            self._with_auth_url(repo, do_pull)

            return {"success": True}

        except GitCommandError as ex:

            conflicts = self.list_conflicts()

            if conflicts:

                return {
                    "success": False,
                    "conflict": True,
                    "conflicted_files": conflicts,
                    "error": "Pull completed with conflicts.",
                }

            return {
                "success": False,
                "error": str(ex),
            }


    # --------------------------------------------------
    # Push
    # --------------------------------------------------

    def push(self, branch=None):

        repo = self.get_repo()

        branch = branch or repo.active_branch.name

        try:

            def do_push(r):

                return r.remotes.origin.push(branch)

            self._with_auth_url(repo, do_push)

            return {"success": True}

        except GitCommandError as ex:

            return {
                "success": False,
                "error": str(ex),
            }


    # --------------------------------------------------
    # Merge
    # --------------------------------------------------

    def merge(self, source_branch):

        repo = self.get_repo()

        try:

            repo.git.merge(source_branch)

            return {"success": True}

        except GitCommandError as ex:

            conflicts = self.list_conflicts()

            if conflicts:

                return {
                    "success": False,
                    "conflict": True,
                    "conflicted_files": conflicts,
                    "error": f"Merging '{source_branch}' caused conflicts.",
                }

            return {
                "success": False,
                "error": str(ex),
            }


    # --------------------------------------------------
    # Compare
    # --------------------------------------------------

    def compare(self, ref_a, ref_b):
        """
        ref_a / ref_b can be branch names, tags, or commit hashes.
        Pass ref_b="" to compare ref_a against the working directory.
        """

        repo = self.get_repo()

        if ref_b:

            diff_text = repo.git.diff(f"{ref_a}..{ref_b}")

        else:

            diff_text = repo.git.diff(ref_a)

        return diff_text or "No differences found."


    # --------------------------------------------------
    # Conflicts
    # --------------------------------------------------

    def list_conflicts(self):

        repo = self.get_repo()

        unmerged = repo.index.unmerged_blobs()

        return list(unmerged.keys())


    def resolve_conflict(self, file_path, strategy):
        """
        strategy: "ours" (keep my version) or "theirs" (keep
        incoming version). Stages the resolved file — does not
        commit; the user commits once all conflicts are resolved.
        """

        if strategy not in ("ours", "theirs"):

            raise ValueError(
                "strategy must be 'ours' or 'theirs'"
            )

        repo = self.get_repo()

        repo.git.checkout(f"--{strategy}", file_path)

        repo.git.add(file_path)

        return {"success": True}


    # --------------------------------------------------
    # AI Commit Message
    # --------------------------------------------------

    def generate_commit_message(self):

        repo = self.get_repo()

        diff_text = repo.git.diff("--staged")

        if not diff_text:

            # Nothing staged yet — fall back to unstaged changes so
            # the user can still get a suggestion before staging.
            diff_text = repo.git.diff()

        if not diff_text:

            return {
                "success": False,
                "error": "No changes to summarize.",
            }

        # Keep the diff to a sane size for the prompt.
        diff_text = diff_text[:6000]

        prompt = (
            "Write a single, concise Git commit message (max 72 "
            "characters for the summary line, imperative mood, e.g. "
            "'Fix CESS waiver validation for EFS scheme') for the "
            "following diff. Only return the commit message text, "
            "nothing else.\n\n"
            f"{diff_text}"
        )

        result = self.llm.generate(
            prompt=prompt,
            temperature=0.2,
            max_tokens=100,
        )

        if not result.get("success"):

            return result

        message = result.get("response", "").strip().strip('"')

        return {
            "success": True,
            "message": message,
        }