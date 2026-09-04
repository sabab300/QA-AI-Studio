# Create: AI/Core/git_config_manager.py

"""
QA AI Studio
Git Config Manager

Version: 1.0

Stores Git Automation settings (repo path, remote URL, branch,
username, access token) in a local JSON file on your own PC.

This file never gets sent anywhere — it's just a plain settings
file next to your database, the same way an app remembers your
last-used folder. Treat it like a password file: don't commit it
into Git, don't email it, don't share the file itself.
"""

import json

from pathlib import Path

from Core.logger import Logger


# BUGFIX (shared Core defect, also present on Desktop): same
# CWD-relative-path issue fixed in playwright_runner.py's
# OUTPUT_FOLDER. Anchored to this file's own location instead.
CONFIG_PATH = Path(__file__).resolve().parent.parent / "Config" / "git_config.json"


class GitConfigManager:

    def __init__(self):

        self.logger = Logger.get_logger()

        CONFIG_PATH.parent.mkdir(
            parents=True,
            exist_ok=True
        )

    # --------------------------------------------------

    def load(self):

        if not CONFIG_PATH.exists():

            return {
                "repo_path": "",
                "remote_url": "",
                "branch": "main",
                "username": "",
                "token": "",
            }

        try:

            with open(CONFIG_PATH, "r", encoding="utf-8") as f:

                return json.load(f)

        except Exception:

            self.logger.exception(
                "Could not read git_config.json, using defaults."
            )

            return {
                "repo_path": "",
                "remote_url": "",
                "branch": "main",
                "username": "",
                "token": "",
            }

    # --------------------------------------------------

    def save(
        self,
        repo_path,
        remote_url,
        branch,
        username,
        token
    ):

        data = {
            "repo_path": repo_path,
            "remote_url": remote_url,
            "branch": branch,
            "username": username,
            "token": token,
        }

        with open(CONFIG_PATH, "w", encoding="utf-8") as f:

            json.dump(data, f, indent=2)

        self.logger.info(
            "Git configuration saved."
        )

        return data