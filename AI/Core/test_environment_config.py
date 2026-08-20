# Create: AI/Core/test_environment_config.py

"""
QA AI Studio
Test Environment Config

Version: 1.0

Stores your REAL test environment details (base URL, test login)
locally, so generated Playwright scripts use actual values instead
of the AI guessing/inventing placeholder URLs and credentials.

Stored in a local JSON file, same pattern as git_config_manager.py —
never sent anywhere except used to build the generation prompt sent
to your local Ollama model.

IMPORTANT: use a TEST/UAT login here, not a production PSW account,
since these values get embedded directly into generated scripts.
"""

import json

from pathlib import Path

from Core.logger import Logger


CONFIG_PATH = Path("Config") / "test_environment_config.json"


class TestEnvironmentConfig:

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
                "base_url": "",
                "username": "",
                "password": "",
                "notes": "",
                "slow_mo_ms": "",
                "default_timeout_ms": "",
            }

        try:

            with open(CONFIG_PATH, "r", encoding="utf-8") as f:

                data = json.load(f)

            # Older config files predate slow_mo_ms/default_timeout_ms
            # — fill them in as blank rather than KeyError-ing, so
            # PlaywrightRunner's own fallback defaults kick in.
            data.setdefault("slow_mo_ms", "")

            data.setdefault("default_timeout_ms", "")

            return data

        except Exception:

            self.logger.exception(
                "Could not read test_environment_config.json, "
                "using defaults."
            )

            return {
                "base_url": "",
                "username": "",
                "password": "",
                "notes": "",
                "slow_mo_ms": "",
                "default_timeout_ms": "",
            }

    # --------------------------------------------------

    def save(
        self,
        base_url,
        username,
        password,
        notes="",
        slow_mo_ms="",
        default_timeout_ms="",
    ):

        data = {
            "base_url": base_url,
            "username": username,
            "password": password,
            "notes": notes,
            # How much every Playwright action is artificially slowed
            # down (milliseconds) and how long Playwright waits for
            # an element/navigation before giving up — both applied
            # at RUN time by PlaywrightRunner, to EVERY script
            # (AI-generated or manually recorded), not baked into the
            # script text itself. See Core/playwright_runner.py.
            "slow_mo_ms": slow_mo_ms,
            "default_timeout_ms": default_timeout_ms,
        }

        with open(CONFIG_PATH, "w", encoding="utf-8") as f:

            json.dump(data, f, indent=2)

        self.logger.info(
            "Test environment configuration saved."
        )

        return data

    # --------------------------------------------------

    def as_prompt_block(self):
        """
        Returns a short block of text to insert into the automation
        generation prompt, or an empty string if nothing is set yet.
        """

        data = self.load()

        if not data.get("base_url") and not data.get("username"):

            return ""

        lines = ["Use these REAL test environment details — do not "
                 "invent a different URL or login:"]

        if data.get("base_url"):

            lines.append(f"Base URL: {data['base_url']}")

        if data.get("username"):

            lines.append(f"Test Username: {data['username']}")

        if data.get("password"):

            lines.append(f"Test Password: {data['password']}")

        if data.get("notes"):

            lines.append(f"Notes: {data['notes']}")

        return "\n".join(lines) + "\n\n"