"""
QA AI Studio
Authenticated URL Discovery Engine
Development #2D

Responsibilities:
    - Open authenticated URL
    - Handle login forms
    - Handle SSO authentication
    - Dismiss blocking security/modals
    - Discover pages/routes
    - Discover navigation
    - Discover forms
    - Discover fields
    - Discover buttons
    - Discover links
    - Discover tabs
    - Discover visible UI elements
    - Return structured knowledge
"""

from __future__ import annotations

from typing import Any

from playwright.sync_api import (
    sync_playwright,
    TimeoutError as PlaywrightTimeoutError,
)


class URLDiscoveryEngine:

    def __init__(
        self,
        headless: bool = True,
        timeout: int = 30000,
    ):
        self.headless = headless
        self.timeout = timeout

    # ================================================================
    # MAIN DISCOVERY
    # ================================================================

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
            "tab_scans": [],
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

                # ----------------------------------------------------
                # OPEN URL
                # ----------------------------------------------------

                print(
                    f"Starting authenticated Playwright discovery: {url}"
                )

                page.goto(
                    url,
                    wait_until="domcontentloaded",
                    timeout=self.timeout,
                )

                try:
                    page.wait_for_load_state(
                        "networkidle",
                        timeout=10000,
                    )
                except Exception:
                    pass

                # ----------------------------------------------------
                # DISMISS INITIAL MODALS
                # ----------------------------------------------------

                self._dismiss_blocking_modals(page)

                # ----------------------------------------------------
                # AUTHENTICATION
                # ----------------------------------------------------

                normalized_auth = (
                    authentication_type or "NONE"
                ).strip().upper()

                if normalized_auth not in (
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

                # ----------------------------------------------------
                # POST AUTHENTICATION WAIT
                # ----------------------------------------------------

                try:
                    page.wait_for_load_state(
                        "domcontentloaded",
                        timeout=10000,
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

                # ----------------------------------------------------
                # DISMISS POST LOGIN MODALS
                # ----------------------------------------------------

                self._dismiss_blocking_modals(page)

                # ----------------------------------------------------
                # FINAL URL
                # ----------------------------------------------------

                result["final_url"] = page.url

                # ----------------------------------------------------
                # DISCOVER CURRENT PAGE
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

                # ----------------------------------------------------
                # DISCOVER NAVIGATION TARGETS
                # ----------------------------------------------------

                self._discover_navigation_targets(
                    page,
                    result,
                )

                # ----------------------------------------------------
                # DISCOVER ALL TABS ON THIS PAGE
                # ----------------------------------------------------
                # Read-only: only clicks elements matched by the tab
                # selector (nav-shaped, not a Submit/Save/Delete/
                # Confirm/Create/Approve control), and even then
                # skips anything whose own label looks mutating, as
                # a second guard. Most real UIs (this app's own PSW
                # example included) only render a tab's fields once
                # that tab is actually clicked, so without this the
                # scan only ever sees whichever tab happened to be
                # open by default.

                self._discover_all_tabs(
                    page,
                    result,
                    page_data,
                )

                # ----------------------------------------------------
                # KNOWLEDGE SUMMARY
                # ----------------------------------------------------

                result["knowledge"] = [
                    {
                        "url": page.url,
                        "title": page_data.get("title", ""),
                        "navigation_count": len(
                            page_data.get("navigation", [])
                        ),
                        "form_count": len(
                            page_data.get("forms", [])
                        ),
                        "field_count": len(
                            page_data.get("fields", [])
                        ),
                        "button_count": len(
                            page_data.get("buttons", [])
                        ),
                        "link_count": len(
                            page_data.get("links", [])
                        ),
                        "tab_count": len(
                            page_data.get("tabs", [])
                        ),
                        "tabs_scanned_count": len(
                            result.get("tab_scans", [])
                        ),
                    }
                ]

                result["success"] = True

                print(
                    "Authenticated Playwright discovery completed."
                )

            except Exception as ex:

                result["error"] = str(ex)

                print(
                    f"Authenticated discovery failed: {ex}"
                )

            finally:

                try:
                    context.close()
                except Exception:
                    pass

                try:
                    browser.close()
                except Exception:
                    pass

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
        # TOKEN AUTHENTICATION
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

            self._dismiss_blocking_modals(page)

            return

        # ------------------------------------------------------------
        # NO CREDENTIALS
        # ------------------------------------------------------------

        if not login_id and not password:
            return

        # ------------------------------------------------------------
        # ALWAYS DISMISS BLOCKING MODALS FIRST
        # ------------------------------------------------------------

        self._dismiss_blocking_modals(page)

        # ------------------------------------------------------------
        # USERNAME
        # ------------------------------------------------------------

        username_selectors = [
            'input[name="username"]',
            'input[name="userName"]',
            'input[name="login"]',
            'input[name="loginId"]',
            'input[name="login_id"]',
            'input[id="usernameInput"]',
            'input[type="email"]',
            'input[autocomplete="username"]',
            'input[placeholder*="user" i]',
            'input[placeholder*="login" i]',
            'input[placeholder*="email" i]',
        ]

        # ------------------------------------------------------------
        # PASSWORD
        # ------------------------------------------------------------

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

            username_field.fill(
                login_id
            )

        if password_field and password:

            password_field.fill(
                password
            )

        # ------------------------------------------------------------
        # LOGIN
        # ------------------------------------------------------------

        if username_field or password_field:

            self._dismiss_blocking_modals(page)

            submit_selectors = [
                'button[type="submit"]',
                'input[type="submit"]',
                'button:has-text("Login")',
                'button:has-text("Log in")',
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

                try:

                    submit.click(
                        timeout=10000
                    )

                except Exception:

                    # Modal may have appeared again.
                    self._dismiss_blocking_modals(page)

                    try:

                        submit.click(
                            timeout=10000,
                            force=True,
                        )

                    except Exception as ex:

                        raise RuntimeError(
                            "Unable to click authentication "
                            f"submit control: {ex}"
                        )

                # ----------------------------------------------------
                # WAIT AFTER LOGIN
                # ----------------------------------------------------

                try:
                    page.wait_for_load_state(
                        "domcontentloaded",
                        timeout=15000,
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

                # ----------------------------------------------------
                # POST LOGIN MODAL
                # ----------------------------------------------------

                self._dismiss_blocking_modals(
                    page
                )

    # ================================================================
    # SECURITY AWARENESS / BLOCKING MODALS
    # ================================================================

    def _dismiss_blocking_modals(
        self,
        page,
    ) -> None:

        # Give dynamic modal time to appear.
        try:
            page.wait_for_timeout(500)
        except Exception:
            pass

        modal_selectors = [
            ".SecurityAwarenessModal",
            "[class*='SecurityAwarenessModal']",
            "[class*='security-awareness']",
            "[class*='SecurityAwareness']",
            ".modal.show",
            ".modal-dialog",
            "[role='dialog']",
        ]

        button_selectors = [
            "button:has-text('Close')",
            "button:has-text('OK')",
            "button:has-text('Ok')",
            "button:has-text('Got it')",
            "button:has-text('Continue')",
            "button:has-text('Proceed')",
            "button:has-text('Accept')",
            "button:has-text('I Agree')",
            "button:has-text('Agree')",
            "[aria-label='Close']",
            "[aria-label='close']",
            "[aria-label='Dismiss']",
            ".btn-close",
            ".close",
            "button.close",
        ]

        # ------------------------------------------------------------
        # TRY MODAL-SPECIFIC BUTTONS
        # ------------------------------------------------------------

        for modal_selector in modal_selectors:

            try:

                modal = page.locator(
                    modal_selector
                )

                count = modal.count()

                if count == 0:
                    continue

                for index in range(count):

                    current_modal = modal.nth(index)

                    try:

                        if not current_modal.is_visible():
                            continue

                    except Exception:
                        continue

                    for button_selector in button_selectors:

                        try:

                            buttons = current_modal.locator(
                                button_selector
                            )

                            button_count = buttons.count()

                            for button_index in range(
                                button_count
                            ):

                                button = buttons.nth(
                                    button_index
                                )

                                try:

                                    if not button.is_visible():
                                        continue

                                    button.click(
                                        timeout=3000,
                                        force=True,
                                    )

                                    page.wait_for_timeout(
                                        500
                                    )

                                    return

                                except Exception:
                                    continue

                        except Exception:
                            continue

            except Exception:
                continue

        # ------------------------------------------------------------
        # TRY GLOBAL BUTTONS
        # ------------------------------------------------------------

        for selector in button_selectors:

            try:

                buttons = page.locator(
                    selector
                )

                count = buttons.count()

                for index in range(count):

                    button = buttons.nth(index)

                    try:

                        if not button.is_visible():
                            continue

                        button.click(
                            timeout=2000,
                            force=True,
                        )

                        page.wait_for_timeout(
                            500
                        )

                        return

                    except Exception:
                        continue

            except Exception:
                continue

        # ------------------------------------------------------------
        # ESCAPE
        # ------------------------------------------------------------

        try:

            page.keyboard.press(
                "Escape"
            )

            page.wait_for_timeout(
                500
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
        # FORMS
        # ------------------------------------------------------------

        try:

            forms = page.locator(
                "form"
            )

            for index in range(
                forms.count()
            ):

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

        except Exception:
            pass

        # ------------------------------------------------------------
        # INPUTS + BUTTONS (shared helper — also used per-tab by
        # _discover_all_tabs, so the two never drift apart)
        # ------------------------------------------------------------

        data["fields"], data["buttons"] = (
            self._extract_fields_and_buttons(page)
        )

        # ------------------------------------------------------------
        # LINKS
        # ------------------------------------------------------------

        try:

            links = page.locator(
                "a"
            )

            for index in range(
                min(links.count(), 1000)
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
                            "visible": self._safe_visible(
                                element
                            ),
                        }
                    )

        except Exception:
            pass

        # ------------------------------------------------------------
        # NAVIGATION
        # ------------------------------------------------------------

        try:

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

        except Exception:
            pass

        # ------------------------------------------------------------
        # TABS
        # ------------------------------------------------------------

        try:

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

        except Exception:
            pass

        return data

    # ================================================================
    # NAVIGATION TARGET DISCOVERY
    # ================================================================

    def _discover_navigation_targets(
        self,
        page,
        result,
    ) -> None:

        try:

            links = page.locator(
                "a"
            )

            seen = {
                item.get("href")
                for item in result["links"]
                if item.get("href")
            }

            for index in range(
                min(links.count(), 1000)
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

                if href.startswith(
                    "javascript:"
                ):
                    continue

                if href in seen:
                    continue

                seen.add(href)

                result["navigation"].append(
                    {
                        "index": index,
                        "text": self._safe_text(
                            element
                        ),
                        "href": href,
                    }
                )

        except Exception:
            pass

    # ================================================================
    # SHARED FIELD/BUTTON EXTRACTION
    # ================================================================
    # Factored out of _discover_page so the initial scan and the
    # per-tab scan (_discover_all_tabs) use exactly one
    # implementation — two copies of this logic drifting apart is
    # exactly the kind of bug that's bitten this codebase before
    # (the login-fill logic existing separately in two files).

    def _extract_fields_and_buttons(
        self,
        page,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:

        fields: list[dict[str, Any]] = []

        buttons: list[dict[str, Any]] = []

        try:

            inputs = page.locator(
                "input:visible, textarea:visible, select:visible"
            )

            for index in range(
                min(inputs.count(), 1000)
            ):

                element = inputs.nth(index)

                field = self._extract_element(
                    element,
                    index,
                )

                field["element_type"] = (
                    self._safe_attr(
                        element,
                        "type",
                    )
                    or element.evaluate(
                        "(e) => e.tagName.toLowerCase()"
                    )
                )

                fields.append(field)

        except Exception:
            pass

        try:

            button_elements = page.locator(
                "button:visible, "
                "input[type='button']:visible, "
                "input[type='submit']:visible, "
                "[role='button']:visible"
            )

            for index in range(
                min(button_elements.count(), 1000)
            ):

                element = button_elements.nth(index)

                buttons.append(
                    self._extract_element(
                        element,
                        index,
                    )
                )

        except Exception:
            pass

        return fields, buttons

    # ================================================================
    # SAFETY GUARD — never click anything mutating-shaped
    # ================================================================
    # Discovery is read-only by construction. Tabs are already a
    # structurally different element class from Submit/Save/Delete
    # buttons, but this is a second, belt-and-suspenders check on
    # the label text itself before anything gets clicked.

    _MUTATING_WORDS = (
        "submit", "save", "delete", "remove", "confirm",
        "create", "approve", "reject", "cancel order",
        "pay", "checkout", "send", "post", "publish",
    )

    def _looks_mutating(self, text: str) -> bool:

        if not text:

            return False

        lowered = text.strip().lower()

        return any(word in lowered for word in self._MUTATING_WORDS)

    # ================================================================
    # MULTI-TAB DISCOVERY (read-only)
    # ================================================================

    def _discover_all_tabs(
        self,
        page,
        result,
        initial_page_data,
    ) -> None:

        MAX_TABS = 20

        try:

            tab_locator = page.locator(
                "[role='tab'], "
                ".tab, "
                ".nav-tabs a, "
                ".tabs a"
            )

            tab_count = min(tab_locator.count(), MAX_TABS)

        except Exception:

            tab_count = 0

        if tab_count == 0:

            return

        # The default/landing view has already been scanned by
        # _discover_page — record it as the first "tab scan" so
        # callers get one consistent list to work from, using
        # whichever tab is currently marked active/selected if we
        # can tell, else a generic label.

        active_label = self._active_tab_label(page, tab_locator, tab_count)

        result["tab_scans"].append(
            {
                "tab_name": active_label or "Default",
                "url": page.url,
                "fields": initial_page_data.get("fields", []),
                "buttons": initial_page_data.get("buttons", []),
            }
        )

        visited_labels = {
            (active_label or "Default").strip().lower()
        }

        for index in range(tab_count):

            try:

                tab_element = page.locator(
                    "[role='tab'], "
                    ".tab, "
                    ".nav-tabs a, "
                    ".tabs a"
                ).nth(index)

                label = self._safe_text(tab_element) or (
                    self._safe_attr(tab_element, "aria-label")
                )

                if not label:

                    continue

                normalized = label.strip().lower()

                if normalized in visited_labels:

                    continue

                if self._looks_mutating(label):

                    # Defensive skip — shouldn't trigger in practice
                    # since this selector targets tab-shaped
                    # elements, but never click it if it does.
                    continue

                if not tab_element.is_visible():

                    continue

                if not tab_element.is_enabled():

                    continue

                try:

                    tab_element.click(timeout=5000)

                except Exception:

                    self._dismiss_blocking_modals(page)

                    try:

                        tab_element.click(
                            timeout=5000, force=True
                        )

                    except Exception:

                        continue

                try:

                    page.wait_for_load_state(
                        "networkidle", timeout=5000
                    )

                except Exception:

                    pass

                self._dismiss_blocking_modals(page)

                tab_fields, tab_buttons = (
                    self._extract_fields_and_buttons(page)
                )

                result["tab_scans"].append(
                    {
                        "tab_name": label.strip(),
                        "url": page.url,
                        "fields": tab_fields,
                        "buttons": tab_buttons,
                    }
                )

                visited_labels.add(normalized)

            except Exception:

                continue

    def _active_tab_label(self, page, tab_locator, tab_count):
        """
        Best-effort guess at which tab was already showing when the
        page loaded (aria-selected='true' or a common "active" CSS
        class). Returns None rather than guessing if nothing clearly
        marks one — callers fall back to a generic label instead of
        trusting a wrong name.
        """

        for index in range(tab_count):

            try:

                element = tab_locator.nth(index)

                selected = self._safe_attr(
                    element, "aria-selected"
                )

                class_name = self._safe_attr(element, "class") or ""

                if (
                    (selected or "").strip().lower() == "true"
                    or "active" in class_name.lower()
                    or "selected" in class_name.lower()
                ):

                    label = self._safe_text(element)

                    if label:

                        return label.strip()

            except Exception:

                continue

        return None

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
            "id": self._safe_attr(
                element,
                "id",
            ),
            "name": self._safe_attr(
                element,
                "name",
            ),
            "type": self._safe_attr(
                element,
                "type",
            ),
            "data_testid": self._safe_attr(
                element,
                "data-testid",
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

                if locator.count() == 0:
                    continue

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
                element.get_attribute(
                    name
                )
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
        