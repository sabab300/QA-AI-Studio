"""
QA AI Studio
Authenticated URL Discovery Engine
Development #2D

Real Playwright discovery engine.

Responsibilities:
    - Open authenticated URL
    - Discover pages/routes
    - Discover tabs/navigation
    - Discover forms
    - Discover input fields
    - Discover buttons
    - Discover links
    - Discover placeholders
    - Discover labels
    - Discover visible UI elements
    - Return structured knowledge for Knowledge Hub

Credentials are memory-only.
"""

from __future__ import annotations

import re
from typing import Any

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError


class URLDiscoveryEngine:

    def __init__(
        self,
        headless: bool = True,
        timeout: int = 30000,
    ):
        self.headless = headless
        self.timeout = timeout

    def discover(
        self,
        url: str,
        credentials: dict | None = None,
        authentication_type: str = "NONE",
    ) -> dict[str, Any]:

        if not url:
            raise ValueError("URL is required.")

        credentials = credentials or {}

        result: dict[str, Any] = {
            "success": False,
            "requested_url": url,
            "final_url": "",
            "authentication_type": authentication_type,
            "pages": [],
            "navigation": [],
            "forms": [],
            "fields": [],
            "buttons": [],
            "links": [],
            "tabs": [],
            "knowledge": [],
            "error": "",
        }

        with sync_playwright() as playwright:

            browser = playwright.chromium.launch(
                headless=self.headless
            )

            context = browser.new_context()

            page = context.new_page()

            page.set_default_timeout(self.timeout)

            try:
                page.goto(
                    url,
                    wait_until="domcontentloaded",
                    timeout=self.timeout,
                )

                page.wait_for_load_state(
                    "networkidle",
                    timeout=10000,
                )

            except PlaywrightTimeoutError:
                # Continue discovery even when networkidle is not reached.
                pass

            # ---------------------------------------------------------
            # Authentication
            # ---------------------------------------------------------

            if authentication_type not in (
                "",
                "NONE",
                "PUBLIC",
                "NO_AUTH",
                "NO AUTH",
            ):
                self._authenticate(
                    page,
                    credentials,
                )

            # ---------------------------------------------------------
            # Final authenticated page
            # ---------------------------------------------------------

            try:
                page.wait_for_load_state(
                    "domcontentloaded",
                    timeout=10000,
                )
            except Exception:
                pass

            result["final_url"] = page.url

            # ---------------------------------------------------------
            # Discover current page
            # ---------------------------------------------------------

            page_data = self._discover_page(page)

            result["pages"].append(page_data)

            result["navigation"].extend(
                page_data.get("navigation", [])
            )

            result["forms"].extend(
                page_data.get("forms", [])
            )

            result["fields"].extend(
                page_data.get("fields", [])
            )

            result["buttons"].extend(
                page_data.get("buttons", [])
            )

            result["links"].extend(
                page_data.get("links", [])
            )

            result["tabs"].extend(
                page_data.get("tabs", [])
            )

            # ---------------------------------------------------------
            # Discover same-page application navigation
            # ---------------------------------------------------------

            self._discover_navigation_targets(
                page,
                result,
            )

            result["success"] = True

            browser.close()

        return result

    # ================================================================
    # AUTHENTICATION
    # ================================================================

    def _authenticate(
        self,
        page,
        credentials: dict,
    ) -> None:

        login_id = (
            credentials.get("Login ID")
            or credentials.get("login_id")
            or credentials.get("username")
            or credentials.get("Username")
            or ""
        )

        password = (
            credentials.get("Password")
            or credentials.get("password")
            or ""
        )

        token = (
            credentials.get("Token")
            or credentials.get("token")
            or ""
        )

        # ------------------------------------------------------------
        # Token authentication
        # ------------------------------------------------------------

        if token:

            page.set_extra_http_headers(
                {
                    "Authorization": f"Bearer {token}"
                }
            )

            page.reload(
                wait_until="domcontentloaded"
            )

            return

        # ------------------------------------------------------------
        # Login form
        # ------------------------------------------------------------

        if not login_id and not password:
            return

        username_selectors = [
            'input[name="username"]',
            'input[name="userName"]',
            'input[name="login"]',
            'input[name="loginId"]',
            'input[name="login_id"]',
            'input[type="email"]',
            'input[autocomplete="username"]',
            'input[placeholder*="user" i]',
            'input[placeholder*="login" i]',
            'input[placeholder*="email" i]',
        ]

        password_selectors = [
            'input[type="password"]',
            'input[name="password"]',
            'input[name="passwd"]',
            'input[autocomplete="current-password"]',
            'input[placeholder*="password" i]',
        ]

        username_field = self._find_first(
            page,
            username_selectors,
        )

        password_field = self._find_first(
            page,
            password_selectors,
        )

        if username_field and login_id:
            username_field.fill(login_id)

        if password_field and password:
            password_field.fill(password)

        if username_field or password_field:

            submit_selectors = [
                'button[type="submit"]',
                'input[type="submit"]',
                'button:has-text("Login")',
                'button:has-text("Sign in")',
                'button:has-text("Sign In")',
                'button:has-text("Continue")',
                '[role="button"]:has-text("Login")',
                '[role="button"]:has-text("Sign in")',
            ]

            submit = self._find_first(
                page,
                submit_selectors,
            )

            if submit:

                submit.click()

                try:
                    page.wait_for_load_state(
                        "domcontentloaded",
                        timeout=self.timeout,
                    )
                except Exception:
                    pass

                try:
                    page.wait_for_load_state(
                        "networkidle",
                        timeout=10000,
                    )
                except Exception:
                    pass

    # ================================================================
    # PAGE DISCOVERY
    # ================================================================

    def _discover_page(
        self,
        page,
    ) -> dict[str, Any]:

        data = {
            "url": page.url,
            "title": "",
            "navigation": [],
            "forms": [],
            "fields": [],
            "buttons": [],
            "links": [],
            "tabs": [],
        }

        try:
            data["title"] = page.title()
        except Exception:
            data["title"] = ""

        # ------------------------------------------------------------
        # Forms
        # ------------------------------------------------------------

        forms = page.locator("form")

        for index in range(forms.count()):

            form = forms.nth(index)

            form_data = {
                "index": index,
                "action": self._safe_attr(form, "action"),
                "method": self._safe_attr(form, "method"),
            }

            data["forms"].append(form_data)

        # ------------------------------------------------------------
        # Inputs
        # ------------------------------------------------------------

        inputs = page.locator(
            "input, textarea, select"
        )

        for index in range(inputs.count()):

            element = inputs.nth(index)

            field = self._extract_element(
                element,
                index,
            )

            field["element_type"] = (
                self._safe_attr(element, "type")
                or element.evaluate(
                    "(e) => e.tagName.toLowerCase()"
                )
            )

            data["fields"].append(field)

        # ------------------------------------------------------------
        # Buttons
        # ------------------------------------------------------------

        buttons = page.locator(
            "button, input[type='button'], "
            "input[type='submit'], "
            "[role='button']"
        )

        for index in range(buttons.count()):

            element = buttons.nth(index)

            data["buttons"].append(
                self._extract_element(
                    element,
                    index,
                )
            )

        # ------------------------------------------------------------
        # Links
        # ------------------------------------------------------------

        links = page.locator("a")

        for index in range(
            min(links.count(), 500)
        ):

            element = links.nth(index)

            href = self._safe_attr(
                element,
                "href",
            )

            text = self._safe_text(
                element
            )

            if href or text:

                data["links"].append(
                    {
                        "index": index,
                        "text": text,
                        "href": href,
                    }
                )

        # ------------------------------------------------------------
        # Navigation / Tabs
        # ------------------------------------------------------------

        nav_elements = page.locator(
            "nav a, "
            "[role='tab'], "
            "[role='navigation'] a, "
            ".nav-link, "
            ".tab, "
            ".tabs a"
        )

        for index in range(
            min(nav_elements.count(), 500)
        ):

            element = nav_elements.nth(index)

            data["navigation"].append(
                self._extract_element(
                    element,
                    index,
                )
            )

        # ------------------------------------------------------------
        # Application tabs
        # ------------------------------------------------------------

        tabs = page.locator(
            "[role='tab'], "
            ".tab, "
            ".nav-tabs a, "
            ".tabs a"
        )

        for index in range(
            min(tabs.count(), 200)
        ):

            element = tabs.nth(index)

            data["tabs"].append(
                self._extract_element(
                    element,
                    index,
                )
            )

        return data

    # ================================================================
    # NAVIGATION TARGET DISCOVERY
    # ================================================================

    def _discover_navigation_targets(
        self,
        page,
        result,
    ) -> None:

        links = page.locator("a")

        seen = {
            item.get("href")
            for item in result["links"]
            if item.get("href")
        }

        for index in range(
            min(links.count(), 500)
        ):

            element = links.nth(index)

            href = self._safe_attr(
                element,
                "href",
            )

            if not href:
                continue

            if href.startswith("#"):
                continue

            if href in seen:
                continue

            if href.startswith("javascript:"):
                continue

            seen.add(href)

            result["navigation"].append(
                {
                    "index": index,
                    "text": self._safe_text(element),
                    "href": href,
                }
            )

    # ================================================================
    # ELEMENT EXTRACTION
    # ================================================================

    def _extract_element(
        self,
        element,
        index: int,
    ) -> dict[str, Any]:

        tag = ""

        try:
            tag = element.evaluate(
                "(e) => e.tagName.toLowerCase()"
            )
        except Exception:
            pass

        return {
            "index": index,
            "tag": tag,
            "id": self._safe_attr(element, "id"),
            "name": self._safe_attr(element, "name"),
            "type": self._safe_attr(element, "type"),
            "placeholder": self._safe_attr(
                element,
                "placeholder",
            ),
            "aria_label": self._safe_attr(
                element,
                "aria-label",
            ),
            "role": self._safe_attr(
                element,
                "role",
            ),
            "title": self._safe_attr(
                element,
                "title",
            ),
            "value": self._safe_attr(
                element,
                "value",
            ),
            "text": self._safe_text(
                element
            ),
            "visible": self._safe_visible(
                element
            ),
            "enabled": self._safe_enabled(
                element
            ),
            "required": self._safe_required(
                element
            ),
        }

    # ================================================================
    # HELPERS
    # ================================================================

    def _find_first(
        self,
        page,
        selectors: list[str],
    ):

        for selector in selectors:

            try:

                locator = page.locator(
                    selector
                ).first

                if locator.count() > 0:
                    if locator.is_visible():
                        return locator

            except Exception:
                continue

        return None

    @staticmethod
    def _safe_attr(
        element,
        name: str,
    ) -> str:

        try:
            return (
                element.get_attribute(name)
                or ""
            )
        except Exception:
            return ""

    @staticmethod
    def _safe_text(
        element,
    ) -> str:

        try:
            return (
                element.inner_text()
                or ""
            ).strip()
        except Exception:
            return ""

    @staticmethod
    def _safe_visible(
        element,
    ) -> bool:

        try:
            return element.is_visible()
        except Exception:
            return False

    @staticmethod
    def _safe_enabled(
        element,
    ) -> bool:

        try:
            return element.is_enabled()
        except Exception:
            return False

    @staticmethod
    def _safe_required(
        element,
    ) -> bool:

        try:
            value = element.get_attribute(
                "required"
            )

            aria = element.get_attribute(
                "aria-required"
            )

            return (
                value is not None
                or aria == "true"
            )

        except Exception:
            return False