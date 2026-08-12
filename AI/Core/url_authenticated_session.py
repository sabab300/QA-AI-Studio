"""
QA AI Studio
URL Authenticated Playwright Session
Development #2C

Creates an authenticated Playwright context.

Security:
    - credentials are memory-only
    - credentials are never logged
    - credentials are never stored in metadata/vector DB
    - storage_state is returned in memory only
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from Core.logger import Logger


class URLAuthenticatedSession:

    DEFAULT_TIMEOUT_MS = 30_000
    DEFAULT_NAVIGATION_TIMEOUT_MS = 30_000

    def __init__(
        self,
        headless=False,
        timeout_ms=DEFAULT_TIMEOUT_MS,
        navigation_timeout_ms=DEFAULT_NAVIGATION_TIMEOUT_MS,
    ):

        self.logger = Logger.get_logger()

        self.headless = headless
        self.timeout_ms = timeout_ms
        self.navigation_timeout_ms = navigation_timeout_ms

    # ==========================================================
    # PUBLIC API
    # ==========================================================

    def authenticate(
        self,
        url: str,
        analysis: Dict[str, Any],
        credentials: Dict[str, str],
    ) -> Dict[str, Any]:

        requested_url = (url or "").strip()

        if not requested_url:

            return {
                "success": False,
                "authenticated": False,
                "error": "URL is required.",
            }

        if not credentials:

            return {
                "success": False,
                "authenticated": False,
                "error": "Credentials are required.",
            }

        try:

            from playwright.sync_api import (
                TimeoutError as PlaywrightTimeoutError,
                sync_playwright,
            )

        except ImportError:

            return {
                "success": False,
                "authenticated": False,
                "error": (
                    "Playwright is not installed. Run:\n"
                    "pip install playwright\n"
                    "playwright install chromium"
                ),
            }

        browser = None
        context = None

        try:

            with sync_playwright() as playwright:

                browser = playwright.chromium.launch(
                    headless=self.headless
                )

                context = browser.new_context(
                    ignore_https_errors=True,
                    user_agent=(
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/137.0 Safari/537.36"
                    ),
                )

                page = context.new_page()

                page.set_default_timeout(
                    self.timeout_ms
                )

                page.set_default_navigation_timeout(
                    self.navigation_timeout_ms
                )

                login_url = (
                    analysis.get("login_url")
                    or analysis.get("final_url")
                    or requested_url
                )

                self.logger.info(
                    "Opening authentication page."
                )

                try:

                    page.goto(
                        login_url,
                        wait_until="domcontentloaded",
                        timeout=self.navigation_timeout_ms,
                    )

                except PlaywrightTimeoutError:

                    self.logger.warning(
                        "Authentication page navigation timed out."
                    )

                try:

                    page.wait_for_timeout(1500)

                except Exception:

                    pass

                auth_type = str(
                    analysis.get(
                        "authentication_type",
                        ""
                    )
                ).upper()

                detected_fields = (
                    analysis.get(
                        "detected_fields",
                        []
                    )
                    or []
                )

                # --------------------------------------------------
                # Fill username/login ID
                # --------------------------------------------------

                login_value = (
                    credentials.get("LOGIN_ID")
                    or credentials.get("Login ID")
                    or credentials.get("USERNAME")
                    or credentials.get("Username")
                    or credentials.get("CREDENTIAL")
                    or credentials.get("Credential")
                )

                password_value = (
                    credentials.get("PASSWORD")
                    or credentials.get("Password")
                )

                token_value = (
                    credentials.get("TOKEN")
                    or credentials.get("Token")
                )

                api_key_value = (
                    credentials.get("API_KEY")
                    or credentials.get("API Key")
                )

                username_field = self._find_field(
                    detected_fields,
                    (
                        "LOGIN_ID",
                        "USERNAME",
                        "EMAIL",
                    ),
                )

                password_field = self._find_field(
                    detected_fields,
                    ("PASSWORD",),
                )

                submit_field = self._find_field(
                    detected_fields,
                    ("LOGIN_SUBMIT",),
                )

                # --------------------------------------------------
                # Username
                # --------------------------------------------------

                username_filled = False

                if login_value:

                    locator = (
                        username_field.get("locator")
                        if username_field
                        else None
                    )

                    username_filled = self._fill_locator(
                        page,
                        locator,
                        login_value,
                    )

                    if not username_filled:

                        username_filled = self._fill_first_available(
                            page,
                            (
                                "input[type='email']",
                                "input[name*='user' i]",
                                "input[id*='user' i]",
                                "input[name*='login' i]",
                                "input[id*='login' i]",
                                "input[name*='email' i]",
                                "input[id*='email' i]",
                            ),
                            login_value,
                        )

                # --------------------------------------------------
                # Password
                # --------------------------------------------------

                password_filled = False

                if password_value:

                    locator = (
                        password_field.get("locator")
                        if password_field
                        else None
                    )

                    password_filled = self._fill_locator(
                        page,
                        locator,
                        password_value,
                    )

                    if not password_filled:

                        password_filled = self._fill_first_available(
                            page,
                            (
                                "input[type='password']",
                                "input[name*='password' i]",
                                "input[id*='password' i]",
                            ),
                            password_value,
                        )

                # --------------------------------------------------
                # Token / API Key
                # --------------------------------------------------

                token_filled = False

                if token_value or api_key_value:

                    token = (
                        token_value
                        or api_key_value
                    )

                    token_filled = self._fill_first_available(
                        page,
                        (
                            "input[name*='token' i]",
                            "input[id*='token' i]",
                            "textarea[name*='token' i]",
                            "input[name*='api' i]",
                            "input[id*='api' i]",
                        ),
                        token,
                    )

                # --------------------------------------------------
                # Submit
                # --------------------------------------------------

                submitted = False

                if submit_field:

                    submitted = self._click_locator(
                        page,
                        submit_field.get("locator"),
                    )

                if not submitted:

                    submitted = self._click_first_available(
                        page,
                        (
                            "button[type='submit']",
                            "input[type='submit']",
                            "button:has-text('Login')",
                            "button:has-text('Log in')",
                            "button:has-text('Sign in')",
                            "[role='button']:has-text('Login')",
                            "[role='button']:has-text('Sign in')",
                        ),
                    )

                # --------------------------------------------------
                # SSO without detectable submit control
                # --------------------------------------------------

                if not submitted and auth_type == "SSO":

                    submitted = self._press_enter(
                        page
                    )

                # --------------------------------------------------
                # Wait for authentication
                # --------------------------------------------------

                try:

                    page.wait_for_load_state(
                        "domcontentloaded",
                        timeout=10_000,
                    )

                except Exception:

                    pass

                try:

                    page.wait_for_timeout(2000)

                except Exception:

                    pass

                # --------------------------------------------------
                # Verify authentication
                #
                # If nothing was actually entered or submitted —
                # e.g. the analyzer detected zero real login fields
                # on this page — there is nothing to verify, and
                # claiming success here would be a false positive:
                # the page never had a login gate to get past in
                # the first place, so "no login page visible now"
                # proves nothing. Stop here with a clear reason
                # instead of pretending authentication happened.
                # --------------------------------------------------

                credentials_attempted = (
                    username_filled
                    or password_filled
                    or token_filled
                )

                if not credentials_attempted:

                    return {
                        "success": False,
                        "authenticated": False,
                        "authentication_pending": True,
                        "url": page.url,
                        "error": (
                            "No login fields were detected on this "
                            "page, so no credentials could be "
                            "entered. This may be a public page "
                            "rather than the actual login screen — "
                            "check the login URL."
                        ),
                    }

                verification = self._verify_authenticated(
                    page,
                    requested_url,
                    submitted,
                )

                if not verification["authenticated"]:

                    # MFA / CAPTCHA / manual SSO may still be pending.
                    return {
                        "success": False,
                        "authenticated": False,
                        "authentication_pending": True,
                        "url": page.url,
                        "error": verification["reason"],
                    }

                # --------------------------------------------------
                # IMPORTANT:
                # Storage state remains memory-only.
                # --------------------------------------------------

                storage_state = context.storage_state()

                return {
                    "success": True,
                    "authenticated": True,
                    "authentication_pending": False,
                    "url": page.url,
                    "requested_url": requested_url,
                    "authentication_type": auth_type,
                    "storage_state": storage_state,
                    "page_title": self._safe_title(page),
                    "message": (
                        "Authenticated Playwright session "
                        "created successfully."
                    ),
                }

        except Exception as ex:

            self.logger.exception(
                "Authenticated Playwright session failed."
            )

            return {
                "success": False,
                "authenticated": False,
                "error": str(ex),
            }

        finally:

            # Browser/context are deliberately closed here.
            # The session state returned above is memory-only.
            try:

                if context:

                    context.close()

            except Exception:

                pass

            try:

                if browser:

                    browser.close()

            except Exception:

                pass

    # ==========================================================
    # FIELD HELPERS
    # ==========================================================

    @staticmethod
    def _find_field(
        fields,
        field_types,
    ) -> Optional[Dict[str, Any]]:

        wanted = set(field_types)

        for field in fields:

            if field.get("field_type") in wanted:

                return field

        return None

    @staticmethod
    def _fill_locator(
        page,
        locator,
        value,
    ):

        if not locator:

            return False

        try:

            page.locator(
                locator
            ).first.fill(value)

            return True

        except Exception:

            return False

    @staticmethod
    def _fill_first_available(
        page,
        selectors,
        value,
    ):

        for selector in selectors:

            try:

                locator = page.locator(
                    selector
                ).first

                if locator.count():

                    locator.fill(value)

                    return True

            except Exception:

                continue

        return False

    @staticmethod
    def _click_locator(
        page,
        locator,
    ):

        if not locator:

            return False

        try:

            page.locator(
                locator
            ).first.click()

            return True

        except Exception:

            return False

    @staticmethod
    def _click_first_available(
        page,
        selectors,
    ):

        for selector in selectors:

            try:

                locator = page.locator(
                    selector
                ).first

                if locator.count():

                    locator.click()

                    return True

            except Exception:

                continue

        return False

    @staticmethod
    def _press_enter(page):

        try:

            page.keyboard.press("Enter")

            return True

        except Exception:

            return False

    # ==========================================================
    # AUTH VERIFICATION
    # ==========================================================

    def _verify_authenticated(
        self,
        page,
        requested_url,
        submitted,
    ):

        try:

            page.goto(
                requested_url,
                wait_until="domcontentloaded",
                timeout=self.navigation_timeout_ms,
            )

        except Exception:

            pass

        try:

            page.wait_for_timeout(1500)

        except Exception:

            pass

        current_url = (
            page.url or ""
        ).lower()

        title = self._safe_title(
            page
        ).lower()

        text = self._safe_text(
            page
        ).lower()

        login_indicators = (
            "login",
            "log in",
            "sign in",
            "signin",
            "username",
            "password",
            "authentication required",
            "single sign-on",
            "sso",
        )

        password_count = 0

        try:

            password_count = page.locator(
                "input[type='password']"
            ).count()

        except Exception:

            password_count = 0

        auth_url = any(
            word in current_url
            for word in (
                "/login",
                "/signin",
                "/sign-in",
                "/auth",
                "/sso",
                "/oauth",
                "/authorize",
            )
        )

        auth_text = sum(
            1
            for word in login_indicators
            if word in text
        )

        if password_count > 0 and (
            auth_url or auth_text >= 2
        ):

            return {
                "authenticated": False,
                "reason": (
                    "Authentication has not completed. "
                    "The login page is still active."
                ),
            }

        if auth_url:

            return {
                "authenticated": False,
                "reason": (
                    "Authentication has not completed. "
                    "Authentication URL is still active."
                ),
            }

        if not submitted:

            return {
                "authenticated": False,
                "reason": (
                    "No submit action was detected or performed, "
                    "so authentication could not be confirmed. "
                    "The absence of a login page is not, on its "
                    "own, proof of being logged in."
                ),
            }

        return {
            "authenticated": True,
            "reason": "Authenticated application page detected.",
        }

    @staticmethod
    def _safe_title(page):

        try:

            return (
                page.title() or ""
            ).strip()

        except Exception:

            return ""

    @staticmethod
    def _safe_text(page):

        try:

            return (
                page.locator("body")
                .inner_text(timeout=5_000)
                .strip()
            )

        except Exception:

            return ""