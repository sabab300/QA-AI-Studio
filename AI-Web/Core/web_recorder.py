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
# ROUND-7-DIAGNOSTIC-ONLY: datetime/Path are used only to name and write
# the optional recorder diagnostic log file (see
# WebRecordingSession._on_diagnostic() below) — nothing else in this
# module needed them before.
import datetime
from pathlib import Path

from Core.logger import Logger
from Core.test_case_repository import TestCaseRepository
from Core.playwright_step_metadata import metadata_comment
from Core.test_environment_config import TestEnvironmentConfig

logger = Logger.get_logger()

_SESSION_LOCK = threading.Lock()
_ACTIVE_SESSION = {"session": None}

# ROUND-7-DIAGNOSTIC-ONLY: sibling of playwright_runner.py's own
# OUTPUT_FOLDER (Output/AutomationRuns) — same path-construction
# pattern (Core/../Output/<name>), used only by
# WebRecordingSession._on_diagnostic() below to persist the optional,
# purely observational click-resolution trace as one JSON-lines file
# per recording session. Never read by anything that builds or
# executes a script; safe to delete at any time.
DIAGNOSTICS_OUTPUT_FOLDER = Path(__file__).resolve().parent.parent / "Output" / "RecorderDiagnostics"

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
    // Labelable elements (input/select/textarea/button/meter/output/
    // progress) expose the platform's OWN label-association algorithm
    // via `.labels`: a wrapping <label> is associated with only the ONE
    // control it actually labels (the first labelable descendant in
    // tree order) -- never with every element nested inside it. A naive
    // closest('label') walk gets this wrong: it would attribute a
    // shared field-group label's text (e.g. "Password*") to EVERY
    // control inside that wrapper, including an unrelated secondary
    // control such as a show/hide password toggle button that merely
    // happens to live in the same <label> as the password <input> it
    // sits beside. Using `.labels` is what keeps that toggle button
    // from silently inheriting the password field's own name.
    if ('labels' in el) {
      var owned = el.labels && el.labels.length ? el.labels[0].innerText.trim() : '';
      return owned || null;
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

  // Generic locator for SPA navigation items whose clickable React/Vue
  // wrapper is not a native <a>/<button>.  Scope the text to a real navigation
  // surface so duplicate dashboard-card labels do not collide with sidebar
  // entries (for example, the same feature name appearing in both places).
  function navigationContextXpathFor(el) {
    if (!el || el.nodeType !== 1) return null;
    var surface = el.closest && el.closest('nav,aside,[role="navigation"],[role="menu"],.sidebar,.side-menu,.sidenav,.navigation,.navbar,.nav,.menu,.list-group');
    if (!surface) return null;
    var text = (el.innerText || '').replace(/\s+/g, ' ').trim();
    if (!text || text.length > 120) return null;
    var surfacePredicate = 'self::nav or self::aside or @role="navigation" or @role="menu"'
      + ' or contains(concat(" ",normalize-space(@class)," ")," sidebar ")'
      + ' or contains(concat(" ",normalize-space(@class)," ")," side-menu ")'
      + ' or contains(concat(" ",normalize-space(@class)," ")," sidenav ")'
      + ' or contains(concat(" ",normalize-space(@class)," ")," navigation ")'
      + ' or contains(concat(" ",normalize-space(@class)," ")," navbar ")'
      + ' or contains(concat(" ",normalize-space(@class)," ")," nav ")'
      + ' or contains(concat(" ",normalize-space(@class)," ")," menu ")'
      + ' or contains(concat(" ",normalize-space(@class)," ")," list-group ")';
    var tag = el.tagName.toLowerCase();
    return '//*[' + surfacePredicate + ']//' + tag + '[normalize-space(.)=' + xpathLiteral(text) + ']';
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
  function locatorMatchCount(locator, root) {
    // ROUND-8-OWNER-SCOPED-INTERACTION-TARGET: optional `root` scopes the
    // match to a container element instead of the whole document. Every
    // pre-existing call site passes exactly one argument, so `root` is
    // always undefined there and `base` falls back to `document` --
    // behavior for every existing caller is completely unchanged.
    try {
      var base = root || document;
      var nodes = [];
      if (locator.strategy === 'testid') nodes = base.querySelectorAll('[data-testid="' + CSS.escape(locator.value) + '"]');
      else if (locator.strategy === 'id') nodes = base.querySelectorAll('#' + CSS.escape(locator.value));
      else if (locator.strategy === 'name') nodes = base.querySelectorAll('[name="' + CSS.escape(locator.value) + '"]');
      else if (locator.strategy === 'placeholder') nodes = base.querySelectorAll('[placeholder="' + CSS.escape(locator.value) + '"]');
      else if (locator.strategy === 'title') nodes = base.querySelectorAll('[title="' + CSS.escape(locator.value) + '"]');
      else if (locator.strategy === 'css' || locator.strategy === 'knowledge_hub') nodes = base.querySelectorAll(locator.value);
      else if (locator.strategy === 'xpath') return document.evaluate(locator.value, base, null, XPathResult.ORDERED_NODE_SNAPSHOT_TYPE, null).snapshotLength;
      else if (locator.strategy === 'label') nodes = Array.prototype.filter.call(base.querySelectorAll('input,select,textarea'), function(node) { return labelFor(node) === locator.value; });
      else if (locator.strategy === 'role') nodes = Array.prototype.filter.call(base.querySelectorAll('button,a,input,select,textarea,[role]'), function(node) { return (attr(node, 'role') || implicitRole(node)) === locator.role && accessibleName(node) === locator.name; });
      else if (locator.strategy === 'text') nodes = Array.prototype.filter.call(base.querySelectorAll('button,a,span,li,td,label'), function(node) { return (node.innerText || '').trim() === locator.value; });
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
    var navigationXpath = navigationContextXpathFor(el);
    if (navigationXpath) {
      var navigationContext = {strategy:'xpath', value:navigationXpath};
      if (locatorMatchCount(navigationContext) === 1) return navigationContext;
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

  // Generic vocabulary for a masked-field visibility toggle -- common,
  // framework-agnostic icon-library/class tokens (Font Awesome, Bootstrap
  // Icons, Material Icons, and plain hand-written markup all converge on
  // these words). Deliberately NOT tied to any application, organization,
  // or literal field name -- it recognizes the UI PATTERN ("a nameless
  // button that shows/hides a masked value"), never a specific product's
  // wording.
  var TOGGLE_VOCAB = /\b(eye|eye-slash|visib(?:le|ility)|invisible|show|hide|reveal|unmask)\b/i;

  // A nameless BUTTON that sits beside a masked (type=password) field is
  // very likely that field's show/hide toggle -- but only ever claim that
  // identity when there is real, inspectable evidence for it (icon
  // semantics and/or a recognizable class token), exactly mirroring what
  // a sighted operator would use to recognize the control. When no such
  // evidence exists, return '' so the caller falls through to the
  // existing safe generic fallback ('Element') rather than inventing a
  // name nothing on the page actually supports.
  function adjunctToggleName(control, container) {
    var scope = container || control.closest('.k-form-field,.form-group,.field,[class*="field"],div') || control.parentElement;
    if (!scope) return '';
    var maskedInput = scope.querySelector('input[type="password"]');
    if (!maskedInput) return '';
    var icon = control.querySelector('i,svg,[class*="icon"]');
    var evidence = [
      control.className, control.getAttribute('aria-pressed') || '',
      icon ? (icon.getAttribute('class') || '') : '',
      icon ? (icon.getAttribute('aria-label') || icon.getAttribute('title') || '') : '',
    ].join(' ');
    if (!TOGGLE_VOCAB.test(evidence)) return '';
    // Parent password-field context: use the masked input's OWN corrected
    // accessible name (never the button's), so the composed identity
    // generalizes to whatever that field is actually called (e.g.
    // "Confirm Password visibility toggle") instead of a hard-coded
    // literal.
    var fieldName = accessibleName(maskedInput);
    return (fieldName ? fieldName + ' ' : '') + 'visibility toggle';
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
      // A shared field-group label (e.g. "Password*") describes the
      // group's PRIMARY form control, not every control nested inside
      // it. Only borrow it here when there is no other input/select/
      // textarea in the group, or when `control` itself already IS
      // that primary control -- otherwise a secondary control sharing
      // the group (a show/hide password toggle button, a clear-field
      // button, etc.) would silently inherit the field's own name, the
      // same over-attribution bug labelFor() above guards against for
      // the implicit-<label> case.
      var primaryControl = container && container.querySelector('input,select,textarea');
      if (!primaryControl || primaryControl === control) {
        var containerLabel = container && container.querySelector('label,.k-label,[class*="label"]');
        name = containerLabel ? (containerLabel.innerText || '').trim() : '';
      }
    }
    if (!name && control.tagName === 'BUTTON') {
      name = adjunctToggleName(control, container);
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
    var navigationXpath = navigationContextXpathFor(el);
    if (navigationXpath) add({strategy:'xpath', value:navigationXpath});
    var contextualXpath = contextualXpathFor(el);
    if (contextualXpath) add({strategy:'xpath', value:contextualXpath});
    var fieldXpath = fieldContextXpathFor(el);
    if (fieldXpath) add({strategy:'xpath', value:fieldXpath});
    if (cssInfo.path && cssInfo.fragileCount === 0 && !isLayoutDependentCss(cssInfo.path) && cssInfo.path.length <= 180) add({strategy:'css', value:cssInfo.path});
    return {locator: primary, fallback_locators: fallbacks, xpath: xpathFor(el)};
  }

  var LAST_POINTERDOWN = null;

  function recordPointerDown(e) {
    if (e && e.target && e.target.nodeType === 1) LAST_POINTERDOWN = {target: e.target, at: Date.now()};
  }
  // A genuine user click always has a real down-gesture that precedes it
  // (mousedown/pointerdown -> mouseup -> click, in that order, for the SAME
  // interaction) -- capture it on `document` in the capture phase so it is
  // observed before any application code can react to it. Two listeners
  // (pointerdown, mousedown) cover both pointer-event-capable and legacy
  // environments; whichever fires first for a given gesture wins.
  try { document.addEventListener('pointerdown', recordPointerDown, true); } catch (e) {}
  try { document.addEventListener('mousedown', recordPointerDown, true); } catch (e) {}

  function pointerdownCorroborates(node) {
    // Causality, not a timing debounce: does not delay or suppress anything,
    // it only asks whether the down-gesture that produced the CURRENT click
    // actually began on this exact node (or one of its descendants). A
    // framework container that merely happens to occupy the space where the
    // pointer landed -- because a preceding action's DOM transition moved,
    // removed, or is still animating something out from under the user's
    // real target -- will fail this check whenever the down-gesture began
    // somewhere else (e.g. on the element that was subsequently replaced).
    // No signal ever captured (e.g. a keyboard-triggered activation) is not
    // treated as contradicting evidence.
    if (!LAST_POINTERDOWN || !LAST_POINTERDOWN.target) return true;
    return node === LAST_POINTERDOWN.target || (node.contains && node.contains(LAST_POINTERDOWN.target));
  }

  function isLargeStructuralSurface(node) {
    // A generic, framework-agnostic signal that `node` is a full-page/large
    // structural surface (dialog wrapper, modal backdrop, overlay, popup
    // container, animation container, layout shell) rather than a compact,
    // purpose-built control. Real custom SPA controls (menu items, cards,
    // sidebar links) are always comparatively small/bounded on screen; a
    // dialog-centering wrapper or backdrop is characteristically large
    // relative to the viewport regardless of which UI framework produced
    // it or what class names/text it carries. Intentionally NOT based on
    // any class name, tag name, or application-specific text.
    try {
      var rect = node.getBoundingClientRect();
      if (!rect || rect.width <= 0 || rect.height <= 0) return false;
      var vw = window.innerWidth || document.documentElement.clientWidth || 1;
      var vh = window.innerHeight || document.documentElement.clientHeight || 1;
      return rect.width >= vw * 0.7 && rect.height >= vh * 0.7;
    } catch (e) { return false; }
  }

  function closestCustomClickable(el) {
    // Many SPAs implement side-menu/cards as DIV/LI/SPAN nodes with a
    // framework click handler instead of a native <a>/<button>.  Those are
    // real user actions and must not disappear from the recorded journey.
    // Keep this bounded and require visible click affordance/evidence; the
    // locator layer below still requires a UNIQUE stable/relative locator.
    var node = el, depth = 0;
    while (node && node !== document.body && depth < 7) {
      var role = attr(node, 'role') || '';
      var tabIndex = attr(node, 'tabindex');
      var cls = safeClassName(node);
      // Navigation frameworks often attach the click handler to a React/Vue
      // ancestor (so node.onclick is empty) and some Bootstrap sidebars do not
      // expose cursor:pointer on the exact text node.  Treat a compact item
      // inside an explicit navigation surface as real click evidence too.
      var navigationSurface = node.closest && node.closest(
        'nav,aside,[role=\"navigation\"],[role=\"menu\"],.sidebar,.side-menu,.sidenav,.navigation,.navbar,.nav,.menu,.list-group'
      );
      var navigationClass = /(?:^|\s)(?:menu-item|menu-link|nav-item|nav-link|sidebar-item|sidebar-link|sidenav-item|sidenav-link|list-group-item|tree-item|treeitem)(?:\s|$)/i.test(cls);
      // Strong evidence: an explicit interactive semantic (ARIA role) or
      // membership in an explicit navigation surface/class. This is a
      // deliberate design choice by whoever built the app and is never
      // second-guessed by geometry or gesture correlation below.
      var strongEvidence = ['button','link','menuitem','tab','treeitem'].indexOf(role) !== -1
        || navigationClass
        || !!navigationSurface;
      // Weak evidence: an incidental affordance (delegated onclick, a
      // focus-trap/roving tabindex, a backdrop-dismiss cursor) that
      // structural framework containers -- dialog wrappers, overlays,
      // backdrops -- very commonly carry for reasons that have nothing to
      // do with the user having targeted THAT container specifically.
      var weakEvidence = !!node.onclick
        || (tabIndex !== null && tabIndex !== '-1')
        || getComputedStyle(node).cursor === 'pointer';
      var clickEvidence = strongEvidence || weakEvidence;
      if (weakEvidence && !strongEvidence && (isLargeStructuralSurface(node) || !pointerdownCorroborates(node))) {
        // Weak-only evidence on a large structural surface, or evidence not
        // corroborated by the gesture that actually produced this click,
        // never promotes the container itself into the clickable target.
        // It may still be a real click ON one of its actual children --
        // that child is discovered on an earlier/later iteration of this
        // same ancestor walk, or via the semantic-leaf path elsewhere.
        clickEvidence = false;
      }
      var text = (node.innerText || '').replace(/\s+/g, ' ').trim();
      // A navigation container can contain many labels. Never record that
      // container as one action; require a compact leaf/item-like candidate.
      var compactNavigationItem = !navigationSurface || navigationClass || role || node.tagName === 'LI' || node.tagName === 'SPAN' || text.length <= 60;
      if (clickEvidence && compactNavigationItem && text && text.length <= 120) return node;
      node = node.parentElement;
      depth++;
    }
    return null;
  }

  // ROUND-6-SVG-SAFE-CLASS: Element.className is a plain string on an
  // HTMLElement but an SVGAnimatedString object on an SVGElement --
  // String(svgEl.className) yields "[object SVGAnimatedString]", not the
  // actual class list, so any `.test(el.className || '')` regex silently
  // never matches an SVG-rendered icon/glyph. getAttribute('class')
  // returns the raw attribute text correctly for BOTH HTMLElement and
  // SVGElement, so it is the generic, framework-agnostic safe source of
  // truth (already the pattern this file uses for icon class evidence in
  // fieldMetadata() above -- this centralizes it for reuse here too).
  function safeClassName(node) {
    if (!node) return '';
    if (typeof node.getAttribute === 'function') {
      var viaAttribute = node.getAttribute('class');
      if (viaAttribute != null) return viaAttribute;
    }
    var viaProperty = node.className;
    return (typeof viaProperty === 'string') ? viaProperty : '';
  }

  // ROUND-6-INTERACTION-TARGET-COVERAGE: the same generic vocabulary
  // Round 5 used to decide whether a click physically landed on a
  // composite widget's own trigger icon -- generalized into a single,
  // reusable "does this physically-clicked element look like something a
  // person could deliberately have aimed at" test, framework-agnostic
  // (no Kendo-only literals beyond the pre-existing class vocabulary
  // already used elsewhere in this file). Used only to GATE whether a
  // physical interaction target is worth preserving, never to decide
  // WHICH element becomes the logical `target` -- that resolution logic
  // in capturedAction() below is completely unchanged by this.
  var TRIGGER_SIGNATURE_CLASS = /(?:k-icon|k-svg-icon|k-select|k-input-button)/i;
  var TRIGGER_SIGNATURE_TAGS = ['svg', 'use', 'path', 'i', 'img', 'button', 'a', 'input'];
  var TRIGGER_SIGNATURE_ROLES = ['button', 'link', 'checkbox', 'switch'];
  function looksLikeInteractionTrigger(node) {
    if (!node || node.nodeType !== 1) return false;
    var tag = (node.tagName || '').toLowerCase();
    if (TRIGGER_SIGNATURE_TAGS.indexOf(tag) !== -1) return true;
    var role = attr(node, 'role') || '';
    if (TRIGGER_SIGNATURE_ROLES.indexOf(role) !== -1) return true;
    return TRIGGER_SIGNATURE_CLASS.test(safeClassName(node));
  }

  // ROUND-6-INTERACTION-TARGET-COVERAGE: centralizes the
  // preserve-the-physical-click-target decision so every redirect
  // pathway in capturedAction() shares one implementation instead of
  // duplicating computeLocator()/validation logic per branch. Returns a
  // locator for `el` only when ALL of these hold -- never merely because
  // `target !== el`:
  //   - a redirect actually happened (`logicalTarget` differs from `el`)
  //   - `el` itself is a recognizable, meaningful trigger/affordance
  //     (looksLikeInteractionTrigger) -- not incidental bubbling from a
  //     plain wrapper div, whitespace, or text node
  //   - computeLocator(el) can find a STABLE, safely unique locator for
  //     it -- computeLocator() already refuses positional/coordinate/
  //     generated-dynamic-ID selectors and ambiguous bare-class
  //     selectors on its own, so those guarantees are inherited for free
  // Callers are responsible for excluding contexts where a physical
  // target must never be recorded as opener/trigger metadata at all
  // (option/date-cell selection -- see capturedAction() below).
  function preserveInteractionTarget(el, logicalTarget) {
    if (!logicalTarget || logicalTarget === el) return null;
    if (!looksLikeInteractionTrigger(el)) return null;
    return computeLocator(el);
  }

  // ROUND-8-OWNER-SCOPED-INTERACTION-TARGET --------------------------
  // preserveInteractionTarget() above only ever succeeds when the
  // physical trigger has a locator that is safely unique across the
  // WHOLE page. Some real, meaningful triggers (e.g. a composite
  // widget's own empty dropdown-arrow icon) never can be -- there may
  // be many visually-identical arrows on the same page, one per
  // instance of the same widget. Such a trigger still has an unambiguous
  // identity if resolved RELATIVE TO the specific widget instance that
  // owns it -- the same widget instance whose logical control (the
  // `logicalTarget`/owner) already has its own stable, page-wide-unique
  // locator. This block adds exactly that fallback: only ever attempted
  // by capturedAction() below when preserveInteractionTarget() already
  // returned null, never instead of it, never weakening
  // computeLocator()'s own global safety rules for any other caller.
  //
  // Generic, framework-agnostic: COMPOSITE_SCOPE_SELECTOR reuses only
  // the same widget-class vocabulary already used elsewhere in this
  // file (the icon/widget branch of capturedAction(), fieldMetadata()'s
  // widgetClass detection) plus standard ARIA composite-widget roles --
  // no PSW-, field-, or product-specific literal (no consignmentCategory,
  // no k-select as a stand-alone rule, etc).
  var COMPOSITE_SCOPE_SELECTOR = '[role="combobox"],[role="listbox"],[role="grid"],[role="dialog"],[role="menu"],.k-dropdown,.k-dropdownlist,.k-combobox,.k-autocomplete,.k-datepicker,.k-picker,.k-widget';

  // Walks UP from the logical owner's OWN parent (deliberately not from
  // ownerEl itself, so the owner's own composite wrapper -- one level up
  // -- is what gets matched, not some ancestor of that) looking for the
  // nearest composite/widget container that contains BOTH the owner and
  // the physical trigger. Bounded depth (10) / stops at document.body,
  // exactly like diagComposedPathSnapshot()'s own bounded ancestor walk
  // below, so this can never run away up a deep/unusual DOM.
  function findCompositeScope(ownerEl, triggerEl) {
    if (!ownerEl) return null;
    var node = ownerEl.parentElement;
    var depth = 0;
    while (node && node !== document.body && depth < 10) {
      if (node.matches && node.matches(COMPOSITE_SCOPE_SELECTOR) &&
          node.contains(ownerEl) && (!triggerEl || node.contains(triggerEl))) {
        return node;
      }
      node = node.parentElement;
      depth += 1;
    }
    return null;
  }

  // Mirrors computeLocator()'s own strategy cascade and priority order,
  // but validates uniqueness with locatorMatchCount(locator, scope) --
  // relative to the composite scope, not the whole document -- and adds
  // two scope-only fallback strategies (a full class-list selector, then
  // a bare-tag selector) that computeLocator() itself never returns,
  // because at document scope a bare class or tag selector is routinely
  // ambiguous. Safe here only because uniqueness is proven against the
  // specific composite-widget instance that was independently proven
  // (by findCompositeScope()'s own `.contains()` checks) to hold both
  // the owner and this trigger.
  function computeScopedLocator(scope, el) {
    if (!scope || !el) return null;
    for (var i = 0; i < 4; i++) {
      var v = attr(el, ['data-testid', 'data-test', 'data-qa', 'data-cy'][i]);
      if (v) {
        var testLocator = { strategy: 'testid', value: v };
        if (locatorMatchCount(testLocator, scope) === 1) return testLocator;
      }
    }
    if (el.id && !looksDynamic(el.id)) {
      var stableIdLocator = { strategy: 'id', value: el.id };
      if (locatorMatchCount(stableIdLocator, scope) === 1) return stableIdLocator;
    }
    if (el.name && !looksDynamic(el.name)) {
      var nameLocator = { strategy: 'name', value: el.name };
      if (locatorMatchCount(nameLocator, scope) === 1) return nameLocator;
    }
    var label = labelFor(el);
    if (label) {
      var labelLocator = { strategy: 'label', value: label };
      if (locatorMatchCount(labelLocator, scope) === 1) return labelLocator;
    }
    var role = attr(el, 'role') || implicitRole(el);
    var name = accessibleName(el);
    if (role && name) {
      var roleLocator = { strategy: 'role', role: role, name: name };
      if (locatorMatchCount(roleLocator, scope) === 1) return roleLocator;
    }
    var placeholder = attr(el, 'placeholder');
    if (placeholder) {
      var placeholderLocator = { strategy: 'placeholder', value: placeholder };
      if (locatorMatchCount(placeholderLocator, scope) === 1) return placeholderLocator;
    }
    var title = attr(el, 'title');
    if (title) {
      var titleLocator = { strategy: 'title', value: title };
      if (locatorMatchCount(titleLocator, scope) === 1) return titleLocator;
    }
    var text = (el.innerText || '').trim();
    if (text && text.length > 0 && text.length <= 60 &&
        ['BUTTON', 'A', 'SPAN', 'LI', 'TD', 'LABEL'].indexOf(el.tagName) !== -1) {
      var textLocator = { strategy: 'text', value: text };
      if (locatorMatchCount(textLocator, scope) === 1) return textLocator;
    }
    // Scope-only fallbacks -- never reachable from the unmodified global
    // computeLocator(), which never returns a bare class/tag selector.
    var classList = (safeClassName(el) || '').trim().split(/\s+/).filter(function(c) { return c && !looksDynamic(c); });
    if (classList.length) {
      var classSelector = '.' + classList.map(function(c) { return CSS.escape(c); }).join('.');
      var classLocator = { strategy: 'css', value: classSelector };
      if (locatorMatchCount(classLocator, scope) === 1) return classLocator;
    }
    var tagSelector = (el.tagName || '').toLowerCase();
    if (tagSelector) {
      var tagLocator = { strategy: 'css', value: tagSelector };
      if (locatorMatchCount(tagLocator, scope) === 1) return tagLocator;
    }
    return null;
  }

  // Only ever called by capturedAction() (and, read-only, by the Round 7
  // diagnostic mirror below) after preserveInteractionTarget() already
  // returned null for the same (el, logicalTarget) pair. Same entry
  // gates as preserveInteractionTarget() (a redirect actually happened;
  // `el` is a recognizable trigger), plus: a composite scope containing
  // both owner and trigger must exist, the trigger must resolve to
  // exactly one element within it, AND the owner itself must still have
  // its own safe global locator (recomputed here, never assumed) -- so
  // an owner-scoped result can never be persisted without a way to
  // re-find that same owner again at replay time.
  function resolveOwnerScopedInteractionTarget(el, logicalTarget) {
    if (!logicalTarget || logicalTarget === el) return null;
    if (!looksLikeInteractionTrigger(el)) return null;
    var scope = findCompositeScope(logicalTarget, el);
    if (!scope) return null;
    var relative = computeScopedLocator(scope, el);
    if (!relative) return null;
    var ownerLocator = computeLocator(logicalTarget);
    if (!ownerLocator) return null;
    return { relative: relative, owner: ownerLocator, scope: scope };
  }
  // ROUND-8-OWNER-SCOPED-INTERACTION-TARGET (end, recorder algorithm) --

  // ROUND-7-DIAGNOSTIC-ONLY -----------------------------------------
  // Everything in this block is purely observational: read-only DOM
  // inspection plus a console.debug() and an optional Python-bridge
  // emission (window.__qa_record_diagnostic, wired only when the
  // recorder host supports it -- see WebRecordingSession._on_diagnostic()
  // in this module's Python half). Nothing here is called from, feeds
  // into, or changes the return value of target selection,
  // computeLocator(), preserveInteractionTarget(), locator generation,
  // replay, or post-action verification. It exists to make the
  // ALREADY-EXISTING recorder decision (which resolution branch fired,
  // whether interaction_target was PRESERVED/REJECTED and exactly why)
  // visible for one live diagnostic pass. Never persisted into
  // recorded_script/the generated Playwright harness; never touches
  // filled/typed field values.
  var DIAG_ANCESTOR_DEPTH = 6;
  var DIAG_HTML_MAX_LEN = 220;

  function diagRedactedOuterHTML(el) {
    try {
      var clone = el.cloneNode(false); // shallow: no children/text ever copied
      if (clone.hasAttribute && clone.hasAttribute('value')) clone.setAttribute('value', '[REDACTED]');
      if ('value' in clone) { try { clone.value = ''; } catch (e) {} }
      var html = clone.outerHTML || '';
      return html.length > DIAG_HTML_MAX_LEN ? html.slice(0, DIAG_HTML_MAX_LEN) + '…[truncated]' : html;
    } catch (e) {
      return '';
    }
  }

  function diagRawTargetSnapshot(el) {
    if (!el || el.nodeType !== 1) return null;
    return {
      tag: (el.tagName || '').toLowerCase(),
      id: el.id || '',
      class: safeClassName(el),
      role: attr(el, 'role') || '',
      type: attr(el, 'type') || '',
      aria_haspopup: attr(el, 'aria-haspopup') || '',
      aria_expanded: attr(el, 'aria-expanded') || '',
      outer_html: diagRedactedOuterHTML(el),
    };
  }

  function diagComposedPathSnapshot(el, maxDepth) {
    var path = [];
    var node = el ? el.parentElement : null;
    var depth = 0;
    while (node && depth < maxDepth) {
      path.push({tag: (node.tagName || '').toLowerCase(), id: node.id || '', class: safeClassName(node), role: attr(node, 'role') || ''});
      node = node.parentElement;
      depth += 1;
    }
    return path;
  }

  // Read-only MIRROR of preserveInteractionTarget()'s own decision --
  // same inputs, same helper functions (looksLikeInteractionTrigger's
  // own tag/role/class checks, computeLocator), same predicates, in
  // the same order -- used only to explain, field by field, the
  // decision the real function already made a few lines away.
  // preserveInteractionTarget() itself is not called, wrapped, or
  // modified by this; it remains exactly as Round 6 left it.
  function diagInteractionTargetDecision(el, logicalTarget, excludedAsOptionOrDate) {
    if (excludedAsOptionOrDate) {
      return {logical_equals_el: logicalTarget === el, looks_like_trigger: null, trigger_evidence: null, computed_locator: null, decision: 'REJECTED', reason: 'option_or_date_excluded'};
    }
    if (!logicalTarget || logicalTarget === el) {
      return {logical_equals_el: true, looks_like_trigger: null, trigger_evidence: null, computed_locator: null, decision: 'REJECTED', reason: 'same_as_logical_target'};
    }
    var tag = (el.tagName || '').toLowerCase();
    var role = attr(el, 'role') || '';
    var cls = safeClassName(el);
    var evidence = {
      tag_match: TRIGGER_SIGNATURE_TAGS.indexOf(tag) !== -1,
      role_match: TRIGGER_SIGNATURE_ROLES.indexOf(role) !== -1,
      class_match: TRIGGER_SIGNATURE_CLASS.test(cls),
      tag: tag, role: role, class_value: cls,
    };
    if (!(evidence.tag_match || evidence.role_match || evidence.class_match)) {
      return {logical_equals_el: false, looks_like_trigger: false, trigger_evidence: evidence, computed_locator: null, decision: 'REJECTED', reason: 'not_trigger_signature'};
    }
    var locator = null;
    try { locator = computeLocator(el); } catch (e) { locator = null; }
    if (locator) {
      return {logical_equals_el: false, looks_like_trigger: true, trigger_evidence: evidence, computed_locator: locator, decision: 'PRESERVED', reason: null};
    }
    // ROUND-8-OWNER-SCOPED-INTERACTION-TARGET (diagnostic mirror only):
    // the global path (computeLocator(el) above, mirroring
    // preserveInteractionTarget()) has already failed with
    // locator_not_safe_or_unique. Read-only mirror of
    // resolveOwnerScopedInteractionTarget()'s own logic -- does not call,
    // wrap, or influence it -- purely to explain, field by field, whether
    // an owner-scoped resolution would additionally succeed.
    var ownerScoped = {
      global_locator_attempt: null, owner_locator: null,
      composite_scope_evidence: null, relative_selector: null,
      scoped_match_count: 0, scoped_unique: false,
    };
    try {
      var scope = findCompositeScope(logicalTarget, el);
      if (scope) {
        ownerScoped.composite_scope_evidence = {
          tag: (scope.tagName || '').toLowerCase(), id: scope.id || '',
          class: safeClassName(scope), role: attr(scope, 'role') || '',
        };
        var relative = computeScopedLocator(scope, el);
        if (relative) {
          ownerScoped.relative_selector = relative;
          ownerScoped.scoped_match_count = locatorMatchCount(relative, scope);
          ownerScoped.scoped_unique = ownerScoped.scoped_match_count === 1;
        }
        var ownerLocatorAttempt = null;
        try { ownerLocatorAttempt = computeLocator(logicalTarget); } catch (e) { ownerLocatorAttempt = null; }
        ownerScoped.owner_locator = ownerLocatorAttempt;
      }
    } catch (e) {
      /* diagnostic must never throw */
    }
    if (ownerScoped.relative_selector && ownerScoped.scoped_unique && ownerScoped.owner_locator) {
      return {
        logical_equals_el: false, looks_like_trigger: true, trigger_evidence: evidence,
        computed_locator: null, decision: 'PRESERVED_OWNER_SCOPED', reason: null,
        owner_scoped: ownerScoped,
      };
    }
    return {
      logical_equals_el: false, looks_like_trigger: true, trigger_evidence: evidence,
      computed_locator: null, decision: 'REJECTED', reason: 'locator_not_safe_or_unique',
      owner_scoped: ownerScoped,
    };
  }

  function emitRecorderDiagnostic(payload) {
    try {
      var wrapped = Object.assign({url: String(location.href), at: Date.now()}, payload);
      console.debug('[QA-RECORDER-DIAG]', JSON.stringify(wrapped));
      if (window.__qa_record_diagnostic) window.__qa_record_diagnostic(JSON.stringify(wrapped));
    } catch (e) {
      /* diagnostic emission must never break recording */
    }
  }
  // ROUND-7-DIAGNOSTIC-ONLY (end) -------------------------------------

  function capturedAction(el, kind, extra) {
    var target = null;
    // A composite widget (combobox, dropdown, date picker, ...) can have a
    // real, physical opening gesture (an arrow/select icon, a calendar
    // trigger button) that is a DIFFERENT DOM node from the semantic
    // control that owns validation/ownership/next-step identity (the
    // underlying input/[role=combobox]). Historically this function
    // collapsed the two into one -- redirecting `target` away from `el`
    // and discarding `el` entirely. That is correct for VALIDATION/
    // OWNERSHIP purposes (the logical control must stay the semantic
    // owner), but it silently threw away the one thing that told replay
    // WHERE the human's trusted gesture actually landed. When a redirect
    // like that happens, `interactionTarget` preserves `el`'s own stable
    // locator (computeLocator() already refuses to return anything that
    // isn't uniquely resolvable on the live page -- never a bare,
    // possibly-ambiguous class like a lone icon selector) alongside the
    // logical `target`, so BOTH identities survive into the recorded
    // step: the semantic owner (unchanged contract) and the actual
    // interaction surface (new, optional, additive).
    //
    // ROUND-6: this preservation now applies uniformly to EVERY pathway
    // below that can redirect `target` away from `el` -- the composite-
    // widget icon case (this file's original, narrower Round 5 coverage),
    // the qualifying-ancestor fallback, closestCustomClickable(), and the
    // single-actionable-descendant fallback -- not only the first one.
    // The single `preserveInteractionTarget()` call near the end of this
    // function (after `target` is finally settled) covers all of them at
    // once, so none of the individual branches below need their own
    // preservation logic any more. Option/date-cell SELECTION (the very
    // first branch) is the one deliberate exception: choosing a value
    // from an already-open popup is a different kind of action and must
    // never be reinterpreted as this control's own opener/trigger
    // metadata, so that branch is excluded from preservation entirely.
    var interactionTarget = null;
    // ROUND-7-DIAGNOSTIC-ONLY: read-only trace of which branch below
    // resolves `target`, captured for click actions only (mirrors
    // interaction_target's own scope). Never read by, and never
    // influences, any of the actual resolution logic that follows.
    var __diagBranch = null;
    var __diagRaw = (kind === 'click') ? diagRawTargetSnapshot(el) : null;
    var __diagPath = (kind === 'click') ? diagComposedPathSnapshot(el, DIAG_ANCESTOR_DEPTH) : null;
    var option = el.closest('[role="option"],option,.k-list-item,.k-item');
    var dateCell = el.closest('[role="gridcell"],.k-calendar-td,.k-calendar td');
    if (option) target = option;
    else if (dateCell) target = dateCell;
    else if (TRIGGER_SIGNATURE_CLASS.test(safeClassName(el))) {
      var widget = el.closest('.k-dropdown,.k-dropdownlist,.k-combobox,.k-autocomplete,.k-datepicker,.k-picker,[role="combobox"]');
      if (widget) {
        target = /(?:k-datepicker|k-picker)/i.test(safeClassName(widget))
          ? (el.closest('button,[role="button"]') || widget.querySelector('input[aria-controls],input[aria-owns],button,[role="button"],input') || widget)
          : (widget.querySelector('input,[role="combobox"],button') || widget);
      }
    }
    if (kind === 'click' && !__diagBranch && (option || dateCell)) __diagBranch = option ? 'option' : 'date-cell';
    if (kind === 'click' && !__diagBranch && target) __diagBranch = 'icon/widget';
    if (!target) target = el.closest('button,a,input,select,textarea,[role="button"],[role="combobox"],[role="link"],[role="menuitem"],[role="tab"]');
    if (kind === 'click' && !__diagBranch && target) __diagBranch = (target === el) ? 'direct/no-redirect' : 'qualifying-ancestor';
    if (!target && kind === 'click') target = closestCustomClickable(el);
    if (kind === 'click' && !__diagBranch && target) __diagBranch = (target === el) ? 'direct/no-redirect' : 'closestCustomClickable';
    if (!target && el.querySelectorAll && (
        getComputedStyle(el).cursor === 'pointer' ||
        /(?:k-input|k-picker|k-dropdown|k-combobox|input-group|button-wrap)/i.test(safeClassName(el))
    )) {
      var actionableChildren = el.querySelectorAll('button,a[href],input,select,textarea,[role="button"],[role="combobox"],[role="link"]');
      if (actionableChildren.length === 1) target = actionableChildren[0];
    }
    if (kind === 'click' && !__diagBranch && target) __diagBranch = 'single-actionable-descendant';
    if (kind === 'click' && !target) {
      emitRecorderDiagnostic({
        raw: __diagRaw, composed_path: __diagPath, resolution_branch: 'unresolved',
        logical_target_locator: null, interaction_target_decision: null,
      });
    }
    if (!target) return null;
    var interactionTargetScope = null;
    if (!option && !dateCell) {
      interactionTarget = preserveInteractionTarget(el, target);
      if (interactionTarget) {
        interactionTargetScope = 'global';
      } else {
        // ROUND-8-OWNER-SCOPED-INTERACTION-TARGET: only attempted when
        // the existing global path just above already returned null --
        // never instead of it, never weakening computeLocator()'s own
        // global uniqueness rules for the direct/global case.
        var ownerScopedResult = resolveOwnerScopedInteractionTarget(el, target);
        if (ownerScopedResult) {
          interactionTarget = ownerScopedResult.relative;
          interactionTargetScope = 'owner';
        }
      }
    }
    if (kind === 'click') {
      var __diagLogicalLocator = null;
      try { __diagLogicalLocator = computeLocator(target); } catch (e) { __diagLogicalLocator = null; }
      var __diagDecision = diagInteractionTargetDecision(el, target, !!(option || dateCell));
      emitRecorderDiagnostic({
        raw: __diagRaw, composed_path: __diagPath,
        resolution_branch: __diagBranch || (target === el ? 'direct/no-redirect' : 'qualifying-ancestor'),
        logical_target_locator: __diagLogicalLocator,
        interaction_target_decision: __diagDecision,
      });
    }
    var targetRole = attr(target, 'role') || implicitRole(target) || '';
    var semanticLeaf = ['button','link','combobox','option','gridcell','menuitem','tab'].indexOf(targetRole) !== -1;
    var customClickable = kind === 'click' && closestCustomClickable(target) === target;
    if (!semanticLeaf && !customClickable && (/^(DIV|SPAN|FORM|FIELDSET|SECTION|DIALOG)$/.test(target.tagName) ||
        /(?:k-header|k-dialog|k-popup|wrapper|container|(?:^|\s)(?:row|col(?:-|\s)))/i.test(safeClassName(target)))) return null;
    var result = Object.assign({kind:kind}, locatorMetadata(target), fieldMetadata(target, kind), extra || {});
    if (kind === 'click' && /(?:k-icon|k-svg-icon|k-input-button)/i.test(safeClassName(el)) && el.closest('.k-datepicker,.k-picker')) {
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
    if (interactionTarget) {
      result.interaction_target = interactionTarget;
      if (interactionTargetScope) result.interaction_target_scope = interactionTargetScope;
    }
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
      return false;
    }
    var signature = JSON.stringify({
      kind: action.kind, locator: action.locator,
      value: action.kind === 'fill' ? action.value : action.expected_value
    });
    var now = Date.now();
    if (LAST_REPORTED && LAST_REPORTED.signature === signature && now - LAST_REPORTED.at < 750) return true;
    LAST_REPORTED = {signature: signature, at: now};
    if (window.__qa_record_action) {
      window.__qa_record_action(JSON.stringify(action));
      return true;
    }
    return false;
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

  // Outcome-confirmed fallback for SPA navigation.
  // Capture first, classify second: React/Vue applications frequently attach
  // navigation to generic wrappers that have no native role/href and may be
  // removed immediately after the click.  Snapshot the composedPath while the
  // old DOM still exists, then report the best unique locator only if the app
  // actually changes route through URL/history.  No application-specific text
  // or CSS class is required.
  var HISTORY_ROUTE_VERSION = 0;

  // ROUND-9-EVENT-DRIVEN-NAVIGATION-CAPTURE ---------------------------------
  // PROVEN DEFECT (Round 9 audit): the previous implementation reconciled a
  // navigation-fallback candidate against HISTORY_ROUTE_VERSION/location.href
  // by SAMPLING them on a fixed ~700ms poll (8 checks, 100ms apart). The
  // route signals themselves were already event-driven (pushState/
  // replaceState wrapped below, plus popstate/hashchange) -- only the
  // consumption of that signal was time-bounded, so a route transition that
  // completed after the poll's last check was permanently missed even though
  // the underlying signal fired correctly. The live evidence for this: a
  // controlled reproduction proved the fallback candidate itself (computed
  // by routeSnapshotAction(), unchanged below) is safe and unique -- it is
  // only the WHEN of reconciliation that was fragile.
  //
  // This replaces the polling deadline with a single pending navigation
  // gesture that is reconciled REACTIVELY, exactly when a route signal
  // actually fires -- never by sampling a clock. One recorder observes one
  // human operator's actions, which happen sequentially, never
  // concurrently, so a single slot is sufficient: a second eligible click
  // before the first gesture's route settles simply supersedes it (see
  // createPendingNavGesture()), mirroring how a real operator's clicks
  // happen one at a time. A long stale-cleanup timer exists purely for
  // memory/state hygiene (a click that never leads anywhere should not pin
  // state forever) and is NEVER the mechanism that decides whether a
  // navigation is legitimate -- only an actual route signal does that.
  var PENDING_NAV_GESTURE = null;
  var NAV_GESTURE_SEQ = 0;
  var NAV_GESTURE_STALE_CLEANUP_MS = 20000; // hygiene only -- see note above

  function navGestureDiag(eventName, gesture, extra) {
    emitRecorderDiagnostic(Object.assign({
      kind: 'navigation_transaction',
      event: eventName,
      gesture_id: gesture ? gesture.id : null,
    }, extra || {}));
  }

  function clearNavGestureStaleTimer(gesture) {
    if (gesture && gesture._staleTimer) {
      clearTimeout(gesture._staleTimer);
      gesture._staleTimer = null;
    }
  }

  function supersedeNavGesture(gesture, reason) {
    if (!gesture || gesture.state !== 'PENDING') return;
    gesture.state = 'SUPERSEDED';
    clearNavGestureStaleTimer(gesture);
    navGestureDiag('navigation_transaction_superseded', gesture, {reason: reason || 'superseded'});
  }

  function completeNavGesture(gesture, finalState, extra) {
    if (!gesture) return;
    gesture.state = finalState;
    clearNavGestureStaleTimer(gesture);
    navGestureDiag('navigation_transaction_completed', gesture, Object.assign({final_state: finalState}, extra || {}));
    if (PENDING_NAV_GESTURE === gesture) PENDING_NAV_GESTURE = null;
  }

  // Creates the single pending navigation gesture for THIS click, if (and
  // only if) routeSnapshotAction() found an eligible fallback candidate for
  // it. A click with no eligible candidate (routeFallback === null) never
  // creates or touches a transaction at all -- it must not be able to
  // supersede/cancel a real gesture already pending from an earlier click
  // whose async navigation simply hasn't resolved yet (item 5 of the Round
  // 9 spec: only a NEWER ELIGIBLE gesture supersedes an older one).
  function createPendingNavGesture(routeFallback, beforeUrl, beforeHistoryVersion) {
    if (!routeFallback) return null;
    if (PENDING_NAV_GESTURE && PENDING_NAV_GESTURE.state === 'PENDING') {
      supersedeNavGesture(PENDING_NAV_GESTURE, 'new_click');
    }
    NAV_GESTURE_SEQ += 1;
    var gesture = {
      id: NAV_GESTURE_SEQ,
      routeFallback: routeFallback,
      beforeUrl: beforeUrl,
      beforeHistoryVersion: beforeHistoryVersion,
      createdAt: Date.now(),
      state: 'PENDING',
      _staleTimer: null,
    };
    gesture._staleTimer = setTimeout(function () {
      // Stale-state hygiene ONLY (see note above the state block). Never
      // suppresses a route signal that has not happened yet by this point --
      // reconcileNavGestureOnRouteSignal() always runs first whenever a
      // signal genuinely fires; this only releases a gesture whose click
      // never led anywhere.
      if (gesture.state === 'PENDING') completeNavGesture(gesture, 'STALE');
    }, NAV_GESTURE_STALE_CLEANUP_MS);
    PENDING_NAV_GESTURE = gesture;
    navGestureDiag('navigation_transaction_created', gesture, {
      candidate_locator: (gesture.routeFallback && gesture.routeFallback.locator) || null,
    });
    return gesture;
  }

  // Reconciles the single pending gesture (if any) against a route signal
  // that just fired for real -- this IS the correctness mechanism (replacing
  // the old fixed-window poll), called directly from the pushState/
  // replaceState/popstate/hashchange wiring below, never from a timer.
  function reconcileNavGestureOnRouteSignal(signalName) {
    var gesture = PENDING_NAV_GESTURE;
    if (!gesture || gesture.state !== 'PENDING') return;
    var urlChanged = String(location.href) !== String(gesture.beforeUrl);
    var historyChanged = HISTORY_ROUTE_VERSION > gesture.beforeHistoryVersion;
    navGestureDiag('navigation_route_signal', gesture, {
      signal: signalName, url_changed: urlChanged, history_changed: historyChanged,
    });
    if (!urlChanged && !historyChanged) return; // this signal didn't move the route (e.g. redundant replaceState)
    var action = gesture.routeFallback;
    action.navigation_outcome = urlChanged ? 'url_changed' : 'history_changed';
    action.destination_url = String(location.href);
    var reported = report(action);
    navGestureDiag('navigation_fallback_reported', gesture, {reported: !!reported});
    completeNavGesture(gesture, reported ? 'REPORTED_FALLBACK' : 'REPORT_FAILED');
  }
  // ROUND-9-EVENT-DRIVEN-NAVIGATION-CAPTURE (state/helpers end) ------------

  // Observe SPA routing without changing application behaviour. Unchanged
  // signal sources from before Round 9 (pushState/replaceState wrapping,
  // popstate, hashchange) -- Round 9 only adds the reconciliation call so a
  // pending gesture is settled the moment one of these actually fires,
  // instead of waiting for the next poll tick.
  try {
    ['pushState', 'replaceState'].forEach(function (method) {
      var original = history[method];
      if (!original || original.__qaRecorderWrapped) return;
      var wrapped = function () {
        var result = original.apply(this, arguments);
        HISTORY_ROUTE_VERSION += 1;
        try { reconcileNavGestureOnRouteSignal(method); } catch (e) {}
        return result;
      };
      wrapped.__qaRecorderWrapped = true;
      history[method] = wrapped;
    });
    window.addEventListener('popstate', function () {
      HISTORY_ROUTE_VERSION += 1;
      try { reconcileNavGestureOnRouteSignal('popstate'); } catch (e) {}
    }, true);
    window.addEventListener('hashchange', function () {
      HISTORY_ROUTE_VERSION += 1;
      try { reconcileNavGestureOnRouteSignal('hashchange'); } catch (e) {}
    }, true);
  } catch (e) {}

  function exactPathLocator(el) {
    if (!el || el.nodeType !== 1) return null;
    var parts = [], node = el, depth = 0;
    while (node && node.nodeType === 1 && node !== document.body && depth < 9) {
      if (node.id && !looksDynamic(node.id)) {
        parts.unshift('#' + CSS.escape(node.id));
        break;
      }
      var testid = attr(node, 'data-testid') || attr(node, 'data-test') || attr(node, 'data-cy') || attr(node, 'data-qa');
      if (testid && !looksDynamic(testid)) {
        var testAttr = attr(node, 'data-testid') ? 'data-testid' : attr(node, 'data-test') ? 'data-test' : attr(node, 'data-cy') ? 'data-cy' : 'data-qa';
        parts.unshift('[' + testAttr + '="' + String(testid).replace(/"/g, '\\"') + '"]');
        break;
      }
      var tag = node.tagName.toLowerCase();
      var segment = tag;
      var stable = Array.prototype.filter.call(node.classList || [], function (c) {
        return !looksDynamic(c) && !isGenericFrameworkClass(c) && /^[A-Za-z_-][A-Za-z0-9_-]*$/.test(c);
      }).slice(0, 2);
      if (stable.length) segment += '.' + stable.map(function(c){ return CSS.escape(c); }).join('.');
      var parent = node.parentElement;
      if (parent) {
        var peers = Array.prototype.filter.call(parent.children, function (x) { return x.tagName === node.tagName; });
        if (peers.length > 1) segment += ':nth-of-type(' + (peers.indexOf(node) + 1) + ')';
      }
      parts.unshift(segment);
      node = parent;
      depth += 1;
    }
    var css = parts.join(' > ');
    if (!css) return null;
    try {
      if (document.querySelectorAll(css).length === 1) return {strategy:'css', value:css};
    } catch (e) {}
    return null;
  }

  function routeSnapshotAction(event) {
    var path = event.composedPath ? event.composedPath() : [];
    if (!path || !path.length) {
      var n = event.target;
      while (n && n.nodeType === 1 && path.length < 12) { path.push(n); n = n.parentElement; }
    }
    var candidates = [];
    for (var i = 0; i < path.length && i < 12; i++) {
      var node = path[i];
      if (!node || node.nodeType !== 1 || node === document.body || node === document.documentElement) continue;
      var text = (accessibleName(node) || node.innerText || node.textContent || '').replace(/\s+/g, ' ').trim();
      if (!text || text.length > 140) continue;
      var meta = locatorMetadata(node);
      if (!meta.locator) {
        // Route snapshots must obey the same persistence-quality contract as
        // ordinary recorder actions.  Prefer a semantic RELATIVE XPath that
        // is unique at click time; never rescue a navigation with an absolute
        // / deeply positional CSS path merely because it happens to be unique.
        // The old exactPathLocator() fallback was the reason a correctly
        // captured SPA navigation could make Stop Recording fail validation.
        var routeXpath = xpathFor(node);
        if (routeXpath && locatorMatchCount({strategy:'xpath', value:routeXpath}) === 1) {
          meta = Object.assign(meta || {}, {
            locator: {strategy:'xpath', value:routeXpath},
            xpath: routeXpath
          });
        }
      }
      if (!meta.locator) continue;
      var role = attr(node, 'role') || implicitRole(node);
      var semanticLeaf = ['button','link','combobox','option','gridcell','menuitem','tab'].indexOf(role) !== -1;
      var customClickable = closestCustomClickable(node) === node;
      var knownInteractiveTag = ['A','BUTTON','LI','INPUT','SELECT','TEXTAREA'].indexOf(node.tagName) !== -1;
      // A route-changed page can expose ANY ancestor in the click's
      // composedPath -- legend, fieldset, section, a plain div wrapper,
      // a heading -- with a unique locator and some visible text once
      // the DOM has settled. None of those are genuine actionable
      // targets on their own, so (mirroring capturedAction()'s own
      // semanticLeaf/customClickable gate for an ordinary click)
      // candidacy here requires the SAME positive proof: a known
      // interactive tag, a real interactive ARIA role, or a proven
      // custom-clickable node. Anything else -- including a FIELDSET/
      // legend newly exposed by a preceding Confirm-click transition --
      // is never eligible, closing off exactly the gap that let a
      // structural container be promoted to a phantom "Click <container
      // text>" navigation step.
      if (!semanticLeaf && !customClickable && !knownInteractiveTag) continue;
      var score = 0;
      if (node === event.target) score += 30;
      if (['A','BUTTON','LI'].indexOf(node.tagName) !== -1) score += 20;
      if (role && ['link','button','menuitem','tab','treeitem'].indexOf(role) !== -1) score += 25;
      if (attr(node, 'onclick') || attr(node, 'tabindex')) score += 15;
      try { if (getComputedStyle(node).cursor === 'pointer') score += 15; } catch (e) {}
      // Prefer compact semantic leaves over wrappers containing an entire menu.
      if (text.length <= 60) score += 20;
      candidates.push({node:node, meta:meta, text:text, role:role, score:score});
    }
    if (!candidates.length) return null;
    candidates.sort(function(a,b){ return b.score - a.score; });
    var best = candidates[0];
    var action = Object.assign({kind:'click'}, best.meta, fieldMetadata(best.node, 'click'));
    action.field_name = action.field_name || best.text;
    action.field_type = 'navigation';
    action.control_role = best.role || 'navigation-item';
    action.control_context = sectionFor(best.node).name || 'Application navigation';
    action.expected_post_state = 'application route changes';
    action.capture_evidence = 'composedPath snapshot; route outcome required';
    return action;
  }

  // ROUND-9-EVENT-DRIVEN-NAVIGATION-CAPTURE: confirmRouteSnapshot()'s fixed
  // ~700ms poll (8 attempts, 100ms apart) previously decided WHETHER a route
  // change counted -- removed. Reconciliation is now performed by
  // reconcileNavGestureOnRouteSignal() (defined above, near
  // PENDING_NAV_GESTURE), called directly from the pushState/replaceState/
  // popstate/hashchange wiring the moment one of those actually fires. The
  // pending-gesture bookkeeping this replaces confirmRouteSnapshot's
  // `captureState` parameter with is created below, immediately after
  // routeSnapshotAction(), exactly where confirmRouteSnapshot used to be
  // armed.

  document.addEventListener('click', function (e) {
    var el = e.target;
    if (!el || el.nodeType !== 1) return;
    var notification = notificationContainerFor(el);
    if (notification) {
      el = actionableNotificationTarget(el, notification);
      if (!el) return;
    }
    if (isTextEntry(el) && !isDynamicTextControl(el)) return;
    var beforeUrl = String(location.href);
    var beforeHistoryVersion = HISTORY_ROUTE_VERSION;
    var routeFallback = routeSnapshotAction(e);
    var action = capturedAction(el, 'click');
    // Create (or supersede-and-replace) the single pending navigation
    // gesture right after capturedAction() resolves -- still well before
    // the normal path's own report() call, and (same reasoning either way)
    // still guaranteed to complete before the application's own click/
    // router handling begins: this listener is registered on `document` in
    // the CAPTURE phase by an init script that always runs before the
    // page's own scripts, so this entire synchronous function body always
    // finishes before any pushState/popstate/hashchange this click
    // eventually triggers, however delayed. capturedAction() runs first
    // (not after) purely so its own click-resolution diagnostic keeps
    // being the first diagnostic emitted for this click -- preserving
    // Round 6/7's diagnostic-ordering contract unchanged; it has no
    // bearing on transaction correctness either way. Returns null (no
    // transaction) when routeSnapshotAction() found no eligible candidate
    // at all -- see createPendingNavGesture()'s own comment on why that
    // must never supersede an already-pending gesture from an earlier
    // click.
    var navGesture = createPendingNavGesture(routeFallback, beforeUrl, beforeHistoryVersion);
    if (!action || !action.locator) {
      if (navGesture) navGestureDiag('normal_action_rejected', navGesture, {});
      return;
    }
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
    var reportedNormally = report(action);
    if (navGesture) {
      navGestureDiag(reportedNormally ? 'normal_action_reported' : 'normal_action_rejected', navGesture, {});
      // Only a SUCCESSFUL normal report completes/clears the gesture. A
      // normal action that reached this far but was still rejected by
      // report()'s own dedup (extremely unlikely here, since the dedup key
      // includes this exact locator+kind and this is the first click on it)
      // must leave the gesture PENDING so a later route signal can still
      // rescue the navigation, mirroring the old captureState.reported
      // semantics exactly.
      if (reportedNormally) completeNavGesture(navGesture, 'REPORTED_NORMAL');
    }
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
        # ROUND-7-DIAGNOSTIC-ONLY: purely observational click-resolution
        # traces from RECORDER_JS's emitRecorderDiagnostic() (see
        # _on_diagnostic() below). Never read by build_script() / never
        # persisted into recorded_script — a separate channel from
        # self.actions.
        self.diagnostics = []
        self._diagnostics_log_path = None
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

        # ROUND-7-DIAGNOSTIC-ONLY: a second, separate bridge for the
        # read-only click-resolution trace RECORDER_JS's
        # emitRecorderDiagnostic() sends (see capturedAction() in
        # RECORDER_JS and _on_diagnostic() below). Entirely additive —
        # __qa_record_action above, and everything it feeds
        # (self.actions / recorded_script / the generated harness), is
        # untouched by this binding's presence.
        await self._context.expose_binding(
            "__qa_record_diagnostic",
            lambda source, raw: self._on_diagnostic(raw),
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

    def _on_diagnostic(self, raw):
        """
        ROUND-7-DIAGNOSTIC-ONLY: receives the read-only click-resolution
        trace RECORDER_JS's emitRecorderDiagnostic() sends over the
        __qa_record_diagnostic binding (added this round, alongside the
        pre-existing __qa_record_action binding wired right above it in
        start_impl()). Never touches self.actions, never influences
        build_script() / recorded_script / the generated Playwright
        harness — purely observational. Appended in-memory to
        self.diagnostics (for programmatic inspection while the session
        is still alive) and appended as one JSON line to a per-session
        log file under DIAGNOSTICS_OUTPUT_FOLDER, so it can be read back
        after the recording session has already ended without needing
        live browser access. Any failure here (bad JSON, disk I/O) is
        swallowed — this must never be able to interrupt recording.
        """

        try:
            entry = json.loads(raw)
        except Exception:
            return

        entry["_test_case_id"] = self.test_case_id
        entry["_received_at"] = datetime.datetime.utcnow().isoformat() + "Z"

        self.diagnostics.append(entry)

        try:
            if self._diagnostics_log_path is None:
                DIAGNOSTICS_OUTPUT_FOLDER.mkdir(parents=True, exist_ok=True)
                token = datetime.datetime.now().strftime("%Y%m%d_%H%M%S%f")
                self._diagnostics_log_path = (
                    DIAGNOSTICS_OUTPUT_FOLDER
                    / f"{self.test_case_id}_{token}_recorder_diagnostics.jsonl"
                )

            with open(self._diagnostics_log_path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry) + "\n")

        except Exception:
            logger.exception(
                "[web-recorder] could not write recorder diagnostic log "
                "entry (non-fatal — instrumentation only, recording "
                "continues normally)."
            )

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
            # The logical/semantic control (PRIMARY_LOCATOR, above) stays
            # the owner for validation, ownership and next-step identity,
            # unchanged. INTERACTION_TARGET is additive and optional: only
            # present when the real recorded gesture landed on a distinct,
            # stable, uniquely-resolvable physical surface belonging to
            # that control (e.g. a composite widget's own trigger icon) --
            # see capturedAction() in the recorder. Absent ("-") for every
            # simple control and for every script recorded before this
            # field existed, so replay must treat it as optional.
            metadata_comment(
                "INTERACTION_TARGET",
                cls._locator_display(action.get("interaction_target") or {}),
            ),
            # ROUND-8-OWNER-SCOPED-INTERACTION-TARGET: disambiguates how
            # replay must resolve INTERACTION_TARGET above -- never by
            # reinterpreting that string's own format (its serialization
            # via _locator_display() is completely unchanged), always via
            # this separate, explicit field. "global" (or absent/"-" for
            # every script recorded before this field existed) means the
            # existing Round 5/6 behavior: resolve INTERACTION_TARGET as
            # a page-wide-unique locator, exactly as before. "owner" means
            # INTERACTION_TARGET is instead a locator relative to the
            # composite/widget scope owned by PRIMARY_LOCATOR, and must be
            # re-resolved within that scope, never against the whole page.
            metadata_comment(
                "INTERACTION_TARGET_SCOPE",
                action.get("interaction_target_scope") or "-",
            ),
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
