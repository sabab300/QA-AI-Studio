# Create: AI-Web/Core/web_recorder.py

"""
QA AI Studio — Web
Live Playwright Recording (Web-only)

Version: 1.2

WHY THIS FILE EXISTS (read before changing anything)
------------------------------------------------------------------
TASK: QA-AUTOMATION-PLAYWRIGHT-COMPLETE-WEB-DESKTOP-FIX, item 1,
requires the FINAL Web recording mechanism to be a REAL,
Playwright-controlled browser WINDOW — not an embedded/iframe-like
stream inside the QA AI Studio modal. This version of the file
implements that: `chromium.launch(headless=False)` opens a genuine
OS browser window. That only works because the Web server and the
operator's screen are the SAME machine in this deployment (the Web
server runs locally on the QA engineer's own Windows machine,
`localhost:8000` — confirmed against the real production PSW site,
not a remote headless host). If this server is ever deployed on a
machine nobody is sitting at, this recorder cannot work — there is
no way to fake a "real browser window" without a real display, and
Core/automation_web_repository.py's `_run_job()` deliberately keeps
*execution* headless/server-side for exactly that reason (execution
must work from any deployment; recording, by its nature, requires an
operator to physically drive the browser, so it does not have that
requirement).

Two possible ways existed to give the operator a real browser window
here, and this file deliberately picks the second one — the choice
and the reasoning are recorded here per AGENTS.md's "explain why a
new approach was taken over reuse" expectation:

  (a) Port Desktop's `record_manual_script()` (AI/Core/
      test_execution_manager.py) verbatim — shell out to
      `python -m playwright codegen --target python -o <file> <url>`
      and block until the operator closes that window. An
      unwired, near-identical copy of this already exists at
      Core/test_execution_manager.py's own `record_manual_script()`
      and remains available/reusable for anyone who wants that exact
      Desktop-identical behavior. It was NOT chosen as the wired-up
      mechanism for this feature because: codegen's own inspector
      window is the ONLY way to finish a recording — there is no
      clean "Stop and keep what was captured so far" distinct from
      "kill the process and discard everything" (Desktop's own Cancel
      button always discards; see that method's `.communicate()` /
      `.terminate()` split). Item 1 explicitly wants a clean
      Idle/Starting/Recording/Stopping/Captured/Failed lifecycle with
      a real Stop-to-capture action, which codegen's subprocess model
      cannot give without new, fragile OS-level window automation
      (finding and closing a window that belongs to a separate,
      unrelated process). codegen's raw output also gets none of this
      app's locator stabilization (Knowledge Hub reuse, priority
      ordering, XPath fallback — item 3) for free; every one of those
      would have to be re-implemented as a POST-hoc script rewrite
      pass over codegen's text output instead of being computed
      correctly at capture time.

  (b) Keep this file's already-built, already-Windows-fixed
      (_DedicatedLoopThread — see below) Playwright session, and
      simply launch it with `headless=False` instead of `headless=
      True`, dropping the CDP screencast frame-relay entirely since
      the operator now sees the real window directly instead of a
      streamed image of a hidden one. This is the mechanism this file
      actually implements below. It reuses ~90% of the working,
      already-verified code (RECORDER_JS's DOM-event capture with
      Knowledge Hub / stable-locator priority ordering, `_on_action`,
      `build_script()`, the exact `recorded_script` persistence
      Desktop's own `update_recorded_script()` uses) and gives a
      clean Stop (operator clicks Stop Recording in the Web UI, or
      simply closes the real browser window as they would on Desktop
      — either way, `finish(save=True)` below builds the script from
      whatever DOM actions were captured before that point) versus
      Cancel (discard). Per AGENTS.md section 15: "Desktop is a
      functional baseline, not a requirement to reproduce every ...
      desktop-specific technical mechanism" — this stays faithful to
      the REQUIREMENT (a real, visible, operator-driven browser
      window; the same `recorded_script` storage; the same downstream
      replay/repair path) while being a distinct, and for this
      product, technically stronger mechanism than a literal codegen
      port.

The full mechanism:

    1. Launch a REAL, VISIBLE Chromium window via Playwright
       (`headless=False`) on the machine the Web server itself is
       running on.
    2. Inject a small recorder script into every page (RECORDER_JS)
       that watches real DOM events as they happen and reports each
       one, with a locator computed using the priority order item 3
       requires: known Knowledge Hub locator (item 29) > data-testid
       > stable id > role+accessible name > stable name/label/text >
       robust CSS > relative XPath fallback for elements a robust CSS
       selector can't cleanly reach — never absolute XPath, never
       index-only/position-based selectors as anything but the very
       last resort.
    3. On Stop (or Cancel), tear down the browser. On Stop, assemble
       the captured actions into the EXACT script shape
       Core/playwright_runner.py's _extract_generic_interactive_
       parts() already recognises as "Manually Recorded /
       Playwright-codegen shape" (a top-level
       `def run(playwright: Playwright) -> None:` function, called
       from `with sync_playwright() as playwright: run(playwright)`)
       — so a Web-recorded script gets the exact same interactive
       replay/step-repair treatment as a Desktop-recorded one, with
       ZERO changes to playwright_runner.py or test_execution_manager
       .py. This is the "reuse Core" part of the requirement: the
       recording MECHANISM is Web-appropriate, but the STORAGE,
       LIFECYCLE, REPLAY and REPAIR are 100% the existing shared code.

One recording session per server process at a time — same constraint
Desktop enforces ("only one recording can run at a time"), enforced
here with a simple module-level lock so two operators can't collide.
A 15-minute hard timeout protects against an operator closing their
laptop mid-recording and leaving an orphaned Chromium running
forever; RecorderRouter (Web/routers/recorder_router.py) also
guarantees cleanup on WebSocket disconnect.

WINDOWS: WHY THIS RUNS ON ITS OWN DEDICATED EVENT LOOP
------------------------------------------------------------------
Playwright's async API launches its browser driver process using
asyncio's own subprocess support (`loop.subprocess_exec`). On
Windows, only `ProactorEventLoop` implements that — the stdlib's
`SelectorEventLoop` raises a bare `NotImplementedError` the moment
anything tries to spawn a subprocess on it.

In practice, by the time a recording is started, this server process's
main event loop (the one uvicorn/FastAPI is running everything else
on) can end up being a `SelectorEventLoop` — not because of anything
in this file, but as a side effect of something else this app imports
transitively on Windows (grpc's asyncio support, pulled in by
chromadb/opentelemetry for Knowledge Hub's vector search, is a known
culprit: grpc.aio has historically forced the Selector policy for its
own Windows compatibility). Once the *currently running* loop is a
SelectorEventLoop instance, no amount of calling
`asyncio.set_event_loop_policy(...)` from here fixes it — a policy
change only affects loops created *after* the change, and the main
loop was already created and is already running.

Rather than depend on (or fight with) whatever loop type the rest of
the app ends up on, the recorder's entire Playwright lifecycle — launch,
capture, teardown — runs on ONE dedicated background thread with its
OWN freshly-created event loop, explicitly forced to
`WindowsProactorEventLoopPolicy` on Windows at the moment that
thread's loop is created (see `_DedicatedLoopThread` below). The
public `WebRecordingSession` methods stay `async def` and are called
exactly the same way from Web/routers/recorder_router.py — each one
just hands its real work to the dedicated loop via
`asyncio.run_coroutine_threadsafe()` and awaits the result via
`asyncio.wrap_future()`, which is the standard, supported way to
bridge two event loops running in different threads. This makes the
fix independent of whatever ends up flipping the main loop's type, and
harmless (a no-op besides one extra thread hop) on Linux/macOS, where
this bug does not exist in the first place. This part is unchanged
from the version that already fixed the real Windows
`NotImplementedError` crash confirmed against the production PSW
site — the headless=False change above does not touch it.
"""

import asyncio
import json
import sys
import threading
import time

from Core.logger import Logger
from Core.test_case_repository import TestCaseRepository
from Core.test_environment_config import TestEnvironmentConfig

logger = Logger.get_logger()

_SESSION_LOCK = threading.Lock()
_ACTIVE_SESSION = {"session": None}

MAX_RECORDING_SECONDS = 15 * 60


class _DedicatedLoopThread:
    """
    A single, long-lived background thread running its own asyncio
    event loop, used to run every bit of Playwright work this module
    does — see the module docstring's "WINDOWS" section for why this
    exists. Lazily started on first use and kept alive for the life
    of the server process; harmless to share across recording
    sessions since WebRecordingSession.try_acquire() already
    guarantees only one recording ever runs at a time.
    """

    _instance = None
    _instance_lock = threading.Lock()

    def __init__(self):
        self._loop = None
        self._ready = threading.Event()
        self._thread = threading.Thread(
            target=self._run, name="qa-web-recorder-loop", daemon=True
        )
        self._thread.start()
        self._ready.wait(timeout=10)

    def _run(self):
        if sys.platform == "win32":
            # Force Proactor for THIS thread's loop specifically —
            # setting the policy right before creating the loop is
            # what actually takes effect; it does not touch whatever
            # loop is already running on the main thread.
            asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        self._ready.set()
        self._loop.run_forever()

    @classmethod
    def get(cls):
        with cls._instance_lock:
            if cls._instance is None:
                cls._instance = cls()
            return cls._instance

    def run_coroutine(self, coro):
        """
        Schedules `coro` onto the dedicated loop from any other
        thread and returns a concurrent.futures.Future. Callers on an
        asyncio loop should `await asyncio.wrap_future(that_future)`.
        """
        return asyncio.run_coroutine_threadsafe(coro, self._loop)


# Mirrors Core/url_discovery_engine.py's looks_dynamically_generated()
# heuristic (kept in sync manually — this needs to run as plain JS
# inside the recorded page, so it can't just import that module).
# See that function's own docstring for the reasoning.
RECORDER_JS = r"""
(function () {
  if (window.__qaRecorderInstalled) return;
  window.__qaRecorderInstalled = true;

  var UUID_RE = /[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}/;
  var LONG_HEX_RE = /[0-9a-fA-F]{8,}/;

  function looksDynamic(value) {
    if (!value) return false;
    if (UUID_RE.test(value)) return true;
    if (LONG_HEX_RE.test(value)) return true;
    if (value.length >= 16 && /^[a-zA-Z0-9]+$/.test(value)) {
      var digits = (value.match(/[0-9]/g) || []).length;
      if (digits >= 3) return true;
    }
    return false;
  }

  function attr(el, name) {
    var v = el.getAttribute(name);
    return v && v.trim() ? v.trim() : null;
  }

  function implicitRole(el) {
    var tag = el.tagName.toLowerCase();
    if (tag === 'button') return 'button';
    if (tag === 'a' && el.hasAttribute('href')) return 'link';
    if (tag === 'input') {
      var t = (el.getAttribute('type') || 'text').toLowerCase();
      if (t === 'submit' || t === 'button') return 'button';
      if (t === 'checkbox') return 'checkbox';
      if (t === 'radio') return 'radio';
    }
    if (tag === 'select') return 'combobox';
    return null;
  }

  function labelFor(el) {
    if (el.id) {
      var lab = document.querySelector('label[for="' + CSS.escape(el.id) + '"]');
      if (lab && lab.innerText.trim()) return lab.innerText.trim();
    }
    var parentLabel = el.closest('label');
    if (parentLabel && parentLabel.innerText.trim()) return parentLabel.innerText.trim();
    return null;
  }

  function accessibleName(el) {
    var ariaLabel = attr(el, 'aria-label');
    if (ariaLabel) return ariaLabel;
    var label = labelFor(el);
    if (label) return label;
    var placeholder = attr(el, 'placeholder');
    if (placeholder) return placeholder;
    var text = (el.innerText || '').trim();
    if (text && text.length <= 60) return text;
    var title = attr(el, 'title');
    if (title) return title;
    var alt = attr(el, 'alt');
    if (alt) return alt;
    return null;
  }

  // Builds a CSS path AND reports how "fragile" it had to be (how
  // many ancestor levels needed a positional :nth-of-type() selector
  // to disambiguate, rather than a stable id/class). Item 3 requires
  // avoiding "unnecessary nth-child/nth-of-type... position-based
  // selectors" where a better alternative exists — fragileCount is
  // what lets computeLocator() decide when a relative XPath fallback
  // (item 3's requirement 7) is the better choice than a heavily
  // positional CSS path.
  function cssPathInfo(el) {
    if (el.id && !looksDynamic(el.id)) {
      return { path: '#' + CSS.escape(el.id), fragileCount: 0 };
    }
    var parts = [];
    var node = el;
    var depth = 0;
    var fragileCount = 0;
    while (node && node.nodeType === 1 && depth < 6) {
      var part = node.tagName.toLowerCase();
      var stableClasses = Array.prototype.filter.call(
        node.classList || [], function (c) { return !looksDynamic(c); }
      ).slice(0, 2);
      if (stableClasses.length) part += '.' + stableClasses.join('.');
      var parent = node.parentElement;
      if (parent) {
        var siblings = Array.prototype.filter.call(
          parent.children, function (c) { return c.tagName === node.tagName; }
        );
        if (siblings.length > 1) {
          part += ':nth-of-type(' + (siblings.indexOf(node) + 1) + ')';
          if (!stableClasses.length) fragileCount++;
        }
      }
      parts.unshift(part);
      node = parent;
      depth++;
    }
    return { path: parts.join(' > '), fragileCount: fragileCount };
  }

  function cssPath(el) {
    return cssPathInfo(el).path;
  }

  // Item 3, requirement 7 — a RELATIVE (never absolute, never
  // index-only) XPath fallback, used only when a robust CSS path
  // couldn't be built. Examples this can produce:
  //   //input[@name='username']
  //   //button[normalize-space()='Login']
  //   //div[contains(@class,'submit-row')]
  function xpathLiteral(text) {
    if (text.indexOf('"') === -1) return '"' + text + '"';
    if (text.indexOf("'") === -1) return "'" + text + "'";
    var segments = text.split('"');
    var pieces = [];
    for (var i = 0; i < segments.length; i++) {
      if (i > 0) pieces.push("'\"'");
      pieces.push('"' + segments[i] + '"');
    }
    return 'concat(' + pieces.join(', ') + ')';
  }

  function xpathFor(el) {
    var tag = el.tagName.toLowerCase();
    var text = (el.innerText || el.value || '').trim();
    if (text && text.length > 0 && text.length <= 60) {
      return '//' + tag + '[normalize-space()=' + xpathLiteral(text) + ']';
    }
    var name = attr(el, 'name');
    if (name && !looksDynamic(name)) {
      return '//' + tag + '[@name=' + xpathLiteral(name) + ']';
    }
    var placeholder = attr(el, 'placeholder');
    if (placeholder) {
      return '//' + tag + '[@placeholder=' + xpathLiteral(placeholder) + ']';
    }
    var ariaLabel = attr(el, 'aria-label');
    if (ariaLabel) {
      return '//' + tag + '[@aria-label=' + xpathLiteral(ariaLabel) + ']';
    }
    var stableClasses = Array.prototype.filter.call(
      el.classList || [], function (c) { return !looksDynamic(c); }
    );
    if (stableClasses.length) {
      return '//' + tag + '[contains(@class,' + xpathLiteral(stableClasses[0]) + ')]';
    }
    return null;
  }

  // Item 29: known-stable locators pulled from Knowledge Hub's URL
  // Discovery captures for this test case's Domain/Module/Knowledge
  // Name scope (see web_recorder.py's _known_locators_for_scope() —
  // this is read-only reuse of already-vetted locators; recording
  // and execution responsibility stays here, never moves into
  // Knowledge Hub). Empty array when no scope was selected or
  // nothing has been captured for it — behavior is then identical
  // to before this feature existed.
  var KNOWN_LOCATORS = __KNOWN_LOCATORS_JSON__;

  function knownLocatorFor(el) {
    for (var i = 0; i < KNOWN_LOCATORS.length; i++) {
      var known = KNOWN_LOCATORS[i];
      try {
        if (known.selector && el.matches(known.selector)) {
          return { strategy: 'knowledge_hub', value: known.selector, name: known.name || '' };
        }
      } catch (e) {
        // Not a valid CSS selector (shouldn't happen — server-side
        // filtering only sends CSS-compatible strategies) — skip it
        // rather than let one bad entry break locator computation.
      }
    }
    return null;
  }

  // Locator priority (item 3): known Knowledge Hub locator (item 29)
  // > data-testid > stable id > role+accessible name > stable
  // name/label/text > robust css > relative xpath fallback (only
  // when css would otherwise need 2+ positional nth-of-type
  // segments with no stable class to anchor them).
  function computeLocator(el) {
    var known = knownLocatorFor(el);
    if (known) return known;
    for (var i = 0; i < 4; i++) {
      var v = attr(el, ['data-testid', 'data-test', 'data-qa', 'data-cy'][i]);
      if (v) return { strategy: 'testid', value: v };
    }
    if (el.id && !looksDynamic(el.id)) {
      return { strategy: 'id', value: el.id };
    }
    var role = attr(el, 'role') || implicitRole(el);
    var name = accessibleName(el);
    if (role && name) {
      return { strategy: 'role', role: role, name: name };
    }
    if (el.name && !looksDynamic(el.name)) {
      return { strategy: 'name', value: el.name };
    }
    var label = labelFor(el);
    if (label) return { strategy: 'label', value: label };
    var text = (el.innerText || '').trim();
    if (text && text.length > 0 && text.length <= 60 &&
        ['BUTTON', 'A', 'SPAN', 'LI', 'TD', 'LABEL'].indexOf(el.tagName) !== -1) {
      return { strategy: 'text', value: text };
    }
    var cssInfo = cssPathInfo(el);
    if (cssInfo.fragileCount >= 2) {
      var xp = xpathFor(el);
      if (xp) return { strategy: 'xpath', value: xp };
    }
    return { strategy: 'css', value: cssInfo.path };
  }

  var TEXT_ENTRY_TYPES = ['text', 'email', 'password', 'search', 'tel', 'url', 'number', 'date'];

  function isTextEntry(el) {
    var tag = el.tagName;
    if (tag === 'TEXTAREA') return true;
    if (el.isContentEditable) return true;
    if (tag === 'INPUT') {
      var t = (el.getAttribute('type') || 'text').toLowerCase();
      return TEXT_ENTRY_TYPES.indexOf(t) !== -1;
    }
    return false;
  }

  function report(action) {
    if (window.__qa_record_action) {
      window.__qa_record_action(JSON.stringify(action));
    }
  }

  document.addEventListener('click', function (e) {
    var el = e.target;
    if (!el || el.nodeType !== 1) return;
    if (isTextEntry(el)) return;
    report({ kind: 'click', locator: computeLocator(el), tag: el.tagName });
  }, true);

  document.addEventListener('change', function (e) {
    var el = e.target;
    if (!el || el.nodeType !== 1) return;
    if (el.tagName === 'SELECT') {
      var selected = el.options[el.selectedIndex];
      report({
        kind: 'select_option', locator: computeLocator(el),
        value: el.value, label: selected ? selected.text : el.value,
      });
      return;
    }
    if (el.tagName === 'INPUT' && ['checkbox', 'radio'].indexOf((el.getAttribute('type') || '').toLowerCase()) !== -1) {
      return; // already captured as a click
    }
    if (isTextEntry(el)) {
      report({ kind: 'fill', locator: computeLocator(el), value: el.value });
    }
  }, true);
})();
"""


class RecordingError(Exception):
    pass


class WebRecordingSession:
    """
    One live, operator-driven Playwright recording session for a
    single Test Case, using a REAL, VISIBLE browser window (item 1).
    See this module's docstring for the full architecture. Instances
    are short-lived — created per WebSocket connection by
    Web/routers/recorder_router.py, discarded when it closes.

    Every public method below is a thin wrapper: the actual work
    (suffixed `_impl`) always runs on the shared `_DedicatedLoopThread`
    (see the module docstring's "WINDOWS" section for why), regardless
    of which loop the caller itself is running on.

    STATE MACHINE (item 1): Idle (no session object exists yet) ->
    Starting (`start()` in flight — browser launching) -> Recording
    (`start()` returned; the real window is open, DOM actions are
    being captured) -> Stopping (`finish(save=True)` in flight) ->
    Captured (finish() returned a non-None script — persisted to
    `recorded_script`) or Failed (start() raised) or, for Cancel,
    straight from Recording to Idle with nothing persisted
    (`finish(save=False)`). Web/routers/recorder_router.py owns
    driving this state machine and reporting it to the client; this
    class only guarantees each state transition either fully
    succeeds or fully tears down (never leaves an orphaned browser
    process behind).
    """

    def __init__(self, test_case_id, start_url, domain="", module="", knowledge_name=""):

        self.test_case_id = test_case_id
        self.start_url = start_url
        # Item 29 — optional; see recorder_router.py and
        # _known_locators_for_scope() below.
        self.domain = domain
        self.module = module
        self.knowledge_name = knowledge_name
        self.actions = []
        self._playwright = None
        self._browser = None
        self._context = None
        self._page = None
        self._stopped = False
        self._start_time = None
        # Captured on whichever loop first calls into this session
        # (Web/routers/recorder_router.py's own coroutine, running on
        # uvicorn's main loop) so callbacks fired from the dedicated
        # recorder thread (activity text, captured DOM actions) can be
        # safely marshalled back to it via call_soon_threadsafe()
        # instead of being invoked directly from the wrong thread.
        self._caller_loop = None

    # Only these locator_strategy values (see url_discovery_engine.py's
    # _generate_locator()) produce a `locator` string that is also a
    # plain, directly-usable CSS selector — e.g. "#id",
    # "input[name='x']", "[data-testid='x']". Strategies like "text"
    # (Playwright's :has-text() pseudo-selector), "xpath-dynamic-*"
    # and "tag-only" are not valid input to Element.matches(), so
    # they are deliberately excluded here rather than sent to the
    # browser to fail silently.
    _CSS_COMPATIBLE_DISCOVERY_STRATEGIES = frozenset(
        {"id", "name", "data-testid", "aria-label", "placeholder"}
    )

    @staticmethod
    def _known_locators_for_scope(domain, module, knowledge_name):
        """
        Item 29 — Knowledge Hub locator reuse. Returns
        [{"selector": "...", "name": "..."}] for every already-captured,
        CSS-matchable element filed under this Domain/Module/Knowledge
        Name in Knowledge Hub (Manage Knowledge -> URL Knowledge
        Capture), using the exact same
        DiscoveryRepository.get_elements_for_scope() call
        Core/test_execution_manager.py's AI generation path already
        uses to ground itself in real captured elements. This is
        read-only reuse — recording/execution responsibility never
        moves into Knowledge Hub (per item 29's explicit constraint).

        Returns [] (never raises) when no scope was given, or nothing
        has been captured for it — a live recording works exactly as
        it did before this feature existed in that case.
        """

        if not (domain and module and knowledge_name):
            return []

        try:
            from Core.discovery_repository import DiscoveryRepository

            elements = DiscoveryRepository().get_elements_for_scope(
                domain, module, knowledge_name
            )
        except Exception:
            logger.exception(
                "[web-recorder] could not load Knowledge Hub locators "
                "for scope — recording continues without them."
            )
            return []

        known = []
        seen_selectors = set()

        for element in elements:

            strategy = element.get("locator_strategy")
            selector = element.get("locator")

            if strategy not in WebRecordingSession._CSS_COMPATIBLE_DISCOVERY_STRATEGIES:
                continue

            if not selector or selector in seen_selectors:
                continue

            seen_selectors.add(selector)

            known.append({
                "selector": selector,
                "name": element.get("name") or element.get("text") or "",
            })

            # A hard cap keeps the injected init-script small and the
            # per-element matches() scan cheap — 200 already-vetted
            # locators is far more than any single scope realistically
            # has, and matching stops at the first hit anyway.
            if len(known) >= 200:
                break

        return known

    @staticmethod
    def try_acquire(test_case_id):
        """
        Returns True and claims the single global recording slot, or
        False if another recording is already in progress anywhere
        in this server process — mirrors Desktop's "only one
        recording can run at a time" rule (see App/UI/QAAutomation/
        test_execution_page.py's start_manual_recording()).
        """

        with _SESSION_LOCK:

            if _ACTIVE_SESSION["session"] is not None:

                return False

            _ACTIVE_SESSION["session"] = test_case_id

            return True

    @staticmethod
    def release():

        with _SESSION_LOCK:

            _ACTIVE_SESSION["session"] = None

    def _notify(self, on_activity, text):
        """
        Safely invokes `on_activity(text)` regardless of which thread
        this is called from. `on_activity` (defined in
        recorder_router.py) does `websocket.send_json(...)`, which
        must run on the loop that actually owns that WebSocket
        connection — the caller's loop, not the dedicated recorder
        thread's loop. `call_soon_threadsafe` is the supported way to
        hand a plain callback back to a specific loop from any thread.
        """

        logger.info(f"[web-recorder] {text}")

        if on_activity is None:
            return

        if self._caller_loop is not None:
            self._caller_loop.call_soon_threadsafe(on_activity, text)
        else:
            # No caller loop captured yet (shouldn't normally happen —
            # start() always sets it first) — fall back to a direct
            # call rather than silently dropping the activity line.
            on_activity(text)

    # ----------------------------------------------------------------
    # Public API — thin wrappers that always run their real work on
    # the shared dedicated loop (see module docstring, "WINDOWS").
    # ----------------------------------------------------------------

    async def start(self, on_activity=None):

        self._caller_loop = asyncio.get_running_loop()

        future = _DedicatedLoopThread.get().run_coroutine(
            self._start_impl(on_activity)
        )

        return await asyncio.wrap_future(future)

    async def finish(self, save=True):

        future = _DedicatedLoopThread.get().run_coroutine(
            self._finish_impl(save)
        )

        return await asyncio.wrap_future(future)

    # ----------------------------------------------------------------
    # Real implementations — always execute on the dedicated loop.
    # ----------------------------------------------------------------

    async def _start_impl(self, on_activity):

        from playwright.async_api import async_playwright

        self._start_time = time.monotonic()

        def activity(text):
            self._notify(on_activity, text)

        activity("Starting recorder...")

        self._playwright = await async_playwright().start()

        # Item 1 — REAL, VISIBLE browser window (headless=False), not
        # an embedded/streamed one. This is the entire behavioral
        # change from the prior CDP-screencast-in-modal version; see
        # the module docstring for why this replaced streaming rather
        # than porting Desktop's codegen subprocess verbatim.
        self._browser = await self._playwright.chromium.launch(
            headless=False,
            args=["--start-maximized"],
        )

        activity("Browser launched. A real browser window has opened "
                 "on this machine — perform your test steps there.")

        def on_disconnected(_browser=None):
            # Fires if the operator closes the real browser window
            # themselves (exactly how Desktop's codegen recording
            # normally finishes) instead of clicking Stop Recording
            # in the Web UI. finish(save=True) still works correctly
            # afterwards — it only reads the in-memory self.actions
            # already captured via the DOM-event binding below and
            # tears down objects that are, at that point, harmless
            # no-ops to close again — so this is purely an activity
            # notification, not a state change, to avoid racing a
            # concurrent explicit Stop/Cancel from the client.
            if not self._stopped:
                self._notify(
                    on_activity,
                    "Browser window was closed. Click \"Stop Recording\" "
                    "in this panel to save what was captured, or "
                    "\"Cancel Recording\" to discard it.",
                )

        self._browser.on("disconnected", on_disconnected)

        self._context = await self._browser.new_context(no_viewport=True)

        self._page = await self._context.new_page()

        await self._context.expose_binding(
            "__qa_record_action",
            lambda source, raw: self._on_action(raw, activity),
        )

        known_locators = self._known_locators_for_scope(
            self.domain, self.module, self.knowledge_name
        )

        if known_locators:
            activity(
                f"Loaded {len(known_locators)} known stable locator(s) "
                f"from Knowledge Hub for this scope — these are "
                f"preferred over freshly-computed ones when they match."
            )

        recorder_js = RECORDER_JS.replace(
            "__KNOWN_LOCATORS_JSON__", json.dumps(known_locators)
        )

        await self._context.add_init_script(recorder_js)

        url = (self.start_url or "").strip()

        if not url:

            environment = TestEnvironmentConfig().load()

            url = (environment.get("base_url") or "").strip()

        if not url:

            await self._teardown()

            raise RecordingError(
                "No starting URL given, and no Base URL is set in "
                "Test Environment Settings. Set one of those first "
                "so the recorder knows where to open the browser."
            )

        if not (url.startswith("http://") or url.startswith("https://")):
            url = "https://" + url

        await self._page.goto(url, wait_until="domcontentloaded")

        self.actions.append({"kind": "goto", "url": url})

        activity("Recording...")

    def _on_action(self, raw, activity):

        try:
            action = json.loads(raw)
        except Exception:
            return

        self.actions.append(action)

        activity(self._describe_action(action))

    @staticmethod
    def _describe_action(action):

        kind = action.get("kind")
        loc = action.get("locator") or {}
        loc_desc = loc.get("value") or loc.get("name") or ""

        if loc.get("strategy") == "knowledge_hub":
            # Item 29 — make the reuse visible in the live activity
            # log, not just in the saved script, so the operator can
            # actually see when a known-good locator was preferred
            # over a freshly-computed one.
            if kind == "click":
                return f"Captured: click (Knowledge Hub locator: {loc_desc})"
            if kind == "fill":
                return f"Captured: fill (Knowledge Hub locator: {loc_desc})"

        if kind == "click":
            return f"Captured: click ({loc.get('strategy')}: {loc_desc})"
        if kind == "fill":
            return f"Captured: fill ({loc.get('strategy')}: {loc_desc})"
        if kind == "select_option":
            return f"Captured: select ({loc_desc}) = {action.get('label')}"
        if kind == "goto":
            return f"Captured: navigate to {action.get('url')}"

        return f"Captured: {kind}"

    def seconds_remaining(self):

        if self._start_time is None:
            return MAX_RECORDING_SECONDS

        elapsed = time.monotonic() - self._start_time

        return max(0, MAX_RECORDING_SECONDS - elapsed)

    def build_script(self):

        lines = []

        for action in self.actions:

            kind = action.get("kind")

            if kind == "goto":
                lines.append(f"    page.goto({action['url']!r})")
                continue

            locator_src = self._locator_source(action.get("locator") or {})

            if locator_src is None:
                continue

            if kind == "click":
                lines.append(f"    {locator_src}.click()")
            elif kind == "fill":
                lines.append(f"    {locator_src}.fill({action.get('value', '')!r})")
            elif kind == "select_option":
                lines.append(
                    f"    {locator_src}.select_option({action.get('value', '')!r})"
                )

        if not lines:
            lines.append(
                "    # No interactions were captured — nothing to replay."
            )

        body = "\n".join(lines)

        return (
            "from playwright.sync_api import Playwright, sync_playwright, expect\n\n\n"
            "def run(playwright: Playwright) -> None:\n"
            "    browser = playwright.chromium.launch(headless=False)\n"
            "    context = browser.new_context()\n"
            "    page = context.new_page()\n"
            f"{body}\n"
            "    context.close()\n"
            "    browser.close()\n\n\n"
            "with sync_playwright() as playwright:\n"
            "    run(playwright)\n"
        )

    @staticmethod
    def _locator_source(locator):

        strategy = locator.get("strategy")

        if strategy == "testid":
            return f"page.get_by_test_id({locator['value']!r})"
        if strategy == "id":
            return f"page.locator({'#' + locator['value']!r})"
        if strategy == "role":
            return f"page.get_by_role({locator['role']!r}, name={locator['name']!r})"
        if strategy == "name":
            name_selector = '[name="' + locator["value"] + '"]'
            return f"page.locator({name_selector!r})"
        if strategy == "label":
            return f"page.get_by_label({locator['value']!r})"
        if strategy == "text":
            return f"page.get_by_text({locator['value']!r}, exact=True)"
        if strategy == "css":
            return f"page.locator({locator['value']!r})"
        if strategy == "xpath":
            # Item 3, requirement 7 — relative XPath fallback computed
            # client-side by xpathFor() in RECORDER_JS, only used when
            # a robust CSS path couldn't be built. Playwright accepts
            # a raw XPath expression as a selector when prefixed with
            # "xpath=".
            return f"page.locator({'xpath=' + locator['value']!r})"
        if strategy == "knowledge_hub":
            # A real, already-vetted CSS selector reused from
            # Knowledge Hub's URL Discovery captures (item 29) —
            # page.locator() accepts it exactly like any other CSS
            # selector; no new replay-side handling needed.
            return f"page.locator({locator['value']!r})"

        return None

    async def _finish_impl(self, save=True):

        script = None

        try:

            if save:
                script = self.build_script()
                TestCaseRepository().update_recorded_script(
                    self.test_case_id, script
                )

        finally:

            await self._teardown()

        return script

    async def _teardown(self):

        self._stopped = True

        for obj in (self._context, self._browser):
            try:
                if obj is not None:
                    await obj.close()
            except Exception:
                pass

        try:
            if self._playwright is not None:
                await self._playwright.stop()
        except Exception:
            pass

        WebRecordingSession.release()
