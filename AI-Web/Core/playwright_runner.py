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
import re
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
import re
import sys

from playwright.sync_api import sync_playwright

_QA_MAX_REPAIR_ROUNDS = __MAX_REPAIR_ROUNDS__

_QA_REPAIRS = []


def _qa_send_event(event):
    print("QA_EVENT::" + json.dumps(event), flush=True)


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
        return page.evaluate("""() => {
            const out = [];
            const seen = new Set();
            const attrPriority = ["data-testid", "data-test", "data-cy", "data-qa", "id", "name"];
            const nodes = document.querySelectorAll(
                "input, button, select, textarea, a, [role], " +
                "[data-testid], [data-test], [data-cy], [data-qa]"
            );
            for (const el of nodes) {
                if (out.length >= 80) break;
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


def _qa_capture_context():
    context = {"url": "", "title": "", "accessibility": [], "dom_candidates": []}
    try:
        context["url"] = page.url
    except Exception:
        pass
    try:
        context["title"] = page.title()
    except Exception:
        pass
    try:
        snapshot = page.accessibility.snapshot() or {}
        context["accessibility"] = _qa_flatten_accessibility(snapshot, [])
    except Exception:
        pass
    try:
        context["dom_candidates"] = _qa_capture_dom_candidates()
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
        loc = page.locator(raw_locator)
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


def _qa_handle_step_failure(step_number, code_text, error_text, attempt):
    locator, value = _qa_extract_locator_and_value(code_text)
    context = _qa_capture_context()
    _qa_send_event({
        "event": "step_failed",
        "step": step_number,
        "code": code_text,
        "locator": locator,
        "value": value,
        "error": error_text,
        "attempt": attempt,
        "url": context["url"],
        "title": context["title"],
        "accessibility": context["accessibility"],
        "dom_candidates": context["dom_candidates"],
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
        if action == "verify_locator":
            check_locator = command.get("locator") or locator
            verify_result = _qa_verify_locator(check_locator)
            _qa_send_event({
                "event": "locator_verified",
                "step": step_number,
                "locator": check_locator,
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


def _qa_run_step(step_number, code_text):
    current_code = code_text
    attempt = 0
    while True:
        try:
            exec(current_code, globals())
            if current_code != code_text:
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
            return
        except Exception as ex:
            attempt += 1
            if attempt > _QA_MAX_REPAIR_ROUNDS:
                _qa_send_event({
                    "event": "step_gave_up",
                    "step": step_number,
                    "code": current_code,
                    "error": str(ex),
                })
                # ROOT CAUSE (confirmed 2026-09-09 from a real
                # production run -- id 36 -- that ended with the
                # unhelpful error_message "SyntaxError: invalid
                # syntax"): this used to be a bare `raise`, which
                # propagates the LAST attempt's exception uncaught out
                # of the whole subprocess -- Python then dumps a full
                # traceback to stderr (exit code 1, indistinguishable
                # from any other crash) and playwright_runner.py's
                # run_script_interactive() readline loop had no
                # "step_gave_up" branch at all, so that event was
                # silently dropped and the operator only ever saw the
                # LAST attempt's raw error text, not "gave up after N
                # repair attempts". A clean, distinct exit code here
                # (matching run_cancelled_by_operator's sys.exit(2)
                # pattern just below) lets run_script_interactive()
                # recognize this specific outcome and report it
                # properly instead of treating it as an unexplained
                # crash.
                sys.exit(3)
            outcome = _qa_handle_step_failure(step_number, current_code, str(ex), attempt)
            if outcome is None:
                _qa_send_event({
                    "event": "run_cancelled_by_operator",
                    "step": step_number,
                })
                sys.exit(2)
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

        match = re.search(
            r"def run\(playwright\):\n(.*?)\n    browser\.close\(\)",
            script_text,
            re.DOTALL,
        )

        if not match:

            return None

        lines = []

        for raw_line in match.group(1).split("\n"):

            line = raw_line.strip()

            if not line:

                continue

            if line.startswith("browser = ") or line.startswith(
                "page = browser.new_page"
            ):

                # Browser/page setup, not a "step" — a failure here
                # is an environment problem (browser won't launch),
                # not a locator problem, so it isn't a candidate for
                # the locator-repair dialog.
                continue

            lines.append(line)

        return lines or None

    def _build_interactive_script(
        self, script_text, slow_mo_ms, timeout_ms, max_repair_rounds
    ):

        lines = self._extract_interactive_parts(script_text)

        if lines is None:

            return None

        harness = INTERACTIVE_HARNESS_TEMPLATE.replace(
            "__MAX_REPAIR_ROUNDS__", str(max_repair_rounds)
        )

        steps_code = "\n".join(
            f"    _qa_run_step({index}, {line!r})"
            for index, line in enumerate(lines)
        )

        return (
            harness
            + "with sync_playwright() as playwright:\n"
            f"    browser = playwright.chromium.launch(headless=False, slow_mo={slow_mo_ms})\n"
            "    page = browser.new_page()\n"
            f"    page.set_default_timeout({timeout_ms})\n"
            f"    page.set_default_navigation_timeout({timeout_ms})\n"
            f"{steps_code}\n"
            "    browser.close()\n\n"
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

            if source:

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
        )

        shim = SPEED_SHIM_TEMPLATE.format(
            slow_mo=slow_mo_ms, timeout=timeout_ms
        )

        setup_or_teardown_markers = (
            ".launch(", ".new_context(", ".new_page(", ".close(",
        )

        body_lines = []

        step_index = 0

        for stmt_source in statements:

            stripped = stmt_source.strip()

            is_setup_or_teardown = (
                "\n" not in stripped
                and any(
                    marker in stripped
                    for marker in setup_or_teardown_markers
                )
            )

            if is_setup_or_teardown:

                body_lines.append("    " + stripped)

            else:

                body_lines.append(
                    f"    _qa_run_step({step_index}, {stmt_source!r})"
                )

                step_index += 1

        if step_index == 0:

            # Nothing that looked like an actual recorded action —
            # either an all-setup script, or codegen output we
            # didn't recognise confidently enough. Safer to report
            # "not supported" than run something with nothing
            # repairable in it.
            return None

        steps_code = "\n".join(body_lines)

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

        self.logger.info(
            f"Running Playwright script interactively: {script_path} "
            f"(slow_mo={slow_mo_ms}ms, timeout={timeout_ms}ms)"
        )

        start = datetime.now()

        repairs = []

        cancelled = False

        gave_up = None

        stdout_lines = []

        process = subprocess.Popen(
            [sys.executable, str(script_path)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
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

                elif event_type == "step_repaired":

                    repair = {
                        "step": event.get("step"),
                        "original": event.get("original_code"),
                        "corrected": event.get("corrected_code"),
                    }

                    repairs.append(repair)

                    if on_repaired:

                        on_repaired(repair)

                elif event_type == "run_cancelled_by_operator":

                    cancelled = True

                elif event_type == "step_gave_up":

                    # ROOT CAUSE (confirmed 2026-09-09, real run id
                    # 36): the interactive harness (see
                    # INTERACTIVE_HARNESS_TEMPLATE's _qa_run_step()
                    # above) already sent this event once
                    # _QA_MAX_REPAIR_ROUNDS was exhausted on a single
                    # step, but nothing here ever listened for it —
                    # the branch simply didn't exist, so the event was
                    # silently dropped and the operator's only signal
                    # was whatever raw text ended up on stderr. Capture
                    # it so the final result carries a clean, specific
                    # reason instead of a bare stack-trace tail.
                    gave_up = {
                        "step": event.get("step"),
                        "code": event.get("code"),
                        "error": event.get("error"),
                    }

        finally:

            try:

                process.stdin.close()

            except Exception:

                pass

            self._current_process = None

        return_code = process.wait()

        stderr_text = process.stderr.read() if process.stderr else ""

        duration = (datetime.now() - start).total_seconds()

        success = (return_code == 0) and not cancelled

        self.logger.info(
            f"Interactive Playwright run finished in {duration:.1f}s "
            f"— {'PASS' if success else ('CANCELLED' if cancelled else ('GAVE UP' if gave_up else 'FAIL'))} "
            f"(exit code {return_code}, {len(repairs)} repair(s))"
        )

        result = {
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

        if gave_up:

            # AutomationExecutionRepository.mark_finished() reads
            # result["error"] FIRST and only falls back to the last
            # non-empty line of stderr when it's absent -- populating
            # it here is what actually fixes Execution Log/Run Details
            # showing "SyntaxError: invalid syntax" (an opaque
            # traceback tail) instead of a real explanation. Note this
            # also makes mark_finished() classify the run as "Error"
            # rather than "Failed" (its has_hard_error check is `there
            # IS an "error" key and no stdout`) -- deliberate: giving
            # up after exhausting repair attempts on one step is not
            # the same thing as a script running cleanly to a failed
            # assertion, so "Error" is the more honest status here,
            # consistent with how this same method already treats
            # "Playwright isn't installed" as Error rather than Fail.
            result["gave_up"] = gave_up
            result["error"] = (
                f"Step {gave_up.get('step')} could not be repaired "
                f"after {max_repair_rounds} attempt(s) — the run was "
                f"stopped rather than left hanging. Last attempted "
                f"code: {gave_up.get('code')!r}. Last error: "
                f"{gave_up.get('error')}"
            )

        return result

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

                process.terminate()

            except Exception:

                pass