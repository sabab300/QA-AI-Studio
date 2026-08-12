"""
QA AI Studio
URL Authenticated Playwright Session

Development #2D-FIX
Permanent in-memory authenticated Playwright context.

Responsibilities:
    - Create Playwright runtime
    - Create browser
    - Create authenticated browser context
    - Keep browser/context/page alive after authentication
    - Provide authenticated context to discovery
    - Close session explicitly during cleanup

Security:
    - Credentials are memory-only
    - Credentials are never logged
    - Credentials are never stored in DB/vector DB
    - Browser state remains memory-only
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from Core.logger import Logger


class URLAuthenticatedSession:

    DEFAULT_TIMEOUT_MS = 30_000
    DEFAULT_NAVIGATION_TIMEOUT_MS = 30_000

    def __init__(
        self,
        headless: bool = False,
        timeout_ms: int = DEFAULT_TIMEOUT_MS,
        navigation_timeout_ms: int = DEFAULT_NAVIGATION_TIMEOUT_MS,
    ):

        self.logger = Logger.get_logger()

        self.headless = headless
        self.timeout_ms = timeout_ms
        self.navigation_timeout_ms = navigation_timeout_ms

        # ------------------------------------------------------
        # IMPORTANT:
        # These objects MUST remain alive after authenticate()
        # ------------------------------------------------------

        self.playwright = None
        self.browser = None
        self.context = None
        self.page = None

        self.authenticated = False

        self.requested_url = ""
        self.authentication_type = "NONE"

    # ==========================================================
    # AUTHENTICATE
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

        # ------------------------------------------------------
        # Close previous session if any
        # ------------------------------------------------------

        self.close()

        try:

            # --------------------------------------------------
            # START PLAYWRIGHT WITHOUT "with"
            #
            # This is the critical #2D-FIX.
            # --------------------------------------------------

            self.playwright = sync_playwright().start()

            self.browser = self.playwright.chromium.launch(
                headless=self.headless
            )

            self.context = self.browser.new_context(
                ignore_https_errors=True,
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 "
                    "(KHTML, like Gecko) "
                    "Chrome/137.0 Safari/537.36"
                ),
            )

            self.page = self.context.new_page()

            self.page.set_default_timeout(
                self.timeout_ms
            )

            self.page.set_default_navigation_timeout(
                self.navigation_timeout_ms
            )

            login_url = (
                analysis.get("login_url")
                or analysis.get("final_url")
                or requested_url
            )

            self.requested_url = requested_url

            self.authentication_type = str(
                analysis.get(
                    "authentication_type",
                    "NONE",
                )
            ).upper()

            self.logger.info(
                "Opening authentication page."
            )

            # --------------------------------------------------
            # OPEN LOGIN PAGE
            # --------------------------------------------------

            try:

                self.page.goto(
                    login_url,
                    wait_until="domcontentloaded",
                    timeout=self.navigation_timeout_ms,
                )

            except PlaywrightTimeoutError:

                self.logger.warning(
                    "Authentication page navigation timed out."
                )

            self.page.wait_for_timeout(1500)

            detected_fields = (
                analysis.get(
                    "detected_fields",
                    [],
                )
                or []
            )

            # --------------------------------------------------
            # CREDENTIAL VALUES
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
            # USERNAME
            # --------------------------------------------------

            username_filled = False

            if login_value:

                locator = (
                    username_field.get("locator")
                    if username_field
                    else None
                )

                username_filled = self._fill_locator(
                    self.page,
                    locator,
                    login_value,
                )

                if not username_filled:

                    username_filled = (
                        self._fill_first_available(
                            self.page,
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
                    )

            # --------------------------------------------------
            # PASSWORD
            # --------------------------------------------------

            password_filled = False

            if password_value:

                locator = (
                    password_field.get("locator")
                    if password_field
                    else None
                )

                password_filled = self._fill_locator(
                    self.page,
                    locator,
                    password_value,
                )

                if not password_filled:

                    password_filled = (
                        self._fill_first_available(
                            self.page,
                            (
                                "input[type='password']",
                                "input[name*='password' i]",
                                "input[id*='password' i]",
                            ),
                            password_value,
                        )
                    )

            # --------------------------------------------------
            # TOKEN / API KEY
            # --------------------------------------------------

            token_filled = False

            if token_value or api_key_value:

                token = (
                    token_value
                    or api_key_value
                )

                token_filled = (
                    self._fill_first_available(
                        self.page,
                        (
                            "input[name*='token' i]",
                            "input[id*='token' i]",
                            "textarea[name*='token' i]",
                            "input[name*='api' i]",
                            "input[id*='api' i]",
                        ),
                        token,
                    )
                )

            # --------------------------------------------------
            # SUBMIT
            # --------------------------------------------------

            submitted = False

            if submit_field:

                submitted = self._click_locator(
                    self.page,
                    submit_field.get("locator"),
                )

            if not submitted:

                submitted = (
                    self._click_first_available(
                        self.page,
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
                )

            # --------------------------------------------------
            # SSO FALLBACK
            # --------------------------------------------------

            if (
                not submitted
                and self.authentication_type == "SSO"
            ):

                submitted = self._press_enter(
                    self.page
                )

            # --------------------------------------------------
            # WAIT
            # --------------------------------------------------

            try:

                self.page.wait_for_load_state(
                    "domcontentloaded",
                    timeout=10_000,
                )

            except Exception:
                pass

            self.page.wait_for_timeout(2000)

            credentials_attempted = (
                username_filled
                or password_filled
                or token_filled
            )

            if not credentials_attempted:

                self.close()

                return {
                    "success": False,
                    "authenticated": False,
                    "authentication_pending": True,
                    "url": "",
                    "error": (
                        "No login credentials could be "
                        "entered on the detected authentication page."
                    ),
                }

            # --------------------------------------------------
            # VERIFY
            # --------------------------------------------------

            verification = self._verify_authenticated(
                self.page,
                requested_url,
                submitted,
            )

            if not verification["authenticated"]:

                # Keep session alive because MFA/manual
                # authentication may still be pending.
                return {
                    "success": False,
                    "authenticated": False,
                    "authentication_pending": True,
                    "url": self.page.url,
                    "error": verification["reason"],
                }

            # --------------------------------------------------
            # AUTHENTICATED
            # --------------------------------------------------

            self.authenticated = True

            storage_state = (
                self.context.storage_state()
            )

            self.logger.info(
                "Authenticated Playwright session "
                "created successfully."
            )

            return {
                "success": True,
                "authenticated": True,
                "authentication_pending": False,
                "url": self.page.url,
                "requested_url": requested_url,
                "authentication_type": (
                    self.authentication_type
                ),
                "storage_state": storage_state,
                "page_title": self._safe_title(
                    self.page
                ),
                "message": (
                    "Authenticated Playwright session "
                    "created successfully."
                ),
            }

        except Exception as ex:

            self.logger.exception(
                "Authenticated Playwright session failed."
            )

            self.close()

            return {
                "success": False,
                "authenticated": False,
                "error": str(ex),
            }

    # ==========================================================
    # CONTEXT ACCESS
    # ==========================================================

    def get_authenticated_context(self):

        if (
            self.context is None
            or self.browser is None
        ):
            raise RuntimeError(
                "Authenticated Playwright context "
                "is not available."
            )

        if not self.authenticated:
            raise RuntimeError(
                "Playwright session is not authenticated."
            )

        return self.context

    # ==========================================================
    # PAGE ACCESS
    # ==========================================================

    def get_authenticated_page(self):

        context = (
            self.get_authenticated_context()
        )

        pages = context.pages

        for page in reversed(pages):

            try:

                if not page.is_closed():
                    self.page = page
                    return page

            except Exception:
                continue

        self.page = context.new_page()

        self.page.set_default_timeout(
            self.timeout_ms
        )

        self.page.set_default_navigation_timeout(
            self.navigation_timeout_ms
        )

        return self.page

    # ==========================================================
    # SESSION STATUS
    # ==========================================================

    def is_authenticated(self) -> bool:

        if not self.authenticated:
            return False

        if self.browser is None:
            return False

        if self.context is None:
            return False

        try:

            if not self.browser.is_connected():
                return False

        except Exception:

            return False

        try:

            return any(
                not page.is_closed()
                for page in self.context.pages
            )

        except Exception:

            return False

    # ==========================================================
    # CLOSE SESSION
    # ==========================================================

    def close(self):

        self.authenticated = False

        try:

            if self.context is not None:
                self.context.close()

        except Exception:
            pass

        self.context = None
        self.page = None

        try:

            if self.browser is not None:
                self.browser.close()

        except Exception:
            pass

        self.browser = None

        try:

            if self.playwright is not None:
                self.playwright.stop()

        except Exception:
            pass

        self.playwright = None

    # ==========================================================
    # FIELD HELPERS
    # ==========================================================

    @staticmethod
    def _find_field(
        fields,
        field_types,
    ):

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

                locator = (
                    page.locator(selector).first
                )

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

                locator = (
                    page.locator(selector).first
                )

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

        text = (
            self._safe_text(page)
            .lower()
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

        auth_text = sum(
            1
            for word in login_indicators
            if word in text
        )

        if password_count > 0 and (
            auth_url
            or auth_text >= 2
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
                    "No authentication submit action "
                    "was detected or performed."
                ),
            }

        return {
            "authenticated": True,
            "reason": (
                "Authenticated application page detected."
            ),
        }

    # ==========================================================
    # SAFE HELPERS
    # ==========================================================

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