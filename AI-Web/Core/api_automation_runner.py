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
from Core.secret_masking import mask_value, is_sensitive_key


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

    def _request_override_text_parts(self, request_overrides, secret_only=False):
        """
        Shared with find_unresolved_variables()/find_missing_variables():
        flattens the per-Test-Case request_overrides (query_params/
        path_params/headers/auth/body — see
        TestCaseRepository.api_request_config_json) into the same kind
        of plain-text list endpoint-only scanning already used, so a
        {{variable}} placeholder introduced ONLY by an override (never
        present on the imported endpoint itself) is still caught by
        Validate/execute-readiness instead of silently reaching
        send() unresolved.

        secret_only=True narrows this to just the auth-credential
        fields (token/header_value/username/password) — the fields
        section P treats as secrets and masks on read — for the
        dedicated "secret references valid" Validate check (section
        M), kept separate from the general "any variable missing"
        check so a missing ordinary header/body variable and a
        missing secret variable are reported distinctly.
        """

        request_overrides = request_overrides or {}

        parts = []

        if not secret_only:

            url_override = request_overrides.get("url")

            if url_override:

                parts.append(str(url_override))

            for item in self._enabled_pairs(request_overrides.get("query_params")):

                parts.append(str(item.get("value", "")))

            for item in self._enabled_pairs(request_overrides.get("path_params")):

                parts.append(str(item.get("value", "")))

            for item in self._enabled_pairs(request_overrides.get("headers")):

                parts.append(str(item.get("value", "")))

            body_override = request_overrides.get("body")

            if body_override is not None:

                parts.append(str(body_override))

            for item in self._enabled_pairs(request_overrides.get("body_params")):

                parts.append(str(item.get("value", "")))

        auth_override = request_overrides.get("auth") or {}

        auth_type = str(auth_override.get("type") or "").strip()

        if auth_type == "Bearer Token":

            parts.append(str(auth_override.get("token", "")))

        elif auth_type == "API Key Header":

            parts.append(str(auth_override.get("header_value", "")))

        elif auth_type == "Basic Auth":

            parts.append(str(auth_override.get("username", "")))

            parts.append(str(auth_override.get("password", "")))

        return parts

    def find_unresolved_variables(self, endpoint, request_overrides=None):
        """
        Scans this endpoint's URL, headers, and body — plus, when
        given, the per-Test-Case request_overrides (section F/I/J) on
        top — for {{variable}} placeholders that are still literally
        present (i.e. weren't resolved at import time from the
        collection's own variables_json — see
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

        text_parts.extend(self._request_override_text_parts(request_overrides))

        names = []

        seen = set()

        for text in text_parts:

            for match in VARIABLE_PATTERN.finditer(text or ""):

                name = match.group(1).strip()

                if name and name not in seen:

                    seen.add(name)

                    names.append(name)

        return names

    def find_missing_variables(self, endpoint, config, request_overrides=None):
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
            name for name in self.find_unresolved_variables(endpoint, request_overrides)
            if name not in known
        ]

    def find_missing_secret_variables(self, endpoint, config, request_overrides=None):
        """
        Validate's dedicated "secret references valid" check (section
        M/I): same missing-variable resolution as
        find_missing_variables(), narrowed to ONLY the auth-credential
        override fields (Bearer token / API Key header value / Basic
        username+password) — i.e. an unresolved {{X-Token}}-style
        secret reference the dependent auth/token flow (section K)
        relies on. Endpoint-level URL/header/body variables are
        intentionally excluded here; those are covered by
        find_missing_variables().
        """

        known = config.get("api_variables") or {}

        names = []

        seen = set()

        for text in self._request_override_text_parts(request_overrides, secret_only=True):

            for match in VARIABLE_PATTERN.finditer(text or ""):

                name = match.group(1).strip()

                if name and name not in seen:

                    seen.add(name)

                    names.append(name)

        return [name for name in names if name not in known]

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

    @staticmethod
    def _build_multipart_body(pairs):
        """
        A minimal, dependency-free multipart/form-data encoder for
        TEXT fields only (no file upload support — QA AI Studio has
        no file-upload infrastructure for API Automation yet; an
        operator needing to test a real file-upload endpoint should
        use Raw/urlencoded with a pre-encoded payload instead).
        `pairs`: an already-substituted [(key, value), ...] list.
        Returns (body_bytes, content_type_with_boundary).
        """

        import uuid

        boundary = uuid.uuid4().hex

        lines = []

        for key, value in pairs:

            lines.append(f"--{boundary}")

            lines.append(f'Content-Disposition: form-data; name="{key}"')

            lines.append("")

            lines.append(value)

        lines.append(f"--{boundary}--")

        lines.append("")

        body_text = "\r\n".join(lines)

        return (
            body_text.encode("utf-8"),
            f"multipart/form-data; boundary={boundary}",
        )

    def _encode_structured_body(self, body_mode, pairs):
        """
        Real x-www-form-urlencoded / multipart encoding for a
        Params-style [{key, value, enabled}] list (already variable-
        substituted by the caller) — used both for a per-Test-Case
        "urlencoded"/"form_data" body override AND to correctly
        re-encode an IMPORTED Postman endpoint whose own body_mode is
        "urlencoded"/"formdata" (see build_request() below: the
        importer stores that shape as a JSON-encoded {key: value}
        dict in body_raw — Core/api_collection_repository.py's
        _parse_request_item() — which previously reached send() as
        literal JSON bytes labeled with an x-www-form-urlencoded/
        multipart Content-Type header; a real server correctly
        rejects/misreads that mismatch, e.g. an OAuth2
        /connect/token endpoint expecting real
        "grant_type=...&client_id=..." bytes).
        Returns (body_bytes, content_type).
        """

        if body_mode == "form_data":

            return self._build_multipart_body(pairs)

        from urllib.parse import urlencode

        return (
            urlencode(pairs).encode("utf-8"),
            "application/x-www-form-urlencoded",
        )

    @staticmethod
    def _enabled_pairs(items):
        """
        Normalizes a Params/Headers-style override list — [{key,
        value, enabled}], enabled defaulting to True when absent —
        down to just the key/value pairs that are actually enabled
        and have a non-empty key.
        """

        out = []

        for item in (items or []):

            if not isinstance(item, dict):

                continue

            if item.get("enabled") is False:

                continue

            key = str(item.get("key") or "").strip()

            if key:

                out.append({"key": key, "value": item.get("value", "")})

        return out

    def build_request(self, endpoint, config, request_overrides=None):
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

        `request_overrides` (optional, section F/I/J): the per-Test-
        Case structured request definition
        (TestCaseRepository.api_request_config_json —
        method/url/query_params/path_params/headers/auth/body) that
        this section adds on TOP of the endpoint's own imported
        values and the Environment's own settings. An override entry
        with enabled=false is dropped entirely; enabled left unset
        defaults to enabled. Environment-level settings (base URL
        override, Environment auth, extra headers, timeout, SSL)
        still apply underneath — this never replaces the Environment,
        only adds the per-endpoint request definition that was
        previously missing.

        `request_overrides["method"]`/`["url"]` (POSTMAN-STYLE-END-
        TO-END-FINAL-COMPLETION section 1): an explicit per-Test-Case
        Method/URL — editable in the frontend's request bar, auto-
        populated from the bound endpoint's own imported method/URL
        but never mutating the source endpoint record itself. A
        blank/absent value falls back to the bound endpoint's own
        method/URL exactly as before.
        """

        request_overrides = request_overrides or {}

        variables = config.get("api_variables") or {}

        method_override = str(request_overrides.get("method") or "").strip().upper()

        method = method_override or (endpoint.get("method") or "GET").upper()

        url_override = str(request_overrides.get("url") or "").strip()

        raw_url = url_override or endpoint.get("url_resolved") or endpoint.get(
            "url_raw", ""
        )

        url = self._substitute(raw_url, variables)

        # Path params — Postman-style ":name" segments — an explicit
        # per-Test-Case override value takes precedence; otherwise the
        # value already resolved at import time (baked into url_raw/
        # url_resolved) is left as-is.
        for item in self._enabled_pairs(request_overrides.get("path_params")):

            url = re.sub(
                r":" + re.escape(item["key"]) + r"(?=/|\?|$)",
                self._substitute(str(item.get("value", "")), variables),
                url,
            )

        base_override = (config.get("api_base_url_override") or "").strip()

        if base_override:

            url = self._apply_base_url_override(url, base_override)

        # Query params — appended on top of whatever query string the
        # endpoint's own URL already carries.
        query_pairs = self._enabled_pairs(request_overrides.get("query_params"))

        if query_pairs:

            from urllib.parse import urlencode

            separator = "&" if "?" in url else "?"

            url = url + separator + urlencode([
                (item["key"], self._substitute(str(item.get("value", "")), variables))
                for item in query_pairs
            ])

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

        # Per-Test-Case header overrides/additions (section F) — take
        # precedence over the collection's own imported headers, since
        # they are an explicit operator choice for THIS test case.
        for item in self._enabled_pairs(request_overrides.get("headers")):

            headers[str(item["key"])] = self._substitute(
                str(item.get("value", "")), variables
            )

        # Extra headers from Test Environment Settings are ADDED on
        # top of whatever the endpoint/override already captured —
        # never silently replacing a header the collection or a
        # per-Test-Case override explicitly set.
        for key, value in (config.get("api_extra_headers") or {}).items():

            headers.setdefault(str(key), str(value))

        auth_override = request_overrides.get("auth") or {}
        auth_override_type = str(auth_override.get("type") or "").strip()

        # "Inherited"/"" (or no override object at all) means "use the
        # Environment's own Auth setting" — section F/G's explicit
        # separation: a per-Test-Case auth choice is only ever an
        # override, never a requirement.
        if auth_override_type and auth_override_type not in ("Inherited", "None"):

            auth_type = auth_override_type

            auth_source = auth_override

        elif auth_override_type == "None":

            auth_type = "None"

            auth_source = {}

        else:

            auth_type = config.get("api_auth_type") or "None"

            auth_source = None  # signals "read from config below"

        auth = None

        if auth_type == "Bearer Token":

            token = (
                auth_source.get("token") if auth_source is not None
                else config.get("api_auth_token")
            )

            if token:

                headers["Authorization"] = f"Bearer {self._substitute(str(token), variables)}"

        elif auth_type == "API Key Header":

            header_name = (
                auth_source.get("header_name") if auth_source is not None
                else config.get("api_auth_header_name")
            )

            header_value = (
                auth_source.get("header_value", "") if auth_source is not None
                else config.get("api_auth_header_value", "")
            )

            if header_name:

                headers[str(header_name)] = self._substitute(str(header_value), variables)

        elif auth_type == "Basic Auth":

            username = (
                auth_source.get("username") if auth_source is not None
                else config.get("api_username")
            )

            if username:

                password = (
                    auth_source.get("password", "") if auth_source is not None
                    else config.get("api_password", "")
                )

                auth = (str(username), str(password))

        # Body (POSTMAN-STYLE-END-TO-END-FINAL-COMPLETION section 6):
        # an explicit per-Test-Case body_mode override — "none" /
        # "raw_json" / "text" / "urlencoded" / "form_data" — decides
        # how the body is built; a legacy override that only sets
        # "body" (no body_mode key — every request config saved before
        # this section existed) keeps its original raw-text-with-
        # guessed-Content-Type behavior exactly as before, so already-
        # saved Test Cases are unaffected. With neither override
        # present, falls back to the endpoint's OWN imported body.
        data = None

        body_mode_override = str(
            request_overrides.get("body_mode") or ""
        ).strip().lower()

        if body_mode_override == "none":

            data = None

        elif body_mode_override in ("urlencoded", "form_data"):

            pairs = [
                (item["key"], self._substitute(str(item.get("value", "")), variables))
                for item in self._enabled_pairs(request_overrides.get("body_params"))
            ]

            if pairs:

                data, content_type = self._encode_structured_body(
                    body_mode_override, pairs
                )

                headers.setdefault("Content-Type", content_type)

        elif body_mode_override in ("raw_json", "text") or (
            "body" in request_overrides and request_overrides["body"] is not None
        ):

            body_raw = (
                request_overrides["body"]
                if "body" in request_overrides and request_overrides["body"] is not None
                else ""
            )

            if body_raw:

                substituted_body = self._substitute(body_raw, variables)

                data = substituted_body.encode("utf-8")

                if body_mode_override == "raw_json":

                    headers.setdefault("Content-Type", "application/json")

                elif body_mode_override == "text":

                    headers.setdefault("Content-Type", "text/plain")

                else:

                    # Legacy override (no body_mode key saved) — the
                    # original content-sniffing behavior.
                    headers.setdefault(
                        "Content-Type",
                        self._guess_content_type(endpoint, substituted_body),
                    )

        else:

            # No override at all — use the endpoint's OWN imported
            # body exactly as imported. A bug fix vs. the original
            # code here: an imported Postman "urlencoded"/"formdata"
            # body is stored as a JSON-encoded {key: value} dict (see
            # api_collection_repository.py's _parse_request_item()),
            # which previously reached send() as literal JSON bytes
            # mislabeled with an x-www-form-urlencoded/multipart
            # Content-Type — a real server (e.g. an OAuth2
            # /connect/token endpoint) would reject or misread that.
            # It is now decoded and properly re-encoded.
            endpoint_body_mode = endpoint.get("body_mode")

            endpoint_body_raw = endpoint.get("body_raw")

            if endpoint_body_mode in ("urlencoded", "formdata") and endpoint_body_raw:

                try:

                    pairs_dict = json.loads(endpoint_body_raw) or {}

                except (TypeError, ValueError):

                    pairs_dict = {}

                pairs = [
                    (str(key), self._substitute(str(value), variables))
                    for key, value in pairs_dict.items()
                ]

                if pairs:

                    data, content_type = self._encode_structured_body(
                        "form_data" if endpoint_body_mode == "formdata" else "urlencoded",
                        pairs,
                    )

                    headers.setdefault("Content-Type", content_type)

            elif endpoint_body_raw:

                substituted_body = self._substitute(endpoint_body_raw, variables)

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
    # Secret masking (section P) — reuses Core/secret_masking.py's
    # already-established "mask the VALUE only when the field NAME
    # looks sensitive, and only when the value isn't itself a
    # {{variable}} placeholder" rule (the exact same rule already
    # used for AI-prompt masking) so Run Details (section K) and
    # anywhere else this result is shown NEVER displays a real
    # Authorization/token/password/secret value — only "********" or
    # a scheme-preserving "Bearer ********".
    # --------------------------------------------------

    @classmethod
    def _mask_headers(cls, headers):
        """
        `headers`: a plain {name: value} dict, as built by
        build_request(). Returns a new dict, same keys, with any
        value whose header NAME looks sensitive (Authorization,
        X-Api-Key, X-Client-Secret, ...) replaced per
        secret_masking.mask_value(). Never mutates the input.
        """

        return {
            key: mask_value(key, value)
            for key, value in (headers or {}).items()
        }

    @classmethod
    def _mask_json_value(cls, value):
        """
        Recursively walks a parsed JSON value (dict/list/scalar),
        masking any dict value whose KEY looks sensitive — the same
        per-field rule _mask_headers() applies to headers, extended
        to nested request/response body objects (e.g. a body of
        {"username": "...", "password": "..."} masks only
        "password"). Non-dict/list scalars are returned unchanged
        (there is no field name to judge sensitivity by at that
        point).
        """

        if isinstance(value, dict):

            masked = {}

            for key, item in value.items():

                if isinstance(item, (dict, list)):

                    masked[key] = cls._mask_json_value(item)

                elif is_sensitive_key(key):

                    masked[key] = mask_value(key, item)

                else:

                    masked[key] = item

            return masked

        if isinstance(value, list):

            return [cls._mask_json_value(item) for item in value]

        return value

    @classmethod
    def _mask_body_text(cls, body_text):
        """
        Masks a JSON request/response body's sensitive fields
        in-place-equivalent (returns a new string) via
        _mask_json_value(). A body that isn't valid JSON (form-
        encoded, plain text, XML, ...) is returned UNCHANGED — there
        is no generic, safe way to identify a "field name" in
        arbitrary text, and section P's masking requirement is scoped
        to the structured cases QA AI Studio itself understands
        (headers and JSON bodies), not a best-effort text scrub that
        could still miss a real secret.
        """

        if not body_text:

            return body_text

        try:

            parsed = json.loads(body_text)

        except (TypeError, ValueError):

            return body_text

        try:

            return json.dumps(cls._mask_json_value(parsed))

        except (TypeError, ValueError):

            return body_text

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

    def send(
        self, endpoint, config, expected_status_override=None,
        request_overrides=None, assertions=None,
    ):
        """
        Actually sends the request. Returns a result dict — never
        raises; a connection/timeout/SSL failure is reported back in
        result["error"] instead, so the caller can show it to the
        operator and offer to fix Test Environment Settings and
        retry, rather than the app crashing on a bad/unreachable
        server.

        `request_overrides` (section F/I/J): passed straight through
        to build_request() — the per-Test-Case Params/Headers/Auth/
        Body definition layered on top of the imported endpoint and
        the Environment's own settings.

        `expected_status_override` is the Test Case's own explicit
        assertion (TestCaseRepository.api_expected_status_code — see
        automation_web_repository.py's execute_api()), used instead
        of the endpoint's own recorded example_response_status when
        set. This is what makes negative testing possible: a Postman
        collection typically only records the happy-path example
        status (e.g. 200), so without an override an endpoint that is
        DELIBERATELY expected to return 400 for this test case could
        never Pass, and an unexpected 200 could never Fail either.

        `assertions` (section F/I/J, a list of the 7 supported types
        — see evaluate_assertions()): when given (non-empty), THIS is
        what decides auto_verdict — Pass only if EVERY assertion
        passes (section J's explicit example: an expected JSON field
        value that doesn't match still FAILS even though the HTTP
        status itself was 200), and result["assertions"] carries each
        individual assertion's pass/fail/message for Run Details
        (section K). When not given, falls back to the legacy
        single-status comparison against expected_status.

        result["auto_verdict"] is "Pass"/"Fail" whenever an
        expectation was actually configured (assertions, or an
        expected_status) — including a hard "Fail" when the request
        itself never completed (connection/timeout/SSL error) but an
        expectation existed to be judged against. It is left as None
        only when there was genuinely nothing configured to check
        against, meaning "this needs a human judgment call", per how
        Interactive Locator Repair already treats genuinely ambiguous
        outcomes.

        result["request"]["headers"]/["body"] are ALWAYS the MASKED
        versions (see _mask_headers()/_mask_body_text(), reusing
        Core/secret_masking.py) — never the real credential values —
        even though the actual outgoing HTTP call below still uses
        the real, unmasked request_spec to talk to the server. Run
        Details (section K) and anything else reading this result
        must never see a real secret.
        """

        request_spec = self.build_request(endpoint, config, request_overrides)

        expected_status = (
            expected_status_override
            if expected_status_override not in (None, "")
            else endpoint.get("example_response_status")
        )

        body_text_for_mask = ""

        if request_spec.get("data"):

            try:

                body_text_for_mask = request_spec["data"].decode("utf-8")

            except Exception:

                body_text_for_mask = "<binary body — not shown>"

        result = {
            "request": {
                key: value for key, value in request_spec.items()
                if key not in ("auth", "headers", "data")
            },
            "expected_status": expected_status,
            "error": None,
            "status_code": None,
            "response_headers": {},
            "response_body_text": "",
            "elapsed_ms": None,
            "auto_verdict": None,
            "assertions": [],
            "extracted_variables": {},
        }

        result["request"]["headers"] = self._mask_headers(request_spec["headers"])

        result["request"]["body"] = self._mask_body_text(body_text_for_mask)

        has_expectation = bool(assertions) or bool(expected_status)

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

            if has_expectation:

                result["auto_verdict"] = "Fail"

            self.logger.exception(
                f"API Automation request failed: "
                f"{request_spec.get('method')} {request_spec.get('url')}"
            )

            return result

        result["elapsed_ms"] = round((time.time() - start) * 1000, 1)

        result["status_code"] = response.status_code

        result["response_headers"] = dict(response.headers)

        result["response_body_text"] = self._safe_body_text(response)

        if assertions:

            evaluation = self.evaluate_assertions(assertions, result)

            result["assertions"] = evaluation["results"]

            result["auto_verdict"] = evaluation["overall"]

        elif expected_status:

            result["auto_verdict"] = (
                "Pass" if int(expected_status) == response.status_code else "Fail"
            )

        return result

    # --------------------------------------------------
    # Assertions & variable extraction (section F/I/J) — evaluated
    # against an already-completed send() result; never re-sends the
    # request. Both are best-effort and never raise: a malformed
    # assertion or extraction rule is reported/skipped individually
    # rather than crashing the whole execution.
    # --------------------------------------------------

    @staticmethod
    def _parse_json_body(result):
        """
        Attempts to parse result["response_body_text"] as JSON.
        Returns the parsed value, or None if the body is empty or
        isn't valid JSON — used by both evaluate_assertions() and
        extract_variables() so a malformed/non-JSON response makes
        every JSON-path check fail cleanly (path "not found") instead
        of raising.
        """

        text = (result or {}).get("response_body_text")

        if not text:

            return None

        try:

            return json.loads(text)

        except (TypeError, ValueError):

            return None

    @staticmethod
    def _resolve_json_path(data, path):
        """
        Minimal JSONPath-ish resolver — enough for the common Postman
        Test-style paths operators actually write, without a full
        JSONPath dependency: an optional leading "$" / "$." prefix,
        dotted object keys ("data.id"), and integer list indices via
        "[N]" ("items[0].id", "a.b[2].c[0]"). Returns (found, value)
        — a two-tuple, not just the value — so a path that genuinely
        resolves to a real `null`/`None` can be told apart from a
        path that simply didn't exist in the response.
        """

        if not path:

            return False, None

        remaining = str(path).strip()

        if remaining.startswith("$"):

            remaining = remaining[1:]

        remaining = remaining.lstrip(".")

        if not remaining:

            return True, data

        current = data

        for segment in remaining.split("."):

            if not segment:

                continue

            segment_match = re.match(r"^([^\[\]]*)((?:\[\d+\])*)$", segment)

            if not segment_match:

                return False, None

            name = segment_match.group(1)

            index_part = segment_match.group(2)

            if name:

                if isinstance(current, dict) and name in current:

                    current = current[name]

                else:

                    return False, None

            for index_str in re.findall(r"\[(\d+)\]", index_part):

                index = int(index_str)

                if isinstance(current, list) and 0 <= index < len(current):

                    current = current[index]

                else:

                    return False, None

        return True, current

    @staticmethod
    def _is_sensitive_json_path(path):
        """
        REMOVE-SCRIPT-LIFECYCLE item 13: does the LEAF segment of a
        json_field_equals/json_field_contains assertion path look like
        a credential field (e.g. "$.access_token",
        "data.client_secret", "tokens[0].api_key")? Reuses the same
        name-based heuristic header/param masking already applies
        (Core.secret_masking.is_sensitive_key) — only the last segment
        is checked, since "$.user.token" is about the token, not the
        user.
        """

        text = str(path or "").strip()

        if text.startswith("$"):

            text = text[1:]

        text = text.strip(".")

        segments = [segment for segment in text.split(".") if segment]

        if not segments:

            return False

        leaf = re.sub(r"\[\d+\]", "", segments[-1])

        return is_sensitive_key(leaf)

    def evaluate_assertions(self, assertions, result):
        """
        Evaluates each configured assertion — section F/I/J's 7
        supported types (status_equals / json_field_exists /
        json_field_equals / json_field_contains / response_time_lte /
        header_exists / header_equals) — against an already-completed
        send() result, in the given order. Returns {"overall":
        "Pass"|"Fail"|None, "results": [...]} — "overall" is "Pass"
        only when EVERY assertion in the list passed, and is None
        (not a vacuous "Pass") for a genuinely EMPTY assertions list —
        "nothing was configured to judge against" is the same "needs
        a human judgment call" signal send() itself uses when neither
        assertions nor an expected status exist. Never raises: an
        assertion with an unknown "type" or a value that can't be
        compared (e.g. a non-numeric response_time_lte) is reported as
        a FAILED assertion with an explanatory message, never skipped
        silently and never a crash.
        """

        assertions = assertions or []

        if not assertions:

            return {"overall": None, "results": []}

        parsed_body = self._parse_json_body(result)

        results = []

        overall_pass = True

        for assertion in assertions:

            assertion = assertion or {}

            assertion_type = str(assertion.get("type") or "").strip()

            label = assertion.get("label") or assertion_type

            passed = False

            message = ""

            try:

                if assertion_type == "status_equals":

                    expected = assertion.get("expected")

                    actual = result.get("status_code")

                    passed = expected is not None and int(expected) == actual

                    message = f"expected status {expected}, got {actual}"

                elif assertion_type == "json_field_exists":

                    path = assertion.get("path")

                    found, _ = self._resolve_json_path(parsed_body, path)

                    passed = found

                    message = (
                        f"path {path} exists" if passed
                        else f"path {path} not found in response body"
                    )

                elif assertion_type == "json_field_equals":

                    path = assertion.get("path")

                    found, value = self._resolve_json_path(parsed_body, path)

                    expected = assertion.get("expected")

                    # A real JSON value is often a number/boolean/null,
                    # but "expected" almost always arrives as a plain
                    # string (a human typed it into the Request
                    # Configuration UI's Assertions tab) -- comparing
                    # strictly would make an operator-authored
                    # assertion against a numeric field (e.g. an id)
                    # fail even when it's obviously "the same value" to
                    # a person reading it. Try the real, typed
                    # comparison first; a string-coerced comparison is
                    # the fallback, never the other way around, so
                    # "7" matches 7 but "7" never matches "7.0" unless
                    # the caller passed a native 7.0.
                    passed = found and (
                        value == expected or str(value) == str(expected)
                    )

                    # REMOVE-SCRIPT-LIFECYCLE item 13: a body field like
                    # $.access_token or $.client_secret is exactly the
                    # kind of assertion an OAuth-style login test writes
                    # — the real value must never land unmasked in this
                    # message (it is returned to the browser AND
                    # persisted verbatim into run history/stdout).
                    if not found:

                        message = f"path {path} not found in response body"

                    elif self._is_sensitive_json_path(path):

                        message = (
                            f"expected {path} to equal the configured value"
                            + (" — match" if passed else " — mismatch")
                        )

                    else:

                        message = f"expected {path} == {expected!r}, got {value!r}"

                elif assertion_type == "json_field_contains":

                    path = assertion.get("path")

                    found, value = self._resolve_json_path(parsed_body, path)

                    expected = assertion.get("expected")

                    passed = (
                        found and expected is not None
                        and str(expected) in str(value)
                    )

                    if not found:

                        message = f"path {path} not found in response body"

                    elif self._is_sensitive_json_path(path):

                        message = (
                            f"expected {path} to contain the configured value"
                            + (" — match" if passed else " — mismatch")
                        )

                    else:

                        message = (
                            f"expected {path} to contain {expected!r}, got {value!r}"
                        )

                elif assertion_type == "response_time_lte":

                    expected = assertion.get("expected")

                    actual = result.get("elapsed_ms")

                    passed = (
                        expected is not None and actual is not None
                        and float(actual) <= float(expected)
                    )

                    message = f"expected response time <= {expected}ms, got {actual}ms"

                elif assertion_type == "header_exists":

                    header_name = str(assertion.get("header") or "")

                    response_headers = result.get("response_headers") or {}

                    passed = any(
                        key.lower() == header_name.lower()
                        for key in response_headers
                    )

                    message = (
                        f"header {header_name} present" if passed
                        else f"header {header_name} not found in response"
                    )

                elif assertion_type == "header_equals":

                    header_name = str(assertion.get("header") or "")

                    expected = assertion.get("expected")

                    response_headers = result.get("response_headers") or {}

                    actual = next(
                        (
                            value for key, value in response_headers.items()
                            if key.lower() == header_name.lower()
                        ),
                        None,
                    )

                    passed = actual is not None and str(actual) == str(expected)

                    # REMOVE-SCRIPT-LIFECYCLE item 13: Authorization,
                    # X-API-Key, Set-Cookie, etc. — a header_equals
                    # assertion against one of these must never echo
                    # the real header value unmasked into this message.
                    if is_sensitive_key(header_name):

                        message = (
                            f"expected header {header_name} to equal the "
                            f"configured value"
                            + (" — match" if passed else " — mismatch")
                        )

                    else:

                        message = f"expected header {header_name} == {expected!r}, got {actual!r}"

                else:

                    message = f"unknown assertion type: {assertion_type!r}"

            except Exception as ex:

                passed = False

                message = f"assertion evaluation error: {ex}"

            if not passed:

                overall_pass = False

            results.append({
                "type": assertion_type,
                "label": label,
                "passed": passed,
                "message": message,
            })

        return {"overall": "Pass" if overall_pass else "Fail", "results": results}

    def extract_variables(self, extraction_rules, result):
        """
        Runs each configured extraction rule — section F: "JSON path
        -> variable, header -> variable" — against an already-
        completed send() result. Returns {variable_name: value, ...}
        for every rule that actually resolved; a rule that doesn't
        resolve (missing JSON path, missing header, blank variable
        name) is silently OMITTED from the returned dict rather than
        written as None/empty — the caller (execute_api(), task I/J)
        is expected to merge this on top of the existing remembered
        api_variables (TestEnvironmentConfig.remember_api_variable()),
        so a flaky/conditional field never clobbers a variable's
        previously remembered value with a blank.

        Each rule: {"type": "json_path", "path": "...", "variable":
        "..."} or {"type": "header", "header": "...", "variable":
        "..."} — "type" defaults to "json_path" when omitted.
        """

        extraction_rules = extraction_rules or []

        parsed_body = self._parse_json_body(result)

        extracted = {}

        for rule in extraction_rules:

            rule = rule or {}

            variable_name = str(rule.get("variable") or "").strip()

            if not variable_name:

                continue

            rule_type = str(rule.get("type") or "json_path").strip()

            if rule_type == "json_path":

                found, value = self._resolve_json_path(parsed_body, rule.get("path"))

                if found:

                    extracted[variable_name] = value

            elif rule_type == "header":

                header_name = str(rule.get("header") or "")

                response_headers = result.get("response_headers") or {}

                value = next(
                    (
                        v for k, v in response_headers.items()
                        if k.lower() == header_name.lower()
                    ),
                    None,
                )

                if value is not None:

                    extracted[variable_name] = value

        return extracted
