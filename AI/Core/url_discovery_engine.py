"""
QA AI Studio - URL Discovery & Workflow Traversal Engine
Location: AI/Core/url_discovery_engine.py
"""

from __future__ import annotations

import logging
import re
import time
from typing import Any, Dict, List, Optional
from playwright.sync_api import BrowserContext, Page, Locator

logger = logging.getLogger(__name__)

# Mutating action keywords that must NEVER be clicked during passive discovery
MUTATING_KEYWORDS = [
    "submit", "save", "create", "confirm", "delete", "post", 
    "update", "send", "process", "apply", "register", "pay", "checkout"
]


class URLDiscoveryEngine:
    """
    Passive & Semi-Active UI Discovery Engine for multi-tab SPAs and business processes.
    """

    def __init__(
        self,
        context: Optional[BrowserContext] = None,
        page: Optional[Page] = None,
        db_conn: Any = None,
        logger_instance: Optional[logging.Logger] = None,
    ):
        self.context = context
        self.page = page or (context.pages[-1] if context and context.pages else None)
        self.db_conn = db_conn
        self.logger = logger_instance or logger

    def _get_active_page(self) -> Optional[Page]:
        """Resolves the currently active page from context or direct reference."""
        if self.page and not self.page.is_closed():
            return self.page
        if self.context and self.context.pages:
            return self.context.pages[-1]
        return None

    def discover(self, url: Optional[str] = None) -> Dict[str, Any]:
        """
        Scans current page and autonomously traverses business process tabs.
        """
        page = self._get_active_page()
        if not page:
            raise RuntimeError("No active Playwright page found in context.")

        page.set_default_navigation_timeout(30000)
        page.set_default_timeout(30000)

        # 1. Ensure navigation or wait for post-login redirects to complete
        if url:
            current_url = page.url.rstrip('/') if page.url else ""
            target_url = url.rstrip('/')

            if current_url != target_url:
                self.logger.info(f"Navigating to discovery target: {url}")
                page.goto(url, wait_until="domcontentloaded", timeout=30000)
            else:
                self.logger.info("Already on target page from auth step. Waiting for post-login rendering...")
                page.wait_for_load_state("domcontentloaded", timeout=30000)

        # 2. Wait for SPA Post-Login Elements to Render
        self._wait_for_dashboard_load(page)

        # 3. Primary Page Discovery
        self.logger.info(f"Scanning primary page state: {page.url}")
        result = self._discover_page(page)

        # 4. Extract & Traverse Discovered Tabs
        tabs = result.get("tabs", [])
        if tabs:
            self.logger.info(f"Discovered {len(tabs)} workflow tabs/steps. Starting safety-aware traversal...")
            self._traverse_workflow_tabs(page, tabs, result)

        return result

    def _wait_for_dashboard_load(self, page: Page):
        """
        Ensures SPA post-login redirects are finished and interactive elements exist.
        """
        self.logger.info("Waiting for post-login DOM elements and SPA network stabilization...")
        
        # Wait up to 10s for common dashboard layout containers or visible inputs/buttons
        try:
            page.wait_for_selector(
                "nav, sidebar, .dashboard, .app-container, [role='navigation'], input:visible, button:visible",
                timeout=10000
            )
        except Exception:
            self.logger.warning("Timeout waiting for explicit layout selectors; proceeding with available DOM.")

        # Give client-side JS rendering frames time to mount components
        page.wait_for_timeout(3000)

    def _discover_page(self, page: Page) -> Dict[str, Any]:
        """Extracts fields, forms, buttons, links, tabs, and navigation targets from current page state."""
        page_title = page.title()
        current_url = page.url

        fields = self._extract_fields(page)
        buttons = self._extract_buttons(page)
        links = self._extract_links(page)
        tabs = self._extract_tabs(page)

        return {
            "success": True,
            "url": current_url,
            "title": page_title,
            "pages": [{"url": current_url, "title": page_title}],
            "forms": self._extract_forms(page),
            "fields": fields,
            "buttons": buttons,
            "links": links,
            "tabs": tabs,
            "navigation_targets": [l for l in links if self._is_nav_link(l)]
        }

    def _traverse_workflow_tabs(self, page: Page, tabs: List[Dict[str, Any]], aggregated_result: Dict[str, Any]):
        """Sequentially clicks tabs, fills prerequisite fields heuristically, and scans tab content safely."""
        for idx, tab in enumerate(tabs):
            tab_name = tab.get("text") or tab.get("aria_label") or f"Tab {idx+1}"
            locator_str = tab.get("locator")

            if not locator_str:
                continue

            self.logger.info(f"Navigating to Tab [{idx+1}/{len(tabs)}]: {tab_name}")
            
            # Fill existing visible fields on current step before transitioning to unlock next tab
            self._fill_prerequisite_fields_heuristically(page)

            try:
                tab_element = page.locator(locator_str).first
                if tab_element.is_visible():
                    tab_element.click(timeout=3000)
                    page.wait_for_timeout(1500)  # Allow SPA rendering

                    # Discover elements rendered inside this new tab
                    sub_scan = self._discover_page(page)
                    
                    # Merge unique new fields & buttons into aggregated result
                    self._merge_scan_results(aggregated_result, sub_scan, tab_name=tab_name)
            except Exception as ex:
                self.logger.warning(f"Could not traverse tab '{tab_name}': {ex}")

    def _fill_prerequisite_fields_heuristically(self, page: Page):
        """Fills visible empty inputs with harmless placeholder data to satisfy required field validations."""
        try:
            inputs = page.locator("input:visible, select:visible, textarea:visible").all()
            for inp in inputs:
                try:
                    is_disabled = inp.is_disabled()
                    if is_disabled:
                        continue

                    tag_name = inp.evaluate("el => el.tagName.toLowerCase()")
                    input_type = inp.get_attribute("type") or "text"
                    val = inp.input_value() if tag_name in ["input", "textarea"] else ""

                    if val:
                        continue  # Already filled

                    # Apply safe heuristic values
                    if input_type in ["text", "search"]:
                        inp.fill("Test Data")
                    elif input_type == "number":
                        inp.fill("1")
                    elif input_type == "email":
                        inp.fill("qa_test@example.com")
                    elif input_type == "date":
                        inp.fill("2026-08-15")
                    elif tag_name == "select":
                        options = inp.locator("option").all()
                        if len(options) > 1:
                            inp.select_option(index=1)
                except Exception:
                    continue
        except Exception as ex:
            self.logger.debug(f"Heuristic fill pass complete with minor skips: {ex}")

    def _extract_buttons(self, page: Page) -> List[Dict[str, Any]]:
        buttons = []
        elements = page.locator("button:visible, input[type='button']:visible, input[type='submit']:visible, [role='button']:visible").all()
        for el in elements:
            try:
                text = el.inner_text().strip() or el.get_attribute("value") or el.get_attribute("aria-label") or ""
                locator_str = self._generate_locator(el)
                
                # Check for mutating action keywords
                is_mutating = any(kw in text.lower() for kw in MUTATING_KEYWORDS)
                
                buttons.append({
                    "text": text,
                    "locator": locator_str,
                    "is_mutating_action": is_mutating,
                    "action_safety": "BLOCKED_DURING_DISCOVERY" if is_mutating else "SAFE"
                })
            except Exception:
                continue
        return buttons

    def _extract_fields(self, page: Page) -> List[Dict[str, Any]]:
        fields = []
        elements = page.locator("input:visible, select:visible, textarea:visible").all()
        for el in elements:
            try:
                field_id = el.get_attribute("id") or ""
                name = el.get_attribute("name") or ""
                placeholder = el.get_attribute("placeholder") or ""
                aria_label = el.get_attribute("aria-label") or ""
                locator_str = self._generate_locator(el)

                fields.append({
                    "element_id": field_id,
                    "name": name,
                    "placeholder": placeholder,
                    "aria_label": aria_label,
                    "locator": locator_str,
                })
            except Exception:
                continue
        return fields

    def _extract_tabs(self, page: Page) -> List[Dict[str, Any]]:
        tabs = []
        tab_elements = page.locator("[role='tab']:visible, .nav-tabs li a:visible, .ant-tabs-tab:visible, ul.tabs li:visible").all()
        for el in tab_elements:
            try:
                text = el.inner_text().strip() or el.get_attribute("aria-label") or ""
                locator_str = self._generate_locator(el)
                tabs.append({"text": text, "locator": locator_str})
            except Exception:
                continue
        return tabs

    def _extract_forms(self, page: Page) -> List[Dict[str, Any]]:
        forms = []
        for el in page.locator("form:visible").all():
            try:
                forms.append({"id": el.get_attribute("id") or "", "action": el.get_attribute("action") or ""})
            except Exception:
                continue
        return forms

    def _extract_links(self, page: Page) -> List[Dict[str, Any]]:
        links = []
        for el in page.locator("a[href]:visible").all():
            try:
                links.append({"text": el.inner_text().strip(), "href": el.get_attribute("href") or ""})
            except Exception:
                continue
        return links

    def _is_nav_link(self, link: Dict[str, Any]) -> bool:
        href = link.get("href", "")
        return bool(href and not href.startswith("#") and not href.startswith("javascript:"))

    def _generate_locator(self, el: Locator) -> str:
        field_id = el.get_attribute("id")
        if field_id:
            return f"#{field_id}"
        name = el.get_attribute("name")
        if name:
            return f"[name='{name}']"
        placeholder = el.get_attribute("placeholder")
        if placeholder:
            return f"[placeholder='{placeholder}']"
        return el.evaluate("el => el.tagName.toLowerCase()")

    def _merge_scan_results(self, main_res: Dict[str, Any], sub_scan: Dict[str, Any], tab_name: str):
        existing_locators = {f.get("locator") for f in main_res["fields"]}
        for field in sub_scan.get("fields", []):
            if field.get("locator") not in existing_locators:
                field["tab_origin"] = tab_name
                main_res["fields"].append(field)
                existing_locators.add(field.get("locator"))

        existing_btn_locators = {b.get("locator") for b in main_res["buttons"]}
        for btn in sub_scan.get("buttons", []):
            if btn.get("locator") not in existing_btn_locators:
                btn["tab_origin"] = tab_name
                main_res["buttons"].append(btn)
                existing_btn_locators.add(btn.get("locator"))