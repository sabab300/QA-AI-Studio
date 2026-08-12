"""
QA AI Studio
Authenticated URL Discovery Engine
Development #2D-FIX

Permanent authenticated Playwright context.

Responsibilities:
    - Receive an already authenticated Playwright context
    - Discover the authenticated application
    - Keep the same browser/context alive during discovery
    - Discover pages, navigation, forms, fields, buttons, links and tabs
    - Never ask for credentials
    - Never create a second browser for authenticated discovery
"""

from __future__ import annotations

from typing import Any

from playwright.sync_api import Page, BrowserContext
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError


class URLDiscoveryEngine:

    def __init__(
        self,
        context: BrowserContext,
        headless: bool = False,
        timeout: int = 30000,
    ):
        if context is None:
            raise ValueError(
                "Authenticated Playwright context is required."
            )

        self.context = context
        self.headless = headless
        self.timeout = timeout

    # ============================================================
    # PUBLIC API
    # ============================================================

    def discover(
        self,
        url: str,
        authentication_type: str = "NONE",
    ) -> dict[str, Any]:

        if not url:
            raise ValueError("URL is required.")

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

        page = self._get_or_create_page()

        page.set_default_timeout(self.timeout)
        page.set_default_navigation_timeout(self.timeout)

        try:
            # ----------------------------------------------------
            # Navigate using SAME authenticated context
            # ----------------------------------------------------
            try:
                page.goto(
                    url,
                    wait_until="domcontentloaded",
                    timeout=self.timeout,
                )
            except PlaywrightTimeoutError:
                pass

            try:
                page.wait_for_load_state(
                    "networkidle",
                    timeout=10000,
                )
            except Exception:
                pass

            result["final_url"] = page.url

            # ----------------------------------------------------
            # Discover current authenticated page
            # ----------------------------------------------------
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

            self._discover_navigation_targets(
                page,
                result,
            )

            result["success"] = True

            return result

        except Exception as ex:
            result["error"] = str(ex)
            return result

    # ============================================================
    # PAGE MANAGEMENT
    # ============================================================

    def _get_or_create_page(self) -> Page:

        pages = self.context.pages

        if pages:
            page = pages[-1]

            try:
                if not page.is_closed():
                    return page
            except Exception:
                pass

        return self.context.new_page()

    # ============================================================
    # PAGE DISCOVERY
    # ============================================================

    def _discover_page(self, page: Page) -> dict[str, Any]:

        data = {
            "url": page.url,
            "title": "",
            "forms": [],
            "fields": [],
            "buttons": [],
            "links": [],
            "navigation": [],
            "tabs": [],
        }

        try:
            data["title"] = page.title()
        except Exception:
            data["title"] = ""

        # --------------------------------------------------------
        # Forms
        # --------------------------------------------------------

        forms = page.locator("form")

        for index in range(min(forms.count(), 100)):

            form = forms.nth(index)

            data["forms"].append(
                {
                    "index": index,
                    "action": self._safe_attr(
                        form,
                        "action",
                    ),
                    "method": self._safe_attr(
                        form,
                        "method",
                    ),
                }
            )

        # --------------------------------------------------------
        # Inputs / Textareas / Selects
        # --------------------------------------------------------

        inputs = page.locator(
            "input, textarea, select"
        )

        for index in range(
            min(inputs.count(), 1000)
        ):

            element = inputs.nth(index)

            data["fields"].append(
                self._extract_element(
                    element,
                    index,
                )
            )

        # --------------------------------------------------------
        # Buttons
        # --------------------------------------------------------

        buttons = page.locator(
            "button, "
            "input[type='button'], "
            "input[type='submit'], "
            "[role='button']"
        )

        for index in range(
            min(buttons.count(), 500)
        ):

            element = buttons.nth(index)

            data["buttons"].append(
                self._extract_element(
                    element,
                    index,
                )
            )

        # --------------------------------------------------------
        # Links
        # --------------------------------------------------------

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

        # --------------------------------------------------------
        # Navigation
        # --------------------------------------------------------

        navigation = page.locator(
            "nav a, "
            "[role='navigation'] a, "
            "[role='tab'], "
            ".nav-link, "
            ".tab, "
            ".tabs a"
        )

        for index in range(
            min(navigation.count(), 500)
        ):

            element = navigation.nth(index)

            data["navigation"].append(
                self._extract_element(
                    element,
                    index,
                )
            )

        # --------------------------------------------------------
        # Tabs
        # --------------------------------------------------------

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

    # ============================================================
    # NAVIGATION TARGET DISCOVERY
    # ============================================================

    def _discover_navigation_targets(
        self,
        page: Page,
        result: dict[str, Any],
    ) -> None:

        links = page.locator("a")

        existing = {
            item.get("href")
            for item in result["navigation"]
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

            if href.startswith("javascript:"):
                continue

            if href in existing:
                continue

            existing.add(href)

            result["navigation"].append(
                {
                    "index": index,
                    "text": self._safe_text(element),
                    "href": href,
                }
            )

    # ============================================================
    # ELEMENT EXTRACTION
    # ============================================================

    def _extract_element(
        self,
        element,
        index: int,
    ) -> dict[str, Any]:

        tag_name = ""

        try:
            tag_name = element.evaluate(
                "(e) => e.tagName.toLowerCase()"
            )
        except Exception:
            pass

        return {
            "index": index,
            "tag_name": tag_name,
            "element_type": self._safe_attr(
                element,
                "type",
            ),
            "id": self._safe_attr(
                element,
                "id",
            ),
            "name": self._safe_attr(
                element,
                "name",
            ),
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
            "text": self._safe_text(
                element
            ),
            "href": self._safe_attr(
                element,
                "href",
            ),
            "locator": self._build_locator(
                element
            ),
        }

    # ============================================================
    # LOCATOR
    # ============================================================

    def _build_locator(self, element) -> str:

        try:

            element_id = self._safe_attr(
                element,
                "id",
            )

            if element_id:
                return f"#{element_id}"

            name = self._safe_attr(
                element,
                "name",
            )

            if name:
                return (
                    f"[name='{name}']"
                )

            placeholder = self._safe_attr(
                element,
                "placeholder",
            )

            if placeholder:
                return (
                    f"[placeholder='{placeholder}']"
                )

            role = self._safe_attr(
                element,
                "role",
            )

            if role:
                return (
                    f"[role='{role}']"
                )

            tag = element.evaluate(
                "(e) => e.tagName.toLowerCase()"
            )

            return tag or ""

        except Exception:
            return ""

    # ============================================================
    # SAFE HELPERS
    # ============================================================

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
    def _safe_text(element) -> str:

        try:
            return (
                element.inner_text()
                or ""
            ).strip()
        except Exception:
            return ""