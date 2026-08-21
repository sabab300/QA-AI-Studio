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

# Every key this config file can hold, with its default value.
# load() fills in any key missing from an older config file (or a
# brand new one) from here, so adding a new setting never breaks a
# config file saved before that setting existed.
DEFAULT_CONFIG = {
    "base_url": "",
    "username": "",
    "password": "",
    "notes": "",
    # How much every Playwright action is artificially slowed down
    # (milliseconds) and how long Playwright waits for an
    # element/navigation before giving up — both applied at RUN time
    # by PlaywrightRunner, to EVERY script (AI-generated or manually
    # recorded), not baked into the script text itself. See
    # Core/playwright_runner.py.
    "slow_mo_ms": "",
    "default_timeout_ms": "",
    # -------------------------------------------------------
    # API Automation ("Execute Against Real Server") settings —
    # see Core/api_automation_runner.py. Kept in this same file
    # rather than a separate config: it's the same underlying idea
    # (real, non-guessed values instead of the AI or the app
    # inventing/guessing something) just for real HTTP calls instead
    # of browser navigation.
    # -------------------------------------------------------
    # "None" / "Bearer Token" / "API Key Header" / "Basic Auth".
    "api_auth_type": "None",
    "api_auth_token": "",
    "api_auth_header_name": "",
    "api_auth_header_value": "",
    "api_username": "",
    "api_password": "",
    # Extra headers (e.g. a PSW-specific header every call needs)
    # sent on every real API request, ADDED to whatever the imported
    # endpoint itself already captured — never replacing those.
    "api_extra_headers": {},
    "api_timeout_seconds": "",
    "api_verify_ssl": True,
    # Swaps just the scheme+host of every request (keeping the
    # endpoint's own path/query) — e.g. run an endpoint imported
    # against SIT against UAT instead, with nothing re-imported or
    # re-edited. Blank = use the endpoint's own URL exactly as
    # imported.
    "api_base_url_override": "",
    # Remembered {{variable}} values that couldn't be resolved from
    # the imported collection file alone (see
    # ApiAutomationRunner.find_unresolved_variables()) — asked once,
    # then reused automatically on every later run. Use
    # remember_api_variable() to update just this, without touching
    # anything else in the config.
    "api_variables": {},
}


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

            return dict(DEFAULT_CONFIG)

        try:

            with open(CONFIG_PATH, "r", encoding="utf-8") as f:

                data = json.load(f)

            # Config files saved before a setting existed predate
            # that key entirely — fill it in from the default rather
            # than KeyError-ing, so old settings files keep working
            # unchanged after an upgrade.
            for key, value in DEFAULT_CONFIG.items():

                data.setdefault(key, value)

            return data

        except Exception:

            self.logger.exception(
                "Could not read test_environment_config.json, "
                "using defaults."
            )

            return dict(DEFAULT_CONFIG)

    # --------------------------------------------------

    def save(
        self,
        base_url,
        username,
        password,
        notes="",
        slow_mo_ms="",
        default_timeout_ms="",
        api_auth_type=None,
        api_auth_token=None,
        api_auth_header_name=None,
        api_auth_header_value=None,
        api_username=None,
        api_password=None,
        api_extra_headers=None,
        api_timeout_seconds=None,
        api_verify_ssl=None,
        api_base_url_override=None,
        api_variables=None,
    ):
        """
        The original 6 params (base_url ... default_timeout_ms) are
        always required, exactly as before — Test Environment
        Settings always passes all of them. Every API-related param
        defaults to None, meaning "leave whatever's already saved
        alone" — this is what lets remember_api_variable() (called
        mid-run, from a completely different dialog) update just
        api_variables without this method's other, unrelated
        defaults wiping out the operator's saved API auth/headers/
        timeout the next time the main Settings dialog is saved.
        """

        existing = self.load()

        def _keep_if_none(new_value, key):

            return existing.get(key) if new_value is None else new_value

        data = {
            "base_url": base_url,
            "username": username,
            "password": password,
            "notes": notes,
            "slow_mo_ms": slow_mo_ms,
            "default_timeout_ms": default_timeout_ms,
            "api_auth_type": _keep_if_none(api_auth_type, "api_auth_type"),
            "api_auth_token": _keep_if_none(
                api_auth_token, "api_auth_token"
            ),
            "api_auth_header_name": _keep_if_none(
                api_auth_header_name, "api_auth_header_name"
            ),
            "api_auth_header_value": _keep_if_none(
                api_auth_header_value, "api_auth_header_value"
            ),
            "api_username": _keep_if_none(api_username, "api_username"),
            "api_password": _keep_if_none(api_password, "api_password"),
            "api_extra_headers": _keep_if_none(
                api_extra_headers, "api_extra_headers"
            ),
            "api_timeout_seconds": _keep_if_none(
                api_timeout_seconds, "api_timeout_seconds"
            ),
            "api_verify_ssl": (
                existing.get("api_verify_ssl", True)
                if api_verify_ssl is None else api_verify_ssl
            ),
            "api_base_url_override": _keep_if_none(
                api_base_url_override, "api_base_url_override"
            ),
            "api_variables": _keep_if_none(
                api_variables, "api_variables"
            ),
        }

        with open(CONFIG_PATH, "w", encoding="utf-8") as f:

            json.dump(data, f, indent=2)

        self.logger.info(
            "Test environment configuration saved."
        )

        return data

    # --------------------------------------------------

    def remember_api_variable(self, name, value):
        """
        Saves ONE resolved {{variable}} value — called right after
        the operator answers a variable prompt during a real API
        run (see ApiVariablePromptDialog /
        ApiAutomationRunner.find_missing_variables()) — without
        touching anything else in the config, so this can be called
        from a completely different, lightweight dialog than the
        main Test Environment Settings one.
        """

        existing = self.load()

        variables = dict(existing.get("api_variables") or {})

        variables[name] = value

        return self.save(
            base_url=existing.get("base_url", ""),
            username=existing.get("username", ""),
            password=existing.get("password", ""),
            notes=existing.get("notes", ""),
            slow_mo_ms=existing.get("slow_mo_ms", ""),
            default_timeout_ms=existing.get("default_timeout_ms", ""),
            api_variables=variables,
        )

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