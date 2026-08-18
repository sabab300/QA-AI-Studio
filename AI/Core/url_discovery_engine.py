"""
QA AI Studio - URL Discovery & Workflow Traversal Engine
Location: AI/Core/url_discovery_engine.py

Guided Business-Flow Discovery
-------------------------------
Historically this engine only ever scanned the single view that was
showing right after login, plus whatever it could reach by blindly
clicking anything that looked like a CSS tab (".ant-tabs-tab",
"[role='tab']", etc). Against PSW's real applications that heuristic
usually finds nothing to click, so discovery silently stopped after
one shallow page (pages/forms/navigation always came back as 0 in
the log). It also threw away most of the attribute detail it had
already read from the DOM before handing candidates to
DiscoveryRepository, which is a big part of why so many buttons
were reported as "no reliable locator" and skipped — the repository
was never even given their id/name/data-testid to look at.

`scan_current_view()` is the fix for both problems. It is the
primitive behind the new guided flow: the operator drives the real,
already-authenticated browser window through the actual business
process by hand (open the form, fill a tab, move to the next
screen, ...) and clicks "Capture This Screen" in QA AI Studio after
each step. Each call scans whatever is on screen *right now* — no
guessing about which tab to click, no risk of the engine wandering
off into an unrelated part of the application — and returns rich,
fully-attributed candidates (id / name / data-testid / aria-label /
placeholder / text / tag / type / required / select options) so
DiscoveryRepository can build a trustworthy locator for nearly
everything on the screen, not just inputs that happen to have a
placeholder.

`discover()` (the older single-shot, auto-traversing entry point) is
kept for backward compatibility with any other caller, and now
reuses the same enriched scanning code internally so it benefits
from the same locator-quality fix.

Safety, unchanged: MUTATING_KEYWORDS controls are still respected.
Nothing in this module ever clicks a Submit/Save/Delete/... control
automatically, whether during the old auto-traversal or the new
guided flow. The guided flow goes further — it never clicks
*anything* automatically; the human drives, this module only reads.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional
from playwright.sync_api import BrowserContext, Page, Locator

logger = logging.getLogger(__name__)

# Mutating action keywords that must NEVER be clicked during passive discovery
MUTATING_KEYWORDS = [
    "submit", "save", "create", "confirm", "delete", "post",
    "update", "send", "process", "apply", "register", "pay", "checkout"
]

FIELD_SELECTOR = "input:visible, select:visible, textarea:visible"
BUTTON_SELECTOR = (
    "button:visible, input[type='button']:visible, "
    "input[type='submit']:visible, [role='button']:visible"
)
LINK_SELECTOR = "a[href]:visible"
TAB_SELECTOR = (
    ".ant-tabs-tab:visible, .nav-tabs li:visible, "
    "[role='tab']:visible, .tab-item:visible"
)
FORM_SELECTOR = "form:visible"


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

    # ================================================================
    # Guided flow entry point — one screen at a time, no auto-clicking
    # ================================================================

    def scan_current_view(self, label: Optional[str] = None) -> Dict[str, Any]:
        """
        Scans exactly what is on screen right now: no navigation, no
        clicking, no tab traversal. This is the safe primitive for the
        guided "capture this screen, confirm, then move to the next
        screen yourself" business-flow walkthrough.

        Returns:
            {
                "success": bool,
                "label": str,
                "url": str,
                "page_title": str,
                "fields": [...],
                "buttons": [...],
                "links": [...],
                "tabs": [...],
                "forms": [...],
                "error": str | None,
            }
        """
        page = self._get_active_page()

        if not page:
            return {
                "success": False,
                "label": label or "",
                "url": "",
                "page_title": "",
                "fields": [],
                "buttons": [],
                "links": [],
                "tabs": [],
                "forms": [],
                "error": "No active Playwright page found in context.",
            }

        try:
            page.set_default_navigation_timeout(30000)
            page.set_default_timeout(30000)

            # Give the SPA a brief moment to settle before reading the DOM —
            # deliberately short since the operator has already driven the
            # browser to this screen themselves and is waiting on us.
            page.wait_for_timeout(500)

            scan = self._discover_page(page)

            return {
                "success": True,
                "label": label or "",
                "url": page.url,
                "page_title": self._safe_title(page),
                "error": None,
                **scan,
            }

        except Exception as ex:
            self.logger.exception("scan_current_view failed.")
            return {
                "success": False,
                "label": label or "",
                "url": self._safe_url(page),
                "page_title": self._safe_title(page),
                "fields": [],
                "buttons": [],
                "links": [],
                "tabs": [],
                "forms": [],
                "error": str(ex),
            }

    @staticmethod
    def _safe_title(page: Page) -> str:
        try:
            return (page.title() or "").strip()
        except Exception:
            return ""

    @staticmethod
    def _safe_url(page: Page) -> str:
        try:
            return page.url or ""
        except Exception:
            return ""

    # ================================================================
    # Legacy single-shot entry point (auto tab traversal)
    # ================================================================

    def discover(self, url: Optional[str] = None, **_kwargs) -> Dict[str, Any]:
        """
        Scans current page and autonomously traverses business process tabs.

        Kept for backward compatibility with older callers. Prefer
        `scan_current_view()` + the guided confirmation flow for new
        work — auto-traversal has no reliable way to know it has
        found the *real* business flow (it can only click things that
        happen to look like a CSS tab), so it is best-effort only.
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
        result["success"] = True
        result["url"] = page.url
        result["page_title"] = self._safe_title(page)
        result["pages"] = [{"url": page.url, "title": result["page_title"]}]

        # 4. Extract & Traverse Discovered Tabs
        tabs = result.get("tabs", [])
        if tabs:
            self.logger.info(f"Discovered {len(tabs)} workflow tabs/steps. Starting safety-aware traversal...")
            self._traverse_workflow_tabs(page, tabs, result)

        return result

    def _wait_for_dashboard_load(self, page: Page):
        """
        Ensures SPA post-login redirects complete and interactive elements render.
        """
        self.logger.info("Waiting for post-login DOM elements and SPA component mounting...")

        try:
            page.wait_for_load_state("domcontentloaded", timeout=10000)
        except Exception:
            self.logger.warning("DOM content loaded event timed out; checking DOM tree...")

        dashboard_selectors = (
            "nav, sidebar, .dashboard, .app-container, [role='navigation'], "
            "form:visible, input:visible, select:visible, button:visible, .ant-tabs, .nav-tabs"
        )
        try:
            page.wait_for_selector(
                dashboard_selectors,
                state="visible",
                timeout=12000
            )
            self.logger.info("Dashboard interactive elements detected.")
        except Exception:
            self.logger.warning("Timeout waiting for visual layout selectors; scanning available DOM.")

        page.wait_for_timeout(3000)

    # ================================================================
    # Locator generation — single implementation (no shadowed duplicate)
    # ================================================================

    def _generate_locator(self, el: Locator, tag_name: str, attrs: Dict[str, Any]) -> (str, str):
        """
        Generates a reliable Playwright locator plus the strategy that
        produced it. Priority: id > name > data-testid > aria-label >
        placeholder > short visible text > type. A bare "tag[type=]"
        selector is returned only as an absolute last resort, and is
        clearly labelled "type" so downstream code (DiscoveryRepository)
        can treat it as low-confidence rather than storing it as if it
        were unique.
        """
        if attrs.get("id"):
            return f"#{attrs['id']}", "id"

        if attrs.get("name"):
            return f"{tag_name}[name='{attrs['name']}']", "name"

        if attrs.get("data_testid"):
            return f"[data-testid='{attrs['data_testid']}']", "data-testid"

        if attrs.get("aria_label"):
            return f"{tag_name}[aria-label='{attrs['aria_label']}']", "aria-label"

        if attrs.get("placeholder"):
            return f"{tag_name}[placeholder='{attrs['placeholder']}']", "placeholder"

        text = attrs.get("text") or ""
        if text and len(text) < 40:
            return f"{tag_name}:has-text('{text}')", "text"

        input_type = attrs.get("type")
        if input_type:
            return f"{tag_name}[type='{input_type}']", "type"

        return tag_name, "tag-only"

    def _read_attrs(self, el: Locator, tag_name: str) -> Dict[str, Any]:
        """Reads the full attribute set once per element, so every
        candidate (field, button, link or tab) carries the same rich
        shape and DiscoveryRepository always has something to work
        with regardless of what kind of element it is."""

        def attr(name):
            try:
                return el.get_attribute(name) or ""
            except Exception:
                return ""

        try:
            text = (el.inner_text() or "").strip()
        except Exception:
            text = ""

        if not text:
            # Buttons rendered as <input type="button" value="..."> have no
            # inner text — fall back to the value attribute.
            text = attr("value")

        try:
            required_attr = el.get_attribute("required")
            aria_required = attr("aria-required")
            required = bool(
                required_attr is not None or aria_required.lower() == "true"
            )
        except Exception:
            required = False

        options = []
        if tag_name == "select":
            try:
                for opt in el.locator("option").all():
                    opt_text = (opt.inner_text() or "").strip()
                    if opt_text:
                        options.append(opt_text)
            except Exception:
                pass

        return {
            "tag": tag_name,
            "type": attr("type"),
            "id": attr("id"),
            "name": attr("name"),
            "data_testid": attr("data-testid") or attr("data-cy"),
            "aria_label": attr("aria-label"),
            "placeholder": attr("placeholder"),
            "text": text[:200],
            "required": required,
            "options": options,
        }

    # ================================================================
    # Per-view scanning
    # ================================================================

    def _discover_page(self, page: Page) -> Dict[str, Any]:
        """Scans both top-level document and embedded iFrames for interactive UI elements."""

        page.wait_for_timeout(1500)

        frames = page.frames
        self.logger.info(f"Scanning {len(frames)} frame(s) for UI elements...")

        all_fields: List[Dict[str, Any]] = []
        all_buttons: List[Dict[str, Any]] = []
        all_links: List[Dict[str, Any]] = []
        all_tabs: List[Dict[str, Any]] = []
        all_forms: List[Dict[str, Any]] = []

        for frame in frames:
            try:
                frame_url = frame.url

                # 1. Fields (inputs / selects / textareas)
                for el in frame.locator(FIELD_SELECTOR).all():
                    try:
                        tag_name = el.evaluate("e => e.tagName.toLowerCase()")
                    except Exception:
                        tag_name = "input"

                    attrs = self._read_attrs(el, tag_name)
                    locator, strategy = self._generate_locator(el, tag_name, attrs)

                    all_fields.append({
                        **attrs,
                        "input_type": attrs.get("type") or "text",
                        "locator": locator,
                        "locator_strategy": strategy,
                        "frame_url": frame_url,
                    })

                # 2. Buttons
                for el in frame.locator(BUTTON_SELECTOR).all():
                    try:
                        tag_name = el.evaluate("e => e.tagName.toLowerCase()")
                    except Exception:
                        tag_name = "button"

                    attrs = self._read_attrs(el, tag_name)
                    locator, strategy = self._generate_locator(el, tag_name, attrs)

                    button_text = attrs.get("text") or ""
                    is_mutating = any(
                        kw in button_text.lower() for kw in MUTATING_KEYWORDS
                    )

                    all_buttons.append({
                        **attrs,
                        "text": button_text,
                        "locator": locator,
                        "locator_strategy": strategy,
                        "frame_url": frame_url,
                        "is_mutating_action": is_mutating,
                        "action_safety": (
                            "BLOCKED_DURING_DISCOVERY" if is_mutating else "SAFE"
                        ),
                    })

                # 3. Navigation tabs / sub-menus
                for el in frame.locator(TAB_SELECTOR).all():
                    try:
                        tag_name = el.evaluate("e => e.tagName.toLowerCase()")
                    except Exception:
                        tag_name = "div"

                    attrs = self._read_attrs(el, tag_name)
                    locator, strategy = self._generate_locator(el, tag_name, attrs)

                    all_tabs.append({
                        **attrs,
                        "locator": locator,
                        "locator_strategy": strategy,
                        "frame_url": frame_url,
                    })

                # 4. Links (excluding pure "#"/javascript: no-ops)
                for el in frame.locator(LINK_SELECTOR).all():
                    href = ""
                    try:
                        href = el.get_attribute("href") or ""
                    except Exception:
                        pass

                    if not href or href.startswith("#") or href.startswith("javascript:"):
                        continue

                    try:
                        tag_name = "a"
                        attrs = self._read_attrs(el, tag_name)
                    except Exception:
                        attrs = {"tag": "a", "type": "", "id": "", "name": "",
                                 "data_testid": "", "aria_label": "",
                                 "placeholder": "", "text": "", "required": False,
                                 "options": []}

                    locator, strategy = self._generate_locator(el, "a", attrs)

                    all_links.append({
                        **attrs,
                        "href": href,
                        "locator": locator,
                        "locator_strategy": strategy,
                        "frame_url": frame_url,
                    })

                # 5. Forms (container-level metadata only)
                for el in frame.locator(FORM_SELECTOR).all():
                    all_forms.append({
                        "id": el.get_attribute("id") or "",
                        "action": el.get_attribute("action") or "",
                        "frame_url": frame_url,
                    })

            except Exception as frame_err:
                self.logger.warning(f"Error scanning frame {frame.url}: {frame_err}")

        return {
            "fields": all_fields,
            "buttons": all_buttons,
            "links": all_links,
            "tabs": all_tabs,
            "forms": all_forms,
        }

    def _traverse_workflow_tabs(self, page: Page, tabs: List[Dict[str, Any]], aggregated_result: Dict[str, Any]):
        """Sequentially clicks tabs, fills prerequisite fields heuristically, and scans tab content safely."""
        for idx, tab in enumerate(tabs):
            tab_name = tab.get("text") or tab.get("aria_label") or f"Tab {idx+1}"
            locator_str = tab.get("locator")

            if not locator_str:
                continue

            self.logger.info(f"Navigating to Tab [{idx+1}/{len(tabs)}]: {tab_name}")

            self._fill_prerequisite_fields_heuristically(page)

            try:
                tab_element = page.locator(locator_str).first
                if tab_element.is_visible():
                    tab_element.click(timeout=3000)
                    page.wait_for_timeout(1500)

                    sub_scan = self._discover_page(page)

                    self._merge_scan_results(aggregated_result, sub_scan, tab_name=tab_name)
            except Exception as ex:
                self.logger.warning(f"Could not traverse tab '{tab_name}': {ex}")

    def _fill_prerequisite_fields_heuristically(self, page: Page):
        """Fills visible empty inputs with harmless placeholder data to satisfy required field validations."""
        try:
            inputs = page.locator(FIELD_SELECTOR).all()
            for inp in inputs:
                try:
                    if inp.is_disabled():
                        continue

                    tag_name = inp.evaluate("el => el.tagName.toLowerCase()")
                    input_type = inp.get_attribute("type") or "text"
                    val = inp.input_value() if tag_name in ["input", "textarea"] else ""

                    if val:
                        continue

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

        for form in sub_scan.get("forms", []):
            main_res.setdefault("forms", []).append(form)
