"""
QA AI Studio
URL Access Analyzer

Version: 1.0

Purpose
-------
Analyze a URL with Playwright and determine whether the target is
publicly accessible or requires authentication.

This module ONLY performs access/authentication analysis.
It does not create the 7-level discovery hierarchy and it does not
store credentials.

Returned authentication types:
    NONE
    LOGIN_FORM
    TOKEN
    API_KEY
    SSO
    UNKNOWN

Security
--------
Credentials are never accepted by this analyzer and are never stored.
The next UI/authentication phase can use the returned field metadata
to collect credentials and create an authenticated Playwright context.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Any, Dict, List, Optional
from urllib.parse import urljoin, urlparse

from Core.logger import Logger


@dataclass
class DetectedField:
    field_type: str
    tag_name: str
    input_type: str
    name: str
    element_id: str
    placeholder: str
    aria_label: str
    locator: str


@dataclass
class AccessAnalysis:
    success: bool
    requested_url: str
    final_url: str = ""
    http_status: Optional[int] = None
    page_title: str = ""
    access_status: str = "UNKNOWN"
    authentication_required: bool = False
    authentication_type: str = "NONE"
    login_url: str = ""
    redirect_chain: Optional[List[str]] = None
    detected_fields: Optional[List[Dict[str, Any]]] = None
    evidence: Optional[List[str]] = None
    login_link_candidates: Optional[List[Dict[str, str]]] = None
    error: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class URLAccessAnalyzer:

    DEFAULT_TIMEOUT_MS = 30_000
    DEFAULT_NAVIGATION_TIMEOUT_MS = 30_000

    LOGIN_WORDS = (
        "login",
        "log in",
        "sign in",
        "signin",
        "authenticate",
        "authentication",
        "account",
        "username",
        "password",
        "credential",
    )

    SSO_WORDS = (
        "single sign-on",
        "single sign on",
        "sso",
        "microsoftonline",
        "okta",
        "auth0",
        "adfs",
        "keycloak",
        "openid",
        "saml",
    )

    TOKEN_WORDS = (
        "token",
        "bearer",
        "access_token",
        "authorization",
        "api key",
        "apikey",
    )

    def __init__(
        self,
        headless: bool = True,
        timeout_ms: int = DEFAULT_TIMEOUT_MS,
        navigation_timeout_ms: int = DEFAULT_NAVIGATION_TIMEOUT_MS,
    ):
        self.logger = Logger.get_logger()
        self.headless = headless
        self.timeout_ms = timeout_ms
        self.navigation_timeout_ms = navigation_timeout_ms

    def analyze(self, url: str) -> Dict[str, Any]:

        requested_url = (url or "").strip()

        validation_error = self._validate_url(requested_url)

        if validation_error:
            return AccessAnalysis(
                success=False,
                requested_url=requested_url,
                access_status="INVALID_URL",
                error=validation_error,
            ).to_dict()

        try:
            from playwright.sync_api import (
                TimeoutError as PlaywrightTimeoutError,
                sync_playwright,
            )
        except ImportError:
            return AccessAnalysis(
                success=False,
                requested_url=requested_url,
                access_status="PLAYWRIGHT_NOT_INSTALLED",
                error=(
                    "Playwright is not installed. Run:\n"
                    "pip install playwright\n"
                    "playwright install chromium"
                ),
            ).to_dict()

        redirect_chain: List[str] = []

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

                page.set_default_timeout(self.timeout_ms)

                page.set_default_navigation_timeout(
                    self.navigation_timeout_ms
                )

                def on_request(request):
                    if request.is_navigation_request() and request.url:
                        if (
                            not redirect_chain
                            or redirect_chain[-1] != request.url
                        ):
                            redirect_chain.append(request.url)

                page.on("request", on_request)

                response = None

                try:
                    response = page.goto(
                        requested_url,
                        wait_until="domcontentloaded",
                        timeout=self.navigation_timeout_ms,
                    )

                except PlaywrightTimeoutError:

                    self.logger.warning(
                        f"URL access navigation timed out: {requested_url}"
                    )

                try:
                    page.wait_for_load_state(
                        "domcontentloaded",
                        timeout=5_000,
                    )
                except Exception:
                    pass

                try:
                    page.wait_for_timeout(1_000)
                except Exception:
                    pass

                final_url = page.url or requested_url

                title = self._safe_title(page)

                fields = self._detect_authentication_fields(page)

                page_text = self._safe_page_text(page)

                html = self._safe_content(page)

                auth_type, evidence = self._classify_authentication(
                    requested_url=requested_url,
                    final_url=final_url,
                    title=title,
                    page_text=page_text,
                    html=html,
                    fields=fields,
                )

                login_url = final_url if auth_type != "NONE" else ""

                http_status = None

                if response is not None:
                    try:
                        http_status = response.status
                    except Exception:
                        http_status = None

                access_status = self._determine_access_status(
                    auth_type=auth_type,
                    http_status=http_status,
                    final_url=final_url,
                    requested_url=requested_url,
                    fields=fields,
                )

                # Only worth looking for a login link when this page
                # itself wasn't classified as the login gate — if it
                # already IS one (LOGIN_FORM/SSO/TOKEN), login_url
                # above is the authoritative answer.
                login_link_candidates = (
                    self._find_login_link_candidates(
                        page, final_url
                    )
                    if auth_type == "NONE"
                    else []
                )

                if auth_type == "NONE" and not login_link_candidates:

                    click_result = self._probe_login_via_click(
                        page, final_url
                    )

                    if click_result:

                        login_link_candidates = [click_result]

                result = AccessAnalysis(
                    success=True,
                    requested_url=requested_url,
                    final_url=final_url,
                    http_status=http_status,
                    page_title=title,
                    access_status=access_status,
                    authentication_required=auth_type != "NONE",
                    authentication_type=auth_type,
                    login_url=login_url,
                    redirect_chain=redirect_chain,
                    detected_fields=[
                        asdict(field) for field in fields
                    ],
                    evidence=evidence,
                    login_link_candidates=login_link_candidates,
                )

                context.close()
                browser.close()

                self.logger.info(
                    "URL Access Analysis completed | "
                    f"URL={requested_url} | "
                    f"Status={access_status} | "
                    f"Auth={auth_type}"
                )

                return result.to_dict()

        except Exception as ex:

            self.logger.exception(
                f"URL Access Analysis failed: {requested_url}"
            )

            return AccessAnalysis(
                success=False,
                requested_url=requested_url,
                final_url=(
                    redirect_chain[-1]
                    if redirect_chain
                    else ""
                ),
                access_status="ACCESS_FAILED",
                redirect_chain=redirect_chain,
                error=str(ex),
            ).to_dict()

    @staticmethod
    def _validate_url(url: str) -> str:

        if not url:
            return "URL is required."

        try:
            parsed = urlparse(url)
        except Exception:
            return "Invalid URL."

        if parsed.scheme not in ("http", "https"):
            return "Only HTTP and HTTPS URLs are supported."

        if not parsed.netloc:
            return "URL must contain a valid host."

        return ""

    @staticmethod
    def _safe_title(page) -> str:

        try:
            return (page.title() or "").strip()
        except Exception:
            return ""

    @staticmethod
    def _safe_page_text(page) -> str:

        try:
            return (
                page.locator("body")
                .inner_text(timeout=5_000)
                .strip()
            )
        except Exception:
            return ""

    @staticmethod
    def _safe_content(page) -> str:

        try:
            return page.content()
        except Exception:
            return ""

    def _detect_authentication_fields(
        self,
        page,
    ) -> List[DetectedField]:

        fields: List[DetectedField] = []

        try:

            locator = page.locator(
                "input, textarea, button, "
                "[role='button'], "
                "[contenteditable='true']"
            )

            count = min(locator.count(), 100)

            for index in range(count):

                element = locator.nth(index)

                try:
                    tag_name = (
                        element.evaluate(
                            "(el) => el.tagName.toLowerCase()"
                        )
                        or ""
                    )
                except Exception:
                    tag_name = ""

                try:
                    input_type = (
                        element.get_attribute("type") or ""
                    ).lower()
                except Exception:
                    input_type = ""

                try:
                    name = (
                        element.get_attribute("name") or ""
                    ).strip()
                except Exception:
                    name = ""

                try:
                    element_id = (
                        element.get_attribute("id") or ""
                    ).strip()
                except Exception:
                    element_id = ""

                try:
                    placeholder = (
                        element.get_attribute("placeholder") or ""
                    ).strip()
                except Exception:
                    placeholder = ""

                try:
                    aria_label = (
                        element.get_attribute("aria-label") or ""
                    ).strip()
                except Exception:
                    aria_label = ""

                label_text = self._safe_label_text(
                    page,
                    element
                )

                combined = " ".join(
                    [
                        input_type,
                        name,
                        element_id,
                        placeholder,
                        aria_label,
                        label_text,
                    ]
                ).lower()

                field_type = self._infer_field_type(
                    tag_name=tag_name,
                    input_type=input_type,
                    combined=combined,
                )

                if not field_type:
                    continue

                locator_text = self._build_locator(
                    element_id=element_id,
                    name=name,
                    placeholder=placeholder,
                    input_type=input_type,
                )

                fields.append(
                    DetectedField(
                        field_type=field_type,
                        tag_name=tag_name,
                        input_type=input_type,
                        name=name,
                        element_id=element_id,
                        placeholder=placeholder,
                        aria_label=aria_label,
                        locator=locator_text,
                    )
                )

        except Exception as ex:

            self.logger.warning(
                f"Authentication field inspection warning: {ex}"
            )

        return self._deduplicate_fields(fields)

    def _find_login_link_candidates(
        self,
        page,
        base_url,
        max_candidates: int = 5,
    ) -> List[Dict[str, str]]:
        """
        When a page has no login form of its own but is plausibly a
        public landing/portal page, real login access is often one
        click away — a "Login" / "Sign In" link elsewhere on the
        page. This looks for exactly that, so the person doesn't
        have to go hunting for the real login URL by hand.

        Deliberately conservative: only matches an <a> whose visible
        text OR href contains an explicit login-shaped word. Returns
        candidates for a human (or a follow-up analysis call) to
        choose from — it does not navigate to any of them itself.
        """

        candidates: List[Dict[str, str]] = []

        seen_urls = set()

        try:

            links = page.locator("a[href]")

            count = min(links.count(), 300)

            for index in range(count):

                element = links.nth(index)

                try:

                    href = element.get_attribute("href") or ""

                    text = (element.inner_text() or "").strip()

                except Exception:

                    continue

                if not href:

                    continue

                signature = f"{text} {href}".lower()

                if not any(
                    word in signature
                    for word in self.LOGIN_WORDS + self.SSO_WORDS
                ):

                    continue

                absolute_url = urljoin(base_url, href)

                if absolute_url in seen_urls:

                    continue

                seen_urls.add(absolute_url)

                candidates.append(
                    {
                        "method": "link",
                        "text": text[:120],
                        "url": absolute_url,
                    }
                )

                if len(candidates) >= max_candidates:

                    break

        except Exception as ex:

            self.logger.warning(
                f"Login link scan warning: {ex}"
            )

        return candidates

    # A login-shaped control is safe to click for discovery purposes
    # (it's navigation, same as clicking a tab or a menu item) — but
    # this is a second guard, same principle used for tab-walking
    # elsewhere in this app: never click anything whose own label
    # suggests a mutating action, even if it happened to also match
    # a login word somehow.
    _PROBE_MUTATING_WORDS = (
        "submit", "save", "delete", "remove", "confirm",
        "create", "approve", "reject", "pay", "checkout",
        "send", "post", "publish", "logout", "log out", "sign out",
    )

    def _probe_login_via_click(
        self,
        page,
        base_url,
    ) -> Optional[Dict[str, Any]]:
        """
        Fallback for when no plain <a href> login link was found —
        common on SPA-style portals where "Login" is a <button> or a
        JS-driven element with no real href, so the passive scan in
        _find_login_link_candidates has nothing to go on.

        Clicks the first visible, enabled, login-worded control it
        finds (link, button, or button-role element) and reports
        where that leads, plus whether real login fields appeared
        there. Only ever attempts one click, and only ever a
        navigation-shaped one — never anything matching
        _PROBE_MUTATING_WORDS.

        Returns a candidate dict shaped like the passive scan's
        (with "method": "click"), or None if nothing usable was
        found or the click didn't lead anywhere new.
        """

        try:

            controls = page.locator(
                "a, button, [role='button'], "
                "input[type='submit'], input[type='button']"
            )

            count = min(controls.count(), 100)

            for index in range(count):

                element = controls.nth(index)

                try:

                    text = (element.inner_text() or "").strip()

                except Exception:

                    text = ""

                if not text:

                    continue

                lowered = text.lower()

                if not any(
                    word in lowered
                    for word in self.LOGIN_WORDS + self.SSO_WORDS
                ):

                    continue

                if any(
                    word in lowered
                    for word in self._PROBE_MUTATING_WORDS
                ):

                    continue

                try:

                    if not element.is_visible():

                        continue

                    if not element.is_enabled():

                        continue

                except Exception:

                    continue

                url_before_click = page.url

                try:

                    element.click(timeout=5_000)

                except Exception as ex:

                    self.logger.warning(
                        f"Login control click warning: {ex}"
                    )

                    continue

                try:

                    page.wait_for_load_state(
                        "domcontentloaded", timeout=8_000
                    )

                except Exception:

                    pass

                try:

                    page.wait_for_timeout(1_500)

                except Exception:

                    pass

                resulting_url = page.url

                if resulting_url == url_before_click:

                    # Click did nothing observable (e.g. opened a
                    # modal rather than navigating) — not useful as
                    # a "go here" URL, so don't report it as one.
                    continue

                fields_found = self._detect_authentication_fields(
                    page
                )

                return {
                    "method": "click",
                    "text": text[:120],
                    "url": resulting_url,
                    "fields_found_there": len(fields_found),
                }

        except Exception as ex:

            self.logger.warning(
                f"Login click-probe warning: {ex}"
            )

        return None

    @staticmethod
    def _safe_label_text(page, element) -> str:

        try:

            element_id = element.get_attribute("id")

            if element_id:

                label = page.locator(
                    f"label[for='{element_id}']"
                ).first

                if label.count():

                    return (
                        label.inner_text(timeout=1_000)
                        or ""
                    ).strip()

        except Exception:
            pass

        try:

            return (
                element.evaluate(
                    """
                    (el) => {
                        const p = el.parentElement;
                        if (!p) return "";
                        return (p.innerText || "").slice(0, 200);
                    }
                    """
                )
                or ""
            ).strip()

        except Exception:
            return ""

    @staticmethod
    def _infer_field_type(
        tag_name: str,
        input_type: str,
        combined: str,
    ) -> str:

        if input_type == "password":
            return "PASSWORD"

        if input_type in ("email",):
            return "LOGIN_ID"

        login_patterns = (
            r"\busername\b",
            r"\buser\s*name\b",
            r"\blogin\b",
            r"\blogin\s*id\b",
            r"\buserid\b",
            r"\buser\s*id\b",
            r"\bemail\b",
        )

        token_patterns = (
            r"\btoken\b",
            r"\bbearer\b",
            r"\baccess[_\s-]?token\b",
            r"\bapi[_\s-]?key\b",
            r"\bapikey\b",
            r"\bauthorization\b",
        )

        if any(
            re.search(pattern, combined)
            for pattern in token_patterns
        ):
            return "TOKEN"

        if any(
            re.search(pattern, combined)
            for pattern in login_patterns
        ):
            return "LOGIN_ID"

        if tag_name in ("button",):

            if any(
                word in combined
                for word in (
                    "login",
                    "log in",
                    "sign in",
                    "signin",
                    "authenticate",
                    "submit",
                )
            ):
                return "LOGIN_SUBMIT"

        if input_type in ("submit", "button"):

            if any(
                word in combined
                for word in (
                    "login",
                    "log in",
                    "sign in",
                    "signin",
                    "authenticate",
                )
            ):
                return "LOGIN_SUBMIT"

        return ""

    @staticmethod
    def _build_locator(
        element_id: str,
        name: str,
        placeholder: str,
        input_type: str,
    ) -> str:

        if element_id:
            return f"#{element_id}"

        if name:
            return f"[name='{name}']"

        if placeholder:
            return f"[placeholder='{placeholder}']"

        if input_type:
            return f"input[type='{input_type}']"

        return ""

    @staticmethod
    def _deduplicate_fields(
        fields: List[DetectedField],
    ) -> List[DetectedField]:

        seen = set()

        result = []

        for field in fields:

            key = (
                field.field_type,
                field.element_id,
                field.name,
                field.placeholder,
                field.locator,
            )

            if key in seen:
                continue

            seen.add(key)

            result.append(field)

        return result

    def _classify_authentication(
        self,
        requested_url: str,
        final_url: str,
        title: str,
        page_text: str,
        html: str,
        fields: List[DetectedField],
    ):
        evidence: List[str] = []

        requested = urlparse(requested_url)
        final = urlparse(final_url)

        requested_host = requested.netloc.lower()
        final_host = final.netloc.lower()

        text = " ".join(
            [
                title,
                page_text[:20_000],
                html[:50_000],
                final_url,
            ]
        ).lower()

        has_password = any(
            field.field_type == "PASSWORD"
            for field in fields
        )

        has_login_id = any(
            field.field_type == "LOGIN_ID"
            for field in fields
        )

        has_token = any(
            field.field_type == "TOKEN"
            for field in fields
        )

        has_login_submit = any(
            field.field_type == "LOGIN_SUBMIT"
            for field in fields
        )

        sso_detected = any(
            word in text
            for word in self.SSO_WORDS
        )

        login_words_detected = any(
            word in text
            for word in self.LOGIN_WORDS
        )

        token_words_detected = any(
            word in text
            for word in self.TOKEN_WORDS
        )

        redirected_to_other_host = (
            bool(requested_host)
            and bool(final_host)
            and requested_host != final_host
        )

        # ---------------------------------------------------------
        # Redirect evidence
        # ---------------------------------------------------------

        if redirected_to_other_host:

            evidence.append(
                f"Navigation redirected from "
                f"{requested_host} to {final_host}."
            )

        # ---------------------------------------------------------
        # Field evidence
        # ---------------------------------------------------------

        if has_password:
            evidence.append(
                "Password input detected."
            )

        if has_login_id:
            evidence.append(
                "Login ID / username input detected."
            )

        if has_login_submit:
            evidence.append(
                "Authentication submit control detected."
            )

        if has_token or token_words_detected:
            evidence.append(
                "Token/API-key authentication evidence detected."
            )

        # ---------------------------------------------------------
        # SSO evidence
        # ---------------------------------------------------------

        if sso_detected:
            evidence.append(
                "SSO authentication indicators detected."
            )

        # ---------------------------------------------------------
        # Strong login form
        # ---------------------------------------------------------

        if has_password and (
            has_login_id or has_login_submit
        ):

            return "LOGIN_FORM", evidence

        # ---------------------------------------------------------
        # Strong SSO detection
        #
        # sso_detected alone is a bare substring match against raw
        # page text/HTML — "sso" can appear in an asset URL, a CSS
        # class, a JS variable, or an unrelated word, with zero
        # relationship to an actual auth gate on THIS page. It is
        # corroborating evidence, not sufficient on its own. Only a
        # real redirect to a different host, an auth-flavored final
        # URL, or an actual detected auth-related field can confirm
        # SSO by itself; bare text needs one of those alongside it.
        # ---------------------------------------------------------

        sso_url_indicators = (
            "sso",
            "login",
            "signin",
            "sign-in",
            "auth",
            "oauth",
            "authorize",
            "openid",
            "saml",
            "adfs",
            "identity",
            "account",
        )

        final_url_has_auth_indicator = any(
            indicator in final_url.lower()
            for indicator in sso_url_indicators
        )

        has_any_auth_field = (
            has_password
            or has_login_id
            or has_token
            or has_login_submit
        )

        strong_sso_signal = (
            redirected_to_other_host
            or final_url_has_auth_indicator
        )

        if strong_sso_signal or (
            sso_detected and has_any_auth_field
        ):

            evidence.append(
                "Authentication/SSO flow detected "
                "from navigation or page indicators."
            )

            return "SSO", evidence

        if sso_detected and not has_any_auth_field:

            evidence.append(
                "The word 'SSO' or similar was found on the page, "
                "but no redirect, auth-flavored URL, or actual "
                "login field was detected — not enough evidence to "
                "confirm an SSO gate on this page."
            )

        # ---------------------------------------------------------
        # Token
        # ---------------------------------------------------------

        if has_token or token_words_detected:

            return "TOKEN", evidence

        # ---------------------------------------------------------
        # Login language without visible fields
        # ---------------------------------------------------------

        if login_words_detected and (
            has_login_submit
            or final_url_has_auth_indicator
        ):

            evidence.append(
                "Login indicators detected but "
                "credentials fields were not directly visible."
            )

            return "SSO", evidence

        return "NONE", evidence

    @staticmethod
    def _determine_access_status(
        auth_type: str,
        http_status: Optional[int],
        final_url: str,
        requested_url: str,
        fields: List[DetectedField],
    ) -> str:

        if http_status in (401, 403):
            return "AUTH_REQUIRED"

        if auth_type != "NONE":
            return "AUTH_REQUIRED"

        if http_status is not None and 200 <= http_status < 400:
            return "PUBLIC"

        if final_url:
            return "PUBLIC"

        return "ACCESS_FAILED"

# Backwards-friendly alias for future service wiring.
AccessAnalyzer = URLAccessAnalyzer