"""
QA AI Studio
URL Authenticated Playwright Session

Development #2D-FIX (Production Grade)
Permanent in-memory authenticated Playwright context.

Responsibilities:
    - Create Playwright runtime (Sync/Async safe)
    - Create browser instance
    - Create authenticated browser context
    - Keep browser/context/page alive after authentication
    - Provide authenticated context/pages safely to discovery workers
    - Close session explicitly during cleanup

Security:
    - Credentials are memory-only
    - Credentials are never logged
    - Credentials are never stored in DB/vector DB
    - Browser state remains memory-only
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

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
        try:
            self.logger = Logger.get_logger()
        except Exception:
            self.logger = logging.getLogger(__name__)

        self.headless = headless
        self.timeout_ms = timeout_ms
        self.navigation_timeout_ms = navigation_timeout_ms

        # ------------------------------------------------------
        # Persistent Memory Objects (Must remain alive post-auth)
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
        analysis: Optional[Dict[str, Any]] = None,
        credentials: Optional[Dict[str, Any]] = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """
        Authenticates a browser session and holds it open in memory.
        """
        analysis_data = analysis or {}
        creds = credentials or {}
        requested_url = (url or kwargs.get("target_url") or "").strip()

        if not requested_url:
            return {
                "success": False,
                "authenticated": False,
                "error": "URL is required.",
            }

        requested_auth_type = str(
            (analysis_data.get("authentication_type") or "NONE")
        ).upper()

        if not creds and requested_auth_type not in {"NONE", "PUBLIC", "NO_AUTH"}:
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

        # Close previous session if existing
        self.close()

        try:
            # Start Playwright runtime without context manager so it stays alive
            self.playwright = sync_playwright().start()

            self.browser = self.playwright.chromium.launch(
                headless=self.headless,
                args=["--disable-dev-shm-usage", "--no-sandbox"],
            )

            self.context = self.browser.new_context(
                ignore_https_errors=True,
                viewport={"width": 1280, "height": 720},
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/137.0.0.0 Safari/537.36"
                ),
            )

            self.page = self.context.new_page()
            self.page.set_default_timeout(self.timeout_ms)
            self.page.set_default_navigation_timeout(self.navigation_timeout_ms)

            login_url = (
                analysis_data.get("login_url")
                or analysis_data.get("final_url")
                or requested_url
            )

            self.requested_url = requested_url
            self.authentication_type = str(
                analysis_data.get("authentication_type", "NONE")
            ).upper()

            self.logger.info("Opening authentication target page.")

            # Navigate to Login Page
            try:
                self.page.goto(
                    login_url,
                    wait_until="domcontentloaded",
                    timeout=self.navigation_timeout_ms,
                )
            except PlaywrightTimeoutError:
                self.logger.warning("Authentication page navigation timed out, attempting execution.")

            self.page.wait_for_timeout(1500)

            if self.authentication_type in {"NONE", "PUBLIC", "NO_AUTH"}:
                self.authenticated = True
                return {
                    "success": True,
                    "authenticated": True,
                    "authentication_pending": False,
                    "url": self.page.url,
                    "requested_url": requested_url,
                    "authentication_type": self.authentication_type,
                    "page_title": self._safe_title(self.page),
                    "message": "Public Playwright discovery session created.",
                    "session": self,
                    "_authenticated_session": self,
                    "authenticated_session": self,
                }

            detected_fields = analysis_data.get("detected_fields", []) or []

            # Extract Credential Values (Case-insensitive matching)
            login_value = self._extract_credential(
                creds,
                ["LOGIN_ID", "Login ID", "USERNAME", "Username", "email", "EMAIL", "username", "login_id", "login"]
            )
            password_value = self._extract_credential(
                creds,
                ["PASSWORD", "Password", "password", "pass"]
            )
            token_value = self._extract_credential(
                creds,
                ["TOKEN", "Token", "token", "API_KEY", "Api Key", "api_key"]
            )

            username_field = self._find_field(
                detected_fields, ("LOGIN_ID", "USERNAME", "EMAIL")
            )
            password_field = self._find_field(
                detected_fields, ("PASSWORD",)
            )
            submit_field = self._find_field(
                detected_fields, ("LOGIN_SUBMIT",)
            )

            # Fill Username / Login ID
            username_filled = False
            if login_value:
                locator = username_field.get("locator") if username_field else None
                username_filled = self._fill_locator(self.page, locator, login_value)

                if not username_filled:
                    username_filled = self._fill_first_available(
                        self.page,
                        (
                            "#usernameInput",
                            "input[type='email']",
                            "input[name*='user' i]",
                            "input[id*='user' i]",
                            "input[name*='login' i]",
                            "input[id*='login' i]",
                            "input[name*='email' i]",
                            "input[id*='email' i]",
                            "input[type='text']",
                        ),
                        login_value,
                    )

            # Fill Password
            password_filled = False
            if password_value:
                locator = password_field.get("locator") if password_field else None
                password_filled = self._fill_locator(self.page, locator, password_value)

                if not password_filled:
                    password_filled = self._fill_first_available(
                        self.page,
                        (
                            "input[type='password']",
                            "input[name*='password' i]",
                            "input[id*='password' i]",
                            "input[name*='pass' i]",
                        ),
                        password_value,
                    )

            # Fill Token/API Key if present
            token_filled = False
            if token_value:
                token_filled = self._fill_first_available(
                    self.page,
                    (
                        "input[name*='token' i]",
                        "input[id*='token' i]",
                        "textarea[name*='token' i]",
                        "input[name*='api' i]",
                        "input[id*='api' i]",
                    ),
                    token_value,
                )

            # Perform Submit Action
            submitted = False
            if submit_field:
                submitted = self._click_locator(self.page, submit_field.get("locator"))

            if not submitted:
                submitted = self._click_first_available(
                    self.page,
                    (
                        "button[type='submit']",
                        "input[type='submit']",
                        "button:has-text('Login')",
                        "button:has-text('Log in')",
                        "button:has-text('Sign in')",
                        "button:has-text('Submit')",
                        "[role='button']:has-text('Login')",
                        "[role='button']:has-text('Sign in')",
                        "button",
                    ),
                )

            if not submitted and self.authentication_type == "SSO":
                submitted = self._press_enter(self.page)

            # Wait for Post-Login Redirect & Network Handshake
            try:
                self.page.wait_for_load_state("networkidle", timeout=10_000)
            except Exception:
                try:
                    self.page.wait_for_load_state("domcontentloaded", timeout=5_000)
                except Exception:
                    pass

            self.page.wait_for_timeout(2000)

            credentials_attempted = username_filled or password_filled or token_filled
            if not credentials_attempted:
                self.close()
                return {
                    "success": False,
                    "authenticated": False,
                    "authentication_pending": True,
                    "url": "",
                    "error": "No login credentials could be entered on the target authentication page.",
                }

            # Verify Authenticated State
            verification = self._verify_authenticated(self.page, requested_url, submitted)

            if not verification["authenticated"]:
                return {
                    "success": False,
                    "authenticated": False,
                    "authentication_pending": True,
                    "url": self.page.url if self.page else "",
                    "error": verification["reason"],
                }

            # Successfully Authenticated
            self.authenticated = True
            storage_state = self.context.storage_state()

            self.logger.info("Authenticated Playwright session created and held in memory.")

            res = {
                "success": True,
                "authenticated": True,
                "authentication_pending": False,
                "url": self.page.url,
                "requested_url": requested_url,
                "authentication_type": self.authentication_type,
                "storage_state": storage_state,
                "page_title": self._safe_title(self.page),
                "message": "Authenticated Playwright session created successfully.",
            }
            # Provide session reference aliases for downstream workers
            res["session"] = self
            res["_authenticated_session"] = self
            res["authenticated_session"] = self
            return res

        except Exception as ex:
            self.logger.exception("Authenticated Playwright session failed.")
            self.close()
            return {
                "success": False,
                "authenticated": False,
                "error": str(ex),
            }

    # ==========================================================
    # CONTEXT & PAGE ACCESSORS (Discovery / Crawler Compatible)
    # ==========================================================

    def get_authenticated_context(self):
        """Returns active Playwright Context or raises if unauthenticated."""
        if self.context is None or self.browser is None:
            raise RuntimeError("Authenticated Playwright context is not available.")
        if not self.authenticated:
            raise RuntimeError("Playwright session is not authenticated.")
        return self.context

    def get_context(self):
        """Safe getter for context."""
        return self.context

    def get_authenticated_page(self):
        """Returns the current active open page or creates a new one in the context."""
        context = self.get_authenticated_context()
        pages = context.pages

        for page in reversed(pages):
            try:
                if not page.is_closed():
                    self.page = page
                    return page
            except Exception:
                continue

        self.page = context.new_page()
        self.page.set_default_timeout(self.timeout_ms)
        self.page.set_default_navigation_timeout(self.navigation_timeout_ms)
        return self.page

    def get_page(self):
        """Safe non-throwing getter for discovery workers."""
        if self.page and not self.page.is_closed():
            return self.page
        if self.context:
            return self.get_authenticated_page()
        return None

    def get_pages(self) -> List[Any]:
        """Returns active page list without throwing coroutine comparison errors."""
        if self.context:
            return [p for p in self.context.pages if not p.is_closed()]
        if self.page and not self.page.is_closed():
            return [self.page]
        return []

    # ==========================================================
    # SESSION STATUS & CLEANUP
    # ==========================================================

    def is_authenticated(self) -> bool:
        if not self.authenticated or self.browser is None or self.context is None:
            return False

        try:
            if not self.browser.is_connected():
                return False
        except Exception:
            return False

        try:
            return any(not page.is_closed() for page in self.context.pages)
        except Exception:
            return False

    def close(self):
        """Explicitly tear down browser context and stop Playwright runtime."""
        self.authenticated = False

        if self.context is not None:
            try:
                self.context.close()
            except Exception:
                pass
            self.context = None

        self.page = None

        if self.browser is not None:
            try:
                self.browser.close()
            except Exception:
                pass
            self.browser = None

        if self.playwright is not None:
            try:
                self.playwright.stop()
            except Exception:
                pass
            self.playwright = None

    # ==========================================================
    # HELPER UTILITIES
    # ==========================================================

    @staticmethod
    def _extract_credential(creds: Dict[str, Any], keys: List[str]) -> str:
        for k in keys:
            if k in creds and creds[k]:
                return str(creds[k]).strip()
        return ""

    @staticmethod
    def _find_field(fields: List[Dict[str, Any]], field_types: tuple) -> Optional[Dict[str, Any]]:
        wanted = set(field_types)
        for field in fields:
            if field.get("field_type") in wanted:
                return field
        return None

    @staticmethod
    def _fill_locator(page: Any, locator: Optional[str], value: str) -> bool:
        if not locator:
            return False
        try:
            page.locator(locator).first.fill(value)
            return True
        except Exception:
            return False

    @staticmethod
    def _fill_first_available(page: Any, selectors: tuple, value: str) -> bool:
        for selector in selectors:
            try:
                loc = page.locator(selector).first
                if loc.count() > 0 and loc.is_visible():
                    loc.fill(value)
                    return True
            except Exception:
                continue
        return False

    @staticmethod
    def _click_locator(page: Any, locator: Optional[str]) -> bool:
        if not locator:
            return False
        try:
            page.locator(locator).first.click()
            return True
        except Exception:
            return False

    @staticmethod
    def _click_first_available(page: Any, selectors: tuple) -> bool:
        for selector in selectors:
            try:
                loc = page.locator(selector).first
                if loc.count() > 0 and loc.is_visible():
                    loc.click()
                    return True
            except Exception:
                continue
        return False

    @staticmethod
    def _press_enter(page: Any) -> bool:
        try:
            page.keyboard.press("Enter")
            return True
        except Exception:
            return False

    def _verify_authenticated(self, page: Any, requested_url: str, submitted: bool) -> Dict[str, Any]:
        current_url = (page.url or "").lower()

        # If current URL is different from login page and doesn't contain auth paths, consider valid
        auth_url_keywords = ("/login", "/signin", "/sign-in", "/auth", "/sso", "/oauth", "/authorize")
        auth_url = any(word in current_url for word in auth_url_keywords)

        # Only attempt soft navigation if still on the initial target page
        if auth_url and requested_url and requested_url.lower() != current_url:
            try:
                page.goto(requested_url, wait_until="domcontentloaded", timeout=self.navigation_timeout_ms)
                page.wait_for_timeout(1500)
                current_url = (page.url or "").lower()
                auth_url = any(word in current_url for word in auth_url_keywords)
            except Exception:
                pass

        text = self._safe_text(page).lower()

        try:
            password_count = page.locator("input[type='password']").count()
        except Exception:
            password_count = 0

        login_indicators = ("login", "log in", "sign in", "signin", "authentication required", "single sign-on")
        auth_text_matches = sum(1 for word in login_indicators if word in text)

        if password_count > 0 and (auth_url or auth_text_matches >= 2):
            return {
                "authenticated": False,
                "reason": "Authentication has not completed. The login form is still active.",
            }

        if auth_url and password_count > 0:
            return {
                "authenticated": False,
                "reason": "Authentication URL is still active.",
            }

        if not submitted and password_count > 0:
            return {
                "authenticated": False,
                "reason": "No authentication submit action was performed.",
            }

        return {
            "authenticated": True,
            "reason": "Authenticated application page detected.",
        }

    @staticmethod
    def _safe_title(page: Any) -> str:
        try:
            return (page.title() or "").strip()
        except Exception:
            return ""

    @staticmethod
    def _safe_text(page: Any) -> str:
        try:
            return page.locator("body").inner_text(timeout=5_000).strip()
        except Exception:
            return ""


# Aliases for backward compatibility across modules
UrlAuthenticatedSession = URLAuthenticatedSession
