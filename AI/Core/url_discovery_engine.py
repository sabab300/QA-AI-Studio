# AI/Core/url_discovery_engine.py

from __future__ import annotations
import re
from typing import Any
from playwright.sync_api import Page, BrowserContext


class URLDiscoveryEngine:

    def __init__(
        self,
        context: BrowserContext,
        headless: bool = False,
        timeout: int = 30000,
    ):
        if context is None:
            raise ValueError("Authenticated Playwright context is required.")

        self.context = context
        self.headless = headless
        self.timeout = timeout

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

        try:
            # Do NOT force re-navigation if already on /Dashboard or target page
            if url not in page.url and "Dashboard" not in page.url:
                page.goto(url, wait_until="domcontentloaded", timeout=self.timeout)

            page.wait_for_timeout(2000)
            result["final_url"] = page.url

            # Extract active DOM state
            page_data = self._discover_page(page)
            result["pages"].append(page_data)
            result["navigation"].extend(page_data.get("navigation", []))
            result["forms"].extend(page_data.get("forms", []))
            result["fields"].extend(page_data.get("fields", []))
            result["buttons"].extend(page_data.get("buttons", []))
            result["links"].extend(page_data.get("links", []))
            result["tabs"].extend(page_data.get("tabs", []))

            self._discover_navigation_targets(page, result)

            result["success"] = True
            return result

        except Exception as ex:
            result["error"] = str(ex)
            return result

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

        # Forms
        forms = page.locator("form")
        for index in range(min(forms.count(), 100)):
            form = forms.nth(index)
            data["forms"].append({
                "index": index,
                "action": self._safe_attr(form, "action"),
                "method": self._safe_attr(form, "method"),
            })

        # Inputs / Textareas / Selects
        inputs = page.locator("input, textarea, select")
        for index in range(min(inputs.count(), 1000)):
            data["fields"].append(self._extract_element(inputs.nth(index), index))

        # Buttons
        buttons = page.locator("button, input[type='button'], input[type='submit'], [role='button']")
        for index in range(min(buttons.count(), 500)):
            data["buttons"].append(self._extract_element(buttons.nth(index), index))

        # Links
        links = page.locator("a")
        for index in range(min(links.count(), 500)):
            element = links.nth(index)
            href = self._safe_attr(element, "href")
            text = self._safe_text(element)
            if href or text:
                data["links"].append({
                    "index": index,
                    "text": text,
                    "href": href,
                    "dynamic_xpath": self.generate_dynamic_xpath({"tag_name": "a", "text": text}),
                })

        # Navigation Bar Links
        navigation = page.locator("nav a, [role='navigation'] a, [role='tab'], .nav-link, .tab, .tabs a")
        for index in range(min(navigation.count(), 500)):
            data["navigation"].append(self._extract_element(navigation.nth(index), index))

        # Tabs
        tabs = page.locator("[role='tab'], .tab, .nav-tabs a, .tabs a")
        for index in range(min(tabs.count(), 200)):
            data["tabs"].append(self._extract_element(tabs.nth(index), index))

        return data

    def _discover_navigation_targets(self, page: Page, result: dict[str, Any]) -> None:
        links = page.locator("a")
        existing = {item.get("href") for item in result["navigation"] if item.get("href")}

        for index in range(min(links.count(), 500)):
            element = links.nth(index)
            href = self._safe_attr(element, "href")

            if not href or href.startswith("#") or href.startswith("javascript:") or href in existing:
                continue

            existing.add(href)
            result["navigation"].append({
                "index": index,
                "text": self._safe_text(element),
                "href": href,
            })

    def _extract_element(self, element, index: int) -> dict[str, Any]:
        tag_name = ""
        try:
            tag_name = element.evaluate("(e) => e.tagName.toLowerCase()")
        except Exception:
            pass

        extracted = {
            "index": index,
            "tag_name": tag_name,
            "element_type": self._safe_attr(element, "type"),
            "id": self._safe_attr(element, "id"),
            "name": self._safe_attr(element, "name"),
            "placeholder": self._safe_attr(element, "placeholder"),
            "aria_label": self._safe_attr(element, "aria-label"),
            "role": self._safe_attr(element, "role"),
            "text": self._safe_text(element),
            "href": self._safe_attr(element, "href"),
        }

        extracted["locator"] = self._build_locator(extracted)
        extracted["dynamic_xpath"] = self.generate_dynamic_xpath(extracted)

        return extracted

    def _build_locator(self, element: dict[str, Any]) -> str:
        element_id = element.get("id", "")
        if element_id and not self._is_dynamic_id(element_id):
            return f"#{element_id}"

        name = element.get("name", "")
        if name and not self._is_dynamic_id(name):
            return f"[name='{name}']"

        placeholder = element.get("placeholder", "")
        if placeholder:
            return f"[placeholder='{placeholder}']"

        element_type = element.get("element_type", "")
        if element_type:
            return f"input[type='{element_type}']"

        role = element.get("role", "")
        if role:
            return f"[role='{role}']"

        return element.get("tag_name", "")

    @staticmethod
    def generate_dynamic_xpath(element: dict) -> str:
        tag = element.get("tag_name", "*").lower() or "*"
        attr_type = element.get("element_type", "")
        placeholder = element.get("placeholder", "")
        text = element.get("text", "").strip()
        name = element.get("name", "")

        if name and not any(char.isdigit() for char in name):
            return f"//{tag}[@name='{name}']"

        if placeholder:
            return f"//{tag}[contains(@placeholder, '{placeholder}')]"

        if text and tag in ["button", "a", "span", "div"]:
            return f"//{tag}[normalize-space()='{text}']"

        if attr_type:
            return f"//{tag}[@type='{attr_type}']"

        return f"//{tag}"

    @staticmethod
    def _is_dynamic_id(value: str) -> str:
        uuid_pattern = r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
        if re.match(uuid_pattern, value):
            return True
        if len(value) > 20 and re.search(r"\d{4,}", value):
            return True
        return False

    @staticmethod
    def _safe_attr(element, name: str) -> str:
        try:
            return element.get_attribute(name) or ""
        except Exception:
            return ""

    @staticmethod
    def _safe_text(element) -> str:
        try:
            return (element.inner_text() or "").strip()
        except Exception:
            return ""