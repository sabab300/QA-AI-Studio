# Create: AI/Core/playwright_runner.py

"""
QA AI Studio
Playwright Runner

Version: 1.0

Actually executes a generated Playwright script in a real browser.

Runs it as a SEPARATE PROCESS (not exec() inside the app) so:
    - A hung or crashed script can't freeze QA AI Studio
    - A timeout can forcibly kill it
    - stdout/stderr are captured cleanly for the result dialog

Requires (not in requirements.txt by default — add these):
    pip install playwright
    playwright install chromium

Safety note: this runs whatever script text you pass it, in a real
browser, against whatever URL is inside that script. Review the
script (View Script) before running it, especially before pointing
it at a real/production PSW environment rather than a test/UAT one.
"""

import ast
import json
import os
import re
import signal
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path

from Core.logger import Logger
from Core.test_environment_config import TestEnvironmentConfig


# BUGFIX (shared Core defect, also present on Desktop): this used to
# be the bare relative path Path("Output") / "AutomationRuns", which
# resolves against the PROCESS'S CURRENT WORKING DIRECTORY — the exact
# same class of bug Database/db_manager.py's DatabaseManager already
# had to fix for metadata.db (see its own comment). A web server can be
# launched from the repo root, from AI-Web/, or via a process manager
# with an unrelated CWD; each would silently create/read a DIFFERENT
# "Output/AutomationRuns" folder, making past run scripts "disappear"
# after a restart from a different directory. Anchored to this file's
# own location instead, exactly like GIT_WORKSPACE_ROOT in
# automation_web_repository.py already does.
#
# IMPORTANT for whoever runs the Web server locally: this folder is
# written to (a brand-new *.py file) on every single Playwright
# execution. If it ever sits inside a directory that uvicorn's
# --reload file watcher is watching, each execution triggers uvicorn
# to see a "source change" and restart the whole server mid-run,
# silently killing the run it just started — surfacing later as
# "Interrupted: the server restarted while this run was in progress."
# This is NOT a crash; it's a self-inflicted restart loop. Always
# launch the Web server via Web/../run_web.py (AI-Web/run_web.py),
# which explicitly restricts the reload watcher to Core/, Web/,
# Database/, and Config/ so this folder is never watched. See
# run_web.py's own module docstring for the full diagnosis.
OUTPUT_FOLDER = Path(__file__).resolve().parent.parent / "Output" / "AutomationRuns"

# WEB PORT ADDITION: where failure screenshots (see EVIDENCE_SHIM_TEMPLATE
# below) are written. Same anchoring reasoning as OUTPUT_FOLDER above.
EVIDENCE_FOLDER = Path(__file__).resolve().parent.parent / "Output" / "Evidence"

DEFAULT_TIMEOUT_SECONDS = 120

# Fallback values used whenever Test Environment Settings' Playback
# Speed / Default Timeout fields are blank or invalid — chosen so
# that scripts are noticeably more forgiving than Playwright's own
# out-of-the-box pace (slow_mo=0, timeout=30000) BY DEFAULT, without
# requiring anyone to discover and configure this first. This is the
# direct fix for "Playwright speed is too fast, test cases fail
# multiple times": every action gets an artificial pause, and
# Playwright is told to wait longer before giving up on an element
# or a navigation.
DEFAULT_SLOW_MO_MS = 300

DEFAULT_ACTION_TIMEOUT_MS = 45000

# Prepended to every script right before it's written to disk and
# run — NEVER stored back into the test case's automation_script or
# recorded_script in the database, so View Script always shows the
# clean script you actually wrote/recorded/generated. Monkeypatches
# Playwright's own launch()/new_page() so this applies uniformly
# whether the script came from AI generation or Manual Recording,
# and however it was hand-edited afterward — no regex-parsing of the
# script's own text required, and changing the Test Environment
# Settings values takes effect on the NEXT run without needing to
# regenerate or re-record anything.
SPEED_SHIM_TEMPLATE = '''# --- QA AI Studio: speed/timeout safety shim (auto-inserted at run time, not saved) ---
# Slows every action down by {slow_mo}ms and raises the default
# wait timeout to {timeout}ms so a real application that renders or
# responds slower than Playwright's default pace doesn't cause
# intermittent failures. Adjust in QA Automation -> Test Environment
# Settings.
from playwright.sync_api import BrowserType as _QA_BrowserType
from playwright.sync_api import Browser as _QA_Browser
from playwright.sync_api import BrowserContext as _QA_BrowserContext

_QA_ORIGINAL_LAUNCH = _QA_BrowserType.launch


def _qa_launch_with_speed_settings(self, **kwargs):
    kwargs.setdefault("slow_mo", {slow_mo})
    return _QA_ORIGINAL_LAUNCH(self, **kwargs)


_QA_BrowserType.launch = _qa_launch_with_speed_settings


def _qa_apply_timeout(page):
    page.set_default_timeout({timeout})
    page.set_default_navigation_timeout({timeout})
    return page


# BUGFIX (found in Part 2 runtime testing): every script this app
# generates or records calls browser.new_page() — a distinct method
# on Browser itself (it creates an implicit context AND the page in
# one call), NOT BrowserContext.new_page() (only used for a second+
# page inside a context you created yourself). Patching only
# BrowserContext.new_page, as this shim originally did, meant the
# per-page timeout below NEVER actually applied to a single script
# produced by this app — silently. Both are patched now so this
# actually applies regardless of which call shape a script uses.
_QA_ORIGINAL_BROWSER_NEW_PAGE = _QA_Browser.new_page


def _qa_browser_new_page_with_timeout(self, *args, **kwargs):
    return _qa_apply_timeout(_QA_ORIGINAL_BROWSER_NEW_PAGE(self, *args, **kwargs))


_QA_Browser.new_page = _qa_browser_new_page_with_timeout

_QA_ORIGINAL_NEW_PAGE = _QA_BrowserContext.new_page


def _qa_new_page_with_timeout(self, *args, **kwargs):
    return _qa_apply_timeout(_QA_ORIGINAL_NEW_PAGE(self, *args, **kwargs))


_QA_BrowserContext.new_page = _qa_new_page_with_timeout
# --- end QA AI Studio shim ---

'''

# WEB PORT ADDITION (not in the desktop app — see PlaywrightRunner's
# force_headless docstring above). Prepended BEFORE SPEED_SHIM_TEMPLATE
# only when running server-side, so a script written/generated with
# headless=False (correct for a desktop operator watching a real
# window) still runs on a server with no display attached.
HEADLESS_SHIM_TEMPLATE = '''# --- QA AI Studio: web server headless shim (auto-inserted at run time, not saved) ---
from playwright.sync_api import BrowserType as _QA_BrowserType_H

_QA_ORIGINAL_LAUNCH_H = _QA_BrowserType_H.launch


def _qa_launch_headless(self, **kwargs):
    kwargs["headless"] = True
    return _QA_ORIGINAL_LAUNCH_H(self, **kwargs)


_QA_BrowserType_H.launch = _qa_launch_headless
# --- end QA AI Studio web headless shim ---

'''

# WEB PORT ADDITION: real failure evidence. The desktop app never
# captures a screenshot on failure (confirmed by reading
# App/UI/QAAutomation/test_execution_page.py — a failed run only ever
# surfaces truncated stderr text). For a server-side/headless run
# nobody is watching live, so a screenshot of the page at the moment
# of failure is the difference between "FAILED" and an actionable
# reason why. This chains onto SPEED_SHIM_TEMPLATE's own
# BrowserContext.new_page patch (this shim's text is always placed
# AFTER it — see run_script()) rather than replacing it, so both the
# timeout-setting AND the page-tracking behaviour apply to every page.
# Only fires for an UNCAUGHT exception reaching the interpreter (a
# normal assert failure or unhandled error in run() does exactly
# this) — never runs for a script that passes, so passing runs never
# get an unused screenshot file. Not used by run_script_interactive(),
# which already surfaces failures live to the operator via its own
# step-by-step event protocol.
# The screenshot path is substituted via a plain string .replace(),
# never str.format() — same reasoning as INTERACTIVE_HARNESS_TEMPLATE
# below: repr() is the only safe way to embed an arbitrary filesystem
# path (Windows backslashes, spaces, drive letters) as a Python string
# literal without hand-rolled escaping bugs.
EVIDENCE_SHIM_TEMPLATE = '''# --- QA AI Studio: failure screenshot shim (auto-inserted at run time, not saved) ---
import sys as _qa_sys_e

from playwright.sync_api import Browser as _QA_Browser_E
from playwright.sync_api import BrowserContext as _QA_BrowserContext_E
from playwright.sync_api import PlaywrightContextManager as _QA_PwCtxMgr_E

_QA_LAST_PAGE_E = {"page": None, "captured": False}


def _qa_track_e(page):
    _QA_LAST_PAGE_E["page"] = page
    return page


# Same fix as SPEED_SHIM_TEMPLATE's: browser.new_page() (what every
# generated/recorded script actually calls) is a Browser method, not
# a BrowserContext one — both are patched so page-tracking for the
# failure screenshot actually fires regardless of call shape.
_QA_ORIGINAL_BROWSER_NEW_PAGE_E = _QA_Browser_E.new_page


def _qa_browser_new_page_track_e(self, *args, **kwargs):
    return _qa_track_e(_QA_ORIGINAL_BROWSER_NEW_PAGE_E(self, *args, **kwargs))


_QA_Browser_E.new_page = _qa_browser_new_page_track_e

_QA_ORIGINAL_NEW_PAGE_E = _QA_BrowserContext_E.new_page


def _qa_new_page_track_e(self, *args, **kwargs):
    return _qa_track_e(_QA_ORIGINAL_NEW_PAGE_E(self, *args, **kwargs))


_QA_BrowserContext_E.new_page = _qa_new_page_track_e


def _qa_capture_e():
    if _QA_LAST_PAGE_E["captured"]:
        return
    page = _QA_LAST_PAGE_E.get("page")
    if page is not None:
        try:
            page.screenshot(path=__SCREENSHOT_PATH_REPR__, full_page=True)
            print("QA_EVIDENCE_SCREENSHOT::" + __SCREENSHOT_PATH_REPR__, flush=True)
            _QA_LAST_PAGE_E["captured"] = True
        except Exception:
            pass


# BUGFIX (found in Part 2 runtime testing): every real script this app
# produces (AI-generated and Manually Recorded alike) uses
# "with sync_playwright() as playwright: ...". When a step fails, the
# exception propagates out of that `with` block BEFORE it ever reaches
# sys.excepthook — and the context manager's own __exit__ calls
# playwright.stop(), which tears down the browser connection first.
# By the time the excepthook below ran, the page was already gone, so
# page.screenshot() silently failed every single time for this (the
# overwhelmingly common) script shape — evidence capture never
# actually fired in practice. Capturing here, on the way OUT of the
# `with` block but before the original __exit__ tears anything down,
# is what actually gets a real screenshot for a real failure.
_QA_ORIGINAL_PW_EXIT_E = _QA_PwCtxMgr_E.__exit__


def _qa_pw_exit_capture_e(self, exc_type, exc_value, exc_tb):
    if exc_type is not None:
        _qa_capture_e()
    return _QA_ORIGINAL_PW_EXIT_E(self, exc_type, exc_value, exc_tb)


_QA_PwCtxMgr_E.__exit__ = _qa_pw_exit_capture_e


def _qa_excepthook_e(exc_type, exc_value, exc_tb):
    # Fallback for scripts that don't use the "with sync_playwright()"
    # form (e.g. a bare start()/stop()) — the __exit__ patch above
    # already handles the common shape by the time this normally runs.
    _qa_capture_e()
    _qa_sys_e.__excepthook__(exc_type, exc_value, exc_tb)


_qa_sys_e.excepthook = _qa_excepthook_e
# --- end QA AI Studio failure screenshot shim ---

'''

# Interactive execution — the direct fix for "Playwright gets stuck
# on a Locator/element/value with no way to correct it and keep
# going": instead of the whole script running to completion (or
# failure) unattended, EVERY step runs through _qa_run_step() below,
# which on failure sends a JSON "step_failed" event on stdout (the
# failing code, its best-guess Locator/value, the error, and a
# snapshot of the real page's accessibility tree) and then BLOCKS
# reading a JSON command back on stdin. QA AI Studio (running this
# subprocess from a background thread — see
# App/UI/QAAutomation/test_execution_worker.py's
# PlaywrightInteractiveWorker) shows the operator a repair dialog,
# collects a corrected Locator/value (or, for cases a locator fix
# alone can't solve — like a dropdown needing select_option()
# instead of fill() — a fully custom replacement statement), and
# sends back one of:
#     {"action": "retry", "locator": "...", "value": "..."}
#     {"action": "retry_code", "code": "..."}
#     {"action": "cancel"}
# If the corrected step succeeds, execution continues from exactly
# where it left off and the fix is reported back as a "step_repaired"
# event so QA AI Studio can offer to save it into the stored script
# (see TestExecutionManager.apply_script_repairs()) — future runs
# then use the corrected line directly, without needing to repair it
# again. This subprocess NEVER decides on its own whether to ask the
# AI for a suggestion — that decision, and the AI call itself, are
# made entirely by QA AI Studio between a manual retry failing and a
# second "step_failed" event being sent for the same step; this
# script only ever executes whatever command it's told.
#
# Uses simple placeholder substitution (__MAX_REPAIR_ROUNDS__), not
# str.format(), because the JSON dict literals below are full of
# literal { } characters that .format() would otherwise try to
# interpret as fields.
INTERACTIVE_HARNESS_TEMPLATE = r'''# --- QA AI Studio: interactive step runner (auto-inserted at run time, not saved) ---
import json
import os
import re
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

_QA_MAX_REPAIR_ROUNDS = __MAX_REPAIR_ROUNDS__
_QA_SELECTION_TIMEOUT_MS = __SELECTION_TIMEOUT_MS__
_QA_TOTAL_STEPS = __TOTAL_STEPS__

_QA_REPAIRS = []
_QA_SELECTIONS = {}
_QA_LAST_DYNAMIC_CONTROL = None


def _qa_safe_action(code_text):
    """Mask likely secrets before emitting an action to browser UI/logs."""
    sensitive = re.search(r"(?i)(password|passwd|token|authorization|secret|api[_-]?key)", code_text or "")
    if not sensitive:
        return code_text
    return re.sub(
        r"(\.fill\(\s*)(['\"]).*?\2",
        r"\1'******'",
        code_text,
        count=1,
    )


def _qa_step_details(code_text, metadata=None):
    locator = _qa_extract_locator_and_value(code_text)[0]
    xpath = ""
    id_match = re.fullmatch(r"#([A-Za-z_][A-Za-z0-9_-]*)", locator or "")
    if id_match:
        xpath = '//*[@id="' + id_match.group(1) + '"]'
    elif locator and locator.startswith("//"):
        xpath = locator
    details = {"action": _qa_safe_action(code_text), "locator": locator, "xpath": xpath}
    details.update(metadata or {})
    details["action"] = _qa_safe_action(code_text)
    # Report the locator that actually executed. Keep primary_locator in the
    # structured metadata so History can still distinguish a recovered step.
    details["locator"] = locator or details.get("primary_locator")
    return details


def _qa_send_event(event):
    print("QA_EVENT::" + json.dumps(event), flush=True)


def _qa_active_page():
    """Return the newest live page from the executing browser context."""
    candidates = []
    current = globals().get("page")
    current_context = globals().get("context")
    if current_context is not None:
        try:
            candidates.extend(reversed(current_context.pages))
        except Exception:
            pass
    current_browser = globals().get("browser")
    if current_browser is not None:
        try:
            for browser_context in reversed(current_browser.contexts):
                candidates.extend(reversed(browser_context.pages))
        except Exception:
            pass
    if current is not None:
        candidates.append(current)
    seen = set()
    for candidate in candidates:
        if id(candidate) in seen:
            continue
        seen.add(id(candidate))
        try:
            if not candidate.is_closed():
                return candidate
        except Exception:
            continue
    raise RuntimeError("No active Playwright page is available for locator repair.")


def _qa_read_command():
    line = sys.stdin.readline()
    if not line:
        return {"action": "cancel"}
    try:
        return json.loads(line)
    except (TypeError, ValueError):
        return {"action": "cancel"}


def _qa_flatten_accessibility(node, out, max_nodes=150):
    if not isinstance(node, dict) or len(out) >= max_nodes:
        return out
    role = node.get("role")
    name = node.get("name")
    if role and role not in ("none", "generic", "text", "InlineTextBox"):
        entry = (role + ": " + name) if name else role
        value = node.get("value")
        if value:
            entry = entry + " = " + str(value)
        out.append(entry)
    for child in (node.get("children") or []):
        _qa_flatten_accessibility(child, out, max_nodes)
    return out


def _qa_capture_dom_candidates():
    # Real, ready-to-use selector STRINGS for whatever stable
    # attributes actually exist on the live page right now, in the
    # same priority order QA AI Studio asks locator repair to follow:
    # data-testid/data-test/data-cy/data-qa, then id, then name.
    # Giving the AI these literal strings (instead of only role/name
    # pairs from the accessibility tree, which it then has to guess
    # how to turn into correct Playwright syntax itself) is what
    # actually fixes "AI suggestion isn't reliably useful" — the model
    # can quote one of these directly instead of inventing one.
    # NOTE: this whole function's TEXT lives inside
    # INTERACTIVE_HARNESS_TEMPLATE's own outer raw triple-single-quoted
    # string below (see that variable's own comment -- everything in
    # this template is written out and run in a separate subprocess,
    # not live Python here) -- so the JS source below is deliberately
    # wrapped in PYTHON's OTHER triple-quote style (double quotes, not
    # single) purely to avoid a nested triple-single-quote prematurely
    # closing that outer string; it has no effect on the JS itself.
    try:
        return _qa_active_page().evaluate(r"""() => {
            const out = [];
            const seen = new Set();
            const attrPriority = ["data-testid", "data-test", "data-cy", "data-qa", "id", "name"];
            const walker = document.createTreeWalker(document.body || document.documentElement, NodeFilter.SHOW_ELEMENT);
            let visited = 0;
            while (out.length < 50 && visited < 10000) {
                const el = walker.nextNode();
                if (!el) break;
                visited += 1;
                if (!el.matches("input,button,select,textarea,a,[role],[data-testid],[data-test],[data-cy],[data-qa]")) continue;
                let selector = null, attrUsed = null;
                for (const attr of attrPriority) {
                    const val = el.getAttribute(attr);
                    if (val) {
                        attrUsed = attr;
                        selector = attr === "id"
                            ? ("#" + CSS.escape(val))
                            : ("[" + attr + '="' + val.replace(/"/g, '\\\\"') + '"]');
                        break;
                    }
                }
                if (!selector || seen.has(selector)) continue;
                seen.add(selector);
                const text = (el.innerText || el.value || el.getAttribute("aria-label") || "").trim().slice(0, 60);
                const rects = el.getClientRects();
                out.push({
                    selector: selector,
                    tag: el.tagName.toLowerCase(),
                    attr: attrUsed,
                    text: text,
                    visible: !!(el.offsetWidth || el.offsetHeight || rects.length),
                });
            }
            return out;
        }""")
    except Exception:
        return []


def _qa_capture_failure_matches(raw_locator):
    """Capture the real elements matched by the failing locator.

    This is especially important for Playwright strict-mode failures: a
    selector may exist but match multiple visible elements (for example
    the two "Create" buttons in Import and Export cards).  The AI needs
    the surrounding semantic/card text for EACH match so it can scope the
    replacement to the intended parent instead of blindly suggesting
    .first or .nth(...).
    """
    if not raw_locator:
        return []
    try:
        loc = _qa_active_page().locator(raw_locator)
        return loc.evaluate_all(r"""els => els.slice(0, 12).map((el, index) => {
            const clean = (v) => (v || "").replace(/\s+/g, " " ).trim();
            const ancestors = [];
            let node = el.parentElement;
            let depth = 0;
            while (node && depth < 7) {
                const txt = clean(node.innerText).slice(0, 180);
                if (txt && txt.length <= 180) {
                    ancestors.push({
                        tag: (node.tagName || "").toLowerCase(),
                        id: node.id || "",
                        role: node.getAttribute && (node.getAttribute("role") || ""),
                        aria: node.getAttribute && (node.getAttribute("aria-label") || ""),
                        text: txt,
                    });
                }
                node = node.parentElement;
                depth += 1;
            }
            return {
                index,
                tag: (el.tagName || "").toLowerCase(),
                text: clean(el.innerText || el.textContent || el.value).slice(0, 120),
                role: el.getAttribute && (el.getAttribute("role") || ""),
                aria: el.getAttribute && (el.getAttribute("aria-label") || ""),
                id: el.id || "",
                name: el.getAttribute && (el.getAttribute("name") || ""),
                ancestors,
            };
        })""")
    except Exception:
        return []


def _qa_capture_context(raw_locator=""):
    context = {
        "url": "",
        "title": "",
        "accessibility": [],
        "dom_candidates": [],
        "failure_matches": [],
    }
    try:
        context["url"] = _qa_active_page().url
    except Exception:
        pass
    try:
        context["title"] = _qa_active_page().title()
    except Exception:
        pass
    # Never serialize the complete accessibility tree here. Large SPAs can
    # allocate hundreds of MB in Chromium while producing that snapshot and
    # the run is already paused waiting for the operator. The bounded DOM
    # candidates below carry enough diagnostic context without a whole-page
    # capture.
    try:
        context["dom_candidates"] = _qa_capture_dom_candidates()
    except Exception:
        pass
    try:
        context["failure_matches"] = _qa_capture_failure_matches(raw_locator)
    except Exception:
        pass
    return context


def _qa_extract_locator_and_value(code_text):
    matches = re.findall(r"'([^']*)'|\"([^\"]*)\"", code_text)
    values = [a if a else b for a, b in matches]
    locator = values[0] if len(values) >= 1 else ""
    value = values[1] if len(values) >= 2 else ""
    return locator, value


def _qa_rebuild_code(code_text, old_locator, old_value, new_locator, new_value):
    # NOTE: the replacement text is always produced with repr(), never
    # by re-wrapping raw text in matching quote characters. An XPath
    # locator very commonly contains a quote itself (e.g.
    # //button[@id='submit']) -- naively inserting that raw text
    # between quote characters corrupts the Python source (the
    # embedded quote ends the string early), which surfaced as
    # "typing an XPath fix just fails" even though Playwright itself
    # accepts XPath (any locator starting with "//" is auto-detected
    # as XPath) without any special handling. repr() always produces
    # a valid, correctly-escaped Python string literal no matter what
    # quote characters (or backslashes) the new text contains.
    result = code_text
    if new_locator is not None and new_locator != old_locator and old_locator:
        result = result.replace("'" + old_locator + "'", repr(new_locator), 1)
        result = result.replace('"' + old_locator + '"', repr(new_locator), 1)
    if new_value is not None and new_value != old_value and old_value:
        result = result.replace("'" + old_value + "'", repr(new_value), 1)
        result = result.replace('"' + old_value + '"', repr(new_value), 1)
    return result


def _qa_locator_expression_from_code(code_text):
    """Return the locator-producing expression from one Playwright step.

    Examples:
      page.get_by_role('button', name='Create').click()
        -> page.get_by_role('button', name='Create')
      page.locator('#x').fill('abc')
        -> page.locator('#x')

    This lets the live verifier validate modern Playwright locator chains
    (get_by_role/get_by_text/filter/first/etc), not only raw CSS/XPath
    strings.  It never performs the action itself.
    """
    code = (code_text or "").strip()
    action_names = (
        "click", "dblclick", "fill", "type", "press", "check",
        "uncheck", "select_option", "set_input_files", "hover",
        "focus", "tap", "drag_to", "dispatch_event",
    )
    for action in action_names:
        marker = "." + action + "("
        pos = code.rfind(marker)
        if pos > 0:
            return code[:pos].strip()
    return code


def _qa_verify_code(code_text):
    result = {"found": False, "count": 0, "visible": False, "actionable": False, "error": ""}
    try:
        expr = _qa_locator_expression_from_code(code_text)
        if not expr:
            result["error"] = "No locator expression found in the code."
            return result
        active_page = _qa_active_page()
        loc = eval(expr, {**globals(), "page": active_page})
        if not hasattr(loc, "count"):
            result["error"] = "The code does not resolve to a Playwright Locator."
            return result
        count = loc.count()
        result["count"] = count
        result["found"] = count > 0
        if result["found"]:
            try:
                loc.first.wait_for(state="visible", timeout=1500)
                result["visible"] = True
                result["actionable"] = bool(loc.first.evaluate(r"""el => {
                    let actionable = el.closest('button,a[href],input,select,textarea,[role=button],[role=link],[role=menuitem],[role=option],[role=tab],[onclick],[tabindex]:not([tabindex="-1"])');
                    let cursorNode = el;
                    while (!actionable && cursorNode && cursorNode !== document.body) {
                        if (getComputedStyle(cursorNode).cursor === 'pointer') actionable = cursorNode;
                        cursorNode = cursorNode.parentElement;
                    }
                    if (!actionable) return false;
                    const style = getComputedStyle(actionable);
                    return !actionable.disabled && actionable.getAttribute('aria-disabled') !== 'true'
                        && style.pointerEvents !== 'none';
                }"""))
            except Exception:
                result["visible"] = False
    except Exception as ex:
        result["error"] = str(ex)
    return result


def _qa_verify_locator(raw_locator):
    # Live-page check only -- deliberately does NOT click/fill/exec
    # anything. This is what lets QA AI Studio show "Locator verified"
    # / "Locator not found" for a candidate (typed manually or from an
    # AI suggestion) BEFORE the operator commits to Retry With This
    # Fix, per the explicit "do not blindly accept an AI locator
    # without checking it against the current page" requirement.
    # Playwright's page.locator() already auto-detects a leading "//"
    # or ".//" as XPath, so no special-casing is needed here for
    # XPath vs CSS/text/etc locators.
    result = {"found": False, "count": 0, "visible": False, "error": ""}
    if not raw_locator:
        result["error"] = "No locator provided."
        return result
    try:
        loc = _qa_active_page().locator(raw_locator)
        count = loc.count()
        result["count"] = count
        result["found"] = count > 0
        if result["found"]:
            try:
                loc.first.wait_for(state="visible", timeout=1500)
                result["visible"] = True
            except Exception:
                result["visible"] = False
    except Exception as ex:
        result["error"] = str(ex)
    return result


def _qa_replace_locator_expression(code_text, locator_expression):
    current = _qa_locator_expression_from_code(code_text)
    if not current or not locator_expression:
        return code_text
    return code_text.replace(current, locator_expression, 1)


def _qa_select_element(code_text, step_number):
    """Wait for one operator click and return bounded, live-verified locators."""
    cancel_file = Path(os.environ.get("QA_SELECTION_CANCEL_FILE", ""))
    if cancel_file.name:
        try:
            cancel_file.unlink(missing_ok=True)
        except Exception:
            pass
    active_page = _qa_active_page()
    binding_key = "_QA_CANCEL_BINDING_READY_" + str(id(active_page.context))
    if not globals().get(binding_key):
        active_page.context.expose_function(
            "_qaSelectionCancelled",
            lambda: bool(cancel_file.name and cancel_file.exists()),
        )
        globals()[binding_key] = True
    try:
        capture = active_page.evaluate(r"""(selectionTimeout) => new Promise((resolve) => {
            const MAX_CANDIDATES = 50;
            const MAX_ANCESTORS = 8;
            const clean = (value, limit = 160) => String(value || "").replace(/\s+/g, " ").trim().slice(0, limit);
            const py = (value) => JSON.stringify(String(value || ""));
            const cssValue = (value) => String(value || "").replace(/\\/g, "\\\\").replace(/"/g, '\\"');
            const dynamic = (value) => {
                const text = String(value || "");
                return !text || text.length > 100 ||
                    /^[0-9a-f]{8}-[0-9a-f-]{27,}$/i.test(text) ||
                    /^[0-9a-f]{20,}$/i.test(text) ||
                    /(?:^|[-_])[0-9a-f]{10,}(?:$|[-_])/i.test(text) ||
                    /(?:^|[-_])(?=[a-z0-9]{16,}(?:$|[-_]))(?=[a-z0-9]*[a-z])(?=(?:[^0-9]*[0-9]){4})[a-z0-9]+(?:$|[-_])/i.test(text) ||
                    /(?:^|[-_])[0-9]{6,}(?:$|[-_])/.test(text) ||
                    /(?:ember|react|vue|ng|mui|chakra)[-_]?[0-9a-f]{5,}/i.test(text) ||
                    /__[A-Za-z0-9_-]{5,}$/.test(text);
            };
            const implicitRole = (el) => {
                const tag = el.tagName.toLowerCase();
                if (tag === "button") return "button";
                if (tag === "a" && el.hasAttribute("href")) return "link";
                if (tag === "select") return "combobox";
                if (tag === "textarea") return "textbox";
                if (tag === "input") {
                    const type = (el.type || "text").toLowerCase();
                    if (["button", "submit", "reset"].includes(type)) return "button";
                    if (type === "checkbox") return "checkbox";
                    if (type === "radio") return "radio";
                    return "textbox";
                }
                return "";
            };
            const actionableFor = (leaf) => {
                const semantic = leaf.closest("button,a[href],input,select,textarea,[role=button],[role=link],[role=menuitem],[role=option],[role=tab],[onclick],[tabindex]:not([tabindex='-1'])");
                if (semantic) return semantic;
                let node = leaf;
                let depth = 0;
                while (node && node !== document.body && depth < MAX_ANCESTORS) {
                    if (getComputedStyle(node).cursor === "pointer") return node;
                    node = node.parentElement;
                    depth += 1;
                }
                return leaf;
            };
            const contextFor = (el) => {
                let node = el.parentElement;
                let depth = 0;
                while (node && depth < MAX_ANCESTORS) {
                    const isContainer = /^(article|section|form|fieldset|li)$/i.test(node.tagName)
                        || /(?:^|\s)(?:k-card|card|panel|tile|menu-item|list-item)(?:\s|$)/i.test(node.className || "")
                        || ["region","group","dialog","menuitem","listitem"].includes(node.getAttribute("role") || "");
                    if (isContainer) {
                        const identities = [];
                        const addIdentity = value => {
                            value = clean(value, 100);
                            if (value && value !== clean(el.innerText, 100) && !identities.includes(value)) identities.push(value);
                        };
                        addIdentity(node.getAttribute("aria-label"));
                        addIdentity(node.getAttribute("title"));
                        node.querySelectorAll("h1,h2,h3,h4,legend,[role=heading],.k-card-title,[class*=title],[class*=heading]").forEach(item => addIdentity(item.innerText || item.getAttribute("aria-label")));
                        node.querySelectorAll("label,p,span,strong").forEach(item => {
                            if (!el.contains(item) && !item.contains(el)) addIdentity(item.innerText);
                        });
                        const unique = identities.find(value => {
                            const literal = xpathLiteral(value);
                            return document.evaluate("//*[normalize-space(.)=" + literal + "]", document, null, XPathResult.ORDERED_NODE_SNAPSHOT_TYPE, null).snapshotLength === 1;
                        }) || identities[0];
                        if (unique) return {element: node, label: unique, tag: node.tagName.toLowerCase(), identities: identities.slice(0, 12)};
                    }
                    node = node.parentElement;
                    depth += 1;
                }
                return {element: null, label: "", tag: "", identities: []};
            };
            const xpathLiteral = (value) => {
                const text = String(value || "");
                if (!text.includes('"')) return '"' + text + '"';
                if (!text.includes("'")) return "'" + text + "'";
                return "concat(" + text.split('"').map((part, index, all) =>
                    '"' + part + '"' + (index < all.length - 1 ? ", '\"', " : "")
                ).join("") + ")";
            };
            let settled = false;
            let timeoutId = null;
            let cancelPollId = null;
            const previousCursor = document.documentElement.style.cursor;
            let outlined = null;
            let previousOutline = "";
            let previousBackground = "";
            const clearOutline = () => {
                if (outlined) {
                    outlined.style.outline = previousOutline;
                    outlined.style.backgroundColor = previousBackground;
                }
                outlined = null;
            };
            const cleanup = () => {
                document.removeEventListener("click", onClick, true);
                document.removeEventListener("mousemove", onMove, true);
                document.removeEventListener("keydown", onKey, true);
                clearOutline();
                document.documentElement.style.cursor = previousCursor;
                if (timeoutId !== null) clearTimeout(timeoutId);
                if (cancelPollId !== null) clearInterval(cancelPollId);
            };
            const finish = (payload) => {
                if (settled) return;
                settled = true;
                cleanup();
                resolve(payload);
            };
            const onClick = (event) => {
                event.preventDefault();
                event.stopPropagation();
                event.stopImmediatePropagation();
                const leaf = event.target && event.target.nodeType === 1 ? event.target : null;
                if (!leaf) return finish({error: "The clicked DOM target is unavailable."});
                const el = actionableFor(leaf);
                const opener = el.matches("[aria-haspopup=listbox],[aria-haspopup=menu],[role=combobox],.k-select,.k-dropdown,.k-dropdownlist,[class*=autocomplete]")
                    || /(?:dropdown|combobox|autocomplete|calendar)/i.test(el.className || "");
                const optionsVisible = [...document.querySelectorAll("[role=option],.k-list-item,.k-calendar")].some(item => {
                    const style = getComputedStyle(item);
                    return style.display !== "none" && style.visibility !== "hidden" && item.getClientRects().length > 0;
                });
                if (opener && !optionsVisible) {
                    document.removeEventListener("click", onClick, true);
                    clearOutline();
                    setTimeout(() => {
                        try { el.click(); } finally { document.addEventListener("click", onClick, true); }
                    }, 0);
                    return;
                }
                const section = contextFor(el);
                const candidates = [];
                const seen = new Set();
                const add = (score, type, expression, locator, label) => {
                    if (!expression || seen.has(expression) || candidates.length >= MAX_CANDIDATES) return;
                    seen.add(expression);
                    candidates.push({score, type, expression, locator: locator || expression, label: clean(label || locator || expression)});
                };
                for (const attr of ["data-testid", "data-test-id", "data-test", "data-cy", "data-qa", "data-automation-id"]) {
                    const value = el.getAttribute(attr);
                    if (value && !dynamic(value)) {
                        const selector = "[" + attr + '=\"' + cssValue(value) + '\"]';
                        add(100, attr, "page.locator(" + py(selector) + ")", selector, attr + ": " + value);
                    }
                }
                if (el.id && !dynamic(el.id)) {
                    const selector = "#" + CSS.escape(el.id);
                    add(95, "id", "page.locator(" + py(selector) + ")", selector, el.id);
                }
                const role = el.getAttribute("role") || implicitRole(el);
                const accessibleName = clean(el.getAttribute("aria-label") || el.innerText || el.value, 100);
                if (role && accessibleName) add(85, "role", "page.get_by_role(" + py(role) + ", name=" + py(accessibleName) + ", exact=True)", "role=" + role, role + ": " + accessibleName);
                const label = clean((el.labels && el.labels[0] && el.labels[0].innerText) || (el.closest("label") && el.closest("label").innerText), 100);
                if (label) add(90, "label", "page.get_by_label(" + py(label) + ", exact=True)", "label=" + label, "Label: " + label);
                for (const attr of ["name", "placeholder", "title"]) {
                    const value = el.getAttribute(attr);
                    if (!value || dynamic(value)) continue;
                    if (attr === "placeholder") add(90, attr, "page.get_by_placeholder(" + py(value) + ", exact=True)", "placeholder=" + value, value);
                    else if (attr === "title") add(90, attr, "page.get_by_title(" + py(value) + ", exact=True)", "title=" + value, value);
                    else {
                        const selector = '[name=\"' + cssValue(value) + '\"]';
                        add(90, attr, "page.locator(" + py(selector) + ")", selector, value);
                    }
                }
                const meaningfulText = clean(el.innerText || el.textContent, 100);
                if (meaningfulText && meaningfulText.length >= 2) add(65, "text", "page.get_by_text(" + py(meaningfulText) + ", exact=True)", "text=" + meaningfulText, meaningfulText);

                if (section.element && section.label && role && accessibleName) {
                    const scopedRole = "page.get_by_text(" + py(section.label) + ", exact=True).locator(\"xpath=ancestor::*[self::section or self::fieldset or self::div][1]\").get_by_role(" + py(role) + ", name=" + py(accessibleName) + ", exact=True)";
                    add(80, "context-role", scopedRole, "section=" + section.label + " >> role=" + role + "[name=" + accessibleName + "]", section.label + " / " + accessibleName);
                }

                const path = [];
                let node = el;
                let depth = 0;
                let anchor = "";
                while (node && node.nodeType === 1 && depth < MAX_ANCESTORS) {
                    const tag = node.tagName.toLowerCase();
                    if (node.id && !dynamic(node.id)) {
                        anchor = "#" + CSS.escape(node.id);
                        if (node !== el) path.unshift(anchor);
                        break;
                    }
                    const testId = node.getAttribute("data-testid");
                    if (testId && !dynamic(testId)) {
                        anchor = '[data-testid=\"' + cssValue(testId) + '\"]';
                        if (node !== el) path.unshift(anchor);
                        break;
                    }
                    let segment = tag;
                    const classes = [...node.classList].filter(value => !dynamic(value) && /^[A-Za-z_-][A-Za-z0-9_-]*$/.test(value)).slice(0, 2);
                    if (classes.length) segment += "." + classes.map(value => CSS.escape(value)).join(".");
                    path.unshift(segment);
                    node = node.parentElement;
                    depth += 1;
                }
                const css = path.join(" > ");
                if (css && !/:nth-|\[[^\]]*(?:style|class\*=)/i.test(css)) add(60, "relative-css", "page.locator(" + py(css) + ")", css, css);

                let xpath = "//" + el.tagName.toLowerCase();
                const xpathAttr = ["data-testid", "data-test-id", "data-test", "data-cy", "data-qa", "data-automation-id", "name", "title"].find(attr => {
                    const value = el.getAttribute(attr);
                    return value && !dynamic(value);
                });
                if (xpathAttr) xpath += "[@" + xpathAttr + "=" + xpathLiteral(el.getAttribute(xpathAttr)) + "]";
                else if (meaningfulText) xpath += "[normalize-space(.)=" + xpathLiteral(meaningfulText) + "]";
                if (section.label && meaningfulText) {
                    const contextualXpath = "//*[self::article or self::section or self::form or self::fieldset or self::li or contains(concat(' ',normalize-space(@class),' '),' k-card ') or contains(@class,'card') or contains(@class,'panel') or contains(@class,'tile')][.//*[normalize-space(.)=" + xpathLiteral(section.label) + "]]//" + el.tagName.toLowerCase() + "[normalize-space(.)=" + xpathLiteral(meaningfulText) + "]";
                    add(75, "context-xpath", "page.locator(" + py("xpath=" + contextualXpath) + ")", "xpath=" + contextualXpath, section.label + " / " + meaningfulText);
                    xpath = contextualXpath;
                }
                add(70, "relative-xpath", "page.locator(" + py("xpath=" + xpath) + ")", "xpath=" + xpath, xpath);
                const exactParts = [];
                let exactNode = el;
                let exactDepth = 0;
                while (exactNode && exactNode !== document.body && exactDepth < MAX_ANCESTORS) {
                    let part = exactNode.tagName.toLowerCase();
                    const parent = exactNode.parentElement;
                    if (parent) {
                        const siblings = [...parent.children].filter(sibling => sibling.tagName === exactNode.tagName);
                        if (siblings.length > 1) part += ":nth-of-type(" + (siblings.indexOf(exactNode) + 1) + ")";
                    }
                    exactParts.unshift(part);
                    if (parent && parent.id && !dynamic(parent.id)) {
                        exactParts.unshift("#" + CSS.escape(parent.id));
                        break;
                    }
                    exactNode = parent;
                    exactDepth += 1;
                }
                const exactCss = exactParts.join(" > ");
                if (exactCss) add(50, "exact-relative-css", "page.locator(" + py(exactCss) + ")", exactCss, "Exact clicked element fallback");
                finish({
                    tag: leaf.tagName.toLowerCase(),
                    actionable_tag: el.tagName.toLowerCase(),
                    role: role,
                    text: clean(el.innerText || el.textContent || el.value),
                    accessible_name: accessibleName,
                    label: label,
                    name: el.getAttribute("name") || "",
                    id: el.id || "",
                    placeholder: el.getAttribute("placeholder") || "",
                    title: el.getAttribute("title") || "",
                    data_testid: el.getAttribute("data-testid") || "",
                    stable_attributes: Object.fromEntries(
                        ["data-testid","data-test-id","data-test","data-cy","data-qa","data-automation-id","name","placeholder","title"]
                            .map(attr => [attr, el.getAttribute(attr)])
                            .filter(pair => pair[1] && !dynamic(pair[1]))
                    ),
                    section: section.label,
                    ancestor_context: section.label,
                    ancestor_summaries: section.identities,
                    classes: [...leaf.classList].slice(0, 12),
                    candidates: candidates.slice(0, MAX_CANDIDATES),
                    xpath: "xpath=" + xpath,
                });
            };
            const onMove = (event) => {
                const next = event.target && event.target.nodeType === 1 ? event.target : null;
                if (!next || next === outlined) return;
                clearOutline();
                outlined = next;
                previousOutline = next.style.outline;
                previousBackground = next.style.backgroundColor;
                next.style.outline = "3px solid #f2c200";
                next.style.backgroundColor = "rgba(255, 235, 59, 0.28)";
            };
            const onKey = (event) => {
                if (event.key !== "Escape") return;
                event.preventDefault();
                finish({cancelled: true, error: "Element selection cancelled with Escape."});
            };
            document.documentElement.style.cursor = "crosshair";
            document.addEventListener("click", onClick, true);
            document.addEventListener("mousemove", onMove, true);
            document.addEventListener("keydown", onKey, true);
            cancelPollId = setInterval(async () => {
                try {
                    if (await window._qaSelectionCancelled()) finish({cancelled: true, error: "Element selection cancelled."});
                } catch (_) {}
            }, 150);
            timeoutId = setTimeout(() => finish({timed_out: true, error: "Element selection timed out while waiting for a browser click."}), selectionTimeout);
        })""", _QA_SELECTION_TIMEOUT_MS)
    except Exception as ex:
        return {"error": str(ex), "candidates": []}

    candidates = (capture or {}).get("candidates") or []
    capture["step_number"] = step_number
    verified = []
    selected = None
    for candidate in sorted(candidates[:50], key=lambda item: item.get("score", 0), reverse=True):
        check = _qa_verify_code(candidate.get("expression") or "")
        candidate["verification"] = check
        if check.get("count") != 1:
            candidate["rejected_reason"] = "not found" if not check.get("count") else f"ambiguous: {check.get('count')} matches"
        elif not check.get("visible"):
            candidate["rejected_reason"] = "not visible"
        elif not check.get("actionable"):
            candidate["rejected_reason"] = "not actionable"
        verified.append(candidate)
        if (
            selected is None
            and check.get("found")
            and check.get("visible")
            and check.get("actionable")
            and check.get("count") == 1
        ):
            selected = candidate
    capture["candidates"] = verified
    capture["selected_candidate"] = selected
    if selected:
        capture["corrected_code"] = _qa_replace_locator_expression(
            code_text, selected["expression"]
        )
    return capture


def _qa_persisted_candidate_codes(code_text, metadata):
    candidates = []
    raw_fallbacks = (metadata or {}).get("fallback_locators") or ""
    for locator in re.split(r"\s*(?:\|\||;)\s*", raw_fallbacks):
        locator = locator.strip()
        if locator and locator != "-":
            role_match = re.fullmatch(r"role=([^|]+)\|name=(.+)", locator)
            if locator.startswith("page."):
                expression = locator
            elif role_match:
                expression = "page.get_by_role(" + repr(role_match.group(1)) + ", name=" + repr(role_match.group(2)) + ")"
            elif locator.startswith("label="):
                expression = "page.get_by_label(" + repr(locator[6:]) + ")"
            elif locator.startswith("placeholder="):
                expression = "page.get_by_placeholder(" + repr(locator[12:]) + ")"
            elif locator.startswith("title="):
                expression = "page.get_by_title(" + repr(locator[6:]) + ")"
            elif locator.startswith("text="):
                expression = "page.get_by_text(" + repr(locator[5:]) + ", exact=True)"
            elif locator.startswith("data-testid="):
                expression = "page.get_by_test_id(" + repr(locator[12:]) + ")"
            elif locator.startswith("name="):
                expression = "page.locator(" + repr('[name="' + locator[5:] + '"]') + ")"
            else:
                expression = "page.locator(" + repr(locator) + ")"
            candidates.append(_qa_replace_locator_expression(
                code_text, expression
            ))
    xpath = ((metadata or {}).get("xpath") or "").strip()
    if xpath and xpath != "-":
        candidates.append(_qa_replace_locator_expression(
            code_text, "page.locator(" + repr(xpath) + ")"
        ))
    return [candidate for candidate in candidates if candidate != code_text]


def _qa_locator_code(display_value):
    value = (display_value or "").strip()
    if not value or value == "-":
        return ""
    if value.startswith("page."):
        return value
    role_match = re.fullmatch(r"role=([^|]+)\|name=(.+)", value)
    if role_match:
        return "page.get_by_role(" + repr(role_match.group(1)) + ", name=" + repr(role_match.group(2)) + ", exact=True)"
    if value.startswith("data-testid="):
        return "page.get_by_test_id(" + repr(value[12:]) + ")"
    if value.startswith("label="):
        return "page.get_by_label(" + repr(value[6:]) + ")"
    if value.startswith("placeholder="):
        return "page.get_by_placeholder(" + repr(value[12:]) + ")"
    if value.startswith("text="):
        return "page.get_by_text(" + repr(value[5:]) + ", exact=True)"
    return "page.locator(" + repr(value) + ")"


def _qa_smart_wait(metadata=None):
    details = metadata or {}
    field_type = str(details.get("field_type") or "").lower()
    action_type = str(details.get("action_type") or "").lower()
    active_page = _qa_active_page()
    try:
        active_page.wait_for_load_state("domcontentloaded", timeout=2000)
    except Exception:
        pass
    try:
        active_page.evaluate("() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))")
    except Exception:
        pass
    selector = ""
    if field_type in {"dropdown", "option", "autocomplete"}:
        selector = "[role=option]:visible,.k-list-item:visible,.k-item:visible,[role=listbox]:visible"
    elif field_type in {"date", "calendar"}:
        selector = "[role=gridcell]:visible,.k-calendar:visible,.k-calendar-container:visible"
    elif field_type == "modal" or action_type in {"open_modal", "dialog"}:
        selector = "[role=dialog]:visible,.k-dialog:visible,.modal.show:visible"
    if selector:
        try:
            active_page.locator(selector).first.wait_for(state="visible", timeout=2000)
        except Exception:
            pass


def _qa_try_parent_reopen(metadata):
    parent_code = _qa_locator_code((metadata or {}).get("parent_locator"))
    if not parent_code:
        return False
    try:
        parent = eval(parent_code, {**globals(), "page": _qa_active_page()})
        if parent.count() != 1 or not parent.is_visible():
            return False
        parent.click()
        _qa_smart_wait({"field_type": "dropdown"})
        return True
    except Exception:
        return False


def _qa_handle_step_failure(step_number, code_text, error_text, attempt, metadata=None):
    locator, value = _qa_extract_locator_and_value(code_text)
    context = _qa_capture_context(locator)
    _qa_send_event({
        "event": "step_failed",
        "step": step_number,
        "total_steps": _QA_TOTAL_STEPS,
        "code": code_text,
        "details": _qa_step_details(code_text, metadata),
        "locator": locator,
        "value": value,
        "error": error_text,
        "attempt": attempt,
        "url": context["url"],
        "title": context["title"],
        "accessibility": context["accessibility"],
        "dom_candidates": context["dom_candidates"],
        "failure_matches": context["failure_matches"],
    })
    # Loop reading commands for this SAME pause -- "verify_locator" is
    # a non-terminal, repeatable check (it does not count as a repair
    # attempt and does not resume the run): it reports back a
    # "locator_verified" event and waits for the NEXT command. Only a
    # terminal action (retry / retry_code / cancel) ever returns from
    # this function.
    while True:
        command = _qa_read_command()
        action = command.get("action")
        if action == "select_element":
            _qa_send_event({"event": "selection_started", "step": step_number, "total_steps": _QA_TOTAL_STEPS})
            while True:
                _qa_send_event({"event": "selection_waiting", "step": step_number, "total_steps": _QA_TOTAL_STEPS, "timeout_seconds": int(_QA_SELECTION_TIMEOUT_MS / 1000)})
                selection = _qa_select_element(code_text, step_number)
                if selection.get("cancelled"):
                    _qa_send_event({"event": "selection_cancelled", "step": step_number, "error": selection.get("error")})
                    break
                if selection.get("error"):
                    _qa_send_event({"event": "selection_failed", "step": step_number, "error": selection.get("error"), "timed_out": bool(selection.get("timed_out"))})
                    break
                selected_candidate = selection.get("selected_candidate") or {}
                selected_event = {
                "event": "element_selected",
                "step": step_number,
                "total_steps": _QA_TOTAL_STEPS,
                "field_name": selection.get("accessible_name") or selection.get("text") or "",
                "field_type": selection.get("role") or selection.get("actionable_tag") or "",
                "section": selection.get("section") or "",
                "clicked": {
                    "tag": selection.get("tag"), "text": selection.get("text"),
                    "classes": selection.get("classes") or [],
                },
                "actionable": {
                    "tag": selection.get("actionable_tag"), "role": selection.get("role"),
                    "name": selection.get("accessible_name"), "id": selection.get("id"),
                    "attributes": selection.get("stable_attributes") or {},
                },
                "ancestor_summaries": selection.get("ancestor_summaries") or [],
                "candidates": selection.get("candidates") or [],
                "selected_candidate": selected_candidate or None,
                "locator": selected_candidate.get("expression") or selected_candidate.get("locator"),
                "xpath": selection.get("xpath"),
                "corrected_code": selection.get("corrected_code"),
                "verification": selected_candidate.get("verification") or {},
                "error": selection.get("error"),
                }
                _qa_send_event(selected_event)
                corrected_code = selection.get("corrected_code")
                if corrected_code:
                    _QA_SELECTIONS[step_number] = selection
                    return corrected_code
                _qa_send_event({"event": "selection_waiting", "step": step_number, "total_steps": _QA_TOTAL_STEPS, "timeout_seconds": int(_QA_SELECTION_TIMEOUT_MS / 1000), "message": "No safe locator was verified; selection remains active."})
            continue
        if action in ("verify_locator", "verify_code"):
            if action == "verify_code":
                check_code = command.get("code") or code_text
                verify_result = _qa_verify_code(check_code)
                checked_value = check_code
            else:
                check_locator = command.get("locator") or locator
                verify_result = _qa_verify_locator(check_locator)
                checked_value = check_locator
            _qa_send_event({
                "event": "locator_verified",
                "step": step_number,
                "mode": "code" if action == "verify_code" else "locator",
                "locator": checked_value,
                "found": verify_result["found"],
                "count": verify_result["count"],
                "visible": verify_result["visible"],
                "error": verify_result["error"],
            })
            continue
        if action == "retry_code":
            return command.get("code") or code_text
        if action == "retry":
            new_locator = command.get("locator", locator)
            new_value = command.get("value", value)
            return _qa_rebuild_code(code_text, locator, value, new_locator, new_value)
        return None


def _qa_run_step(step_number, code_text, metadata=None):
    global _QA_LAST_DYNAMIC_CONTROL
    current_code = code_text
    attempt = 0
    while True:
        _qa_send_event({"event": "step_started", "step": step_number, "total_steps": _QA_TOTAL_STEPS, "attempt": attempt + 1, "details": _qa_step_details(current_code, metadata)})
        try:
            exec(current_code, globals())
            _qa_smart_wait(metadata)
            if str((metadata or {}).get("field_type") or "").lower() in {"dropdown", "calendar", "autocomplete", "accordion"}:
                _QA_LAST_DYNAMIC_CONTROL = current_code
            if current_code != code_text:
                selection = _QA_SELECTIONS.pop(step_number, {})
                _QA_REPAIRS.append({
                    "step": step_number,
                    "original": code_text,
                    "corrected": current_code,
                    "selection": selection,
                })
                _qa_send_event({
                    "event": "step_repaired",
                    "step": step_number,
                    "original_code": code_text,
                    "corrected_code": current_code,
                    "selection": selection,
                })
            _qa_send_event({"event": "step_passed", "step": step_number, "total_steps": _QA_TOTAL_STEPS, "attempt": attempt + 1, "details": _qa_step_details(current_code, metadata)})
            return
        except Exception as ex:
            if attempt == 0:
                _qa_send_event({"event": "auto_repair_attempted", "step": step_number, "total_steps": _QA_TOTAL_STEPS})
                if _qa_try_parent_reopen(metadata):
                    try:
                        exec(current_code, globals())
                        _qa_smart_wait(metadata)
                        _qa_send_event({"event": "parent_reopen_succeeded", "step": step_number, "total_steps": _QA_TOTAL_STEPS})
                        _qa_send_event({"event": "step_passed", "step": step_number, "total_steps": _QA_TOTAL_STEPS, "attempt": attempt + 1, "details": _qa_step_details(current_code, metadata)})
                        return
                    except Exception:
                        pass
                for fallback_code in _qa_persisted_candidate_codes(current_code, metadata):
                    try:
                        exec(fallback_code, globals())
                        current_code = fallback_code
                        _qa_send_event({
                            "event": "fallback_succeeded", "step": step_number,
                            "total_steps": _QA_TOTAL_STEPS,
                            "details": _qa_step_details(current_code, metadata),
                        })
                        _QA_REPAIRS.append({
                            "step": step_number,
                            "original": code_text,
                            "corrected": current_code,
                        })
                        _qa_send_event({
                            "event": "step_repaired",
                            "step": step_number,
                            "original_code": code_text,
                            "corrected_code": current_code,
                        })
                        _qa_send_event({
                            "event": "step_passed",
                            "step": step_number,
                            "total_steps": _QA_TOTAL_STEPS,
                            "attempt": attempt + 1,
                            "details": _qa_step_details(current_code, metadata),
                        })
                        return
                    except Exception:
                        continue
            attempt += 1
            if attempt > _QA_MAX_REPAIR_ROUNDS:
                _qa_send_event({
                    "event": "step_gave_up",
                    "step": step_number,
                    "code": current_code,
                    "error": str(ex),
                })
                raise
            outcome = _qa_handle_step_failure(step_number, current_code, str(ex), attempt, metadata)
            if outcome is None:
                _qa_send_event({
                    "event": "run_cancelled_by_operator",
                    "step": step_number,
                })
                sys.exit(2)
            _qa_send_event({"event": "step_retrying", "step": step_number, "attempt": attempt + 1})
            current_code = outcome
# --- end QA AI Studio interactive step runner ---

'''


# ================================================================
# Post-recording dynamic-locator scan
# ================================================================
#
# Everything above this point is TEXT (the INTERACTIVE_HARNESS_TEMPLATE
# string) written out and run in a SEPARATE subprocess — none of it is
# live Python in this process. The regexes and function below ARE real,
# live code in this process: used right after Manual Recording finishes
# (see test_execution_page.py's on_recording_finished()) to flag locators
# that look dynamically generated BEFORE the first replay, instead of
# only discovering them when a replay fails.
_SCAN_ATTR_ID_RE = re.compile(r"\[id=['\"]([^'\"]+)['\"]\]")
_SCAN_ATTR_NAME_RE = re.compile(r"\[name=['\"]([^'\"]+)['\"]\]")
_SCAN_CSS_ID_RE = re.compile(r"#([A-Za-z0-9_-]+)")


def _scan_extract_locator(code_text):
    """
    Same convention the interactive harness's own
    _qa_extract_locator_and_value() uses: the first quoted string on
    a line is treated as that line's locator.
    """
    matches = re.findall(r"'([^']*)'|\"([^\"]*)\"", code_text)
    values = [a if a else b for a, b in matches]
    return values[0] if values else ""


def scan_script_for_dynamic_locators(script_text):
    """
    Scans a recorded/generated script's locators for an id or name
    attribute value that looks dynamically generated (see
    looks_dynamically_generated() in url_discovery_engine.py) and
    returns one flagged entry per risky line — purely informational,
    never modifies the script. The operator decides what, if
    anything, to do about a flagged line, via View Script right now
    or the Locator Repair popup the first time it actually fails
    during a replay.

    Returns a list of {"line_number", "code", "locator", "value"}
    dicts, empty if nothing looks risky.
    """

    from Core.url_discovery_engine import looks_dynamically_generated

    flagged = []

    for line_number, line in enumerate(script_text.splitlines(), start=1):

        stripped = line.strip()

        if not stripped or stripped.startswith("#"):

            continue

        locator = _scan_extract_locator(stripped)

        if not locator:

            continue

        candidate_value = None

        for pattern in (_SCAN_ATTR_ID_RE, _SCAN_ATTR_NAME_RE, _SCAN_CSS_ID_RE):

            match = pattern.search(locator)

            if match:

                candidate_value = match.group(1)

                break

        if candidate_value and looks_dynamically_generated(candidate_value):

            flagged.append({
                "line_number": line_number,
                "code": stripped,
                "locator": locator,
                "value": candidate_value,
            })

    return flagged


class PlaywrightRunner:

    def __init__(self, force_headless=False):

        self.logger = Logger.get_logger()

        self.environment_config = TestEnvironmentConfig()

        # Set only while an interactive run (see
        # run_script_interactive()) is in progress, so
        # cancel_current_run() — called from the UI thread when the
        # operator hits Cancel — has a live process to terminate.
        self._current_process = None

        # WEB PORT ADDITION (not in the desktop app): on the desktop,
        # a generated/recorded script's own chromium.launch(headless=
        # False) is correct — there's a monitor right there for the
        # operator to watch. A server has no display, so a script
        # written with headless=False would just hang/crash. When
        # force_headless=True, run_script() prepends an extra shim
        # (below) that forces headless=True regardless of what the
        # script itself asks for — same monkeypatch technique the
        # existing SPEED_SHIM_TEMPLATE already uses, so nothing about
        # script generation/recording needs to change.
        self.force_headless = force_headless

        # WEB PORT ADDITION: set by cancel_current_run() right before
        # terminating the subprocess, so run_script()/run_script_interactive()
        # can tell "the operator/API caller cancelled this" apart from
        # "the script's own process crashed/was killed for some other
        # reason" — both look identical from a bare negative return code.
        self._cancel_requested = False

        OUTPUT_FOLDER.mkdir(parents=True, exist_ok=True)

        EVIDENCE_FOLDER.mkdir(parents=True, exist_ok=True)

    # --------------------------------------------------

    def _resolve_speed_settings(self):
        """
        Reads Playback Speed / Default Timeout from Test Environment
        Settings, falling back to DEFAULT_SLOW_MO_MS /
        DEFAULT_ACTION_TIMEOUT_MS whenever the stored value is
        blank, not a number, or negative — a typo in that dialog
        should never silently disable the safety net (or, worse,
        crash the run).
        """

        environment = self.environment_config.load()

        slow_mo_ms = self._parse_non_negative_int(
            environment.get("slow_mo_ms"), DEFAULT_SLOW_MO_MS
        )
        if slow_mo_ms not in {100, 300, 700, 1200}:
            slow_mo_ms = DEFAULT_SLOW_MO_MS

        timeout_ms = self._parse_non_negative_int(
            environment.get("default_timeout_ms"),
            DEFAULT_ACTION_TIMEOUT_MS,
        )

        return slow_mo_ms, timeout_ms

    @staticmethod
    def _parse_non_negative_int(raw_value, fallback):

        try:

            value = int(str(raw_value).strip())

            return value if value >= 0 else fallback

        except (TypeError, ValueError):

            return fallback

    # --------------------------------------------------

    def _strip_code_fences(self, text):
        """
        Same fix as TestExecutionManager._strip_code_fences() —
        duplicated here so already-stored scripts (generated before
        that fix existed) also get cleaned right before running,
        without needing to regenerate them.
        """

        if not text:

            return text

        text = text.strip()

        match = re.search(
            r"```(?:[a-zA-Z0-9]*)?\n(.*?)```",
            text,
            re.DOTALL,
        )

        if match:

            return match.group(1).strip()

        lines = text.split("\n")

        if lines and lines[0].strip().startswith("```"):

            lines = lines[1:]

        if lines and lines[-1].strip() == "```":

            lines = lines[:-1]

        return "\n".join(lines).strip()

    # --------------------------------------------------

    def _looks_like_pytest_style(self, script_text):
        """
        Old scripts (generated before the standalone-format prompt
        fix) commonly use @pytest.fixture / class-based tests and
        have no if __name__ == "__main__" block, so `python
        script.py` just does nothing or errors on indentation —
        they need pytest itself to run, and rely on fixtures
        (like the pytest-playwright plugin's `page` fixture) that
        may not even be installed. Rather than guess and produce a
        confusing traceback, detect this up front and say so
        clearly.
        """

        has_main_block = "__main__" in script_text

        has_pytest_markers = (
            "@pytest" in script_text
            or "def test_" in script_text
            or "class Test" in script_text
        )

        return has_pytest_markers and not has_main_block

    # --------------------------------------------------

    def is_playwright_installed(self):

        try:

            import playwright  # noqa: F401

            return True

        except ImportError:

            return False

    # --------------------------------------------------

    def run_script(
        self, script_text, tc_number="script", timeout_seconds=None,
        on_process_started=None,
    ):
        """
        `on_process_started`, if given, is called once with the
        running subprocess.Popen — same convention as
        record_manual_script() — so a caller running this from a
        background job (see
        Core/automation_web_repository.py's TestCasesWeb._run_job())
        can look the process up later for a genuine Cancel action.
        Also settable via self._current_process /
        cancel_current_run(), same mechanism run_script_interactive()
        already uses.
        """

        if not self.is_playwright_installed():

            return {
                "success": False,
                "error": (
                    "Playwright isn't installed yet. Run these two "
                    "commands in your terminal, then try again:\n\n"
                    "pip install playwright\n"
                    "playwright install chromium"
                ),
            }

        timeout_seconds = timeout_seconds or DEFAULT_TIMEOUT_SECONDS

        script_text = self._strip_code_fences(script_text)

        if self._looks_like_pytest_style(script_text):

            return {
                "success": False,
                "error": (
                    "This script was generated in an older pytest-"
                    "style format that isn't directly runnable here. "
                    "Click 'Update Automation' on this test case to "
                    "regenerate it in the standalone format, then "
                    "try Execute again."
                ),
            }

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

        safe_name = "".join(
            c if c.isalnum() else "_" for c in tc_number
        )

        script_path = OUTPUT_FOLDER / f"{safe_name}_{timestamp}.py"

        slow_mo_ms, timeout_ms = self._resolve_speed_settings()

        screenshot_path = EVIDENCE_FOLDER / f"{safe_name}_{timestamp}_failure.png"

        evidence_shim = EVIDENCE_SHIM_TEMPLATE.replace(
            "__SCREENSHOT_PATH_REPR__", repr(str(screenshot_path))
        )

        script_with_shim = (
            SPEED_SHIM_TEMPLATE.format(slow_mo=slow_mo_ms, timeout=timeout_ms)
            + evidence_shim
            + script_text
        )

        if self.force_headless:

            script_with_shim = HEADLESS_SHIM_TEMPLATE + script_with_shim

        script_path.write_text(script_with_shim, encoding="utf-8")

        self.logger.info(
            f"Running Playwright script: {script_path} "
            f"(slow_mo={slow_mo_ms}ms, timeout={timeout_ms}ms)"
        )

        start = datetime.now()

        self._cancel_requested = False

        # BUGFIX/IMPROVEMENT (web port): this used to be subprocess.run(),
        # which blocks with no way to reach the child process from
        # another thread — there was no way to implement a genuine
        # Cancel for a queued/background Execute (see
        # TestCasesWeb.cancel_execution() in
        # automation_web_repository.py). Popen + communicate(timeout=)
        # is functionally equivalent for the normal/timeout paths
        # (matches subprocess.run's own documented implementation)
        # but exposes self._current_process so cancel_current_run()
        # — already used by the interactive path — works here too.
        process = subprocess.Popen(
            [sys.executable, str(script_path)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

        self._current_process = process

        if on_process_started:

            on_process_started(process)

        try:

            try:

                stdout, stderr = process.communicate(timeout=timeout_seconds)

            except subprocess.TimeoutExpired:

                process.kill()

                stdout, stderr = process.communicate()

                duration = (datetime.now() - start).total_seconds()

                self.logger.warning(
                    f"Playwright script timed out after {timeout_seconds}s"
                )

                return {
                    "success": False,
                    "error": (
                        f"Script did not finish within {timeout_seconds} "
                        f"seconds and was stopped. It may be waiting on "
                        f"a page element that never appeared, or a wrong "
                        f"URL/selector. Currently using {slow_mo_ms}ms "
                        f"slow motion and a {timeout_ms}ms per-action "
                        f"timeout — if the app is just slow to load, try "
                        f"raising the timeout in Test Environment "
                        f"Settings rather than the overall run timeout."
                    ),
                    "stdout": stdout or "",
                    "stderr": stderr or "",
                    "duration": duration,
                    "script_path": str(script_path),
                    "slow_mo_ms": slow_mo_ms,
                    "timeout_ms": timeout_ms,
                }

            duration = (datetime.now() - start).total_seconds()

            cancelled = self._cancel_requested and process.returncode != 0

            passed = process.returncode == 0

            found_screenshot = None

            for line in (stdout or "").splitlines():

                if line.startswith("QA_EVIDENCE_SCREENSHOT::"):

                    candidate = line[len("QA_EVIDENCE_SCREENSHOT::"):]

                    if Path(candidate).exists():

                        found_screenshot = candidate

            self.logger.info(
                f"Playwright script finished in {duration:.1f}s — "
                f"{'CANCELLED' if cancelled else ('PASS' if passed else 'FAIL')} "
                f"(exit code {process.returncode})"
            )

            return {
                "success": passed,
                "cancelled": cancelled,
                "stdout": stdout,
                "stderr": stderr,
                "return_code": process.returncode,
                "duration": duration,
                "script_path": str(script_path),
                "screenshot_path": found_screenshot,
                "slow_mo_ms": slow_mo_ms,
                "timeout_ms": timeout_ms,
            }

        except Exception as ex:

            self.logger.exception(
                "Playwright script execution failed to start."
            )

            return {
                "success": False,
                "error": str(ex),
                "script_path": str(script_path),
            }

        finally:

            self._current_process = None

    # --------------------------------------------------
    # Interactive execution — pause on a broken locator, ask the
    # operator, retry, and (if confirmed) persist the fix.
    # --------------------------------------------------

    def _extract_interactive_parts(self, script_text):
        """
        Only scripts built by
        TestExecutionManager._build_playwright_script() have this
        exact, guaranteed shape: a "def run(playwright):" function
        with ONE unindented Playwright statement per line and no
        nested blocks (loops/ifs/functions) — that constraint is
        baked into the AI generation prompt specifically so this
        kind of mechanical line-by-line wrapping is safe.

        Manually Recorded scripts (Playwright's own codegen output)
        can use context managers, multiple pages, loops — arbitrary
        structure — so wrapping them the same way isn't safe. This
        returns None for anything that doesn't match; the caller
        then tries _extract_generic_interactive_parts() (handles the
        Manually Recorded shape) before finally falling back to the
        normal, non-interactive run_script().
        """

        try:
            tree = ast.parse(script_text)
        except SyntaxError:
            return None
        run_func = next(
            (node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "run"),
            None,
        )
        if run_func is None:
            return None

        source_lines = script_text.splitlines()
        metadata_by_action_line = {}
        pending = None
        for line_number, raw_line in enumerate(source_lines, start=1):
            line = raw_line.strip()
            marker = re.match(r"#\s*QA_STEP:\s*(\d+)(?:\s*\|\s*(.*))?", line, re.IGNORECASE)
            if marker:
                pending = {"step": int(marker.group(1)), "description": (marker.group(2) or "").strip()}
                continue
            metadata_match = re.match(
                r"#\s*(DESCRIPTION|FIELD_TYPE|FIELD_NAME|SECTION|ACTION_TYPE|PARENT_LOCATOR|PRIMARY_LOCATOR|FALLBACK_LOCATORS|XPATH|IS_SENSITIVE|SENSITIVE|SOURCE):\s*(.*)",
                line, re.IGNORECASE,
            )
            if metadata_match and pending is not None:
                key = metadata_match.group(1).lower()
                pending["is_sensitive" if key == "sensitive" else key] = metadata_match.group(2).strip()
                continue
            if pending is not None and line and not line.startswith("#"):
                metadata_by_action_line[line_number] = pending
                pending = None

        parts = []
        has_structured_steps = bool(metadata_by_action_line)
        for statement in run_func.body:
            metadata = metadata_by_action_line.get(statement.lineno)
            if has_structured_steps and metadata is None:
                continue
            source = ast.get_source_segment(script_text, statement) or ""
            if not self._is_executable_recorded_statement(statement, source):
                continue
            metadata = metadata or {
                "step": len(parts) + 1,
                "description": "Execute action",
            }
            parts.append({
                "step": metadata["step"],
                "metadata": metadata,
                "code": source.strip(),
            })
        return parts or None

    @staticmethod
    def _is_executable_recorded_statement(statement, source):
        if any(token in source for token in (
            ".launch(", ".new_context(", ".new_page(",
            "context.close(", "browser.close(",
        )):
            return False
        if not isinstance(statement, ast.Expr) or not isinstance(statement.value, ast.Call):
            return False
        call = statement.value
        function = call.func
        if isinstance(function, ast.Name):
            return function.id in {"expect", "assert"}
        if not isinstance(function, ast.Attribute):
            return False
        # A bare page.locator(...) / page.get_by_*(...) constructs a locator
        # but performs no browser/test action.
        if isinstance(function.value, ast.Name) and function.value.id == "page":
            if function.attr == "locator" or function.attr.startswith("get_by_"):
                return False
        return function.attr in {
            "goto", "click", "fill", "press", "check", "uncheck",
            "select_option", "set_input_files", "hover", "dblclick",
            "type", "clear", "focus", "tap", "drag_to", "expect",
            "to_be_visible", "to_have_text", "to_have_value",
            "to_be_checked", "to_contain_text", "to_have_url",
        }

    def _build_interactive_script(
        self, script_text, slow_mo_ms, timeout_ms, max_repair_rounds
    ):

        lines = self._extract_interactive_parts(script_text)

        if lines is None:

            return None

        harness = INTERACTIVE_HARNESS_TEMPLATE.replace(
            "__MAX_REPAIR_ROUNDS__", str(max_repair_rounds)
        ).replace("__SELECTION_TIMEOUT_MS__", str(max(5000, min(timeout_ms * 2, 120000)))).replace("__TOTAL_STEPS__", str(len(lines)))

        steps_code = "\n".join(
            f"        _qa_run_step({item['step']}, {item['code']!r}, {item['metadata']!r})"
            for item in lines
        )

        return (
            harness
            + "with sync_playwright() as playwright:\n"
            f"    browser = playwright.chromium.launch(headless=False, slow_mo={slow_mo_ms})\n"
            "    page = browser.new_page()\n"
            f"    page.set_default_timeout({timeout_ms})\n"
            f"    page.set_default_navigation_timeout({timeout_ms})\n"
            "    try:\n"
            f"{steps_code}\n"
            "    finally:\n"
            "        browser.close()\n\n"
            "_qa_send_event({\"event\": \"run_finished\", "
            "\"repairs\": _QA_REPAIRS})\n"
            "print('TEST PASSED')\n"
        )

    def _extract_generic_interactive_parts(self, script_text):
        """
        Covers Manually Recorded scripts. Playwright's own `codegen
        --target python` tool (see
        TestExecutionManager.record_manual_script(), which always
        invokes codegen with that exact target) emits ONE fixed
        wrapper shape regardless of what was actually recorded:

            def run(playwright: Playwright) -> None:
                <whatever was recorded — loops, multiple tabs or
                 contexts, comments, arbitrary structure>

            with sync_playwright() as playwright:
                run(playwright)

        Unlike _extract_interactive_parts() above (which assumes
        ONE flat, unindented statement per line — only ever true for
        our own AI-generated shape), this uses the `ast` module so a
        whole compound statement — a `for` loop, an `if`, a `with
        page.expect_popup() as ...:` block spanning several lines —
        is kept and repaired as ONE unit, exactly as it was
        recorded, instead of requiring it to be split apart. A
        locator failure inside a multi-line step re-runs that whole
        step (e.g. the whole loop) on retry, not just the one
        failing line — a reasonable trade-off for not having to
        guess how to safely slice into arbitrary nested code.

        Returns a list of exact source strings (one per top-level
        statement inside run()'s body, in original order) or None
        if the script doesn't match this shape — the caller then
        falls back to the normal, non-interactive run_script(),
        exactly as it always has for anything unrecognised.
        """

        try:

            tree = ast.parse(script_text)

        except SyntaxError:

            return None

        run_func = None

        for node in tree.body:

            if isinstance(node, ast.FunctionDef) and node.name == "run":

                run_func = node

                break

        if run_func is None or not run_func.body:

            return None

        if (
            not run_func.args.args
            or run_func.args.args[0].arg != "playwright"
        ):

            # Not the standard codegen shape — rather than guess at
            # a renamed parameter, treat this as unsupported and let
            # it fall back to a normal (non-interactive) run.
            return None

        if "with sync_playwright() as playwright:" not in script_text:

            return None

        statements = []

        for stmt in run_func.body:

            source = ast.get_source_segment(script_text, stmt)

            if source and not re.fullmatch(
                r"\s*page\.(?:locator|get_by_[a-z_]+)\(.*\)\s*", source,
            ):

                statements.append(source)

        return statements or None

    def _extract_script_imports(self, script_text):
        """
        Returns the exact source text of every top-level import
        statement in the recorded script (e.g. "from playwright.sync_api
        import Playwright, sync_playwright, expect"), in original
        order, or "" if none/parsing fails.

        Root cause fix (2026-09-09): a Manually Recorded (Playwright
        codegen) script's steps commonly reference names from that
        import line INSIDE run()'s body — most importantly expect()
        for assertions, which `--target python` emits by default for
        every recorded assertion. _build_generic_interactive_script()
        below only ever wraps run()'s BODY statements; it never
        carried the script's own import line along, so the interactive
        harness executed every such statement with
        exec(code, globals()) against the HARNESS's globals(), which
        only defines what INTERACTIVE_HARNESS_TEMPLATE itself imports
        (json/re/sys/sync_playwright) — never expect(). The result:
        any step using expect(...) failed with
        "NameError: name 'expect' is not defined" on every single
        attempt, forever, no matter what locator/value fix the
        operator applied — and because that failure still routes
        through the SAME step_failed event as a real locator miss, the
        operator was shown a locator-repair prompt for a problem no
        locator fix could ever solve, making replay LOOK like it
        "doesn't properly continue" past that step. Carrying the
        script's own imports along fixes this at the source, for
        whatever names it actually imports — not just expect().
        """

        try:

            tree = ast.parse(script_text)

        except SyntaxError:

            return ""

        lines = []

        for node in tree.body:

            if isinstance(node, (ast.Import, ast.ImportFrom)):

                source = ast.get_source_segment(script_text, node)

                if source:

                    lines.append(source)

        return "\n".join(lines)

    def _build_generic_interactive_script(
        self, script_text, slow_mo_ms, timeout_ms, max_repair_rounds
    ):
        """
        The Manually Recorded counterpart to _build_interactive_script().
        run()'s original setup/teardown statements (launching the
        browser, opening a context/page, closing them) are executed
        EXACTLY as recorded — whatever variable names or number of
        contexts/pages/tabs were used — rather than stripped and
        regenerated like the flat AI-generated path does, since a
        Manually Recorded script's setup isn't guaranteed to have
        that same fixed one-browser-one-page shape. Speed/timeout is
        applied the same structure-agnostic way run_script() already
        applies it to every script (SPEED_SHIM_TEMPLATE monkeypatches
        launch()/new_page() at the Playwright class level), so it
        doesn't matter what the recorded launch()/new_page() calls
        look like.

        Only the statements that AREN'T setup/teardown (i.e. the
        actual recorded actions — clicks, fills, assertions, ...)
        are wrapped in _qa_run_step() and become repairable; setup/
        teardown failures are environment problems ("browser wouldn't
        launch"), not locator problems, same as the AI-generated path.
        """

        statements = self._extract_generic_interactive_parts(script_text)

        if statements is None:

            return None

        harness = INTERACTIVE_HARNESS_TEMPLATE.replace(
            "__MAX_REPAIR_ROUNDS__", str(max_repair_rounds)
        ).replace("__SELECTION_TIMEOUT_MS__", str(max(5000, min(timeout_ms * 2, 120000))))

        shim = SPEED_SHIM_TEMPLATE.format(
            slow_mo=slow_mo_ms, timeout=timeout_ms
        )

        setup_or_teardown_markers = (
            ".launch(", ".new_context(", ".new_page(", ".close(",
        )

        body_lines = []
        cleanup_lines = []

        step_index = 1

        for stmt_source in statements:

            stripped = stmt_source.strip()

            is_setup_or_teardown = (
                "\n" not in stripped
                and any(
                    marker in stripped
                    for marker in setup_or_teardown_markers
                )
            )

            if is_setup_or_teardown and ".close(" in stripped:

                cleanup_lines.append(stripped)

            elif is_setup_or_teardown:

                body_lines.append("    " + stripped)

            else:

                body_lines.append(
                    f"        _qa_run_step({step_index}, {stmt_source!r})"
                )

                step_index += 1

        if step_index == 1:

            # Nothing that looked like an actual recorded action —
            # either an all-setup script, or codegen output we
            # didn't recognise confidently enough. Safer to report
            # "not supported" than run something with nothing
            # repairable in it.
            return None

        harness = harness.replace("__TOTAL_STEPS__", str(step_index - 1))

        setup_lines = [line for line in body_lines if line.startswith("    ") and not line.startswith("        ")]
        action_lines = [line for line in body_lines if line.startswith("        ")]
        guarded_cleanup = []
        for close_statement in cleanup_lines:
            guarded_cleanup.extend([
                "        try:",
                "            " + close_statement,
                "        except Exception:",
                "            pass",
            ])
        steps_code = "\n".join(
            setup_lines + ["    try:"] + action_lines + ["    finally:"]
            + (guarded_cleanup or ["        pass"])
        )

        # Carry the recorded script's OWN top-level imports along —
        # see _extract_script_imports()'s docstring for why this is
        # required for expect() (and anything else the script itself
        # imports) to actually be defined when each step's exact
        # source text is exec()'d inside _qa_run_step(). Placed after
        # the harness so nothing here can shadow the harness's own
        # names; re-importing sync_playwright itself (already imported
        # above) is harmless.
        script_imports = self._extract_script_imports(script_text)

        imports_block = (script_imports + "\n\n") if script_imports else ""

        return (
            shim
            + harness
            + imports_block
            + "with sync_playwright() as playwright:\n"
            f"{steps_code}\n\n"
            "_qa_send_event({\"event\": \"run_finished\", "
            "\"repairs\": _QA_REPAIRS})\n"
            "print('TEST PASSED')\n"
        )

    def run_script_interactive(
        self,
        script_text,
        tc_number="script",
        on_step_failed=None,
        on_repaired=None,
        on_locator_verified=None,
        on_element_selected=None,
        on_runtime_event=None,
        max_repair_rounds=3,
    ):
        """
        Interactive counterpart to run_script() — for a single,
        operator-watched Execute only (never a multi-test batch run,
        where nobody would be there to answer a prompt; the caller
        is responsible for only using this when exactly one test
        case is being run).

        Whenever a step fails, calls on_step_failed(event_dict)
        SYNCHRONOUSLY and uses whatever decision dict it returns. If
        that decision is {"action": "verify_locator", "locator": ...}
        (a live-page existence/visibility check that does NOT attempt
        the step and does NOT count as a repair round), the harness
        reports the result as a "locator_verified" event, which this
        method relays to on_locator_verified(event_dict) the exact
        same way, and keeps doing so for as many verify round-trips as
        the operator asks for — only once a terminal decision (retry /
        retry_code / cancel) comes back does the harness resume the
        run. This is what lets the UI test an AI-suggested or manually
        typed locator against the CURRENT live page before committing
        to Retry With This Fix.

        On the original terminal decision path, the caller (a
        background QThread; see
        App/UI/QAAutomation/test_execution_worker.py's
        PlaywrightInteractiveWorker) is expected to relay the event
        to the UI thread, show the repair dialog, and block until
        the operator answers. Tries the flat AI-generated shape
        first (_build_interactive_script()), then the standard
        Manually Recorded / Playwright-codegen shape
        (_build_generic_interactive_script()) — covering both kinds
        of script this app produces. Returns
        {"interactive_supported": False} immediately, without
        running anything, only if NEITHER shape matches — the caller
        should fall back to run_script() in that case.
        """

        if not self.is_playwright_installed():

            return {
                "interactive_supported": True,
                "success": False,
                "error": (
                    "Playwright isn't installed yet. Run these two "
                    "commands in your terminal, then try again:\n\n"
                    "pip install playwright\n"
                    "playwright install chromium"
                ),
            }

        script_text = self._strip_code_fences(script_text)

        slow_mo_ms, timeout_ms = self._resolve_speed_settings()

        interactive_script = self._build_interactive_script(
            script_text, slow_mo_ms, timeout_ms, max_repair_rounds
        )

        if interactive_script is None:

            interactive_script = self._build_generic_interactive_script(
                script_text, slow_mo_ms, timeout_ms, max_repair_rounds
            )

        if interactive_script is None:

            return {"interactive_supported": False}

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S%f")

        safe_name = "".join(
            c if c.isalnum() else "_" for c in tc_number
        )

        script_path = (
            OUTPUT_FOLDER / f"{safe_name}_{timestamp}_interactive.py"
        )

        script_path.write_text(interactive_script, encoding="utf-8")
        self._current_script_path = script_path

        self.logger.info(
            f"Running Playwright script interactively: {script_path} "
            f"(slow_mo={slow_mo_ms}ms, timeout={timeout_ms}ms)"
        )

        start = datetime.now()

        repairs = []

        cancelled = False

        stdout_lines = []

        self._cancel_requested = False
        popen_kwargs = {}
        if os.name == "nt":
            popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            popen_kwargs["start_new_session"] = True
        process = subprocess.Popen(
            [sys.executable, str(script_path)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            env={**os.environ, "QA_SELECTION_CANCEL_FILE": str(script_path) + ".selection.cancel"},
            **popen_kwargs,
        )

        self._current_process = process

        try:

            while True:

                line = process.stdout.readline()

                if not line:

                    break

                line = line.rstrip("\n")

                if not line.startswith("QA_EVENT::"):

                    stdout_lines.append(line)

                    continue

                try:

                    event = json.loads(line[len("QA_EVENT::"):])

                except (TypeError, ValueError):

                    continue

                event_type = event.get("event")

                if event_type in {
                    "step_started", "step_passed", "step_retrying",
                    "fallback_succeeded",
                    "selection_started", "selection_waiting",
                    "selection_cancelled", "selection_failed",
                    "auto_repair_attempted", "parent_reopen_succeeded",
                } and on_runtime_event:
                    on_runtime_event(event)

                if event_type == "step_failed":

                    decision = (
                        on_step_failed(event) if on_step_failed
                        else {"action": "cancel"}
                    )

                    command = decision or {"action": "cancel"}

                    process.stdin.write(json.dumps(command) + "\n")

                    process.stdin.flush()

                elif event_type == "locator_verified":

                    # Result of a non-terminal "verify_locator" check
                    # the harness performed for the SAME still-open
                    # step_failed pause — relay it and, exactly like
                    # step_failed above, write back whatever decision
                    # comes back (which may itself be another
                    # verify_locator, another round of Ask AI having
                    # happened out-of-band, or a terminal retry/
                    # retry_code/cancel that finally lets the harness
                    # resume).
                    decision = (
                        on_locator_verified(event) if on_locator_verified
                        else {"action": "cancel"}
                    )

                    command = decision or {"action": "cancel"}

                    process.stdin.write(json.dumps(command) + "\n")

                    process.stdin.flush()

                elif event_type == "element_selected":

                    self.logger.info(
                        "[repair] click target tag=%s text=%s actionable=%s section=%r keys=%s",
                        (event.get("clicked") or {}).get("tag"),
                        (event.get("clicked") or {}).get("text"),
                        event.get("actionable"), event.get("section"),
                        sorted(event.keys()),
                    )
                    for candidate in (event.get("candidates") or [])[:50]:
                        verification = candidate.get("verification") or {}
                        self.logger.info(
                            "[repair] candidate type=%s score=%s locator=%s count=%s visible=%s actionable=%s rejected=%s",
                            candidate.get("type"), candidate.get("score"),
                            candidate.get("expression") or candidate.get("locator"),
                            verification.get("count"), verification.get("visible"),
                            verification.get("actionable"), candidate.get("rejected_reason"),
                        )
                    self.logger.info(
                        "[repair] subprocess event emitted=element_selected step=%s selected=%s corrected_code=%s xpath=%s",
                        event.get("step"), event.get("locator"),
                        event.get("corrected_code"), event.get("xpath"),
                    )

                    if on_element_selected:

                        on_element_selected(event)

                elif event_type == "step_repaired":

                    repair = {
                        "step": event.get("step"),
                        "original": event.get("original_code"),
                        "corrected": event.get("corrected_code"),
                        "selection": {
                            "selected_candidate": event.get("selected_candidate") or {},
                            "candidates": event.get("candidates") or [],
                            "xpath": event.get("xpath"),
                            "section": event.get("section"),
                            "accessible_name": event.get("field_name"),
                        },
                    }

                    repairs.append(repair)

                    if on_repaired:

                        on_repaired(repair)

                elif event_type == "run_cancelled_by_operator":

                    cancelled = True

        finally:

            try:

                process.stdin.close()

            except Exception:

                pass

            self._current_process = None
            try:
                Path(str(script_path) + ".selection.cancel").unlink(missing_ok=True)
            except Exception:
                pass
            self._current_script_path = None

        return_code = process.wait()
        cancelled = cancelled or (self._cancel_requested and return_code != 0)

        stderr_text = process.stderr.read() if process.stderr else ""

        duration = (datetime.now() - start).total_seconds()

        success = (return_code == 0) and not cancelled

        self.logger.info(
            f"Interactive Playwright run finished in {duration:.1f}s "
            f"— {'PASS' if success else ('CANCELLED' if cancelled else 'FAIL')} "
            f"(exit code {return_code}, {len(repairs)} repair(s))"
        )

        return {
            "interactive_supported": True,
            "success": success,
            "cancelled": cancelled,
            "stdout": "\n".join(stdout_lines),
            "stderr": stderr_text,
            "return_code": return_code,
            "duration": duration,
            "script_path": str(script_path),
            "slow_mo_ms": slow_mo_ms,
            "timeout_ms": timeout_ms,
            "repairs": repairs,
        }

    def cancel_current_run(self):
        """
        Called from the UI thread (Cancel Execution) while an
        interactive run is in progress on the background thread — or,
        on the web port, from a request handler while a background
        Execute job (run_script(), not just run_script_interactive())
        is in progress on its own worker thread. Popen.terminate() is
        safe to call from another thread.
        """

        process = self._current_process

        if process and process.poll() is None:

            self._cancel_requested = True

            try:
                if os.name == "nt":
                    subprocess.run(
                        ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                        timeout=8, check=False,
                    )
                else:
                    os.killpg(process.pid, signal.SIGTERM)
            except Exception:
                try:
                    process.kill()
                except Exception:
                    pass

    def cancel_current_selection(self):
        process = self._current_process
        if process and process.poll() is None:
            control_path = Path(str(getattr(self, "_current_script_path", "")) + ".selection.cancel")
            if control_path.name != ".selection.cancel":
                control_path.touch()
