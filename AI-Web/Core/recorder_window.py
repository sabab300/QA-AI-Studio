# Create: AI-Web/Core/recorder_window.py

"""
QA AI Studio — recorder window visibility, diagnostics and focus control.

Why this exists
---------------
The Web recorder opens a REAL, headed Chromium window on the machine the QA AI Studio
*server* runs on (see Core/web_recorder.py).  A headed window can fail to be seen for
reasons that have nothing to do with Playwright:

  * the server is not running in the signed-in user's interactive desktop session
    (Windows service / scheduled task / SSH or other non-interactive session, or a Linux
    host without a display) — the window is created somewhere the user can never see;
  * the window is created but stays behind other windows (Windows' foreground-lock rule
    stops a background process from taking focus), minimised, or off every monitor;
  * the browser is not installed / cannot start.

This module makes each of those cases observable and recoverable from the QA AI Studio
interface instead of a silent "nothing happened":

  * `environment_report()` — no browser launched; explains whether a headed window CAN be
    shown to the user and what to change if not;
  * `RecorderWindowController` — locates the browser window(s) the recorder just started,
    reports whether each is visible / minimised / off-screen, and brings them to the front
    (also callable on demand from the Record dialog's "Show recorder window" button);
  * `run_launch_probe()` — opens a clearly-labelled test window, reports what the OS says
    about it, and closes it, so a person can confirm "I can see the window" BEFORE a real
    recording.

Everything here is generic: it inspects operating-system state only, never page content,
never any application-specific name, selector or URL.  OS-level window control is
implemented for Windows (ctypes, no third-party package); on other platforms the checks
degrade to "not verifiable" and rely on the browser's own `bring_to_front()`.  Nothing in
this module can make a recording fail: every OS call is guarded and reported, not raised.
"""

import ctypes
import logging
import os
import sys
import time

logger = logging.getLogger("QA_AI_STUDIO")

_BROWSER_EXE_TOKENS = ("chrome", "chromium", "headless_shell", "msedge")
_BROWSER_WINDOW_CLASSES = ("Chrome_WidgetWin_1", "Chrome_WidgetWin_0")
_MIN_VISIBLE_SIDE = 80  # px: smaller than this is a helper/hidden window, not a usable browser window


# ---------------------------------------------------------------------------
# Pure logic (platform independent, unit-testable with fake data)
# ---------------------------------------------------------------------------

def rect_overlap_area(rect, bounds):
    """Pixel area of `rect` that lies inside `bounds`; both are (left, top, right, bottom)."""
    left, top = max(rect[0], bounds[0]), max(rect[1], bounds[1])
    right, bottom = min(rect[2], bounds[2]), min(rect[3], bounds[3])
    return max(0, right - left) * max(0, bottom - top)


def classify_window(window, virtual_screen):
    """
    One OS window -> 'visible' | 'minimized' | 'hidden' | 'off_screen' | 'tiny'.
    `window` carries visible/minimized/rect; `virtual_screen` is the union of all monitors
    (left, top, right, bottom) or None when unknown.
    """
    if not window.get("visible"):
        return "hidden"
    if window.get("minimized"):
        return "minimized"
    rect = window.get("rect") or (0, 0, 0, 0)
    width, height = rect[2] - rect[0], rect[3] - rect[1]
    if width < _MIN_VISIBLE_SIDE or height < _MIN_VISIBLE_SIDE:
        return "tiny"
    if virtual_screen and rect_overlap_area(rect, virtual_screen) < (width * height) * 0.25:
        return "off_screen"
    return "visible"


_STATE_TEXT = {
    "visible": ("The recorder window is open and on screen.", ""),
    "minimized": ("The recorder window is open but minimised.",
                  "Click 'Show recorder window' in this panel, or restore it from the taskbar."),
    "hidden": ("The recorder window exists but is hidden.",
               "Click 'Show recorder window' in this panel."),
    "off_screen": ("The recorder window is open but outside every connected monitor.",
                   "Click 'Show recorder window' in this panel; if it stays off-screen, reconnect the monitor it was last on."),
    "tiny": ("The recorder window exists but is too small to use.",
             "Click 'Show recorder window' in this panel."),
}


def summarize_windows(windows, virtual_screen=None):
    """
    Collapse the browser windows found into one verdict.
    status: 'visible' | 'minimized' | 'hidden' | 'off_screen' | 'tiny' | 'not_found'
    """
    if not windows:
        return {
            "status": "not_found",
            "message": "No browser window belonging to the recorder was found on this machine's desktop.",
            "remedy": ("If QA AI Studio was started as a Windows service, scheduled task or from a remote shell, its windows "
                       "open in a session you cannot see: stop it and start it again from a normal terminal opened on the "
                       "desktop you are signed in to (python run_web.py), then retry. Otherwise check the taskbar and any "
                       "other monitor, and whether security software is blocking the browser."),
            "windows": [],
        }
    rated = []
    for window in windows:
        item = dict(window)
        item["state"] = classify_window(window, virtual_screen)
        rated.append(item)
    order = ("visible", "minimized", "off_screen", "tiny", "hidden")
    best = min((w["state"] for w in rated), key=order.index)
    message, remedy = _STATE_TEXT[best]
    return {"status": best, "message": message, "remedy": remedy, "windows": rated}


def evaluate_environment(info):
    """
    `info` (all optional): platform, interactive (bool|None), window_station, session_id,
    display (bool|None), browser_installed (bool|None), recording_active (bool).
    Returns {"ok": bool, "checks": [{id, status, title, message, remedy}]} where status is
    'ok' | 'warn' | 'fail'.
    """
    checks = []

    def add(check_id, status, title, message, remedy=""):
        checks.append({"id": check_id, "status": status, "title": title, "message": message, "remedy": remedy})

    platform = info.get("platform") or sys.platform
    interactive = info.get("interactive")
    if platform == "win32":
        if interactive is False:
            add("desktop_session", "fail", "Interactive desktop session",
                f"QA AI Studio is running in a non-interactive Windows session "
                f"(session {info.get('session_id')}, window station '{info.get('window_station')}'). "
                "A browser window opened from here is never shown on your screen.",
                "Stop the server and start it from a normal Command Prompt / PowerShell opened on the desktop you are "
                "signed in to (python run_web.py). Do not run it as a Windows service, scheduled task or over SSH.")
        elif interactive is True:
            add("desktop_session", "ok", "Interactive desktop session",
                f"QA AI Studio is running in your interactive desktop session (session {info.get('session_id')}).")
        else:
            add("desktop_session", "warn", "Interactive desktop session",
                "Could not determine which Windows session the server runs in.",
                "Start the server from a terminal opened on the desktop you are signed in to.")
    elif platform.startswith("linux"):
        if info.get("display") is False:
            add("desktop_session", "fail", "Display available",
                "This server has no graphical display (DISPLAY / WAYLAND_DISPLAY is not set), so a headed browser "
                "window cannot be shown.",
                "Run QA AI Studio on the machine you are sitting at, or on a host with a desktop session.")
        else:
            add("desktop_session", "ok", "Display available", "A graphical display is available to the server.")
    else:
        add("desktop_session", "ok", "Desktop session", "Interactive session assumed on this platform.")

    installed = info.get("browser_installed")
    if installed is False:
        add("browser", "fail", "Recorder browser installed",
            "The Playwright Chromium browser is not installed for this QA AI Studio environment.",
            "Run: python -m playwright install chromium")
    elif installed is True:
        add("browser", "ok", "Recorder browser installed", "The Playwright Chromium browser is installed.")
    else:
        add("browser", "warn", "Recorder browser installed", "Could not check whether Chromium is installed.")

    if info.get("recording_active"):
        add("recording_slot", "warn", "No other recording running",
            "A recording is already in progress on this server; only one can run at a time.",
            "Finish or cancel the other recording first.")
    else:
        add("recording_slot", "ok", "No other recording running", "The recorder is free.")

    return {"ok": not any(c["status"] == "fail" for c in checks), "checks": checks}


# ---------------------------------------------------------------------------
# Windows backend (ctypes only; libraries are injectable so the logic can be tested anywhere)
# ---------------------------------------------------------------------------

class _PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [
        ("dwSize", ctypes.c_ulong),
        ("cntUsage", ctypes.c_ulong),
        ("th32ProcessID", ctypes.c_ulong),
        ("th32DefaultHeapID", ctypes.c_size_t),
        ("th32ModuleID", ctypes.c_ulong),
        ("cntThreads", ctypes.c_ulong),
        ("th32ParentProcessID", ctypes.c_ulong),
        ("pcPriClassBase", ctypes.c_long),
        ("dwFlags", ctypes.c_ulong),
        ("szExeFile", ctypes.c_wchar * 260),
    ]


class _RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long), ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


class NullBackend:
    """Platforms without OS-level window inspection: everything reports 'not verifiable'."""

    supported = False
    name = "none"

    def session_info(self):
        return {"interactive": None, "session_id": None, "window_station": None}

    def browser_pids(self):
        return set()

    def all_browser_pids(self):
        return set()

    def windows_for_pids(self, pids):
        return []

    def virtual_screen(self):
        return None

    def bring_to_front(self, hwnd):
        return {"foreground": False}


class Win32Backend:
    supported = True
    name = "win32"

    SW_SHOW, SW_RESTORE = 5, 9
    SM_XVIRTUALSCREEN, SM_YVIRTUALSCREEN, SM_CXVIRTUALSCREEN, SM_CYVIRTUALSCREEN = 76, 77, 78, 79

    def __init__(self, user32=None, kernel32=None):
        windll = getattr(ctypes, "windll", None)
        self.user32 = user32 if user32 is not None else (windll.user32 if windll else None)
        self.kernel32 = kernel32 if kernel32 is not None else (windll.kernel32 if windll else None)
        if self.user32 is None or self.kernel32 is None:
            raise OSError("Win32 API is not available on this platform.")
        self._declare_signatures()

    def _declare_signatures(self):
        """64-bit safe prototypes (handles must not be truncated to C int)."""
        vp, ul = ctypes.c_void_p, ctypes.c_ulong
        declarations = [
            (self.user32, "GetForegroundWindow", None, vp), (self.user32, "GetProcessWindowStation", None, vp),
            (self.user32, "IsIconic", [vp], None), (self.user32, "IsWindowVisible", [vp], None),
            (self.user32, "IsZoomed", [vp], None), (self.user32, "ShowWindow", [vp, ctypes.c_int], None),
            (self.user32, "SetForegroundWindow", [vp], None),
            (self.user32, "GetWindowThreadProcessId", [vp, vp], ul),
            (self.user32, "GetClassNameW", [vp, vp, ctypes.c_int], None),
            (self.user32, "GetWindowTextW", [vp, vp, ctypes.c_int], None),
            (self.user32, "GetWindowRect", [vp, vp], None),
            (self.user32, "SetWindowPos", [vp, vp, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_uint], None),
            (self.user32, "GetUserObjectInformationW", [vp, ctypes.c_int, vp, ul, vp], None),
            (self.kernel32, "CreateToolhelp32Snapshot", [ul, ul], vp),
            (self.kernel32, "Process32FirstW", [vp, vp], None), (self.kernel32, "Process32NextW", [vp, vp], None),
            (self.kernel32, "CloseHandle", [vp], None),
            (self.kernel32, "ProcessIdToSessionId", [ul, vp], None),
        ]
        for library, name, argtypes, restype in declarations:
            try:
                function = getattr(library, name)
                if argtypes is not None:
                    function.argtypes = argtypes
                if restype is not None:
                    function.restype = restype
            except Exception:
                logger.debug("[recorder-window] could not declare %s", name, exc_info=True)

    # -- session ---------------------------------------------------------
    def session_info(self):
        info = {"interactive": None, "session_id": None, "window_station": None}
        try:
            session = ctypes.c_ulong(0)
            if self.kernel32.ProcessIdToSessionId(os.getpid(), ctypes.byref(session)):
                info["session_id"] = int(session.value)
        except Exception:
            logger.debug("[recorder-window] ProcessIdToSessionId failed", exc_info=True)
        try:
            station = self.user32.GetProcessWindowStation()
            buffer = ctypes.create_unicode_buffer(256)
            needed = ctypes.c_ulong(0)
            if station and self.user32.GetUserObjectInformationW(station, 2, buffer, ctypes.sizeof(buffer), ctypes.byref(needed)):
                info["window_station"] = buffer.value
        except Exception:
            logger.debug("[recorder-window] GetUserObjectInformationW failed", exc_info=True)
        # "WinSta0" is the only window station that is attached to a physical/remote desktop;
        # services (session 0) run on a private, invisible one.
        if info["window_station"] is not None:
            info["interactive"] = str(info["window_station"]).lower() == "winsta0" and info["session_id"] != 0
        elif info["session_id"] is not None:
            info["interactive"] = info["session_id"] != 0
        return info

    # -- processes -------------------------------------------------------
    def _process_table(self):
        snapshot = self.kernel32.CreateToolhelp32Snapshot(0x2, 0)
        rows = []
        if not snapshot or snapshot == ctypes.c_void_p(-1).value or snapshot == -1:
            return rows
        try:
            entry = _PROCESSENTRY32W()
            entry.dwSize = ctypes.sizeof(entry)
            ok = self.kernel32.Process32FirstW(snapshot, ctypes.byref(entry))
            while ok:
                rows.append((int(entry.th32ProcessID), int(entry.th32ParentProcessID), entry.szExeFile))
                ok = self.kernel32.Process32NextW(snapshot, ctypes.byref(entry))
        finally:
            self.kernel32.CloseHandle(snapshot)
        return rows

    def all_browser_pids(self):
        """PIDs of every browser process on the machine (fallback when the process tree is not visible)."""
        return {pid for pid, _parent, exe in self._process_table()
                if any(token in (exe or "").lower() for token in _BROWSER_EXE_TOKENS)}

    def browser_pids(self):
        """PIDs of browser processes that descend from this server process."""
        rows = self._process_table()
        children = {}
        for pid, parent, _exe in rows:
            children.setdefault(parent, []).append(pid)
        names = {pid: (exe or "").lower() for pid, _parent, exe in rows}
        found, stack, seen = set(), [os.getpid()], set()
        while stack:
            current = stack.pop()
            if current in seen:
                continue
            seen.add(current)
            for child in children.get(current, []):
                stack.append(child)
                if any(token in names.get(child, "") for token in _BROWSER_EXE_TOKENS):
                    found.add(child)
        return found

    # -- windows ---------------------------------------------------------
    def virtual_screen(self):
        try:
            left = self.user32.GetSystemMetrics(self.SM_XVIRTUALSCREEN)
            top = self.user32.GetSystemMetrics(self.SM_YVIRTUALSCREEN)
            width = self.user32.GetSystemMetrics(self.SM_CXVIRTUALSCREEN)
            height = self.user32.GetSystemMetrics(self.SM_CYVIRTUALSCREEN)
            if width > 0 and height > 0:
                return (left, top, left + width, top + height)
        except Exception:
            logger.debug("[recorder-window] GetSystemMetrics failed", exc_info=True)
        return None

    def windows_for_pids(self, pids):
        pids = set(pids)
        collected = []
        callback_type = getattr(ctypes, "WINFUNCTYPE", ctypes.CFUNCTYPE)

        @callback_type(ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p)
        def visit(hwnd, _lparam):
            try:
                owner = ctypes.c_ulong(0)
                self.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
                if int(owner.value) not in pids:
                    return 1
                klass = ctypes.create_unicode_buffer(256)
                self.user32.GetClassNameW(hwnd, klass, 256)
                if klass.value not in _BROWSER_WINDOW_CLASSES:
                    return 1
                title = ctypes.create_unicode_buffer(512)
                self.user32.GetWindowTextW(hwnd, title, 512)
                rect = _RECT()
                self.user32.GetWindowRect(hwnd, ctypes.byref(rect))
                collected.append({
                    "hwnd": int(hwnd or 0),
                    "pid": int(owner.value),
                    "title": title.value,
                    "visible": bool(self.user32.IsWindowVisible(hwnd)),
                    "minimized": bool(self.user32.IsIconic(hwnd)),
                    "maximized": bool(self.user32.IsZoomed(hwnd)),
                    "rect": (int(rect.left), int(rect.top), int(rect.right), int(rect.bottom)),
                })
            except Exception:
                logger.debug("[recorder-window] window enumeration entry failed", exc_info=True)
            return 1

        self.user32.EnumWindows(visit, 0)
        # Chromium keeps several invisible helper windows per process: only windows that carry a
        # title (the real top-level browser windows) are meaningful to the user.
        return [w for w in collected if w["title"] or w["visible"]]

    def bring_to_front(self, hwnd):
        """Restore + raise one window.  Windows blocks background processes from stealing
        focus, so use the documented workarounds in order and report what actually happened."""
        result = {"foreground": False, "method": None}
        try:
            handle = ctypes.c_void_p(hwnd)
            if self.user32.IsIconic(handle):
                self.user32.ShowWindow(handle, self.SW_RESTORE)
            else:
                self.user32.ShowWindow(handle, self.SW_SHOW)
            # 1) plain request
            self.user32.SetForegroundWindow(handle)
            if self.user32.GetForegroundWindow() == hwnd:
                return {"foreground": True, "method": "SetForegroundWindow"}
            # 2) simulate an ALT key press: Windows then lets this process take the foreground
            self.user32.keybd_event(0x12, 0, 0, 0)
            self.user32.keybd_event(0x12, 0, 0x0002, 0)
            self.user32.SetForegroundWindow(handle)
            if self.user32.GetForegroundWindow() == hwnd:
                return {"foreground": True, "method": "alt-key"}
            # 3) briefly make it topmost so it sits above everything, then restore normal z-order
            HWND_TOPMOST, HWND_NOTOPMOST = ctypes.c_void_p(-1), ctypes.c_void_p(-2)
            flags = 0x0001 | 0x0002 | 0x0040  # NOSIZE | NOMOVE | SHOWWINDOW
            self.user32.SetWindowPos(handle, HWND_TOPMOST, 0, 0, 0, 0, flags)
            self.user32.SetWindowPos(handle, HWND_NOTOPMOST, 0, 0, 0, 0, flags)
            self.user32.SetForegroundWindow(handle)
            result = {"foreground": self.user32.GetForegroundWindow() == hwnd, "method": "topmost"}
        except Exception:
            logger.debug("[recorder-window] bring_to_front failed", exc_info=True)
            result["error"] = "bring_to_front failed"
        return result


def default_backend():
    if sys.platform == "win32":
        try:
            return Win32Backend()
        except Exception:
            logger.warning("[recorder-window] Win32 API unavailable; window checks disabled", exc_info=True)
    return NullBackend()


# ---------------------------------------------------------------------------
# Controller used by the recorder
# ---------------------------------------------------------------------------

class RecorderWindowController:
    """
    Tracks the browser window(s) started by ONE recorder launch.

    mark_before() must be called immediately before the browser is launched; the windows that
    appear afterwards (browser processes descending from this server that did not exist before)
    belong to the recorder.  Concurrent headless executions never create windows, so they cannot
    be mistaken for it.
    """

    def __init__(self, backend=None):
        self.backend = backend or default_backend()
        self._before = set()
        self._before_all = set()
        self._pids = set()

    def _all_pids(self):
        function = getattr(self.backend, "all_browser_pids", None)
        return set(function()) if function else set()

    def mark_before(self):
        try:
            self._before = set(self.backend.browser_pids())
            self._before_all = self._all_pids()
        except Exception:
            logger.debug("[recorder-window] mark_before failed", exc_info=True)
            self._before = set()
            self._before_all = set()

    def _windows(self):
        pids = set(self.backend.browser_pids()) - self._before
        if not pids and not self._pids:
            # The process tree was not visible (e.g. the browser is not parented to this server):
            # fall back to any browser process that did not exist before the launch.
            pids = self._all_pids() - self._before_all
        self._pids |= pids
        return self.backend.windows_for_pids(self._pids or pids), self.backend.virtual_screen()

    def locate(self, timeout=6.0, interval=0.25):
        """Wait (bounded) for the recorder window to appear and describe it."""
        if not self.backend.supported:
            return {"supported": False, "status": "unknown",
                    "message": "Window position cannot be verified on this platform; the browser reported that it opened.",
                    "remedy": "", "windows": []}
        deadline = time.monotonic() + timeout
        summary = None
        while True:
            try:
                windows, screen = self._windows()
                summary = summarize_windows(windows, screen)
            except Exception as error:
                logger.debug("[recorder-window] locate failed", exc_info=True)
                return {"supported": True, "status": "error", "message": f"Window check failed: {error}",
                        "remedy": "", "windows": []}
            if summary["status"] != "not_found" or time.monotonic() >= deadline:
                break
            time.sleep(interval)
        summary["supported"] = True
        return summary

    def focus(self, timeout=3.0):
        """Locate the window and bring it to the front; report what the OS says afterwards."""
        report = self.locate(timeout=timeout)
        if not report.get("supported") or report["status"] in ("not_found", "error", "unknown"):
            report["focused"] = False
            return report
        target = next((w for w in report["windows"] if w["state"] == report["status"]), report["windows"][0])
        outcome = self.backend.bring_to_front(target["hwnd"])
        after = self.locate(timeout=0.0)
        after["focused"] = bool(outcome.get("foreground"))
        after["focus_method"] = outcome.get("method")
        if after["status"] == "visible" and not after["focused"]:
            after["message"] = ("The recorder window is open and on screen, but Windows did not let it take focus. "
                                "Look for the flashing browser icon on the taskbar and click it.")
        return after


# ---------------------------------------------------------------------------
# Environment report + launch probe
# ---------------------------------------------------------------------------

def _display_available():
    if sys.platform.startswith("linux"):
        return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))
    return None


def chromium_installed():
    """True/False when the Playwright Chromium download can be located, None when it cannot be told."""
    root = os.environ.get("PLAYWRIGHT_BROWSERS_PATH")
    if root == "0":
        return None
    if not root:
        if sys.platform == "win32":
            root = os.path.join(os.environ.get("LOCALAPPDATA", ""), "ms-playwright")
        elif sys.platform == "darwin":
            root = os.path.expanduser("~/Library/Caches/ms-playwright")
        else:
            root = os.path.expanduser("~/.cache/ms-playwright")
    try:
        if not os.path.isdir(root):
            return False
        return any(name.startswith("chromium-") for name in os.listdir(root))
    except OSError:
        return None


def environment_report(backend=None, recording_active=False, browser_installed=None):
    backend = backend or default_backend()
    if browser_installed is None:
        browser_installed = chromium_installed()
    session = backend.session_info()
    info = {
        "platform": "win32" if getattr(backend, "name", "") == "win32" else sys.platform,
        "interactive": session.get("interactive"),
        "session_id": session.get("session_id"),
        "window_station": session.get("window_station"),
        "display": _display_available(),
        "browser_installed": browser_installed,
        "recording_active": recording_active,
    }
    report = evaluate_environment(info)
    report["environment"] = {k: info[k] for k in ("platform", "session_id", "window_station", "display")}
    report["os_window_checks"] = bool(backend.supported)
    return report


PROBE_PAGE = (
    "<!doctype html><html><head><meta charset='utf-8'><title>QA AI Studio - recorder test window</title>"
    "<style>body{font-family:Segoe UI,Arial,sans-serif;background:#10243d;color:#fff;display:flex;"
    "align-items:center;justify-content:center;height:100vh;margin:0;text-align:center}"
    "div{max-width:640px;padding:24px}h1{font-size:32px;margin:0 0 12px}p{font-size:18px;line-height:1.5}</style>"
    "</head><body><div><h1>You can see the recorder window</h1>"
    "<p>This is a test window opened by QA AI Studio. Nothing is being recorded.<br>"
    "Go back to QA AI Studio and confirm, or simply close this window.</p></div></body></html>"
)


async def run_launch_probe(hold_seconds=8.0, backend=None):
    """
    Open a clearly-labelled headed test window with the exact launch options the recorder uses,
    describe what the OS reports about it, hold it open `hold_seconds` (or until the user closes
    it), then close it.  Must run on the recorder's dedicated event loop (see web_recorder).
    Never raises: failures are returned as {"launched": False, "error": ...}.
    """
    import asyncio
    from playwright.async_api import async_playwright

    controller = RecorderWindowController(backend)
    started = time.monotonic()
    report = {"launched": False, "error": None, "window": None, "focus": None}
    playwright = browser = None
    try:
        controller.mark_before()
        playwright = await async_playwright().start()
        browser = await playwright.chromium.launch(headless=False, args=["--start-maximized"])
        context = await browser.new_context(no_viewport=True)
        page = await context.new_page()
        await page.set_content(PROBE_PAGE)
        report["launched"] = True
        try:
            await page.bring_to_front()
        except Exception:
            logger.debug("[recorder-window] page.bring_to_front failed", exc_info=True)
        loop = asyncio.get_running_loop()
        report["window"] = await loop.run_in_executor(None, controller.locate)
        report["focus"] = await loop.run_in_executor(None, controller.focus)
        closed = asyncio.Event()
        browser.on("disconnected", lambda _b=None: closed.set())
        try:
            await asyncio.wait_for(closed.wait(), timeout=hold_seconds)
            report["closed_by_user"] = True
        except asyncio.TimeoutError:
            report["closed_by_user"] = False
    except Exception as error:
        logger.warning("[recorder-window] launch probe failed: %s", error)
        report["error"] = str(error) or error.__class__.__name__
    finally:
        for obj in (browser, playwright):
            try:
                if obj is not None:
                    await (obj.close() if obj is browser else obj.stop())
            except Exception:
                pass
    report["duration_ms"] = int((time.monotonic() - started) * 1000)
    report["verdict"] = probe_verdict(report)
    return report


def probe_verdict(report):
    """Plain-language outcome of a launch probe: {ok, title, message, remedy}."""
    if not report.get("launched"):
        message = report.get("error") or "The browser could not be started."
        remedy = "Run: python -m playwright install chromium" if "executable" in message.lower() else \
            "Check the Runtime Activity / server log for the browser start-up error."
        return {"ok": False, "title": "The recorder browser did not start", "message": message, "remedy": remedy}
    window = report.get("focus") or report.get("window") or {}
    status = window.get("status")
    if status == "visible":
        text = "A test window opened on this machine's desktop."
        if window.get("focused") is False and window.get("supported"):
            return {"ok": True, "title": "Window opened (not in front)",
                    "message": text + " Windows kept another application in front, so look for the browser icon on the taskbar.",
                    "remedy": "During a recording use 'Show recorder window' to bring it forward."}
        return {"ok": True, "title": "Recorder window works", "message": text, "remedy": ""}
    if status == "unknown":
        return {"ok": True, "title": "Window opened",
                "message": "The browser started. This platform cannot report the window position, so confirm you can see it.",
                "remedy": ""}
    if status in ("minimized", "hidden", "off_screen", "tiny"):
        return {"ok": False, "title": "Window opened but is not usable on screen",
                "message": window.get("message", ""), "remedy": window.get("remedy", "")}
    return {"ok": False, "title": "Window not found on your desktop",
            "message": window.get("message") or "The browser started but no window could be found on this desktop.",
            "remedy": window.get("remedy") or ""}
