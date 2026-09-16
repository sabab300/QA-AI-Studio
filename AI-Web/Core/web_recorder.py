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
import ast
import json
import sys
import threading
import time

from Core.logger import Logger
from Core.test_case_repository import TestCaseRepository
from Core.playwright_step_metadata import metadata_comment
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
    if (/__[A-Za-z0-9_-]{5,}$/.test(value)) return true;
    if (value.length >= 16 && /^[a-zA-Z0-9]+$/.test(value)) {
      var digits = (value.match(/[0-9]/g) || []).length;
      if (digits >= 3) return true;
    }
    return false;
  }

  function isGenericFrameworkClass(value) {
    return /^(?:k-icon|k-svg-icon|k-select|k-input|k-widget|k-button-icon|icon|wrapper|container|row|col|btn|form-control|input-group|p-\d+|m[btxy]?-\d+|p[btxy]?-\d+|col-(?:xs|sm|md|lg|xl)-\d+)$/i.test(value || '');
  }

  function isLayoutDependentCss(path) {
    if (!path) return true;
    var depth = (path.match(/\s>\s/g) || []).length;
    return depth > 2 || /:nth-(?:child|of-type)|(?:^|[.\s>])(?:row|col(?:-|[.\s>])|p-\d+|m[btxy]?-\d+|p[btxy]?-\d+)(?:[.\s>:]|$)|(?:^|\s>\s)(?:form|fieldset)(?:[.\s>:]|$)/i.test(path);
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
        node.classList || [], function (c) { return !looksDynamic(c) && !isGenericFrameworkClass(c); }
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
    if (el.id && !looksDynamic(el.id)) {
      return '//*[@id=' + xpathLiteral(el.id) + ']';
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
    var text = (el.innerText || '').trim();
    if (text && text.length > 0 && text.length <= 60) {
      return '//' + tag + '[normalize-space()=' + xpathLiteral(text) + ']';
    }
    var stableClasses = Array.prototype.filter.call(
      el.classList || [], function (c) { return !looksDynamic(c) && !isGenericFrameworkClass(c); }
    );
    if (stableClasses.length) {
      return '//' + tag + '[contains(@class,' + xpathLiteral(stableClasses[0]) + ')]';
    }
    return null;
  }

  function sectionFor(el) {
    var node = el.parentElement;
    var depth = 0;
    while (node && depth < 8) {
      var isContainer = /^(ARTICLE|SECTION|FORM|FIELDSET|LI)$/.test(node.tagName)
        || /(?:^|\s)(?:k-card|card|panel|tile|menu-item|list-item)(?:\s|$)/i.test(node.className || '')
        || ['region','group','dialog','menuitem','listitem'].indexOf(attr(node, 'role') || '') !== -1;
      if (isContainer) {
        var identities = [];
        function addIdentity(value) {
          value = (value || '').replace(/\s+/g, ' ').trim().slice(0, 100);
          if (value && value !== accessibleName(el) && identities.indexOf(value) === -1) identities.push(value);
        }
        addIdentity(attr(node, 'aria-label')); addIdentity(attr(node, 'title'));
        node.querySelectorAll('h1,h2,h3,h4,legend,[role="heading"],.k-card-title,[class*="title"],[class*="heading"],label,p,span,strong').forEach(function(item) {
          if (!el.contains(item) && !item.contains(el)) addIdentity(item.innerText);
        });
        if (identities.length) return {element: node, name: identities[0], identities: identities.slice(0, 12)};
      }
      node = node.parentElement;
      depth++;
    }
    return {element: null, name: '', identities: []};
  }

  function contextualXpathFor(el) {
    var section = sectionFor(el), text = (el.innerText || '').replace(/\s+/g, ' ').trim();
    if (!section.name || !text || text.length > 100) return null;
    return '//*[self::article or self::section or self::form or self::fieldset or self::li or contains(concat(" ",normalize-space(@class)," ")," k-card ") or contains(@class,"card") or contains(@class,"panel") or contains(@class,"tile")]'
      + '[.//*[normalize-space()=' + xpathLiteral(section.name) + ']]//'
      + el.tagName.toLowerCase() + '[normalize-space(.)=' + xpathLiteral(text) + ']';
  }

  function fieldContextXpathFor(el) {
    var container = el.closest('.k-form-field,.form-group,.field,[class*="form-field"],[class*="field-wrap"],[class*="input-group"]');
    if (!container) return null;
    var label = container.querySelector('label,.k-label,[class*="field-label"]');
    var labelText = label && (label.innerText || '').replace(/\s+/g, ' ').trim();
    if (!labelText || labelText.length > 120) return null;
    var tag = el.tagName.toLowerCase();
    var role = attr(el, 'role') || implicitRole(el);
    var suffix = role ? '[@role=' + xpathLiteral(role) + ']' : '';
    return '//*[self::div or self::section or self::fieldset or self::label]'
      + '[.//*[self::label or contains(@class,"label")][normalize-space()=' + xpathLiteral(labelText) + ']]//'
      + tag + suffix;
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
  function locatorMatchCount(locator) {
    try {
      var nodes = [];
      if (locator.strategy === 'testid') nodes = document.querySelectorAll('[data-testid="' + CSS.escape(locator.value) + '"]');
      else if (locator.strategy === 'id') nodes = document.querySelectorAll('#' + CSS.escape(locator.value));
      else if (locator.strategy === 'name') nodes = document.querySelectorAll('[name="' + CSS.escape(locator.value) + '"]');
      else if (locator.strategy === 'placeholder') nodes = document.querySelectorAll('[placeholder="' + CSS.escape(locator.value) + '"]');
      else if (locator.strategy === 'title') nodes = document.querySelectorAll('[title="' + CSS.escape(locator.value) + '"]');
      else if (locator.strategy === 'css' || locator.strategy === 'knowledge_hub') nodes = document.querySelectorAll(locator.value);
      else if (locator.strategy === 'xpath') return document.evaluate(locator.value, document, null, XPathResult.ORDERED_NODE_SNAPSHOT_TYPE, null).snapshotLength;
      else if (locator.strategy === 'label') nodes = Array.prototype.filter.call(document.querySelectorAll('input,select,textarea'), function(node) { return labelFor(node) === locator.value; });
      else if (locator.strategy === 'role') nodes = Array.prototype.filter.call(document.querySelectorAll('button,a,input,select,textarea,[role]'), function(node) { return (attr(node, 'role') || implicitRole(node)) === locator.role && accessibleName(node) === locator.name; });
      else if (locator.strategy === 'text') nodes = Array.prototype.filter.call(document.querySelectorAll('button,a,span,li,td,label'), function(node) { return (node.innerText || '').trim() === locator.value; });
      return nodes.length;
    } catch (e) { return 0; }
  }

  function computeLocator(el) {
    var known = knownLocatorFor(el);
    if (known && locatorMatchCount(known) === 1) return known;
    for (var i = 0; i < 4; i++) {
      var v = attr(el, ['data-testid', 'data-test', 'data-qa', 'data-cy'][i]);
      if (v) {
        var testLocator = { strategy: 'testid', value: v };
        if (locatorMatchCount(testLocator) === 1) return testLocator;
      }
    }
    if (el.id && !looksDynamic(el.id)) {
      var stableIdLocator = { strategy: 'id', value: el.id };
      if (locatorMatchCount(stableIdLocator) === 1) return stableIdLocator;
    }
    if (el.name && !looksDynamic(el.name)) {
      var nameLocator = { strategy: 'name', value: el.name };
      if (locatorMatchCount(nameLocator) === 1) return nameLocator;
    }
    var label = labelFor(el);
    if (label) {
      var labelLocator = { strategy: 'label', value: label };
      if (locatorMatchCount(labelLocator) === 1) return labelLocator;
    }
    var role = attr(el, 'role') || implicitRole(el);
    var name = accessibleName(el);
    if (role && name) {
      var roleLocator = { strategy: 'role', role: role, name: name };
      if (locatorMatchCount(roleLocator) === 1) return roleLocator;
    }
    var placeholder = attr(el, 'placeholder');
    if (placeholder) {
      var placeholderLocator = { strategy: 'placeholder', value: placeholder };
      if (locatorMatchCount(placeholderLocator) === 1) return placeholderLocator;
    }
    var title = attr(el, 'title');
    if (title) {
      var titleLocator = { strategy: 'title', value: title };
      if (locatorMatchCount(titleLocator) === 1) return titleLocator;
    }
    var text = (el.innerText || '').trim();
    if (text && text.length > 0 && text.length <= 60 &&
        ['BUTTON', 'A', 'SPAN', 'LI', 'TD', 'LABEL'].indexOf(el.tagName) !== -1) {
      var textLocator = { strategy: 'text', value: text };
      if (locatorMatchCount(textLocator) === 1) return textLocator;
    }
    var contextualXpath = contextualXpathFor(el);
    if (contextualXpath) {
      var contextual = {strategy:'xpath', value:contextualXpath};
      if (locatorMatchCount(contextual) === 1) return contextual;
    }
    var fieldXpath = fieldContextXpathFor(el);
    if (fieldXpath && locatorMatchCount({strategy:'xpath', value:fieldXpath}) === 1) {
      return {strategy:'xpath', value:fieldXpath};
    }
    var cssInfo = cssPathInfo(el);
    if (cssInfo.fragileCount > 0 || isLayoutDependentCss(cssInfo.path)) {
      var xp = xpathFor(el);
      if (xp && locatorMatchCount({strategy:'xpath', value:xp}) === 1) return { strategy: 'xpath', value: xp };
      return null;
    }
    var cssLocator = { strategy: 'css', value: cssInfo.path };
    if (cssInfo.path && locatorMatchCount(cssLocator) === 1) return cssLocator;
    var fallbackXpath = xpathFor(el);
    if (fallbackXpath && locatorMatchCount({strategy:'xpath', value:fallbackXpath}) === 1) {
      return {strategy:'xpath', value:fallbackXpath};
    }
    // A recorder event without a unique locator is diagnostic only. Never
    // serialize an ambiguous generic selector such as .k-icon or //span.
    return null;
  }

  function fieldMetadata(el, kind) {
    var control = el;
    if (!['INPUT', 'SELECT', 'TEXTAREA', 'BUTTON', 'A'].includes(control.tagName)) {
      control = el.closest('button,a,label,[role="button"],[role="combobox"]') || el;
    }
    var inputType = (attr(control, 'type') || '').toLowerCase();
    var role = attr(control, 'role') || implicitRole(control) || '';
    var widgetClass = ((control.closest('.k-datepicker,.k-calendar,.k-dropdown,.k-dropdownlist,.k-combobox,.k-autocomplete,.k-picker') || {}).className || '');
    var name = accessibleName(control) || attr(control, 'name') || '';
    if (!name) {
      var container = control.closest('.k-form-field,.form-group,.field,[class*="field"]');
      var containerLabel = container && container.querySelector('label,.k-label,[class*="label"]');
      name = containerLabel ? (containerLabel.innerText || '').trim() : '';
    }
    if (!name || /^(span|div|k-icon)$/i.test(name)) {
      name = attr(control, 'name') || attr(control, 'placeholder') || attr(control, 'title') || 'Element';
    }
    var type = 'text';
    if (inputType === 'password') type = 'password';
    else if (role !== 'gridcell' && (inputType === 'date' || /date|calendar/i.test((control.className || '') + ' ' + widgetClass + ' ' + name))) type = /calendar/i.test(widgetClass) ? 'calendar' : 'date';
    else if (inputType === 'file') type = 'file';
    else if (role === 'option' || /(?:k-list-item|k-item|option)/i.test(control.className || '')) type = 'option';
    else if (role === 'gridcell' || /(?:calendar|date-cell|k-calendar)/i.test(control.className || '')) type = 'date';
    else if (control.tagName === 'SELECT' || role === 'combobox' || control.getAttribute('aria-haspopup') === 'listbox' || /dropdown|combobox|autocomplete|k-select/i.test((control.className || '') + ' ' + widgetClass)) type = /autocomplete/i.test((control.className || '') + ' ' + widgetClass) ? 'autocomplete' : 'dropdown';
    else if (/accordion|expansion-panel/i.test(control.className || '')) type = 'accordion';
    else if (role === 'dialog' || /modal|dialog/i.test(control.className || '')) type = 'modal';
    else if (control.tagName === 'BUTTON' || role === 'button' || kind === 'click') type = 'button';
    return { field_name: name.slice(0, 120), field_type: type, tag: control.tagName.toLowerCase(), section: sectionFor(control).name };
  }

  function locatorMetadata(el) {
    var primary = computeLocator(el);
    var fallbacks = [];
    var seen = {};
    function add(locator) {
      if (!locator) return;
      var key = JSON.stringify(locator);
      if (key === JSON.stringify(primary) || seen[key] || fallbacks.length >= 4 || locatorMatchCount(locator) !== 1) return;
      seen[key] = true;
      fallbacks.push(locator);
    }
    var role = attr(el, 'role') || implicitRole(el), name = accessibleName(el), label = labelFor(el);
    if (role && name) add({strategy:'role', role:role, name:name});
    if (label) add({strategy:'label', value:label});
    if (el.name && !looksDynamic(el.name)) add({strategy:'name', value:el.name});
    var placeholder = attr(el, 'placeholder'), title = attr(el, 'title');
    if (placeholder) add({strategy:'placeholder', value:placeholder});
    if (title) add({strategy:'title', value:title});
    if (el.id && !looksDynamic(el.id)) add({strategy:'id', value:el.id});
    var cssInfo = cssPathInfo(el);
    var contextualXpath = contextualXpathFor(el);
    if (contextualXpath) add({strategy:'xpath', value:contextualXpath});
    var fieldXpath = fieldContextXpathFor(el);
    if (fieldXpath) add({strategy:'xpath', value:fieldXpath});
    if (cssInfo.path && cssInfo.fragileCount === 0 && !isLayoutDependentCss(cssInfo.path) && cssInfo.path.length <= 180) add({strategy:'css', value:cssInfo.path});
    return {locator: primary, fallback_locators: fallbacks, xpath: xpathFor(el)};
  }

  function capturedAction(el, kind, extra) {
    var target = null;
    var option = el.closest('[role="option"],option,.k-list-item,.k-item');
    var dateCell = el.closest('[role="gridcell"],.k-calendar-td,.k-calendar td');
    if (option) target = option;
    else if (dateCell) target = dateCell;
    else if (/(?:k-icon|k-svg-icon|k-select|k-input-button)/i.test(el.className || '')) {
      var widget = el.closest('.k-dropdown,.k-dropdownlist,.k-combobox,.k-autocomplete,.k-datepicker,.k-picker,[role="combobox"]');
      if (widget) target = /(?:k-datepicker|k-picker)/i.test(widget.className || '')
        ? (el.closest('button,[role="button"]') || widget.querySelector('input[aria-controls],input[aria-owns],button,[role="button"],input') || widget)
        : (widget.querySelector('input,[role="combobox"],button') || widget);
    }
    if (!target) target = el.closest('button,a,input,select,textarea,[role="button"],[role="combobox"],[role="link"]');
    if (!target && el.querySelectorAll && (
        getComputedStyle(el).cursor === 'pointer' ||
        /(?:k-input|k-picker|k-dropdown|k-combobox|input-group|button-wrap)/i.test(el.className || '')
    )) {
      var actionableChildren = el.querySelectorAll('button,a[href],input,select,textarea,[role="button"],[role="combobox"],[role="link"]');
      if (actionableChildren.length === 1) target = actionableChildren[0];
    }
    if (!target) return null;
    var targetRole = attr(target, 'role') || implicitRole(target) || '';
    var semanticLeaf = ['button','link','combobox','option','gridcell','menuitem','tab'].indexOf(targetRole) !== -1;
    if (!semanticLeaf && (/^(DIV|SPAN|FORM|FIELDSET|SECTION|DIALOG)$/.test(target.tagName) ||
        /(?:k-header|k-dialog|k-popup|wrapper|container|(?:^|\s)(?:row|col(?:-|\s)))/i.test(target.className || ''))) return null;
    var result = Object.assign({kind:kind}, locatorMetadata(target), fieldMetadata(target, kind), extra || {});
    if (kind === 'click' && /(?:k-icon|k-svg-icon|k-input-button)/i.test(el.className || '') && el.closest('.k-datepicker,.k-picker')) {
      result.field_type = 'calendar';
      var dateInput = el.closest('.k-datepicker,.k-picker').querySelector('input');
      if (dateInput) {
        result.field_name = accessibleName(dateInput) || result.field_name;
        // Icon text such as "calendar" describes the glyph, not the date
        // field. Do not serialize it as a semantic fallback for this step.
        result.fallback_locators = (result.fallback_locators || []).filter(function(locator) {
          return locator.strategy !== 'role' && locator.strategy !== 'text';
        });
      }
    }
    result.control_role = attr(target, 'role') || implicitRole(target) || '';
    result.control_context = sectionFor(target).name || '';
    var owns = attr(target, 'aria-controls') || attr(target, 'aria-owns');
    result.popup_locator = owns ? {strategy:'id', value:owns} : null;
    return result;
  }

  function notificationContainerFor(el) {
    return el.closest('[role="status"],[role="alert"],[aria-live]:not([aria-live="off"]),.toast,.notification,.snackbar,.k-notification,.k-notification-container,[class*="toast"],[class*="notification"],[class*="snackbar"]');
  }

  function actionableNotificationTarget(el, notification) {
    if (!notification) return el;
    var target = el.closest('button,a[href],[role="button"],[role="link"]');
    return target && target !== notification && notification.contains(target) ? target : null;
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

  function isDynamicTextControl(el) {
    var role = attr(el, 'role') || implicitRole(el) || '';
    return role === 'combobox'
      || attr(el, 'aria-haspopup') === 'listbox'
      || !!el.closest('.k-dropdown,.k-dropdownlist,.k-combobox,.k-autocomplete,.k-picker');
  }

  var LAST_REPORTED = null;

  function report(action) {
    if (!action || !action.locator) {
      console.warn('QA AI Studio Recorder skipped an action without a unique locator.');
      return;
    }
    var signature = JSON.stringify({
      kind: action.kind, locator: action.locator,
      value: action.kind === 'fill' ? action.value : action.expected_value
    });
    var now = Date.now();
    if (LAST_REPORTED && LAST_REPORTED.signature === signature && now - LAST_REPORTED.at < 750) return;
    LAST_REPORTED = {signature: signature, at: now};
    if (window.__qa_record_action) {
      window.__qa_record_action(JSON.stringify(action));
    }
  }

  var POPUP_TRANSACTION = null;
  var POPUP_SEQUENCE = 0;
  var PENDING_DYNAMIC_FILL = null;
  var POPUP_SELECTOR = '[role="listbox"],[role="grid"],.k-list-container,.k-popup,.k-calendar,.k-calendar-container';

  function visibleElement(el) {
    if (!el || !el.isConnected) return false;
    var style = getComputedStyle(el);
    return style.display !== 'none' && style.visibility !== 'hidden' && el.getClientRects().length > 0;
  }

  function closePopupTransaction(reason) {
    if (!POPUP_TRANSACTION) return;
    POPUP_TRANSACTION.state = 'CLOSED';
    POPUP_TRANSACTION.closed_reason = reason || 'completed';
    POPUP_TRANSACTION = null;
  }

  function sameLocator(left, right) {
    return !!left && !!right && JSON.stringify(left) === JSON.stringify(right);
  }

  function flushPendingDynamicFill(tx) {
    if (!PENDING_DYNAMIC_FILL || (tx && PENDING_DYNAMIC_FILL.transaction !== tx)) return;
    var pending = PENDING_DYNAMIC_FILL;
    PENDING_DYNAMIC_FILL = null;
    report(pending.action);
  }

  function controlledPopup(opener) {
    var owns = opener && (attr(opener, 'aria-controls') || attr(opener, 'aria-owns'));
    if (!owns) return null;
    for (var id of owns.split(/\s+/)) {
      var candidate = document.getElementById(id);
      if (candidate) return candidate;
    }
    return null;
  }

  function reconcilePopupTransaction() {
    var tx = POPUP_TRANSACTION;
    if (!tx || tx.state === 'CLOSED') return;
    var controlled = controlledPopup(tx.opener_element);
    if (controlled) {
      tx.popup_element = controlled;
      tx.popup_id = controlled.id || '';
      tx.evidence = 'opener aria-controls/aria-owns';
      return;
    }
    var visiblePopups = Array.prototype.filter.call(document.querySelectorAll(POPUP_SELECTOR), visibleElement);
    if (visiblePopups.length === 1) {
      tx.popup_element = visiblePopups[0];
      tx.popup_id = visiblePopups[0].id || '';
      tx.evidence = 'single visible popup after opener';
    }
  }

  function beginPopupTransaction(action, opener) {
    if (POPUP_TRANSACTION && POPUP_TRANSACTION.state === 'OPEN' &&
        sameLocator(POPUP_TRANSACTION.action.locator, action.locator) &&
        Date.now() - POPUP_TRANSACTION.started_at < 2500) {
      action.control_transaction = POPUP_TRANSACTION.id;
      return false;
    }
    flushPendingDynamicFill(POPUP_TRANSACTION);
    closePopupTransaction('superseded by another dynamic control');
    POPUP_TRANSACTION = {
      id: 'popup-' + (++POPUP_SEQUENCE), action: action, opener_element: opener,
      widget_element: opener.closest('.k-dropdown,.k-dropdownlist,.k-combobox,.k-autocomplete,.k-datepicker,.k-picker,[role="combobox"]'),
      popup_element: controlledPopup(opener), popup_id: '', state: 'OPEN',
      started_at: Date.now(), evidence: 'opener interaction'
    };
    if (POPUP_TRANSACTION.popup_element) {
      POPUP_TRANSACTION.popup_id = POPUP_TRANSACTION.popup_element.id || '';
      POPUP_TRANSACTION.evidence = 'opener aria-controls/aria-owns';
    }
    action.control_transaction = POPUP_TRANSACTION.id;
    setTimeout(reconcilePopupTransaction, 0);
    setTimeout(reconcilePopupTransaction, 80);
    return true;
  }

  function elementBelongsToTransaction(el, tx) {
    if (!el || !tx) return false;
    reconcilePopupTransaction();
    if (el === tx.opener_element || (tx.widget_element && tx.widget_element.contains(el))) return true;
    if (tx.popup_element && tx.popup_element.contains(el)) return true;
    var popup = el.closest(POPUP_SELECTOR);
    if (popup && tx.popup_element === popup) return true;
    if (popup && popup.id && tx.popup_id === popup.id) return true;
    return false;
  }

  function provenOwnerFor(el) {
    var popup = el.closest(POPUP_SELECTOR);
    var tx = POPUP_TRANSACTION;
    if (tx && tx.state !== 'CLOSED' && elementBelongsToTransaction(el, tx)) {
      tx.state = 'SELECTING';
      return {owner:tx.action, transaction:tx, popup:popup || tx.popup_element, evidence:tx.evidence || 'active control transaction'};
    }
    var labelledBy = popup && attr(popup, 'aria-labelledby');
    var opener = labelledBy ? document.getElementById(labelledBy.split(/\s+/)[0]) : null;
    var active = document.activeElement;
    if (!opener && popup && active && active.nodeType === 1) {
      var controls = attr(active, 'aria-controls') || attr(active, 'aria-owns');
      if (popup.id && controls && controls.split(/\s+/).indexOf(popup.id) !== -1) opener = active;
    }
    if (opener) {
      var ownerAction = capturedAction(opener, 'click');
      if (ownerAction && ['dropdown','autocomplete','calendar'].indexOf(ownerAction.field_type) !== -1) {
        beginPopupTransaction(ownerAction, opener);
        POPUP_TRANSACTION.popup_element = popup;
        POPUP_TRANSACTION.popup_id = (popup && popup.id) || '';
        POPUP_TRANSACTION.state = 'SELECTING';
        POPUP_TRANSACTION.evidence = labelledBy ? 'popup aria-labelledby opener' : 'active opener controls popup';
        return {owner:ownerAction, transaction:POPUP_TRANSACTION, popup:popup, evidence:POPUP_TRANSACTION.evidence};
      }
    }
    return {owner:null, transaction:null, popup:popup, evidence:'no active control-scoped popup transaction matched'};
  }

  function finishSelectionTransaction(tx) {
    if (!tx) return;
    setTimeout(function () {
      if (POPUP_TRANSACTION !== tx) return;
      var expanded = attr(tx.opener_element, 'aria-expanded');
      if (!visibleElement(tx.popup_element) || expanded === 'false') closePopupTransaction('popup closed after selection');
      else tx.state = 'OPEN';
    }, 120);
  }

  document.addEventListener('click', function (e) {
    var el = e.target;
    if (!el || el.nodeType !== 1) return;
    var notification = notificationContainerFor(el);
    if (notification) {
      el = actionableNotificationTarget(el, notification);
      if (!el) return;
    }
    if (isTextEntry(el) && !isDynamicTextControl(el)) return;
    var action = capturedAction(el, 'click');
    if (!action) return;
    if (action.field_type === 'dropdown' || action.field_type === 'autocomplete') { action.kind = 'open_dropdown'; action.expected_post_state = 'owning popup visible or aria-expanded=true'; }
    else if (action.field_type === 'calendar') { action.kind = 'open_calendar'; action.expected_post_state = 'owning calendar visible'; }
    else if (action.field_type === 'option') { action.kind = 'select_option'; action.expected_value = action.field_name; action.expected_post_state = 'owning field reflects value or popup closes'; }
    else if (action.field_type === 'date') { action.kind = 'select_date'; action.expected_value = action.field_name; action.expected_post_state = 'owning date field reflects value or calendar closes'; }
    if (['option','date'].indexOf(action.field_type) !== -1) {
      var ownership = provenOwnerFor(el), owner = ownership.owner;
      flushPendingDynamicFill(ownership.transaction);
      action.parent_locator = owner ? owner.locator : null;
      action.popup_locator = owner ? (owner.popup_locator || action.popup_locator) : action.popup_locator;
      if (!action.popup_locator && ownership.popup && ownership.popup.id) {
        action.popup_locator = {strategy:'id', value:ownership.popup.id};
      }
      action.control_context = owner ? (owner.control_context || action.control_context) : action.control_context;
      action.control_transaction = ownership.transaction ? ownership.transaction.id : null;
      action.ownership_diagnostic = {
        detected_opener: owner ? owner.locator : null,
        detected_popup: ownership.popup ? (ownership.popup.id || ownership.popup.getAttribute('role') || ownership.popup.className || '') : '',
        resolved_parent: action.parent_locator,
        evidence: ownership.evidence,
        accepted: !!owner
      };
      action.transaction_opener = action.parent_locator;
      action.transaction_popup = action.popup_locator || (
        ownership.popup ? {strategy:'text', value:(ownership.popup.getAttribute('role') || ownership.popup.className || ownership.popup.tagName || 'popup')} : null
      );
      action.ownership_evidence = ownership.evidence;
      action.ownership_accepted = !!owner;
      finishSelectionTransaction(ownership.transaction);
    }
    if (['dropdown','calendar','autocomplete','accordion'].indexOf(action.field_type) !== -1) {
      if (!beginPopupTransaction(action, el.closest('button,input,select,[role="combobox"]') || el)) return;
    }
    else if (['option','date'].indexOf(action.field_type) === -1 && !elementBelongsToTransaction(el, POPUP_TRANSACTION)) closePopupTransaction('unrelated actionable interaction');
    report(action);
  }, true);

  document.addEventListener('input', function (e) {
    var el = e.target;
    if (!el || el.nodeType !== 1 || !isTextEntry(el) || !isDynamicTextControl(el)) return;
    var tx = POPUP_TRANSACTION;
    if (!tx || tx.state !== 'OPEN' || !elementBelongsToTransaction(el, tx)) return;
    var fillAction = capturedAction(el, 'fill', {
      value: el.value, expected_value: el.value,
      expected_post_state: 'value equals entered value'
    });
    if (!fillAction) return;
    fillAction.control_transaction = tx.id;
    fillAction.parent_locator = tx.action.locator;
    fillAction.popup_locator = tx.action.popup_locator || fillAction.popup_locator;
    PENDING_DYNAMIC_FILL = {transaction: tx, action: fillAction};
  }, true);

  document.addEventListener('change', function (e) {
    var el = e.target;
    if (!el || el.nodeType !== 1) return;
    if (el.tagName === 'SELECT') {
      var selected = el.options[el.selectedIndex];
      report(capturedAction(el, 'select_option', {
        value: el.value, label: selected ? selected.text : el.value,
        expected_value: selected ? selected.text : el.value,
        expected_post_state: 'owning field reflects selected value',
      }));
      return;
    }
    if (el.tagName === 'INPUT' && ['checkbox', 'radio'].indexOf((el.getAttribute('type') || '').toLowerCase()) !== -1) {
      return; // already captured as a click
    }
    if (isTextEntry(el)) {
      if (isDynamicTextControl(el)) {
        // Kendo and similar widgets emit a late change event after an
        // option has already been selected. Genuine user search input is
        // captured by the input listener and flushed before that option.
        flushPendingDynamicFill(POPUP_TRANSACTION);
        return;
      }
      var fillAction = capturedAction(el, 'fill', {value: el.value, expected_value: el.value, expected_post_state: 'value equals entered value'});
      var belongsToPopup = POPUP_TRANSACTION && elementBelongsToTransaction(el, POPUP_TRANSACTION);
      if (belongsToPopup) {
        fillAction.control_transaction = POPUP_TRANSACTION.id;
        fillAction.parent_locator = POPUP_TRANSACTION.action.locator;
        fillAction.popup_locator = POPUP_TRANSACTION.action.popup_locator || fillAction.popup_locator;
      }
      if (fillAction && ['dropdown','autocomplete','calendar'].indexOf(fillAction.field_type) !== -1 &&
          !belongsToPopup) beginPopupTransaction(fillAction, el);
      else if (!belongsToPopup) closePopupTransaction('unrelated text interaction');
      report(fillAction);
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

        diagnostic = action.get("ownership_diagnostic") or {}
        if diagnostic:
            logger.info(
                "[recorder-ownership] transaction=%s accepted=%s opener=%s popup=%s evidence=%s",
                action.get("control_transaction") or "-",
                bool(diagnostic.get("accepted")),
                self._locator_display(diagnostic.get("detected_opener") or {}) or "-",
                str(diagnostic.get("detected_popup") or "-")[:120],
                str(diagnostic.get("evidence") or "-")[:160],
            )

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
        step_number = 1

        for action in self.actions:

            kind = action.get("kind")

            if kind == "goto":
                statement = f"page.goto({action['url']!r})"
                lines.extend(self._structured_step_lines(
                    step_number, action, statement,
                    field_type="navigation", field_name="Application URL",
                ))
                step_number += 1
                continue

            locator_src = self._locator_source(action.get("locator") or {})

            if locator_src is None:
                continue

            if kind in {"click", "open_dropdown", "open_calendar", "select_date"}:
                statement = f"{locator_src}.click()"
            elif kind == "fill":
                statement = f"{locator_src}.fill({action.get('value', '')!r})"
            elif kind == "select_option":
                statement = (
                    f"{locator_src}.select_option({action.get('value', '')!r})"
                    if action.get("tag") == "select"
                    else f"{locator_src}.click()"
                )
            else:
                continue
            lines.extend(self._structured_step_lines(step_number, action, statement))
            step_number += 1

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

    @classmethod
    def _structured_step_lines(
        cls, number, action, statement, field_type=None, field_name=None,
    ):
        kind = action.get("kind") or "execute"
        field_type = field_type or action.get("field_type") or (
            "dropdown" if kind == "select_option" else "button" if kind == "click" else "text"
        )
        field_name = field_name or action.get("field_name") or "Element"
        sensitive = field_type == "password" or bool(
            any(token in field_name.lower() for token in ("password", "passwd", "secret", "token"))
        )
        primary = (
            str(action.get("url") or "") if kind == "goto"
            else cls._locator_display(action.get("locator") or {})
        ) or "-"
        fallbacks = [
            cls._locator_display(locator) for locator in action.get("fallback_locators") or []
        ]
        fallbacks = [locator for locator in fallbacks if locator and locator != primary]
        xpath = action.get("xpath") or "-"
        description = (
            "Open application" if kind == "goto" else
            f"Enter {field_name}" if kind == "fill" else
            f"Select {field_name}" if kind == "select_option" else
            f"Select date {field_name}" if kind == "select_date" else
            f"Open {field_name}" if kind in {"open_dropdown", "open_calendar"} else
            f"Click {field_name}"
        )
        metadata = [
            metadata_comment("QA_STEP", f"{number:03d}"),
            metadata_comment("DESCRIPTION", description),
            metadata_comment("FIELD_TYPE", field_type),
            metadata_comment("FIELD_NAME", field_name),
            metadata_comment("SECTION", action.get("section")),
            metadata_comment("ACTION_TYPE", kind),
            metadata_comment("CONTROL_ROLE", action.get("control_role")),
            metadata_comment("CONTROL_CONTEXT", action.get("control_context")),
            metadata_comment("CONTROL_TRANSACTION", action.get("control_transaction")),
            metadata_comment("TRANSACTION_OPENER", cls._locator_display(action.get("transaction_opener") or {})),
            metadata_comment("TRANSACTION_POPUP", cls._locator_display(action.get("transaction_popup") or {})),
            metadata_comment("OWNERSHIP_EVIDENCE", action.get("ownership_evidence")),
            metadata_comment("OWNERSHIP_ACCEPTED", "true" if action.get("ownership_accepted") else "false"),
            metadata_comment("PARENT_LOCATOR", cls._locator_display(action.get("parent_locator") or {})),
            metadata_comment("POPUP_LOCATOR", cls._locator_display(action.get("popup_locator") or {})),
            metadata_comment("EXPECTED_VALUE", action.get("expected_value") or action.get("value")),
            metadata_comment("EXPECTED_POST_STATE", action.get("expected_post_state")),
            metadata_comment("PRIMARY_LOCATOR", primary),
            metadata_comment("FALLBACK_LOCATORS", "; ".join(fallbacks)),
            metadata_comment("XPATH", xpath),
            metadata_comment("SENSITIVE", "true" if sensitive else "false"),
            metadata_comment("SOURCE", "recorded"),
            statement,
        ]
        return ["    " + line for line in metadata]

    @staticmethod
    def _locator_display(locator):
        strategy = locator.get("strategy")
        if strategy == "role":
            return f"role={locator.get('role', '')}|name={locator.get('name', '')}"
        if strategy == "testid":
            return f"data-testid={locator.get('value', '')}"
        if strategy in {"label", "name", "placeholder", "title", "text"}:
            return f"{strategy}={locator.get('value', '')}"
        if strategy == "id":
            return "#" + str(locator.get("value") or "")
        if strategy == "xpath":
            return "xpath=" + str(locator.get("value") or "")
        return str(locator.get("value") or "")

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
        if strategy == "placeholder":
            return f"page.get_by_placeholder({locator['value']!r})"
        if strategy == "title":
            return f"page.get_by_title({locator['value']!r})"
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
                try:
                    ast.parse(script)
                except SyntaxError as ex:
                    raise RecordingError(
                        "Recorder generated invalid Python and did not save it: "
                        f"line {ex.lineno or '?'}: {ex.msg}. Captured actions remain in this recorder session."
                    ) from ex
                # The in-page recorder has already ranked/normalized candidates.
                # Refuse persistence if an executable step still crosses the same
                # structural/semantic gate used by Validate and replay repair.
                from Core.test_execution_manager import TestExecutionManager
                quality = TestExecutionManager.assess_playwright_script_quality(script)
                invalid = [
                    item for item in quality
                    if item.get("classification") == "Invalid"
                    or not item.get("semantic_valid", True)
                    or not item.get("parent_valid", True)
                    or not item.get("action_valid", True)
                ]
                if invalid:
                    details = "; ".join(
                        f"Step {item['step']}: " + next(
                            reason for reason in (
                                item.get("reason") if item.get("classification") == "Invalid" else "",
                                item.get("semantic_reason") if not item.get("semantic_valid", True) else "",
                                item.get("parent_reason") if not item.get("parent_valid", True) else "",
                                item.get("action_reason") if not item.get("action_valid", True) else "",
                            ) if reason
                        )
                        for item in invalid[:8]
                    )
                    raise RecordingError(
                        "Recorder did not save unsafe executable steps. " + details
                    )
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
