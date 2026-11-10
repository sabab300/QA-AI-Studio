"""Isolated, read-only browser session for UI/UX inspection (Playwright + Chromium).

Safety properties (each enforced here, not by the caller):
  * one fresh browser context per session: no shared cookies, storage, service workers or downloads;
  * DNS is pinned to the addresses that were safety-checked (``--host-resolver-rules``), everything else is NOTFOUND;
  * every request/redirect is checked against the run's allowed origins; WebSockets are closed;
  * after sign-in the session is read-only: non-GET requests and form submissions are blocked;
  * dialogs are dismissed, pop-ups are closed, downloads are refused;
  * credentials are used only for the run and never written to evidence or logs.
"""
from __future__ import annotations

import base64
import io
import re
import threading
from urllib.parse import urlsplit

from . import dom_probe
from .security import AuthorizedTarget, TargetError, origin_of, request_allowed


class RunCancelled(Exception):
    pass


class BrowserUnavailable(Exception):
    """Playwright or its browser is not installed / cannot start."""


DESTRUCTIVE_LINK = re.compile(r"\b(delete|remove|logout|log out|sign ?out|approve|submit|pay|purchase|cancel|reset|destroy|revoke|disable)\b", re.I)
SAFE_ACTIONS = {"hover", "focus", "click", "press", "wait"}
SAFE_KEYS = {"Tab", "Shift+Tab", "Enter", "Escape", "ArrowDown", "ArrowUp", "ArrowLeft", "ArrowRight", "Space"}


def _launch_args(pins: dict) -> list:
    # IP-literal hosts need a rule too: "MAP * ~NOTFOUND" also covers literals, which is what blocks every unlisted address.
    rules = [f"MAP {host.strip('[]')} {ip}" for host, ip in pins.items()]
    rules.append("MAP * ~NOTFOUND")
    return ["--host-resolver-rules=" + ", ".join(rules), "--disable-quic", "--disable-background-networking", "--no-proxy-server",
            "--disable-gpu", "--force-color-profile=srgb", "--font-render-hinting=none", "--hide-scrollbars"]


def check_browser(timeout_ms=15000) -> tuple[bool, str]:
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            b = p.chromium.launch(headless=True, timeout=timeout_ms)
            b.close()
        return True, "Chromium is available."
    except Exception as error:
        return False, f"The browser could not be started ({type(error).__name__}). Run 'playwright install chromium' on the server."


def render_svg_png(svg_bytes: bytes, max_side=4096) -> bytes:
    """Rasterise sanitised SVG in a JavaScript-disabled, network-less page using <img> (the SVG-as-image sandbox)."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise BrowserUnavailable("Playwright is not installed.") from None
    uri = "data:image/svg+xml;base64," + base64.b64encode(svg_bytes).decode("ascii")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--host-resolver-rules=MAP * ~NOTFOUND", "--disable-gpu"])
        try:
            context = browser.new_context(java_script_enabled=False, viewport={"width": 1024, "height": 768}, service_workers="block")
            context.route("**/*", lambda r: r.continue_() if r.request.url.startswith("data:") else r.abort())
            page = context.new_page()
            page.set_content(f'<html><body style="margin:0;background:#fff"><img id="i" src="{uri}"></body></html>')
            page.wait_for_load_state("load")
            box = page.locator("#i").bounding_box()
            if not box or box["width"] < 1 or box["height"] < 1:
                raise BrowserUnavailable("The SVG has no renderable size.")
            w, h = min(int(box["width"]) + 1, max_side), min(int(box["height"]) + 1, max_side)
            page.set_viewport_size({"width": w, "height": h})
            return page.locator("#i").screenshot(type="png")
        finally:
            browser.close()


class BrowserSession:
    def __init__(self, target: AuthorizedTarget, viewport=(1440, 900), auth=None, headless=True, cancel: threading.Event | None = None,
                 nav_timeout_ms=30000, ignore_https_errors=False, color_scheme="light", gate=None, video_dir=None):
        self.target = target
        # gate(): called at every safe point; it may block while the run is paused and must raise RunCancelled when the run is stopped
        self.gate = gate
        # a screen recording is only made when no credential is ever typed on screen (form sign-in is excluded)
        self.video_dir = video_dir if (video_dir and (auth or {}).get("type") != "form") else None
        self.video_path = None
        self.viewport = (int(viewport[0]), int(viewport[1]))
        self.auth = auth or {"type": "none"}
        self.headless, self.cancel = headless, cancel
        self.nav_timeout_ms = nav_timeout_ms
        self.ignore_https_errors = bool(ignore_https_errors and target.internal)
        self.color_scheme = color_scheme
        self.blocked: list = []
        self.failed: list = []
        self.console_errors: list = []
        self.read_only = True        # read-only from the very first request: a page must not POST while it loads; only the sign-in step lifts this
        self._secrets: list = []
        self._pw = self._browser = self.context = self.page = None

    # ------------------------------------------------------------------ lifecycle
    def __enter__(self):
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            raise BrowserUnavailable("Playwright is not installed on the server.") from None
        self._pw = sync_playwright().start()
        try:
            self._browser = self._pw.chromium.launch(headless=self.headless, timeout=20000, args=_launch_args(self.target.pins))
        except Exception as error:
            self._pw.stop()
            raise BrowserUnavailable(f"The browser could not be started ({type(error).__name__}).") from None
        opts = dict(viewport={"width": self.viewport[0], "height": self.viewport[1]}, device_scale_factor=1, service_workers="block",
                    accept_downloads=False, locale="en-US", timezone_id="UTC", color_scheme=self.color_scheme, reduced_motion="reduce",
                    ignore_https_errors=self.ignore_https_errors, java_script_enabled=True, bypass_csp=False)
        if self.auth.get("type") == "basic":
            opts["http_credentials"] = {"username": self.auth["username"], "password": self.auth["password"], "origin": self.target.origin}
            self._secrets += [self.auth["username"], self.auth["password"]]
        if self.video_dir:
            opts["record_video_dir"] = str(self.video_dir)
            opts["record_video_size"] = {"width": min(self.viewport[0], 1280), "height": min(self.viewport[1], 800)}
        self.context = self._browser.new_context(**opts)
        if not hasattr(self.context, "route_web_socket"):
            self.close()
            raise BrowserUnavailable("Upgrade Playwright to a version that can block WebSockets before running UI/UX tests.")
        self.context.route_web_socket("**/*", lambda ws: ws.close())
        self.context.route("**/*", self._route)
        self.context.add_init_script("document.addEventListener('submit',e=>{if(window.__uiuxAllowSubmit)return;e.preventDefault();e.stopImmediatePropagation();},true);")
        self.context.on("page", self._on_new_page)
        self.page = self.context.new_page()
        self.page.set_default_timeout(self.nav_timeout_ms)
        self.page.on("dialog", lambda d: d.dismiss())
        self.page.on("console", lambda m: self.console_errors.append(m.text[:200]) if m.type == "error" and len(self.console_errors) < 30 else None)
        self.page.on("requestfailed", lambda r: self._request_failed(r))
        if self.auth.get("type") == "cookie":
            self._secrets.append(self.auth.get("value", ""))
            self.context.add_cookies([{"name": self.auth["name"], "value": self.auth["value"], "url": self.target.origin, "httpOnly": True}])
        return self

    def __exit__(self, *exc):
        self.close()
        return False

    def close(self):
        vpath = None
        if self.page is not None and self.video_dir:
            try:
                video = self.page.video
                vpath = str(video.path()) if video is not None else None      # the path is known while recording; the file is complete after the context closes
            except Exception:
                vpath = None
        for closer in (lambda: self.context and self.context.close(), lambda: self._browser and self._browser.close(), lambda: self._pw and self._pw.stop()):
            try:
                closer()
            except Exception:
                pass
        self.context = self._browser = self._pw = self.page = None
        if vpath:
            import os
            try:
                if os.path.exists(vpath) and os.path.getsize(vpath) > 0:
                    self.video_path = vpath
            except OSError:
                self.video_path = None

    @property
    def secrets(self):
        return [s for s in self._secrets if s]

    def check_cancel(self):
        if self.cancel is not None and self.cancel.is_set():
            raise RunCancelled()
        if self.gate is not None:
            self.gate()

    def live_frame(self, quality=55) -> bytes | None:
        """A small JPEG of what the browser shows right now (viewport only). Used for the live view, which is a sequence of
        these frames - not a video stream - and is labelled that way. Never raises: a failed frame is simply skipped."""
        try:
            return self.page.screenshot(type="jpeg", quality=quality, full_page=False, animations="disabled", caret="hide", timeout=4000)
        except Exception:
            return None

    @property
    def current_url(self) -> str:
        try:
            return re.sub(r"[?#].*$", "", self.page.url)
        except Exception:
            return ""

    # ------------------------------------------------------------------ request policy
    def _route(self, route):
        request = route.request
        ok, reason = request_allowed(request.url, self.target.allowed_origins)
        if not ok:
            if len(self.blocked) < 100:
                self.blocked.append({"url": re.sub(r"[?#].*$", "", request.url)[:200], "reason": reason})
            return route.abort("blockedbyclient")
        if self.read_only and request.method.upper() not in ("GET", "HEAD", "OPTIONS"):
            if len(self.blocked) < 100:
                self.blocked.append({"url": re.sub(r"[?#].*$", "", request.url)[:200], "reason": f"{request.method} blocked: UI/UX testing is read-only"})
            return route.abort("blockedbyclient")
        headers = None
        if self.auth.get("type") == "bearer" and origin_of(request.url) == self.target.origin:
            headers = {**request.headers, "authorization": "Bearer " + self.auth["token"]}
            self._secrets.append(self.auth["token"])
        route.continue_(headers=headers) if headers else route.continue_()

    def _request_failed(self, request):
        failure = (request.failure or "")
        if "blockedbyclient" in str(failure).lower() or len(self.failed) >= 50:
            return
        self.failed.append({"url": re.sub(r"[?#].*$", "", request.url)[:200], "error": str(failure)[:100]})

    def _on_new_page(self, page):
        if self.page is not None and page != self.page:
            try:
                page.close()
            except Exception:
                pass

    # ------------------------------------------------------------------ navigation
    def _absolute(self, route: str) -> str:
        route = (route or "/").strip()
        if "://" in route or route.startswith("//") or re.search(r"[\s\x00-\x1f]", route) or "\\" in route:
            raise TargetError("The route must be a path on the authorized application (for example /login).")
        if not route.startswith("/"):
            route = "/" + route
        return self.target.origin + route

    def sign_in(self):
        """Form login before read-only mode starts. Credentials are used here only."""
        a = self.auth
        if a.get("type") != "form":
            return
        self._secrets += [a["password"], a["username"]]
        self.read_only = False       # the sign-in step is the only time a non-GET request is allowed
        try:
            self.page.goto(self._absolute(a.get("login_path", "/login")), wait_until="load", timeout=self.nav_timeout_ms)
            self.page.evaluate("() => { window.__uiuxAllowSubmit = true; }")   # only the sign-in form of this document may submit
            self.page.fill(a["user_selector"], a["username"], timeout=8000)
            self.page.fill(a["pass_selector"], a["password"], timeout=8000)
            with self.page.expect_navigation(wait_until="load", timeout=self.nav_timeout_ms):
                self.page.click(a["submit_selector"], timeout=8000)
        finally:
            self.read_only = True
        if a.get("failure_selector") and self.page.locator(a["failure_selector"]).count():
            raise TargetError("Sign-in failed: the application reported an error. Check the credentials in the authentication settings.")

    def open(self, route: str):
        self.check_cancel()
        url = self._absolute(route)
        try:
            response = self.page.goto(url, wait_until="load", timeout=self.nav_timeout_ms)
        except Exception as error:
            text = str(error)
            if "net::ERR_NAME_NOT_RESOLVED" in text or "ERR_CONNECTION_REFUSED" in text or "ERR_ADDRESS" in text:
                raise TargetError("The application could not be reached (or it redirected outside the authorized origin). Check that it is running and the URL is correct.") from None
            if "Timeout" in text:
                raise TargetError("The page did not finish loading before the timeout.") from None
            raise TargetError(f"The page could not be opened ({type(error).__name__}).") from None
        if origin_of(self.page.url) not in self.target.allowed_origins:
            raise TargetError("The page redirected outside the authorized application and was not tested.")
        self.read_only = True
        status = response.status if response else 0
        return {"status": status, "final_url": re.sub(r"[?#].*$", "", self.page.url)}

    def stabilize(self, quiet_ms=150) -> dict:
        self.check_cancel()
        p = self.page
        p.add_style_tag(content=dom_probe.STABILIZE_CSS)
        try:
            p.wait_for_load_state("networkidle", timeout=6000)
            idle = True
        except Exception:
            idle = False
        info = p.evaluate(dom_probe.SETTLE_JS, quiet_ms)
        p.evaluate(dom_probe.PROBE_JS)
        return {"network_idle": idle, **info}

    # ------------------------------------------------------------------ observations
    def probe(self) -> dict:
        self.check_cancel()
        self.page.evaluate(dom_probe.PROBE_JS)
        return self.page.evaluate("() => window.__uiuxProbe.inspect()")

    def screenshot(self, full_page=True) -> tuple[bytes, int, int]:
        self.check_cancel()
        data = self.page.screenshot(full_page=full_page, type="png", animations="disabled", caret="hide")
        from PIL import Image
        with Image.open(io.BytesIO(data)) as im:
            return data, im.width, im.height

    def clip_screenshot(self, x, y, w, h, pad=6) -> bytes:
        x0, y0 = max(0, x - pad), max(0, y - pad)
        return self.page.screenshot(type="png", animations="disabled", caret="hide", full_page=True,
                                    clip={"x": x0, "y": y0, "width": max(1, w + 2 * pad), "height": max(1, h + 2 * pad)})

    def mask_rects(self, selectors: list) -> list:
        rects = []
        for sel in selectors[:30]:
            rects += self.page.evaluate("s => window.__uiuxProbe.rectsFor(s)", sel)
        return rects

    def element_handle(self, index: int):
        return self.page.evaluate_handle("i => (window.__uiuxEls || [])[i] || null", index).as_element()

    def describe(self, index: int):
        return self.page.evaluate("i => window.__uiuxProbe.describeIndex(i)", index)

    def hover_state(self, index: int) -> dict | None:
        el = self.element_handle(index)
        if not el:
            return None
        el.scroll_into_view_if_needed(timeout=3000)
        el.hover(timeout=3000)
        self.page.wait_for_timeout(60)
        return self.describe(index)

    def focus_state(self, index: int) -> dict | None:
        el = self.element_handle(index)
        if not el:
            return None
        el.focus()
        self.page.keyboard.press("Shift+Tab")
        self.page.keyboard.press("Tab")  # keyboard-initiated focus so :focus-visible applies
        self.page.wait_for_timeout(60)
        return self.describe(index)

    def reset_pointer(self):
        self.page.mouse.move(0, 0)
        try:
            self.page.evaluate("() => document.activeElement && document.activeElement.blur && document.activeElement.blur()")
        except Exception:
            pass

    def tab_walk(self, max_steps=60) -> dict:
        """Press Tab repeatedly and record where focus lands, to verify reachability and a visible focus indicator."""
        self.page.evaluate("() => document.activeElement && document.activeElement.blur && document.activeElement.blur()")
        self.page.evaluate("() => window.scrollTo(0,0)")
        stops, same, last = [], 0, None
        for _ in range(max_steps):
            self.check_cancel()
            self.page.keyboard.press("Tab")
            info = self.page.evaluate("() => window.__uiuxProbe.focusInfo()")
            if not info:
                break
            key = (info["selector"], info["x"], info["y"])
            if key == last:
                same += 1
                if same >= 3:
                    stops.append({**info, "trap": True})
                    break
            else:
                same = 0
            if stops and info["selector"] == stops[0]["selector"]:
                break  # cycled back to the first stop
            stops.append(info)
            last = key
        return {"stops": stops, "steps": len(stops)}

    def run_actions(self, actions: list) -> list:
        """Execute a restricted, declarative action list (mapped interactive states). Anything else is refused."""
        log = []
        for act in (actions or [])[:12]:
            self.check_cancel()
            kind = act.get("action")
            if kind not in SAFE_ACTIONS:
                raise TargetError(f"The state action '{kind}' is not permitted in UI/UX testing.")
            if kind == "wait":
                self.page.wait_for_timeout(min(int(act.get("ms", 200)), 2000))
            elif kind == "press":
                if act.get("key") not in SAFE_KEYS:
                    raise TargetError("That key is not permitted in a state action.")
                self.page.keyboard.press(act["key"])
            else:
                sel = str(act.get("selector", ""))
                loc = self.page.locator(sel).first
                if kind == "click":
                    text = (loc.inner_text(timeout=2000) or "") + " " + (loc.get_attribute("aria-label") or "")
                    typ = (loc.get_attribute("type") or "").lower()
                    if typ == "submit" or DESTRUCTIVE_LINK.search(text) or loc.evaluate("e => !!e.closest('form') && (e.tagName==='BUTTON' && (e.type||'submit')==='submit')"):
                        raise TargetError("Clicking that control could submit or change data and is not permitted in UI/UX testing.")
                    loc.click(timeout=3000)
                elif kind == "hover":
                    loc.hover(timeout=3000)
                elif kind == "focus":
                    loc.focus(timeout=3000)
            log.append({"action": kind, "selector": act.get("selector", ""), "key": act.get("key", "")})
            self.page.wait_for_timeout(80)
        return log

    def safe_links(self, limit=30) -> list:
        links = self.page.evaluate("() => window.__uiuxProbe.links()")
        out, seen = [], set()
        for link in links:
            href = link["href"]
            if not href or href in seen or origin_of(href) not in self.target.allowed_origins:
                continue
            if DESTRUCTIVE_LINK.search(link["text"]) or DESTRUCTIVE_LINK.search(urlsplit(href).path):
                continue
            seen.add(href)
            out.append({"path": urlsplit(href).path or "/", "text": link["text"]})
            if len(out) >= limit:
                break
        return out
