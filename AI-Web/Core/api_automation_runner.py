# Create: AI/Core/api_automation_runner.py

"""
QA AI Studio
API Automation Runner

Version: 1.0

Actually sends a REAL HTTP request for an imported API Collection
endpoint, against the REAL server — the direct fix for "API
Automation should send request to URL system, not just Ollama":
until now, "Generate Automation" only ever produced a `requests`-
based Python script as TEXT (grounded in the real, imported
endpoint, but never executed by the app itself — see
TestExecutionManager.generate_automation()'s "API" branch). Execute
then showed "Manual Review Required... run it in your own test
environment" for every API-type test case, every time.

This module is the part that was missing: given a REAL, imported
endpoint record (method/url/headers/body straight from
Core/api_collection_repository.py — never an AI-generated guess) and
the operator's own Test Environment Settings (base URL override,
auth, extra headers, timeout, SSL verification — see
Core/test_environment_config.py), it builds and sends the actual
HTTP request with Python's `requests` library and reports back
exactly what happened: status code, response headers/body, timing,
or the real connection/timeout/SSL error if the request couldn't
complete at all.

Deliberately does NOT parse or execute the Ollama-generated script
text for this — building the request directly from the structured,
already-real endpoint record is simpler and far safer than exec()ing
arbitrary LLM-written Python against a real, possibly
production-adjacent PSW server.
"""

import json
import re
import time

from Core.logger import Logger


# Matches Postman-style {{variableName}} placeholders in a URL,
# header value, or body — the SAME placeholder syntax
# Core/api_collection_repository.py already understands for
# collection-level variables; this additionally covers anything that
# collection-level resolution couldn't fill in (most commonly a
# variable that only ever lived in a Postman "Environment" file,
# which isn't part of the collection export QA AI Studio imports).
VARIABLE_PATTERN = re.compile(r"\{\{\s*([^{}]+?)\s*\}\}")

DEFAULT_API_TIMEOUT_SECONDS = 30


class ApiAutomationRunner:

    def __init__(self):

        self.logger = Logger.get_logger()

    # --------------------------------------------------
    # Variable resolution
    # --------------------------------------------------

    def find_unresolved_variables(self, endpoint):
        """
        Scans this endpoint's URL, headers, and body for
        {{variable}} placeholders that are still literally present
        (i.e. weren't resolved at import time from the collection's
        own variables_json — see
        ApiCollectionRepository._resolve_variables()). Returns the
        unique names found, in first-seen order — knows nothing
        about whether a value is already available anywhere; see
        find_missing_variables() for that.
        """

        text_parts = [
            endpoint.get("url_resolved") or endpoint.get("url_raw") or ""
        ]

        headers_raw = endpoint.get("headers_json")

        if headers_raw:

            text_parts.append(
                headers_raw if isinstance(headers_raw, str)
                else json.dumps(headers_raw)
            )

        if endpoint.get("body_raw"):

            text_parts.append(endpoint["body_raw"])

        names = []

        seen = set()

        for text in text_parts:

            for match in VARIABLE_PATTERN.finditer(text or ""):

                name = match.group(1).strip()

                if name and name not in seen:

                    seen.add(name)

                    names.append(name)

        return names

    def find_missing_variables(self, endpoint, config):
        """
        Same as find_unresolved_variables(), narrowed to names that
        don't already have a remembered value in Test Environment
        Settings' api_variables (see
        TestEnvironmentConfig.remember_api_variable()) — this is
        what the "ask once, remember for reuse" flow checks before
        prompting the operator, so a variable already answered on an
        earlier run never asks again.
        """

        known = config.get("api_variables") or {}

        return [
            name for name in self.find_unresolved_variables(endpoint)
            if name not in known
        ]

    def _substitute(self, text, variables):

        if not text:

            return text

        def _replace(match):

            name = match.group(1).strip()

            return str(variables.get(name, match.group(0)))

        return VARIABLE_PATTERN.sub(_replace, text)

    # --------------------------------------------------
    # Building the real request
    # --------------------------------------------------

    def _apply_base_url_override(self, url, base_override):
        """
        Swaps just the scheme+host portion of `url` for
        `base_override`, keeping the endpoint's own path/query
        exactly as imported — e.g. an endpoint imported against
        https://sit.psw.gov.pk/... can be pointed at a different
        environment (UAT, a local instance, ...) without touching
        the stored endpoint at all.
        """

        base_override = base_override.strip().rstrip("/")

        if not base_override:

            return url

        if not re.match(r"^https?://", base_override, re.IGNORECASE):

            base_override = "https://" + base_override

        return re.sub(
            r"^https?://[^/]+", base_override, url, count=1,
            flags=re.IGNORECASE,
        )

    def _guess_content_type(self, endpoint, body_text):

        if endpoint.get("body_mode") == "urlencoded":

            return "application/x-www-form-urlencoded"

        stripped = (body_text or "").strip()

        if stripped[:1] in ("{", "["):

            return "application/json"

        return "text/plain"

    def build_request(self, endpoint, config):
        """
        Returns a plain dict of everything needed to actually send
        this endpoint's request — {method, url, headers, data,
        auth, timeout, verify} — built from the REAL endpoint record
        plus the operator's Test Environment Settings. Never mutates
        `endpoint` or `config`; safe to call repeatably (e.g. once
        to check for missing variables, again to actually send).

        Any {{variable}} still present after substitution (i.e. the
        operator hasn't supplied a value for it) is left as the
        literal "{{name}}" text — send() will still fire the
        request as-is if that happens, since a real 4xx/5xx from a
        genuinely broken URL is more informative than silently
        refusing to try.
        """

        variables = config.get("api_variables") or {}

        method = (endpoint.get("method") or "GET").upper()

        raw_url = endpoint.get("url_resolved") or endpoint.get(
            "url_raw", ""
        )

        url = self._substitute(raw_url, variables)

        base_override = (config.get("api_base_url_override") or "").strip()

        if base_override:

            url = self._apply_base_url_override(url, base_override)

        headers = {}

        headers_raw = endpoint.get("headers_json")

        try:

            header_list = (
                headers_raw if isinstance(headers_raw, list)
                else json.loads(headers_raw or "[]")
            )

        except (TypeError, ValueError):

            header_list = []

        for item in header_list:

            key = (item or {}).get("key")

            if key:

                headers[str(key)] = self._substitute(
                    str(item.get("value", "")), variables
                )

        # Extra headers from Test Environment Settings are ADDED on
        # top of whatever the endpoint itself already captured at
        # import time — never silently replacing a header the
        # collection explicitly set.
        for key, value in (config.get("api_extra_headers") or {}).items():

            headers.setdefault(str(key), str(value))

        auth_type = config.get("api_auth_type") or "None"

        auth = None

        if auth_type == "Bearer Token" and config.get("api_auth_token"):

            headers.setdefault(
                "Authorization", f"Bearer {config['api_auth_token']}"
            )

        elif (
            auth_type == "API Key Header"
            and config.get("api_auth_header_name")
        ):

            headers.setdefault(
                config["api_auth_header_name"],
                config.get("api_auth_header_value", ""),
            )

        elif auth_type == "Basic Auth" and config.get("api_username"):

            auth = (
                config.get("api_username", ""),
                config.get("api_password", ""),
            )

        body_raw = endpoint.get("body_raw")

        data = None

        if body_raw:

            substituted_body = self._substitute(body_raw, variables)

            data = substituted_body.encode("utf-8")

            headers.setdefault(
                "Content-Type",
                self._guess_content_type(endpoint, substituted_body),
            )

        timeout = self._parse_timeout(config.get("api_timeout_seconds"))

        verify = config.get("api_verify_ssl", True)

        return {
            "method": method,
            "url": url,
            "headers": headers,
            "data": data,
            "auth": auth,
            "timeout": timeout,
            "verify": verify,
        }

    def _parse_timeout(self, raw_value, fallback=DEFAULT_API_TIMEOUT_SECONDS):

        try:

            value = float(str(raw_value).strip())

            return value if value > 0 else fallback

        except (TypeError, ValueError):

            return fallback

    # --------------------------------------------------
    # Sending
    # --------------------------------------------------

    def _safe_body_text(self, response, max_len=8000):

        try:

            text = response.text

        except Exception:

            return "<could not decode response body as text>"

        if len(text) > max_len:

            return (
                text[:max_len]
                + f"\n...(truncated — {len(text)} characters total)"
            )

        return text

    def send(self, endpoint, config):
        """
        Actually sends the request. Returns a result dict — never
        raises; a connection/timeout/SSL failure is reported back in
        result["error"] instead, so the caller can show it to the
        operator and offer to fix Test Environment Settings and
        retry, rather than the app crashing on a bad/unreachable
        server.

        result["auto_verdict"] is "Pass" ONLY when this endpoint has
        a stored expected status (example_response_status, captured
        from the original Postman export) AND the real response
        matched it exactly — anything else (no expected status on
        file, a mismatch, or an outright request failure) leaves
        auto_verdict as None, meaning "this needs a human judgment
        call", per how Interactive Locator Repair already treats
        genuinely ambiguous outcomes.
        """

        request_spec = self.build_request(endpoint, config)

        expected_status = endpoint.get("example_response_status")

        result = {
            "request": {
                key: value for key, value in request_spec.items()
                if key != "auth"
            },
            "expected_status": expected_status,
            "error": None,
            "status_code": None,
            "response_headers": {},
            "response_body_text": "",
            "elapsed_ms": None,
            "auto_verdict": None,
        }

        start = time.time()

        try:

            import requests

            response = requests.request(
                request_spec["method"],
                request_spec["url"],
                headers=request_spec["headers"],
                data=request_spec["data"],
                auth=request_spec["auth"],
                timeout=request_spec["timeout"],
                verify=request_spec["verify"],
            )

        except Exception as ex:

            result["error"] = str(ex)

            result["elapsed_ms"] = round((time.time() - start) * 1000, 1)

            self.logger.exception(
                f"API Automation request failed: "
                f"{request_spec.get('method')} {request_spec.get('url')}"
            )

            return result

        result["elapsed_ms"] = round((time.time() - start) * 1000, 1)

        result["status_code"] = response.status_code

        result["response_headers"] = dict(response.headers)

        result["response_body_text"] = self._safe_body_text(response)

        if expected_status and int(expected_status) == response.status_code:

            result["auto_verdict"] = "Pass"

        return result