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
import builtins as _qa_builtins_module
import hashlib
import json
import os
import re
import signal
import subprocess
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

from Core.logger import Logger
from Core.playwright_semantics import semantic_name_compatible
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
APPLICATION_ROOT = Path(__file__).resolve().parent.parent

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
import time
from pathlib import Path

from Core.playwright_semantics import semantic_name_compatible
# Task-1 audit D5: `expect` must exist in the harness namespace -- recorded /
# imported assertion steps (expect(...).to_be_visible(), ...) are executed by
# _qa_run_step() via exec(code, globals()) and previously failed with
# "name 'expect' is not defined" on the flat (non-generic) extraction path.
from playwright.sync_api import sync_playwright, expect
from Core.calendar_engine import CalendarAction, CalendarError, CalendarVerificationError, verify_selection
# DATE-ENTRY-ENGINE: generic, application-independent date entry + verification
# (masked / native / plain text date inputs). See Core/date_entry_engine.py.
from Core import date_entry_engine as _qa_date_engine

# --------------------------------------------------------------------
# RUNTIME IDENTITY PROOF (2026-09-25) — child side.
#
# Root-cause context: a prior "name 'time' is not defined" failure
# during post-action verification was traced to the PARENT (FastAPI/
# uvicorn) process running with --no-reload, which never re-imports
# Core.playwright_runner after it is first loaded — so an on-disk fix
# to this template can sit unused in a long-running server process
# until it is actually restarted. This banner is written into every
# generated child script and printed to stdout the instant that child
# process starts, BEFORE any browser is launched or any step runs, so
# the operator/dev can always tell — by reading Output/AutomationRuns/
# *_interactive.py itself, or this printed line — exactly which
# harness template (by hash) actually produced the file that is about
# to execute. __HARNESS_VERSION__ below is substituted by the PARENT
# at build time with the sha256 of the exact INTERACTIVE_HARNESS_TEMPLATE
# text it used; if that value here ever fails to match what the parent
# logged as "harness_template_sha256" for this same run, the parent
# and the file on disk have diverged and the process needs a restart.
print(
    "QA_CHILD_DIAGNOSTIC::" + json.dumps({
        "harness_version": "__HARNESS_VERSION__",
        "python": sys.executable,
        "cwd": os.getcwd(),
        "harness_file": __file__,
        "time_available": "time" in dir(),
        "time_module": getattr(time, "__file__", "<built-in>"),
    }),
    flush=True,
)
# --- end runtime identity proof ---

_QA_MAX_REPAIR_ROUNDS = __MAX_REPAIR_ROUNDS__
_QA_SELECTION_TIMEOUT_MS = __SELECTION_TIMEOUT_MS__
_QA_DISCOVERY_TIMEOUT_MS = __DISCOVERY_TIMEOUT_MS__
_QA_STATE_READINESS_TIMEOUT_MS = __STATE_READINESS_TIMEOUT_MS__
_QA_TOTAL_STEPS = __TOTAL_STEPS__
# UNIVERSAL STEP COMPLETION GATE: Replay Speed is human-observation
# pacing ONLY -- it is never used for application/state synchronization
# (that remains entirely state-driven: the precondition/resolution
# cascade above, and the completion gate inside _qa_verify_post_action()
# below). Applied exactly once per step, AFTER that step has already
# been proven PASS (after its own step_passed event, immediately before
# control returns so Step N+1's precondition gate begins) -- never
# inside locator resolution, readiness polling, or post-action
# completion polling. Reuses the SAME configured value that already
# paces Playwright's own slow_mo (see _build_interactive_script()),
# rather than introducing a second, separately-configured setting.
_QA_REPLAY_SPEED_MS = __REPLAY_SPEED_MS__


def _qa_replay_speed_pause():
    if _QA_REPLAY_SPEED_MS > 0:
        try:
            _qa_active_page().wait_for_timeout(_QA_REPLAY_SPEED_MS)
        except Exception:
            pass

_QA_REPAIRS = []
_QA_SELECTIONS = {}
_QA_LAST_DYNAMIC_CONTROL = None
# ROUND-8-OWNER-SCOPED-INTERACTION-TARGET: holds the single resolved
# ElementHandle (or None) for the current step's owner-scoped physical
# interaction target, if any. Populated only by
# _qa_resolve_owner_scoped_trigger(), called from _qa_run_step() BEFORE
# the step's one exec() -- never after, never as a repair/retry. Reused
# across steps as scratch space only; a step that does not use the
# owner-scoped path leaves this at None.
_QA_OWNER_SCOPED_TRIGGER_HANDLE = {"handle": None}
_QA_STATE = {
    "current_step": 0, "last_passed_step": 0, "failed_step": None,
    "repair_state": "idle", "pending_selection_step": None,
    "repair_generation": 0,
}


def _qa_state_fields():
    return dict(_QA_STATE)


def _qa_clear_failed_control():
    try:
        _qa_active_page().evaluate(r"""() => {
            document.querySelectorAll('[data-qa-ai-repair-target]').forEach(el => {
                el.style.outline = el.dataset.qaAiPreviousOutline || '';
                el.style.backgroundColor = el.dataset.qaAiPreviousBackground || '';
                delete el.dataset.qaAiPreviousOutline;
                delete el.dataset.qaAiPreviousBackground;
                el.removeAttribute('data-qa-ai-repair-target');
            });
        }""")
    except Exception:
        pass


def _qa_mark_failed_control(code_text, metadata=None):
    """Persistently mark the smallest visible failed control, independently of hover."""
    _qa_clear_failed_control()
    try:
        expression = _qa_locator_expression_from_code(code_text)
        target = eval(expression, {**globals(), "page": _qa_active_page()}) if expression else None
        if target is None or not hasattr(target, "count") or target.count() < 1:
            field_name = str((metadata or {}).get("field_name") or "").strip()
            if not field_name:
                return False
            page_obj = _qa_active_page()
            alternatives = [page_obj.get_by_label(field_name, exact=True), page_obj.get_by_role("button", name=field_name, exact=True), page_obj.get_by_text(field_name, exact=True)]
            target = next((item for item in alternatives if item.count() == 1), None)
            if target is None:
                return False
        candidate = next((target.nth(i) for i in range(min(target.count(), 20)) if target.nth(i).is_visible()), None)
        if candidate is None:
            return False
        candidate.evaluate(r"""el => {
            const composite = el.closest('.k-datepicker,.k-dropdown,.k-combobox,.k-autocomplete,.k-form-field,.form-group,[role=combobox]');
            const mark = composite || el;
            mark.dataset.qaAiPreviousOutline = mark.style.outline || '';
            mark.dataset.qaAiPreviousBackground = mark.style.backgroundColor || '';
            mark.setAttribute('data-qa-ai-repair-target', 'true');
            mark.style.outline = '3px solid #f2c200';
            mark.style.backgroundColor = 'rgba(255, 235, 59, 0.28)';
            mark.scrollIntoView({block:'center', inline:'nearest', behavior:'instant'});
        }""")
        return True
    except Exception:
        return False


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
    if event.get("step") is not None:
        event.setdefault("repair_generation", _QA_STATE["repair_generation"])
        event.setdefault("current_step", _QA_STATE["current_step"])
        event.setdefault("last_passed_step", _QA_STATE["last_passed_step"])
        event.setdefault("failed_step", _QA_STATE["failed_step"])
        event.setdefault("repair_state", _QA_STATE["repair_state"])
        event.setdefault("pending_selection_step", _QA_STATE["pending_selection_step"])
    print("QA_EVENT::" + json.dumps(event), flush=True)


def _qa_active_page():
    """Preserve the script's page ownership rather than stealing it for popups."""
    candidates = []
    current = globals().get("page")
    if current is not None:
        try:
            if not current.is_closed():
                return current
            # A closed explicitly owned popup can return to its live opener.
            opener = current.opener()
            seen_openers = set()
            while opener is not None and id(opener) not in seen_openers:
                seen_openers.add(id(opener))
                if not opener.is_closed():
                    globals()["page"] = opener
                    return opener
                opener = opener.opener()
        except Exception:
            pass
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
    live = []
    for candidate in candidates:
        if id(candidate) in seen:
            continue
        seen.add(id(candidate))
        try:
            if not candidate.is_closed():
                live.append(candidate)
        except Exception:
            continue
    if len(live) == 1:
        globals()["page"] = live[0]
        return live[0]
    if live:
        raise RuntimeError("Ambiguous Playwright page ownership: no live script-bound page.")
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
        "frames": [],
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
    # One bounded snapshot at failure, never a whole-DOM polling loop.
    # Frame probes are diagnostic only: they do not dispatch actions or
    # silently change the executing page/frame.
    try:
        active = _qa_active_page()
        for frame in list(active.frames)[:20]:
            from urllib.parse import urlsplit, urlunsplit
            parts = urlsplit(frame.url)
            host = parts.netloc.rsplit("@", 1)[-1]
            path = re.sub(r"\(S\([^)]*\)\)", "[SESSION]", parts.path)
            item = {
                "url": urlunsplit((parts.scheme, host, path, "", ""))[:500],
                "name": str(frame.name or "")[:120],
                "main_frame": frame == active.main_frame,
                "locator_count": None,
            }
            if raw_locator:
                try:
                    item["locator_count"] = frame.locator(raw_locator).count()
                except Exception as ex:
                    item["probe_error"] = type(ex).__name__
            context["frames"].append(item)
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


def _qa_verify_code(code_text, metadata=None):
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
                loc.first.wait_for(state="visible", timeout=_QA_DISCOVERY_TIMEOUT_MS)
                result["visible"] = True
                result["actionable"] = bool(loc.first.evaluate(r"""el => {
                    let actionable = el.closest('button,a[href],input,select,textarea,[role=button],[role=link],[role=menuitem],[role=option],[role=tab],[onclick],[draggable="true"],[tabindex]:not([tabindex="-1"])');
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
        if result["count"] == 1 and result["visible"]:
            try:
                capture = loc.first.evaluate(r"""el => {
                    const clean = value => String(value || '').replace(/\s+/g, ' ').trim();
                    const label = el.labels && el.labels.length ? clean(el.labels[0].innerText) : '';
                    const icon = el.querySelector ? el.querySelector('i,svg,[class*="icon"]') : null;
                    return {
                        accessible_name: clean(el.getAttribute('aria-label') || label || el.innerText || el.value || el.getAttribute('placeholder')),
                        text: clean(el.innerText || el.textContent),
                        role: clean(el.getAttribute('role')),
                        actionable_tag: (el.tagName || '').toLowerCase(),
                        input_type: el.tagName === 'INPUT' ? (el.type || 'text').toLowerCase() : '',
                        label: label,
                        name: clean(el.getAttribute('name')),
                        id: clean(el.id),
                        placeholder: clean(el.getAttribute('placeholder')),
                        title: clean(el.getAttribute('title')),
                        stable_attributes: {'aria-label': clean(el.getAttribute('aria-label'))},
                        // Supporting-only evidence (never a primary name source --
                        // see _qa_semantic_compatibility()'s name_evidence list): a
                        // control's own class attribute and, when present, a child
                        // icon's class/aria-label/title. This is what lets a
                        // recorded identity like "Password visibility toggle",
                        // derived at record time from a class token such as
                        // "toggle-password", be independently re-confirmed against
                        // the SAME real DOM fact at replay time -- without ever
                        // weakening the name-matching algorithm itself.
                        class_name: clean(el.className),
                        icon_evidence: icon ? clean((icon.getAttribute('class') || '') + ' ' + (icon.getAttribute('aria-label') || '') + ' ' + (icon.getAttribute('title') || '')) : '',
                        section: ''
                    };
                }""")
                semantic_ok, semantic_reason = _qa_semantic_compatibility(capture, metadata)
                result["semantic_compatible"] = semantic_ok
                result["semantic_reason"] = semantic_reason
            except Exception as semantic_error:
                result["semantic_compatible"] = False
                result["semantic_reason"] = str(semantic_error)
        else:
            result["semantic_compatible"] = False
            result["semantic_reason"] = "Candidate did not resolve to one visible element."
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


def _qa_semantic_compatibility(capture, metadata):
    expected_name = str((metadata or {}).get("field_name") or "").strip()
    expected_type = str((metadata or {}).get("field_type") or "").strip().lower()
    action_type = str((metadata or {}).get("action_type") or "").strip().lower()
    actual_name = str(capture.get("accessible_name") or capture.get("text") or "").strip()
    actual_role = str(capture.get("role") or "").lower()
    actual_tag = str(capture.get("actionable_tag") or "").lower()
    expected_context = str((metadata or {}).get("control_context") or (metadata or {}).get("section") or "").strip()
    actual_context = str(capture.get("section") or "").strip()
    generic_names = {"", "action", "field", "selection", "element", "button"}
    tokens = lambda value: set(re.findall(r"[a-z0-9]+", value.lower())) - {"the", "a", "an", "field", "control"}
    expected_tokens, actual_tokens = tokens(expected_name), tokens(actual_name)
    name_evidence = [
        actual_name, capture.get("label"), capture.get("name"), capture.get("id"),
        capture.get("placeholder"), capture.get("title"),
        (capture.get("stable_attributes") or {}).get("aria-label"),
        # Class/icon tokens are supporting evidence ONLY (per the recorder's
        # own adjunctToggleName() derivation in web_recorder.py) -- they
        # never override a real accessible name above; they only let a
        # class-token-derived identity (e.g. "Password visibility toggle"
        # from class="toggle-password") be recognized as compatible when
        # the control genuinely has no accessible name of its own.
        capture.get("class_name"), capture.get("icon_evidence"),
    ]
    normalized_expected = re.sub(r"\s+", " ", expected_name).strip().casefold()
    normalized_actual = re.sub(r"\s+", " ", actual_name).strip().casefold()
    complete_semantic_name = bool(
        normalized_expected
        and normalized_actual
        and str((metadata or {}).get("primary_locator") or "").startswith("role=")
    )
    name_compatible = (
        expected_name.lower() in generic_names
        or (complete_semantic_name and normalized_actual == normalized_expected)
        or (not complete_semantic_name and semantic_name_compatible(expected_name, name_evidence))
    )
    if not name_compatible:
        return False, f"Selected element belongs to '{actual_name or actual_role or actual_tag}'. Expected '{expected_name}'."
    if expected_context and actual_context:
        expected_context_tokens, actual_context_tokens = tokens(expected_context), tokens(actual_context)
        if expected_context_tokens and actual_context_tokens and not expected_context_tokens.intersection(actual_context_tokens):
            return False, f"Selected element is in '{actual_context}'. Expected context '{expected_context}'."
    expected_popup = str((metadata or {}).get("popup_locator") or "").strip()
    popup_owner = str(capture.get("popup_owner") or "").strip()
    if expected_popup.startswith("#") and popup_owner and expected_popup[1:] != popup_owner:
        return False, f"Selected option belongs to popup '{popup_owner}'. Expected '{expected_popup[1:]}'."
    type_roles = {
        "dropdown": {"combobox", "button"}, "combobox": {"combobox"},
        "autocomplete": {"combobox", "textbox"}, "option": {"option"},
        "date": {"gridcell", "textbox", "button"}, "calendar": {"gridcell", "button"},
        "checkbox": {"checkbox"}, "radio": {"radio"},
        # Framework SPAs often implement genuine clickable navigation/menu
        # controls as DIV/LI/SPAN with pointer/onclick/tabindex evidence.
        # _qa_verify_code already requires those nodes to be actionable, so
        # permit them for recorded click/button semantics without weakening
        # uniqueness, visibility, or name matching.
        "button": {"button", "link", "div", "li", "span", "p"},
    }
    input_type = str(capture.get("input_type") or "text").lower()
    input_role = (
        "button" if input_type in {"button", "submit", "reset", "image"} else
        input_type if input_type in {"checkbox", "radio"} else "textbox"
    )
    effective_role = actual_role or ({"input": input_role, "select": "combobox", "button": "button", "a": "link"}.get(actual_tag) or actual_tag)
    # Non-ARIA calendars can expose actionable DIV/SPAN date cells.
    # Accept only an exact full recorded date title, never a day-number
    # match or a generated ID. The caller still enforces uniqueness,
    # visibility and actionability before dispatch.
    if (expected_type == "date" and action_type == "select_date"
            and actual_tag in {"div", "span", "td", "a"}
            and str(capture.get("title") or "").strip().casefold() == expected_name.casefold()
            and expected_name and not expected_name.isdigit()):
        effective_role = "gridcell"
    allowed = type_roles.get(expected_type)
    # Scripts recorded before checkbox/radio had their own FIELD_TYPE tag a
    # checkbox/radio click as a generic "button" but still carry the control's
    # own recorded ARIA role. Accept exactly that recorded role -- never a
    # wider set -- so the element must still match what was recorded.
    recorded_control_role = str((metadata or {}).get("control_role") or "").strip().lower()
    if (allowed and expected_type == "button"
            and recorded_control_role in {"checkbox", "radio", "switch", "tab", "menuitem", "menuitemcheckbox",
                                           "menuitemradio", "option", "treeitem"}):
        allowed = set(allowed) | {recorded_control_role}
    if allowed and effective_role not in allowed:
        return False, f"Selected {effective_role or actual_tag} is not semantically compatible with recorded {expected_type} action {action_type or "click"}."
    if action_type in {"fill", "type"} and (
        (actual_tag not in {"input", "textarea"} and effective_role != "textbox")
        or (actual_tag == "input" and input_role != "textbox")
    ):
        return False, "Selected element does not accept text input."
    return True, "Semantic and action context match."


def _qa_previous_opener_name():
    match = re.search(
        r"get_by_role\([^,]+,\s*name=(['\"])(.*?)\1",
        _QA_LAST_DYNAMIC_CONTROL or "",
    )
    return re.sub(r"\s+", " ", match.group(2)).strip().casefold() if match else ""


def _qa_select_element(code_text, step_number, metadata=None):
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
        capture = active_page.evaluate(r"""(selectionArgs) => new Promise((resolve) => {
            const selectionTimeout = selectionArgs.timeout;
            const selectingChild = ["select_option", "select_date"].includes(selectionArgs.actionType)
                || ["option", "date"].includes(selectionArgs.fieldType);
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
                    if (["button", "submit", "reset", "image"].includes(type)) return "button";
                    if (type === "checkbox") return "checkbox";
                    if (type === "radio") return "radio";
                    return "textbox";
                }
                return "";
            };
            // GENERIC-ACTIONABLE-OWNER-RESOLUTION: same corroboration-based
            // rule as web_recorder.py's closestCustomClickable() (see that
            // function's own comment for the live forensic evidence this
            // is based on). Mirrored here rather than shared at runtime
            // because the recorder and this interactive Selection Mode
            // harness are two separate
            // injected scripts; kept behaviorally identical by design and by
            // the shared regression tests covering both.
            const TEST_ID_ATTRS = ["data-testid", "data-test", "data-qa", "data-cy"];
            const hasStableTestId = (node) => TEST_ID_ATTRS.some((name) => node.getAttribute && node.getAttribute(name));
            const INTERACTIVE_ARIA_STATE_ATTRS = ["aria-pressed", "aria-expanded", "aria-selected", "aria-checked", "aria-haspopup"];
            const hasInteractiveAriaState = (node) => INTERACTIVE_ARIA_STATE_ATTRS.some((name) => node.hasAttribute && node.hasAttribute(name));
            const isLargeStructuralSurface = (node) => {
                try {
                    const rect = node.getBoundingClientRect();
                    if (!rect || rect.width <= 0 || rect.height <= 0) return false;
                    const vw = window.innerWidth || document.documentElement.clientWidth || 1;
                    const vh = window.innerHeight || document.documentElement.clientHeight || 1;
                    return rect.width >= vw * 0.7 && rect.height >= vh * 0.7;
                } catch (e) { return false; }
            };
            const actionableFor = (leaf) => {
                const semanticSelector = "button,a[href],input,select,textarea,[role=button],[role=link],[role=menuitem],[role=option],[role=tab],[role=gridcell],[role=combobox]";
                if (leaf.matches("label[for]")) {
                    const associated = document.getElementById(leaf.getAttribute("for"));
                    if (associated && associated.matches(semanticSelector)) return associated;
                }
                const controlledId = leaf.getAttribute("aria-controls") || leaf.getAttribute("aria-owns");
                if (controlledId) {
                    const controlled = document.getElementById(controlledId);
                    const child = controlled && controlled.querySelector(semanticSelector);
                    if (child) return child;
                }
                const semantic = leaf.closest(semanticSelector);
                if (semantic) return semantic;
                const descendant = leaf.querySelector && leaf.querySelector(semanticSelector);
                if (descendant) return descendant;
                // GENERIC-ACTIONABLE-OWNER-RESOLUTION: legacyNode replays the
                // EXACT pre-existing rule below (a generic container tag is
                // always skipped, cursor:pointer alone is enough on anything
                // else) unchanged, so any click that already resolves
                // correctly today keeps resolving to the identical element.
                // preferredNode additionally allows a generic container
                // (DIV/SPAN/FORM/FIELDSET/SECTION/DIALOG or a framework
                // wrapper class) to qualify, but ONLY with independent static
                // corroboration beyond cursor:pointer (its own onclick,
                // tabindex, stable id, test-id attribute, or interactive ARIA
                // state) -- never from cursor:pointer alone, and never past a
                // large structural surface (dialog/overlay boundary).
                let node = leaf;
                let depth = 0;
                let legacyNode = null;
                let preferredNode = null;
                let corroborationSearchOpen = true;
                while (node && node !== document.body && depth < MAX_ANCESTORS) {
                    const genericContainer = /^(DIV|SPAN|FORM|FIELDSET|SECTION|DIALOG)$/.test(node.tagName)
                        || /(?:k-window|k-dialog|k-popup|k-calendar|wrapper|container)/i.test(node.className || "");
                    const cursorPointer = getComputedStyle(node).cursor === "pointer";
                    if (!legacyNode && !genericContainer && cursorPointer) legacyNode = node;
                    if (corroborationSearchOpen && !preferredNode && genericContainer) {
                        const tabIndexAttr = node.getAttribute && node.getAttribute("tabindex");
                        // GENERIC-ACTIONABLE-OWNER-RESOLUTION: a stable id/
                        // test-id can never promote a node BY ITSELF -- see
                        // the matching comment in web_recorder.py's
                        // closestCustomClickable() for the full reasoning.
                        // It only ever strengthens a node that already
                        // shows its own interactivity signal OR its own
                        // cursor:pointer; it never substitutes for either.
                        const ownInteractivitySignal = !!node.onclick
                            || (tabIndexAttr !== null && tabIndexAttr !== undefined && tabIndexAttr !== "-1")
                            || hasInteractiveAriaState(node);
                        const ownAddressability = (node.id && !dynamic(node.id)) || hasStableTestId(node);
                        const ownCorroboration = ownInteractivitySignal || (cursorPointer && ownAddressability);
                        if (ownCorroboration) preferredNode = node;
                    }
                    if (corroborationSearchOpen && isLargeStructuralSurface(node) && node !== leaf) corroborationSearchOpen = false;
                    node = node.parentElement;
                    depth += 1;
                }
                return preferredNode || legacyNode || leaf;
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
                if (opener && !optionsVisible && selectingChild) {
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
                    popup_owner: ((el.closest('[role="listbox"],.k-list-container,.k-popup,.k-calendar') || {}).id || ''),
                    classes: [...leaf.classList].slice(0, 12),
                    candidates: candidates.slice(0, MAX_CANDIDATES),
                    xpath: "xpath=" + xpath,
                });
            };
            const onKey = (event) => {
                if (event.key !== "Escape") return;
                event.preventDefault();
                finish({cancelled: true, error: "Element selection cancelled with Escape."});
            };
            document.documentElement.style.cursor = "crosshair";
            document.addEventListener("click", onClick, true);
            document.addEventListener("keydown", onKey, true);
            cancelPollId = setInterval(async () => {
                try {
                    if (await window._qaSelectionCancelled()) finish({cancelled: true, error: "Element selection cancelled."});
                } catch (_) {}
            }, 150);
            timeoutId = setTimeout(() => finish({timed_out: true, error: "Element selection timed out while waiting for a browser click."}), selectionTimeout);
        })""", {"timeout": _QA_SELECTION_TIMEOUT_MS, "actionType": str((metadata or {}).get("action_type") or "").lower(), "fieldType": str((metadata or {}).get("field_type") or "").lower()})
    except Exception as ex:
        return {"error": str(ex), "candidates": []}

    candidates = (capture or {}).get("candidates") or []
    capture["step_number"] = step_number
    verified = []
    selected = None
    for candidate in sorted(candidates[:50], key=lambda item: item.get("score", 0), reverse=True):
        check = _qa_verify_code(candidate.get("expression") or "")
        candidate["verification"] = check
        semantic_ok, semantic_reason = _qa_semantic_compatibility(capture, metadata)
        selected_name = re.sub(
            r"\s+", " ", str(capture.get("accessible_name") or capture.get("text") or "")
        ).strip().casefold()
        previous_opener = _qa_previous_opener_name()
        if semantic_ok and previous_opener and selected_name == previous_opener:
            semantic_ok = False
            semantic_reason = "Selected element is the preceding dynamic-control opener, not the failed child action."
        candidate["semantic_compatible"] = semantic_ok
        candidate["semantic_reason"] = semantic_reason
        if check.get("count") != 1:
            candidate["rejected_reason"] = "not found" if not check.get("count") else f"ambiguous: {check.get('count')} matches"
        elif not check.get("visible"):
            candidate["rejected_reason"] = "not visible"
        elif not check.get("actionable"):
            candidate["rejected_reason"] = "not actionable"
        elif not semantic_ok:
            candidate["rejected_reason"] = semantic_reason
        verified.append(candidate)
        if (
            selected is None
            and check.get("found")
            and check.get("visible")
            and check.get("actionable")
            and check.get("count") == 1
            and semantic_ok
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
    primary = _qa_locator_code((metadata or {}).get("primary_locator"))
    if primary:
        candidates.append(_qa_replace_locator_expression(code_text, primary))
    raw_fallbacks = (metadata or {}).get("fallback_locators") or ""
    for locator in re.split(r"\s*(?:\|\||;)\s*", raw_fallbacks):
        locator = locator.strip()
        if locator and locator != "-":
            role_match = re.fullmatch(r"role=([^|]+)\|name=(.+)", locator)
            if locator.startswith("page."):
                expression = locator
            elif role_match:
                expression = "page.get_by_role(" + repr(role_match.group(1)) + ", name=" + repr(role_match.group(2)) + ", exact=True)"
            elif locator.startswith("label="):
                expression = "page.get_by_label(" + repr(locator[6:]) + ", exact=True)"
            elif locator.startswith("placeholder="):
                expression = "page.get_by_placeholder(" + repr(locator[12:]) + ", exact=True)"
            elif locator.startswith("title="):
                expression = "page.get_by_title(" + repr(locator[6:]) + ", exact=True)"
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
    return list(dict.fromkeys(
        candidate for candidate in candidates if candidate != code_text
    ))


def _qa_candidate_identity(code_text):
    expression = _qa_locator_expression_from_code(code_text)
    id_match = re.search(r"locator\((['\"])#([^'\"]+)\1\)", expression)
    xpath_id = re.search(r"@id\s*=\s*(['\"])([^'\"]+)\1", expression)
    if id_match:
        return "id:" + id_match.group(2)
    if xpath_id:
        return "id:" + xpath_id.group(2)
    return re.sub(r"\s+", "", expression)


def _qa_resolution_candidates(code_text, metadata):
    raw = [("primary", code_text)]
    details = metadata or {}
    field_name = str(details.get("field_name") or "").strip()
    control_role = str(details.get("control_role") or "").strip().lower()
    if field_name and control_role:
        semantic = (
            "page.get_by_role(" + repr(control_role) + ", name="
            + repr(field_name) + ", exact=True)"
        )
        raw.append(("exact_semantic", _qa_replace_locator_expression(code_text, semantic)))
    normalized_primary = _qa_locator_code(details.get("primary_locator"))
    if normalized_primary:
        raw.append(("normalized_primary", _qa_replace_locator_expression(code_text, normalized_primary)))
    persisted = _qa_persisted_candidate_codes(code_text, metadata)
    xpath = str((metadata or {}).get("xpath") or "").strip()
    for candidate in persisted:
        source = "xpath" if xpath not in {"", "-"} and xpath in candidate else "fallback"
        raw.append((source, candidate))
    seen = set()
    candidates = []
    for source, candidate in raw:
        identity = _qa_candidate_identity(candidate)
        if not identity or identity in seen:
            continue
        seen.add(identity)
        candidates.append({"code": candidate, "source": source, "identity": identity})
    return candidates


def _qa_resolve_action(code_text, metadata):
    """Resolve a locator only after giving the application time to reach this step.

    A recorded journey is stateful: login, role switches, menu selections and saves can
    trigger client-side routing/rendering without a traditional navigation event.  A
    single instantaneous locator probe therefore creates false "locator not found"
    repairs while the next screen is still rendering.  Keep the short per-candidate
    discovery probe, but retry the *whole saved candidate set* for a bounded readiness
    window before escalating to repair/manual selection.
    """
    import time

    candidates = _qa_resolution_candidates(code_text, metadata)
    deadline = time.monotonic() + (_QA_STATE_READINESS_TIMEOUT_MS / 1000.0)
    last_attempts = []
    readiness_round = 0

    while True:
        readiness_round += 1
        attempts = []
        for base_candidate in candidates:
            # Never retain live Locator state between rounds; only immutable code/source.
            candidate = dict(base_candidate)
            check = _qa_verify_code(candidate["code"], metadata)
            candidate.update(check)
            candidate["readiness_round"] = readiness_round
            attempts.append(candidate)
            if (check.get("count") == 1 and check.get("visible")
                    and check.get("actionable") and check.get("semantic_compatible")):
                return candidate["code"], candidate, attempts

        last_attempts = attempts
        if time.monotonic() >= deadline:
            return None, None, last_attempts

        if readiness_round == 1:
            _qa_send_event({
                "event": "application_readiness_wait",
                "step": _QA_STATE.get("current_step"),
                "total_steps": _QA_TOTAL_STEPS,
                "timeout_ms": _QA_STATE_READINESS_TIMEOUT_MS,
                "message": "Saved locators are not ready yet; waiting for the application state before repair."
            })

        # Allow SPA route changes, async data binding and framework rendering to settle.
        # This is deliberately a short poll, not another Playwright action timeout.
        try:
            _qa_active_page().wait_for_timeout(300)
        except Exception:
            time.sleep(0.3)


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
        return "page.get_by_label(" + repr(value[6:]) + ", exact=True)"
    if value.startswith("placeholder="):
        return "page.get_by_placeholder(" + repr(value[12:]) + ", exact=True)"
    if value.startswith("text="):
        return "page.get_by_text(" + repr(value[5:]) + ", exact=True)"
    return "page.locator(" + repr(value) + ")"


# ROUND-8-OWNER-SCOPED-INTERACTION-TARGET -----------------------------
# A physical interaction target can be a meaningful, deliberately-aimed-at
# trigger (e.g. a composite widget's own dropdown-arrow icon) that has NO
# safe GLOBAL locator -- computeLocator() in RECORDER_JS correctly
# refuses to invent one -- while still belonging to a composite/widget
# control whose LOGICAL OWNER does have a stable, globally-unique
# locator. When the recorder proved (at record time) that the trigger
# resolves uniquely RELATIVE TO that owner's own composite/widget scope
# (see findCompositeScope()/computeScopedLocator()/
# resolveOwnerScopedInteractionTarget() in RECORDER_JS), it persists that
# relative locator as INTERACTION_TARGET plus a new, separate
# INTERACTION_TARGET_SCOPE: "owner" metadata field -- the persisted
# INTERACTION_TARGET string format itself is completely unchanged
# (still whatever _locator_display() already produces); only the new
# scope field distinguishes "resolve this globally" (absent/"-"/
# "global", the existing Round 5/6 behavior, entirely unchanged) from
# "resolve this relative to the owner's composite scope" (new).
#
# This is the replay-side counterpart: it NEVER trusts a stored scope
# locator for the composite container itself -- it re-derives the
# composite scope live, at replay time, by walking up from the owner's
# OWN (freshly re-resolved, freshly re-validated) element, exactly
# mirroring findCompositeScope()'s own walk-up logic. If the owner
# cannot be re-resolved uniquely, if no composite scope can be found
# containing it, or if the persisted relative locator does not resolve
# to EXACTLY one element within that scope, this returns None and the
# caller performs no owner-scoped action at all (silent no-op, same
# fail-safe convention as the existing global interaction-target path).
# Nothing here ever executes a click, fills a value, or otherwise causes
# a side effect -- it only ever resolves and validates, before any
# action, in keeping with "resolve everything before the single exec()".
def _qa_parse_relative_locator(display_value):
    """
    Reverse-parses a persisted locator display string (the same format
    _locator_display() in web_recorder.py already produces for every
    other locator field in this script) into a {strategy, value/role/
    name} dict usable for scoped DOM matching. This is a NEW, standalone
    parser -- it deliberately does not reuse/modify _qa_locator_code()
    (that function targets Playwright Python code generation for GLOBAL
    locators only, and is missing name=/title= handling that this
    owner-scoped path needs; changing it would risk the existing global
    path this round must not touch).
    """
    value = (display_value or "").strip()
    if not value or value == "-":
        return None
    role_match = re.fullmatch(r"role=([^|]+)\|name=(.+)", value)
    if role_match:
        return {"strategy": "role", "role": role_match.group(1), "name": role_match.group(2)}
    if value.startswith("data-testid="):
        return {"strategy": "testid", "value": value[12:]}
    if value.startswith("label="):
        return {"strategy": "label", "value": value[6:]}
    if value.startswith("name="):
        return {"strategy": "name", "value": value[5:]}
    if value.startswith("placeholder="):
        return {"strategy": "placeholder", "value": value[12:]}
    if value.startswith("title="):
        return {"strategy": "title", "value": value[6:]}
    if value.startswith("text="):
        return {"strategy": "text", "value": value[5:]}
    if value.startswith("#"):
        return {"strategy": "id", "value": value[1:]}
    if value.startswith("xpath="):
        return {"strategy": "xpath", "value": value[6:]}
    return {"strategy": "css", "value": value}


# Re-derives the composite scope from a freshly-resolved owner
# ElementHandle (never from a stored scope locator -- there is no such
# thing persisted), then resolves the persisted relative locator inside
# that scope, requiring exactly one match. Mirrors, on the live DOM,
# findCompositeScope() + the per-strategy matching computeScopedLocator()/
# locatorMatchCount(locator, root) already validated at record time.
# COMPOSITE_SCOPE_SELECTOR intentionally reuses only the same generic
# ARIA-role/widget-class vocabulary already used elsewhere in this file
# and in RECORDER_JS -- no PSW-, field-, or Kendo-field-specific literal.
_QA_OWNER_SCOPE_JS = r"""(function(args) {
  var owner = args.owner;
  var rel = args.relative;
  if (!owner || !rel) return null;
  function attr(node, name) { return node.getAttribute ? node.getAttribute(name) : null; }
  function implicitRole(node) {
    var tag = (node.tagName || '').toLowerCase();
    if (tag === 'button') return 'button';
    if (tag === 'a' && node.hasAttribute('href')) return 'link';
    if (tag === 'select') return 'combobox';
    return '';
  }
  function accessibleName(node) {
    var label = attr(node, 'aria-label');
    if (label) return label.trim();
    return (node.innerText || node.textContent || '').trim();
  }
  var COMPOSITE_SCOPE_SELECTOR = '[role="combobox"],[role="listbox"],[role="grid"],[role="dialog"],[role="menu"],.k-dropdown,.k-dropdownlist,.k-combobox,.k-autocomplete,.k-datepicker,.k-picker,.k-widget';
  var scope = null;
  var node = owner.parentElement;
  var depth = 0;
  while (node && node !== document.body && depth < 10) {
    if (node.matches && node.matches(COMPOSITE_SCOPE_SELECTOR) && node.contains(owner)) { scope = node; break; }
    node = node.parentElement;
    depth += 1;
  }
  if (!scope) return null;
  var nodes = [];
  try {
    if (rel.strategy === 'testid') nodes = scope.querySelectorAll('[data-testid="' + CSS.escape(rel.value) + '"]');
    else if (rel.strategy === 'id') nodes = scope.querySelectorAll('#' + CSS.escape(rel.value));
    else if (rel.strategy === 'name') nodes = scope.querySelectorAll('[name="' + CSS.escape(rel.value) + '"]');
    else if (rel.strategy === 'placeholder') nodes = scope.querySelectorAll('[placeholder="' + CSS.escape(rel.value) + '"]');
    else if (rel.strategy === 'title') nodes = scope.querySelectorAll('[title="' + CSS.escape(rel.value) + '"]');
    else if (rel.strategy === 'css') nodes = scope.querySelectorAll(rel.value);
    else if (rel.strategy === 'label') nodes = Array.prototype.filter.call(scope.querySelectorAll('input,select,textarea'), function(n) {
      var id = n.id;
      var lab = id ? document.querySelector('label[for="' + CSS.escape(id) + '"]') : n.closest('label');
      var text = lab ? (lab.innerText || '').trim() : '';
      return text === rel.value;
    });
    else if (rel.strategy === 'role') nodes = Array.prototype.filter.call(scope.querySelectorAll('button,a,input,select,textarea,[role]'), function(n) {
      return (attr(n, 'role') || implicitRole(n)) === rel.role && accessibleName(n) === rel.name;
    });
    else if (rel.strategy === 'text') nodes = Array.prototype.filter.call(scope.querySelectorAll('button,a,span,li,td,label'), function(n) {
      return (n.innerText || '').trim() === rel.value;
    });
  } catch (e) { return null; }
  if (!nodes || nodes.length !== 1) return null;
  return nodes[0];
})"""


# ROUND-8C-DIAGNOSTIC-ONLY: a read-only twin of _QA_OWNER_SCOPE_JS above,
# used strictly to populate failure telemetry (owner_scope_found,
# interaction_target_match_count) when the proven resolution JS above
# returns null. It performs no click, selects nothing, and its result is
# NEVER used to decide what gets clicked -- only _QA_OWNER_SCOPE_JS's own
# result does that, unchanged, byte-for-byte, from Round 8. Kept as a
# separate script (rather than changing _QA_OWNER_SCOPE_JS's return
# contract) so the proven Round 8 resolution path -- Step 12's regression
# case included -- is not touched in any way by Round 8C.
_QA_OWNER_SCOPE_DIAGNOSTIC_JS = r"""(function(args) {
  var owner = args.owner;
  var rel = args.relative;
  var result = {scope_found: false, match_count: 0};
  if (!owner || !rel) return result;
  function attr(node, name) { return node.getAttribute ? node.getAttribute(name) : null; }
  function implicitRole(node) {
    var tag = (node.tagName || '').toLowerCase();
    if (tag === 'button') return 'button';
    if (tag === 'a' && node.hasAttribute('href')) return 'link';
    if (tag === 'select') return 'combobox';
    return '';
  }
  function accessibleName(node) {
    var label = attr(node, 'aria-label');
    if (label) return label.trim();
    return (node.innerText || node.textContent || '').trim();
  }
  var COMPOSITE_SCOPE_SELECTOR = '[role="combobox"],[role="listbox"],[role="grid"],[role="dialog"],[role="menu"],.k-dropdown,.k-dropdownlist,.k-combobox,.k-autocomplete,.k-datepicker,.k-picker,.k-widget';
  var scope = null;
  var node = owner.parentElement;
  var depth = 0;
  while (node && node !== document.body && depth < 10) {
    if (node.matches && node.matches(COMPOSITE_SCOPE_SELECTOR) && node.contains(owner)) { scope = node; break; }
    node = node.parentElement;
    depth += 1;
  }
  if (!scope) return result;
  result.scope_found = true;
  var nodes = [];
  try {
    if (rel.strategy === 'testid') nodes = scope.querySelectorAll('[data-testid="' + CSS.escape(rel.value) + '"]');
    else if (rel.strategy === 'id') nodes = scope.querySelectorAll('#' + CSS.escape(rel.value));
    else if (rel.strategy === 'name') nodes = scope.querySelectorAll('[name="' + CSS.escape(rel.value) + '"]');
    else if (rel.strategy === 'placeholder') nodes = scope.querySelectorAll('[placeholder="' + CSS.escape(rel.value) + '"]');
    else if (rel.strategy === 'title') nodes = scope.querySelectorAll('[title="' + CSS.escape(rel.value) + '"]');
    else if (rel.strategy === 'css') nodes = scope.querySelectorAll(rel.value);
    else if (rel.strategy === 'label') nodes = Array.prototype.filter.call(scope.querySelectorAll('input,select,textarea'), function(n) {
      var id = n.id;
      var lab = id ? document.querySelector('label[for="' + CSS.escape(id) + '"]') : n.closest('label');
      var text = lab ? (lab.innerText || '').trim() : '';
      return text === rel.value;
    });
    else if (rel.strategy === 'role') nodes = Array.prototype.filter.call(scope.querySelectorAll('button,a,input,select,textarea,[role]'), function(n) {
      return (attr(n, 'role') || implicitRole(n)) === rel.role && accessibleName(n) === rel.name;
    });
    else if (rel.strategy === 'text') nodes = Array.prototype.filter.call(scope.querySelectorAll('button,a,span,li,td,label'), function(n) {
      return (n.innerText || '').trim() === rel.value;
    });
  } catch (e) { return result; }
  result.match_count = nodes ? nodes.length : 0;
  return result;
})"""


def _qa_resolve_owner_scoped_trigger(metadata, resolved_owner=None):
    """
    Resolves the owner-scoped physical interaction target for the
    current step, or returns (None, diagnostics) if it cannot be
    safely, uniquely resolved. Performs no side-effecting action.
    Called from _qa_run_step() BEFORE that step's single exec() --
    never after a click, never as a repair/retry.

    ROUND-8C: resolved_owner, when provided, is the enriched candidate
    dict already produced and verified by the generic readiness/
    semantic cascade (_qa_resolve_action()) -- i.e. the exact logical
    owner this replay run actually selected, whether via "primary",
    "exact_semantic", or "fallback". When present, this is the ONLY
    source used to derive the owner locator: no independent
    re-resolution from metadata["primary_locator"] is performed here.
    This is the fix for the proven Case A defect -- owner-scoped
    resolution silently re-deriving the owner from the raw primary
    locator and failing whenever that owner only resolves via
    fallback/exact_semantic, even though the generic cascade had
    already found a safe, verified owner through that path. The
    metadata-only branch below is a defensive fallback for isolated/
    direct calls (e.g. tests) and is never exercised by the production
    call site in _qa_run_step(), which always passes resolved_owner.

    Returns a (element_handle_or_None, diagnostics) tuple. diagnostics
    is always a dict (never None) so the caller can emit full
    telemetry regardless of outcome; it only ever contains locator/
    source/count/visibility bookkeeping -- never credentials or filled
    values.
    """
    details = metadata or {}
    relative_display = details.get("interaction_target")
    diagnostics = {
        "logical_owner_source": None,
        "logical_owner_locator": None,
        "logical_owner_verified": False,
        "owner_scope_found": False,
        "interaction_target_requested": relative_display,
        "interaction_target_match_count": None,
        "interaction_target_visible": False,
        "interaction_target_actionable": None,
        "failure_reason": None,
    }
    if resolved_owner and resolved_owner.get("code"):
        owner_code = _qa_locator_expression_from_code(resolved_owner["code"])
        diagnostics["logical_owner_source"] = resolved_owner.get("source")
        diagnostics["logical_owner_locator"] = resolved_owner.get("code")
    else:
        owner_display = details.get("primary_locator")
        owner_code = _qa_locator_code(owner_display) if owner_display else ""
        diagnostics["logical_owner_source"] = "primary_locator_fallback"
        diagnostics["logical_owner_locator"] = owner_display
    relative = _qa_parse_relative_locator(relative_display)
    if not owner_code or not relative:
        diagnostics["failure_reason"] = "no_owner_or_relative_locator"
        return None, diagnostics
    try:
        # ROUND-8C: re-verify against the full step metadata (not None)
        # so a stale/mismatched owner is caught by the same semantic
        # check the generic cascade itself relies on -- belt-and-
        # suspenders defense-in-depth, never the ONLY gate (the cascade
        # in _qa_resolve_action() already required this for whichever
        # candidate it selected), but it keeps this function safe to
        # call on its own too. Trivially permissive when metadata omits
        # field_name/field_type (matches Round 8's original behavior for
        # direct/isolated calls), never restrictive of anything the
        # cascade would have already allowed through.
        owner_check = _qa_verify_code(owner_code, metadata)
        if not (owner_check.get("count") == 1 and owner_check.get("visible")
                and owner_check.get("semantic_compatible", True)):
            diagnostics["failure_reason"] = "owner_not_uniquely_resolved"
            diagnostics["interaction_target_match_count"] = 0
            return None, diagnostics
        diagnostics["logical_owner_verified"] = True
        active_page = _qa_active_page()
        owner_locator = eval(owner_code, {**globals(), "page": active_page})
        owner_handle = owner_locator.first.element_handle()
        if owner_handle is None:
            diagnostics["failure_reason"] = "owner_handle_unavailable"
            return None, diagnostics
        result_handle = active_page.evaluate_handle(
            _QA_OWNER_SCOPE_JS, {"owner": owner_handle, "relative": relative}
        )
        element_handle = result_handle.as_element() if result_handle else None
        if element_handle is None:
            try:
                scope_diag = active_page.evaluate(
                    _QA_OWNER_SCOPE_DIAGNOSTIC_JS, {"owner": owner_handle, "relative": relative}
                ) or {}
            except Exception:
                scope_diag = {}
            diagnostics["owner_scope_found"] = bool(scope_diag.get("scope_found"))
            match_count = scope_diag.get("match_count", 0)
            diagnostics["interaction_target_match_count"] = match_count
            if not scope_diag.get("scope_found"):
                diagnostics["failure_reason"] = "owner_scope_not_found"
            elif not match_count:
                diagnostics["failure_reason"] = "interaction_target_not_found"
            else:
                diagnostics["failure_reason"] = "interaction_target_ambiguous"
            return None, diagnostics
        diagnostics["owner_scope_found"] = True
        diagnostics["interaction_target_match_count"] = 1
        if not element_handle.is_visible():
            diagnostics["failure_reason"] = "interaction_target_not_visible"
            return None, diagnostics
        diagnostics["interaction_target_visible"] = True
        # ROUND-8C safety rule (spec item 8G): a uniquely-resolved,
        # visible physical target that is nonetheless not actionable
        # (explicitly disabled, or pointer-events: none) must still not
        # be clicked. Deliberately narrow -- it never requires the
        # target to match any particular tag/role whitelist (a bare
        # icon <span> like Step 12/14's own .k-icon.k-i-arrow-s has no
        # such semantics and must keep working exactly as proven), it
        # only rejects a target that is affirmatively marked unusable.
        try:
            actionable = bool(element_handle.evaluate(r"""el => {
                if (el.disabled) return false;
                if (el.getAttribute('aria-disabled') === 'true') return false;
                const style = getComputedStyle(el);
                return style.pointerEvents !== 'none' && style.visibility !== 'hidden' && style.display !== 'none';
            }"""))
        except Exception:
            actionable = False
        if not actionable:
            diagnostics["interaction_target_actionable"] = False
            diagnostics["failure_reason"] = "interaction_target_not_actionable"
            return None, diagnostics
        diagnostics["interaction_target_actionable"] = True
        return element_handle, diagnostics
    except Exception as ex:
        diagnostics["failure_reason"] = "exception: " + str(ex)
        return None, diagnostics
# ROUND-8-OWNER-SCOPED-INTERACTION-TARGET (end) ------------------------


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
        parent_count = parent.count()
        parent_visible = parent.is_visible() if parent_count == 1 else False
        if parent_count != 1 or not parent_visible:
            _qa_send_event({"event": "parent_reopen_failed", "step": _QA_STATE["current_step"], "error": f"Parent locator resolved count={parent_count}, visible={parent_visible}."})
            return False
        parent.click()
        _qa_smart_wait({"field_type": "dropdown"})
        return True
    except Exception as ex:
        _qa_send_event({"event": "parent_reopen_failed", "step": _QA_STATE["current_step"], "error": str(ex)})
        return False


class _QaCalendarConfirmationError(RuntimeError):
    """No date action was dispatched; require the existing repair dialog."""


class _QaPostActionVerificationError(RuntimeError):
    pass


class _QaOwnerScopedTargetResolutionError(RuntimeError):
    """
    ROUND-8C: raised when metadata declares INTERACTION_TARGET_SCOPE:
    owner, a verified logical owner was (or should have been) available,
    but the owner-scoped PHYSICAL interaction target could not be
    safely, uniquely resolved. Raised strictly BEFORE exec() -- no
    action, physical or logical, has been executed for this step when
    this is raised. Handled as an immediate, non-retried failure (never
    entering the auto-repair/fallback-candidate/parent-reopen retry
    machinery) so that owner-scoped processing never silently falls
    through to clicking the plain logical/fallback control as though it
    were equivalent to the declared physical trigger.
    """
    def __init__(self, diagnostics):
        self.diagnostics = diagnostics or {}
        reason = self.diagnostics.get("failure_reason") or "unresolved"
        super().__init__("Owner-scoped interaction target could not be safely resolved: " + str(reason))


class _QaLocatorResolutionError(RuntimeError):
    def __init__(self, attempts):
        self.attempts = attempts
        summary = "; ".join(
            f"{item.get('source')}: count={item.get('count', 0)}, visible={bool(item.get('visible'))}, actionable={bool(item.get('actionable'))}, semantic={bool(item.get('semantic_compatible'))}"
            for item in attempts
        )
        super().__init__("No saved locator candidate resolved safely. " + summary)


class _QaNonReplayableRuntimePlaceholderError(RuntimeError):
    """
    PERSISTENCE SAFETY: raised when a step's OWN recorded code_text --
    i.e. what was persisted into recorded_script and handed to this run
    before any resolution ever happened -- is itself a runtime-only
    internal placeholder (e.g. "_QA_OWNER_SCOPED_TRIGGER_HANDLE['handle']
    .click()"), rather than an independently replayable Playwright
    statement. Such a placeholder has no meaning outside the single
    execution that originally resolved and assigned it; there is no
    stale handle, owner, or locator safe to reuse for it. This is the
    fail-closed response to a previously-corrupted recorded_script (see
    _qa_is_durable_persistable_code()'s docstring for how such
    corruption could have been written in the first place) -- it
    protects every OTHER test case that may already carry the same kind
    of corruption, independent of the persistence-guard fix that stops
    new corruption from being written.

    Raised BEFORE the per-step while-loop even starts (never inside a
    retry/auto-repair attempt), so: zero exec() calls for this step,
    zero reuse of any handle left over from a prior step, and no auto-
    repair/fallback/parent-reopen machinery is ever entered for it.
    """
    def __init__(self, step_number, code_text):
        self.step_number = step_number
        self.code_text = code_text
        super().__init__(
            "NON_REPLAYABLE_RUNTIME_PLACEHOLDER: recorded step %s's own code is a "
            "runtime-only internal placeholder and cannot be safely executed: %r"
            % (step_number, code_text)
        )


# PERSISTENCE SAFETY ---------------------------------------------------
# Every internal, execution-scoped-only reference this harness ever
# substitutes into current_code for a single run's exec() -- never
# meaningful outside that one run -- is listed here. Extend this tuple,
# never relax the check below, if a future execution-time adaptation
# introduces another such internal handle/global.
_QA_NON_REPLAYABLE_RUNTIME_MARKERS = (
    "_QA_OWNER_SCOPED_TRIGGER_HANDLE",
)


def _qa_contains_non_replayable_runtime_marker(code):
    text = str(code or "")
    return any(marker in text for marker in _QA_NON_REPLAYABLE_RUNTIME_MARKERS)


def _qa_is_durable_persistable_code(code):
    """
    PERSISTENCE SAFETY (central guard, item 4): the ONLY gate that
    decides whether a candidate "corrected" code string is eligible to
    be saved into durable recorded_script and independently replayed in
    a future process/run.

    This is the fix for the proven repair-persistence corruption: Step
    14's owner-scoped PHYSICAL-TRIGGER substitution (current_code
    reassigned to "_QA_OWNER_SCOPED_TRIGGER_HANDLE['handle'].click()" --
    an execution-scoped adaptation, never a locator repair) was, before
    this fix, indistinguishable from a genuine durable locator repair
    (e.g. Step 13's page.get_by_role('option', name='Commercial',
    exact=True).click()) by the ONLY test previously applied
    ("current_code != code_text"). Both are "a change"; only one is
    safe to persist. This function is explicit about WHY a candidate is
    or is not durable, rather than guessing from the fact that it
    changed at all:

      1. Known-internal-reference rejection (explicit provenance): any
         code referencing one of this harness's own execution-scoped
         runtime handles (_QA_NON_REPLAYABLE_RUNTIME_MARKERS) is never
         durable, full stop -- this is checked first and is the primary
         basis for the decision, not a fallback.
      2. Defensive sanity check (never the primary basis by itself):
         the code must also reduce to a real locator-producing
         expression that starts with "page." -- the same shape every
         genuine durable repair already has (auto-repair fallback
         candidates, manually-selected repairs, the generic cascade's
         own resolved_code). This catches anything else that doesn't
         look like an ordinary Playwright statement, even if it never
         happens to mention a known marker by name.

    Legitimate durable repairs -- primary-to-fallback locator repair,
    exact-semantic locator repair, manual/user-selected repair, and any
    other persisted page.* candidate -- are unaffected: all of those
    satisfy both checks exactly as before.
    """
    if not code:
        return False
    stripped = str(code).strip()
    if _qa_contains_non_replayable_runtime_marker(stripped):
        return False
    expression = _qa_locator_expression_from_code(stripped)
    return bool(expression) and expression.startswith("page.")


def _qa_post_action_event_name(post_check):
    """
    Section 13: separate ACTION/APPLICATION-STATE failures from a bug
    in the verification infrastructure itself, so an operator (or a
    server log) never mistakes "our verifier crashed" for "the
    application didn't reach the expected state" — the exact
    confusion a prior "name 'time' is not defined" regression caused,
    since it surfaced as an ordinary post_action_failed event
    indistinguishable from a real mismatch. Purely a naming/labeling
    change: _qa_run_step()'s control flow (never repeat the
    side-effecting action once verification is anything but a clean
    "verified": True) is identical for all three event names.
    """
    if post_check.get("verified"):
        return "post_action_verified"
    if post_check.get("infrastructure_error"):
        return "post_action_infrastructure_error"
    return "post_action_failed"


_QA_POPUP_SURFACE_SELECTOR = (
    '[role="listbox"],[role="option"],[role="grid"],[role="dialog"],'
    '.k-list-container,.k-popup,.k-calendar,.k-animation-container,.k-list'
)


def _qa_visible_popup_surface_ids():
    """Stable-for-one-replay-tick identities of every currently visible
    popup-like surface on the page. Used both to snapshot what was
    ALREADY open before a side-effecting action (_qa_popup_surface_snapshot(),
    called from _qa_run_step() right before exec()) and, inside
    _qa_verify_post_action(), to tell a genuinely NEW popup -- opened BY
    the action just executed -- apart from one that was already open
    elsewhere on the page before it. Never PSW/field-specific: the
    selector is the same generic listbox/dialog/Kendo-surface vocabulary
    Round 4 already used; this only adds identity so "visible" can mean
    "newly visible" (ownership-aware) instead of "visible at all"
    (previously acceptable from an unrelated, already-open popup)."""
    try:
        return _qa_active_page().locator(_QA_POPUP_SURFACE_SELECTOR).evaluate_all(r"""
            els => els.filter(el => {
                const s = getComputedStyle(el);
                return s.display !== 'none' && s.visibility !== 'hidden' && el.getClientRects().length > 0;
            }).map(el => el.id || el.getAttribute('data-qa-popup-key') ||
                (el.tagName + ':' + Array.prototype.indexOf.call(el.parentNode ? el.parentNode.children : [], el)))
        """)
    except Exception:
        return []


def _qa_popup_surface_snapshot():
    """Call immediately BEFORE executing a side-effecting action (never
    after) so post-action verification can require NEW popup evidence
    instead of accepting whatever was already visible on the page."""
    return _qa_visible_popup_surface_ids()


def _qa_wait_for_next_step_readiness(next_metadata):
    """UNIVERSAL STEP COMPLETION GATE / NEXT-STEP READINESS GATE (item 4):
    for step shapes that have no other deterministic post-condition of
    their own (a plain button, a navigation/goto, a generic click) --
    flow D (navigation -> destination control delayed) and the "button:
    next-step readiness" case from the spec -- wait, state-based and
    bounded by the SAME configured readiness timeout every other dynamic
    wait in this harness already uses (never a fixed sleep), for the next
    recorded step's own locator to become resolvable, visible, actionable,
    and semantically compatible with its recorded identity. This reuses
    the exact same generic _qa_verify_code() check the dropdown/modal
    branches already use for their own next-step lookahead -- nothing
    field-type- or application-specific.

    Returns True the moment readiness is observed, so the caller can cite
    it as strong completion evidence. Returns False -- never raises -- when
    no next_metadata/locator was supplied at all (nothing to look ahead
    to, e.g. the script's last step) or when the window elapses without
    observing readiness. False is never itself a failure signal here: a
    step with nothing meaningful to look ahead to, or whose real next step
    will simply keep polling under its OWN precondition gate when it
    starts, must not be forced to fail merely because this best-effort
    lookahead didn't confirm it early.
    """
    if not next_metadata:
        return False
    next_locator_code = _qa_locator_code((next_metadata or {}).get("primary_locator"))
    if not next_locator_code:
        return False
    deadline = time.monotonic() + (_QA_STATE_READINESS_TIMEOUT_MS / 1000.0)
    while time.monotonic() < deadline:
        try:
            next_check = _qa_verify_code(next_locator_code, next_metadata)
            if (next_check.get("count") == 1 and next_check.get("visible")
                    and next_check.get("actionable") and next_check.get("semantic_compatible")):
                return True
        except Exception:
            pass
        try:
            _qa_active_page().wait_for_timeout(100)
        except Exception:
            break
    return False


def _qa_date_entry_plan(code_text, metadata=None):
    """Plan date-aware entry for ONE step, or None for the ordinary path.

    DATE-ENTRY-ENGINE: a recorded `locator.fill('<date literal>')` used to be
    replayed as a raw fill. A raw fill never produces key events, so an input
    mask that owns the field re-renders its prompt template around the
    injected text (e.g. '01/10/2026__/__/____'). This only plans anything
    when ALL of these hold, decided from the live field itself (never from an
    application name, id or format): the step is exactly one .fill() of a
    date-shaped string literal, the target resolves to exactly one editable
    text-like/date <input>, and the step is not sensitive. Anything else
    (and any planning error) returns None, leaving the step on the exact
    pre-existing exec() path. Planning performs no action.
    """
    try:
        import ast
        meta = metadata or {}
        if str(meta.get("field_type") or "").lower() == "password" or str(
            meta.get("is_sensitive", meta.get("sensitive", False))
        ).lower() in {"true", "yes", "1"}:
            return None
        tree = ast.parse((code_text or "").strip())
        if len(tree.body) != 1 or not isinstance(tree.body[0], ast.Expr):
            return None
        call = tree.body[0].value
        if not (
            isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute)
            and call.func.attr == "fill" and len(call.args) == 1 and not call.keywords
            and isinstance(call.args[0], ast.Constant) and isinstance(call.args[0].value, str)
        ):
            return None
        value = call.args[0].value
        if not _qa_date_engine.is_date_shaped(value):
            return None
        locator = eval(ast.unparse(call.func.value), {**globals(), "page": _qa_active_page()})
        if not hasattr(locator, "count") or locator.count() != 1:
            return None
        info = _qa_date_engine.inspect_input(locator.first)
        if not _qa_date_engine.entry_applicable(info, value):
            return None
        return {"locator": locator.first, "value": value, "info": info}
    except Exception:
        return None


def _qa_exec_action(code_text, metadata=None):
    """Execute one recorded action exactly once.

    Ordinary steps run through the unchanged exec() path. A date literal
    filled into a date/text input is entered the way that control expects
    (native ISO fill for <input type=date>, real keyboard entry for text and
    masked inputs) by Core.date_entry_engine. This is still ONE action per
    call: the verdict is always left to _qa_verify_post_action(), and nothing
    here is ever retried after an uncertain verification.
    """
    plan = _qa_date_entry_plan(code_text, metadata)
    if plan is None:
        exec(_qa_prepare_exec_code(code_text), globals())
        return
    report = _qa_date_engine.enter_date_value(_qa_active_page(), plan["locator"], plan["value"], plan["info"])
    _qa_send_event({
        "event": "date_entry_performed", "step": _QA_STATE.get("current_step"),
        "total_steps": _QA_TOTAL_STEPS, **report,
    })


def _qa_verify_date_entry(node, expected, actual, metadata=None):
    """Date-aware verdict for a fill whose expected value is a date.

    Returns None when `expected` is not date-shaped (callers then use the
    ordinary strict comparison, unchanged). Only reached when the field does
    not hold the entered text verbatim: the same calendar date in another
    display format verifies; leftover mask slots, partial dates, different
    dates and non-dates do not. Mask characters are never stripped to force a
    match, and an exact match is never re-judged.
    """
    if not _qa_date_engine.is_date_shaped(expected):
        return None
    try:
        info = _qa_date_engine.inspect_input(node)
        hint = _qa_date_engine.format_hint_of(info) or str((metadata or {}).get("date_format") or "")
        settled = _qa_date_engine.settled_value(node, actual)
        result = _qa_date_engine.compare_date_values(expected, settled, hint)
        if result["verified"] and info.get("bad_input"):
            result.update({
                "verified": False, "status": "incomplete",
                "reason": "The date input reports an unparsable (incomplete) value.",
            })
    except Exception:
        return None
    return {
        "verified": bool(result["verified"]), "checked": True, "reason": result["reason"],
        "date_verification": {key: result[key] for key in ("status", "expected", "actual")},
    }


def _qa_verify_post_action(code_text, metadata=None, next_metadata=None, pre_action_popup_ids=None, resolved_owner=None):
    """Verify deterministic semantic outcomes; report skipped when no safe assertion exists.

    UNIVERSAL STEP COMPLETION GATE: resolved_owner, when provided, is the
    SAME enriched candidate dict (source/code) the precondition/
    resolution gate (_qa_resolve_action()) already selected and verified
    for this exact step -- whichever candidate source it used (primary,
    exact_semantic, or fallback). When present, it is the PREFERRED
    source for re-deriving the owning control below, instead of
    independently re-deriving it from metadata["primary_locator"] alone.
    This is the same Case-A-shaped defect Round 8C fixed for owner-scoped
    ACTION resolution, proven still present here by a live run: Step 14's
    physical trigger executed correctly (Round 8C's own fix working), but
    this function's OWN prior independent re-derivation from the raw,
    never-resolving PRIMARY_LOCATOR still failed immediately with "Owning
    control is no longer uniquely resolvable" -- before any popup/aria-
    expanded evidence was ever even polled for. metadata["primary_locator"]
    remains the fallback derivation when no resolved_owner is supplied
    (isolated/legacy callers), so nothing here narrows what already
    worked.
    """
    field_type = str((metadata or {}).get("field_type") or "").lower()
    action_type = str((metadata or {}).get("action_type") or "").lower()
    sensitive = field_type == "password" or str(
        (metadata or {}).get("is_sensitive", (metadata or {}).get("sensitive", False))
    ).lower() in {"true", "yes", "1"}
    expected = str((metadata or {}).get("expected_value") or "").strip()
    calendar_data = (metadata or {}).get('calendar_metadata')
    if action_type == 'select_date' and calendar_data and calendar_data != '{}':
        try:
            verify_selection(_qa_active_page(), json.loads(calendar_data))
            return {'verified': True, 'checked': True, 'reason': 'Exact selected date verified in its owning input.'}
        except CalendarError as ex:
            return {'verified': False, 'checked': True, 'reason': str(ex)}
        except Exception as ex:
            return {'verified': False, 'checked': True, 'reason': 'Calendar verification could not complete (' + type(ex).__name__ + ').'}
    # Task-1 Phase 3: a recorded COMPOUND block (for / with / if / try ...)
    # is executed as one opaque step. It has no single locator/value to
    # re-verify, and previously crashed here with "invalid syntax" AFTER
    # the block had already executed. Report it honestly as executed but
    # not independently verifiable (checked=False) instead of failing.
    try:
        import ast as _qa_ast_probe
        _qa_probe_body = _qa_ast_probe.parse(code_text.strip()).body
        if _qa_probe_body and not isinstance(_qa_probe_body[0], (_qa_ast_probe.Expr, _qa_ast_probe.Assert)):
            return {
                "verified": True, "checked": False,
                "reason": "Compound recorded block executed; it has no single deterministic post-condition, so it was not independently verified.",
            }
    except SyntaxError:
        pass
    _, code_value = _qa_extract_locator_and_value(code_text)
    expected = expected or (code_value if action_type in {"fill", "type", "select_option"} else "")
    if action_type in {"goto", "navigation", "execute"} or code_text.lstrip().startswith(("assert ", "expect(")):
        # UNIVERSAL STEP COMPLETION GATE (flow D: navigation -> destination
        # control delayed -> wait dynamically -> PASS only when destination
        # ready): a navigation/goto/execute step has no deterministic post-
        # condition of its own, but when the next recorded step's control is
        # known, waiting for it here is exactly the state-based (never
        # sleep-based) synchronization the spec calls for -- this is a bounded
        # wait, not an additional pass/fail branch: it never turns this into
        # a failure, it only lets the step wait for real destination-readiness
        # evidence before declaring itself complete.
        next_ready = _qa_wait_for_next_step_readiness(next_metadata)
        return {
            "verified": True, "checked": next_ready,
            "reason": (
                "The next recorded step's control became resolvable, visible, and semantically "
                "matches its recorded identity."
            ) if next_ready else "Step itself supplied the available verification.",
        }
    try:
        expression = _qa_locator_expression_from_code(code_text)
        locator = eval(expression, {**globals(), "page": _qa_active_page()}) if expression else None
        if action_type in {"fill", "type"} and expected:
            if locator is None or not hasattr(locator, "count") or locator.count() != 1:
                return {"verified": False, "checked": True, "reason": "Filled field is no longer uniquely resolvable."}
            node = locator.first
            sensitive = sensitive or node.get_attribute("type") == "password" or expected.upper() == "[REDACTED]"
            if sensitive:
                # Display metadata is not an assertion value. Resolve the
                # actual action argument locally, never in event payloads.
                import ast
                action = ast.parse(code_text.strip()).body[0].value
                argument = action.args[0] if action.args else next(
                    (kw.value for kw in action.keywords if kw.arg in {"value", "text"}), None
                )
                if argument is None:
                    return {"verified": False, "checked": True, "reason": "Sensitive input verification could not resolve the entered value."}
                expected_input = eval(compile(ast.Expression(argument), "<input verification>", "eval"), globals())
                actual_input = node.input_value()
                verified = actual_input == str(expected_input)
                return {"verified": verified, "checked": True, "reason": "Sensitive input value verified." if verified else "Sensitive input does not match the entered value."}
            actual = node.input_value()
            if actual == expected:
                return {"verified": True, "checked": True, "reason": f"Expected value '{expected}', found '{actual}'."}
            date_result = _qa_verify_date_entry(node, expected, actual, metadata)
            if date_result is not None:
                return date_result
            return {"verified": False, "checked": True, "reason": f"Expected value '{expected}', found '{actual}'."}
        if action_type == "select_option" and locator is not None and hasattr(locator, "count"):
            # Native <select>: the deterministic post-condition is the control's
            # own selected option, not a popup (a native list has none).
            if locator.count() == 1 and locator.first.evaluate("el => el.tagName") == "SELECT":
                selected = locator.first.evaluate(
                    "el => ({value: el.value, labels: Array.from(el.selectedOptions).map(o => (o.text || '').trim())})"
                )
                wanted = {item for item in (expected, code_value) if item}
                ok = (not wanted) or bool(wanted & ({selected.get("value")} | set(selected.get("labels") or [])))
                return {
                    "verified": ok, "checked": True,
                    "reason": ("Selected option verified in the native select." if ok else
                               f"Native select shows value '{selected.get('value')}' / label {selected.get('labels')}, expected '{expected or code_value}'."),
                }
        if action_type == "upload" or ".set_input_files(" in code_text:
            if locator is None or not hasattr(locator, "count") or locator.count() != 1:
                return {"verified": False, "checked": True, "reason": "Upload input is no longer uniquely resolvable."}
            attached = locator.first.evaluate("el => Array.from(el.files || []).map(file => file.name)")
            return {
                "verified": bool(attached), "checked": True,
                "reason": ("Chosen file(s) attached to the input: " + ", ".join(attached)) if attached
                          else "The input holds no file after the upload step.",
            }
        if field_type == "modal" or action_type in {"open_modal", "dialog"}:
            # UNIVERSAL STEP COMPLETION GATE: the recorder tags a control
            # as field_type "modal" whenever it lives inside a dialog/
            # modal wrapper (role=dialog, or a modal/dialog class) --
            # this covers BOTH opening and closing a modal, and nothing
            # in the persisted metadata reliably distinguishes direction.
            # So this checks for the generically correct thing either
            # way: the set of visible popup/dialog-like surfaces on the
            # page actually CHANGING as a result of this action -- a
            # surface appearing is evidence an opener completed, a
            # previously-open surface disappearing is evidence a close
            # completed. Reuses the exact same generic
            # _qa_visible_popup_surface_ids() ownership machinery Round 5
            # already built for dropdowns (never PSW-/Kendo-specific --
            # the selector already covers [role="dialog"]).
            post_action_dynamic_deadline_s = _QA_STATE_READINESS_TIMEOUT_MS / 1000.0
            deadline = time.monotonic() + post_action_dynamic_deadline_s
            pre_ids = set(pre_action_popup_ids or [])
            next_locator_code = _qa_locator_code((next_metadata or {}).get("primary_locator"))
            while time.monotonic() < deadline:
                try:
                    if set(_qa_visible_popup_surface_ids()) != pre_ids:
                        return {"verified": True, "checked": True, "reason": "Modal/dialog visibility state changed (opened or closed)."}
                except Exception:
                    pass
                if next_locator_code and next_metadata:
                    try:
                        next_check = _qa_verify_code(next_locator_code, next_metadata)
                        if (next_check.get("count") == 1 and next_check.get("visible")
                                and next_check.get("actionable") and next_check.get("semantic_compatible")):
                            return {
                                "verified": True, "checked": True,
                                "reason": "The next recorded step's control became resolvable, visible, and semantically matches its recorded identity.",
                            }
                    except Exception:
                        pass
                _qa_active_page().wait_for_timeout(100)
            return {
                "verified": False, "checked": True,
                "reason": (
                    "Modal/dialog visibility did not change after a "
                    f"{post_action_dynamic_deadline_s:.1f}-second synchronization window."
                ),
            }
        popup_evidence = any(
            str((metadata or {}).get(key) or "").strip() not in {"", "-"}
            for key in ("popup_locator", "transaction_popup")
        ) or str((metadata or {}).get("control_role") or "").lower() == "combobox"
        if ((field_type in {"dropdown", "combobox", "autocomplete", "calendar"}
             or action_type in {"open_dropdown", "open_calendar"}) and popup_evidence):
            # ROUND-5-INTERACTION-TARGET: `code_text` is whatever actually
            # got clicked -- as of this round, that can now be a distinct
            # physical interaction surface (e.g. a composite widget's own
            # trigger icon) rather than the logical/semantic control
            # itself. Popup/ARIA state (aria-expanded, aria-controls,
            # aria-owns) belongs to the LOGICAL control described by this
            # step's own metadata, never to whatever happened to be
            # clicked -- so resolve the owning node from the ALREADY-
            # VERIFIED logical owner this step's precondition gate
            # selected (resolved_owner, whichever source it used),
            # falling back to metadata["primary_locator"] only when no
            # resolved_owner was supplied, and falling back further to
            # the already-resolved `locator` (code_text's own target)
            # when neither is available/unique. For every script
            # recorded before Round 8C and every step where the cascade
            # selected "primary", resolved_owner's code and
            # metadata["primary_locator"] name the same element, so this
            # is a no-op there.
            resolved_owner_code = (
                _qa_locator_expression_from_code(resolved_owner["code"])
                if resolved_owner and resolved_owner.get("code") else ""
            )
            owner_locator_code = resolved_owner_code or _qa_locator_code((metadata or {}).get("primary_locator"))
            owner_locator = locator
            if owner_locator_code:
                try:
                    candidate_owner = eval(owner_locator_code, {**globals(), "page": _qa_active_page()})
                    if hasattr(candidate_owner, "count") and candidate_owner.count() == 1:
                        owner_locator = candidate_owner
                except Exception:
                    pass
            if owner_locator is None or not hasattr(owner_locator, "count") or owner_locator.count() != 1:
                return {"verified": False, "checked": True, "reason": "Owning control is no longer uniquely resolvable."}
            node = owner_locator.first
            # Dynamic SPA widgets (Kendo/React/etc.) often mount their popup on
            # the next animation/render tick.  An immediate check can therefore
            # report POST_ACTION_UNCERTAIN even though the click succeeded.
            # Poll briefly and accept independent runtime evidence from the
            # owning control, its aria-controls/aria-owns target, or a visible
            # popup surface.  Do not rely on the recorded popup UUID: framework
            # generated IDs can legitimately change between record and replay.
            # Section 8 readiness signal: the NEXT recorded step's own
            # locator becoming uniquely resolvable and visible is
            # independent, strong evidence that THIS step's popup
            # really did open — e.g. "Commercial" appearing is
            # evidence Consignment Category's dropdown opened, even on
            # a render tick where aria-expanded/aria-owns/the generic
            # popup-surface scan above haven't (yet, or ever, for this
            # particular widget) reported anything. Computed lazily,
            # only once per poll iteration, and never allowed to raise
            # past this check — a bad/absent next-step locator simply
            # contributes no extra evidence, it never fails the step.
            next_locator_code = _qa_locator_code((next_metadata or {}).get("primary_locator"))
            # ROUND-4-DYNAMIC-SYNC: this used to be an isolated, hard-
            # coded `+ 3.0` literal — the ONLY wait budget in the whole
            # harness not owned by the operator-configured timeout
            # system. Every other "give the SPA time to reach an
            # expected state" wait (_qa_resolve_action()'s pre-action
            # readiness polling, above) already scales off
            # _QA_STATE_READINESS_TIMEOUT_MS (5-15s, derived from the
            # configured Execute timeout at harness-build time). This
            # is the exact same category of wait -- an async
            # dropdown/calendar/popup mount tick -- so it now shares
            # that same budget instead of its own disconnected,
            # un-configurable 3-second ceiling. The polling loop itself
            # (100ms interval, multiple independent positive-evidence
            # checks, next-step-readiness) was already correctly
            # state-based, not sleep-based -- only the deadline size
            # and its ownership were wrong. A slow render now gets the
            # same runway a slow locator resolution already gets;
            # nothing here got a blanket, unconditional longer sleep.
            post_action_dynamic_deadline_s = _QA_STATE_READINESS_TIMEOUT_MS / 1000.0
            deadline = time.monotonic() + post_action_dynamic_deadline_s
            last_expanded = None
            while time.monotonic() < deadline:
                try:
                    last_expanded = node.get_attribute("aria-expanded")
                    if last_expanded == "true":
                        return {"verified": True, "checked": True, "reason": "Owning control reports aria-expanded=true."}
                    owns = (node.get_attribute("aria-controls") or node.get_attribute("aria-owns") or "").strip()
                    if owns:
                        for popup_id in owns.split():
                            owned = _qa_active_page().locator("#" + popup_id)
                            if owned.count() == 1 and owned.first.is_visible():
                                return {"verified": True, "checked": True, "reason": "Owning control's runtime popup is visible."}
                    # ROUND-5-POPUP-OWNERSHIP: a page can legitimately have
                    # more than one popup-capable control. "Something
                    # matching the generic surface selector is visible
                    # ANYWHERE on the page" is not ownership evidence -- it
                    # would equally (and wrongly) satisfy this step if some
                    # OTHER, unrelated popup happened to already be open.
                    # Require NEW evidence instead: a popup-like surface
                    # that was not present in the pre-action snapshot taken
                    # immediately before this step's action executed
                    # (_qa_popup_surface_snapshot(), in _qa_run_step()).
                    # This still supports a framework portal that renders
                    # its popup outside the control's own DOM subtree --
                    # judged by newness, never by ancestry -- and costs
                    # nothing extra when no snapshot was supplied (treated
                    # as "nothing was visible before", i.e. identical to
                    # the previous behavior).
                    current_popup_ids = _qa_visible_popup_surface_ids()
                    new_popup_ids = [
                        popup_id for popup_id in current_popup_ids
                        if popup_id not in (pre_action_popup_ids or [])
                    ]
                    if new_popup_ids:
                        return {"verified": True, "checked": True, "reason": "A new popup/calendar surface became visible."}
                except Exception:
                    pass
                if next_locator_code and next_metadata:
                    try:
                        # ROUND-4-DYNAMIC-SYNC (items K/L): reuse the SAME
                        # generic verifier the pre-action readiness loop
                        # uses (_qa_resolve_action() -> _qa_verify_code(),
                        # above) instead of a bespoke count()==1/is_visible()
                        # check. A page element that happens to be the only
                        # match for the next step's raw locator string, but
                        # belongs to a different field/role/section (an
                        # unrelated popup elsewhere on the page -- item K),
                        # is rejected the same way a mismatched AI-suggested
                        # locator candidate is rejected during pre-action
                        # resolution: count()==1 AND visible AND actionable
                        # AND semantic_compatible against the next step's own
                        # recorded metadata (field_name/type/context) -- "safe
                        # semantic compatibility, not merely something
                        # exists" (item L). Nothing here is field-name- or
                        # application-specific; the comparison is purely
                        # structural, driven entirely by next_metadata.
                        next_check = _qa_verify_code(next_locator_code, next_metadata)
                        if (next_check.get("count") == 1 and next_check.get("visible")
                                and next_check.get("actionable") and next_check.get("semantic_compatible")):
                            return {
                                "verified": True, "checked": True,
                                "reason": "The next recorded step's control became resolvable, visible, and semantically matches its recorded identity.",
                            }
                    except Exception:
                        pass
                _qa_active_page().wait_for_timeout(100)
            return {
                "verified": False, "checked": True,
                "reason": (
                    "Owning popup/calendar did not become visible after a "
                    f"{post_action_dynamic_deadline_s:.1f}-second synchronization window."
                ),
            }
        if field_type in {"checkbox", "radio"} or action_type in {"check", "uncheck"}:
            kind_label = "Radio" if field_type == "radio" else "Checkbox"
            if locator is None or locator.count() != 1:
                return {"verified": False, "checked": True, "reason": f"{kind_label} is no longer uniquely resolvable."}
            node = locator.first
            expects_unchecked = (
                action_type == "uncheck" or ".uncheck(" in code_text
                or str((metadata or {}).get("expected_post_state") or "").strip().lower().startswith("unchecked")
            )
            state = node.is_checked()
            verified = state is (not expects_unchecked)
            if verified:
                reason = f"{kind_label} is " + ("unchecked as expected." if expects_unchecked else "checked as expected.")
            else:
                reason = f"{kind_label} " + ("did not become unchecked." if expects_unchecked else "did not become checked.")
            return {"verified": verified, "checked": True, "reason": reason}
        if field_type in {"option", "date"} and expected:
            parent_code = _qa_locator_code((metadata or {}).get("parent_locator"))
            if parent_code:
                parent = eval(parent_code, {**globals(), "page": _qa_active_page()})
                if hasattr(parent, "count") and parent.count() == 1:
                    is_value_control = parent.first.evaluate(
                        "el => ['INPUT', 'TEXTAREA', 'SELECT'].includes(el.tagName)"
                    )
                    actual = parent.first.input_value() if is_value_control else (parent.first.inner_text() or "")
                    popup_open = _qa_active_page().locator('[role="listbox"],.k-list-container,.k-popup,.k-calendar').evaluate_all(r"""els => els.some(el => getComputedStyle(el).display !== 'none' && getComputedStyle(el).visibility !== 'hidden' && el.getClientRects().length)""")
                    ok = expected.lower() in str(actual).lower() or not popup_open
                    return {"verified": ok, "checked": True, "reason": f"Owning field does not reflect '{expected}' and its popup remains open."}
        # A successful Playwright click already proves that its target was
        # uniquely actionable before dispatch. Modal/dialog buttons and
        # navigation/rerender controls commonly disappear afterwards; their
        # old locator is therefore not a valid post-condition.
        # UNIVERSAL STEP COMPLETION GATE (item 3 "button: recorded expected
        # post-state and/or next-step readiness"; item 4's generic NEXT-STEP
        # READINESS GATE): a plain button/click with no other deterministic
        # post-condition still gets one bounded, state-based look ahead at
        # the next recorded step before declaring itself complete -- this
        # never turns into a failure by itself (see
        # _qa_wait_for_next_step_readiness's own contract), it only lets a
        # button-triggered async transition (e.g. Create -> first form
        # control) finish before Step N is marked PASS.
        next_ready = _qa_wait_for_next_step_readiness(next_metadata)
        return {
            "verified": True, "checked": next_ready,
            "reason": (
                "The next recorded step's control became resolvable, visible, and semantically "
                "matches its recorded identity."
            ) if next_ready else "Action completed; no deterministic post-condition was required.",
        }
    except Exception as ex:
        # Section 13: NameError/AttributeError/TypeError/KeyError here
        # can only come from a bug in THIS function's own Python code
        # (a typo'd name, a wrong argument count, a missing key) — they
        # are never how Playwright reports "the application didn't do
        # what I expected" (that comes back as playwright's own Error/
        # TimeoutError subclasses, or as a plain False from an is_*()
        # call, both already handled as ordinary "verified": False
        # results above). Tagging these separately is what let a prior
        # "name 'time' is not defined" failure be mistaken for a real
        # Consignment Category state problem instead of what it always
        # was: a bug in this verifier itself. The action is still
        # never repeated either way — see _qa_post_action_event_name()
        # and _qa_run_step()'s _QaPostActionVerificationError handling,
        # neither of which treats infrastructure_error as safe to retry.
        infrastructure_error = isinstance(
            ex, (NameError, AttributeError, TypeError, KeyError, UnboundLocalError)
        )
        reason = "Sensitive input verification failed (" + type(ex).__name__ + ")." if sensitive else str(ex)
        if infrastructure_error and not sensitive:
            reason = (
                f"Verification infrastructure error ({type(ex).__name__}: {ex}). "
                "This is a bug in the verifier itself, not evidence the application "
                "failed to reach the expected state."
            )
        return {
            "verified": False, "checked": True, "reason": reason,
            "infrastructure_error": infrastructure_error,
        }
def _qa_handle_step_failure(step_number, code_text, error_text, attempt, metadata=None):
    if _QA_STATE["failed_step"] != step_number:
        _QA_STATE["repair_generation"] += 1
    _QA_STATE.update({
        "current_step": step_number, "failed_step": step_number,
        "repair_state": "waiting", "pending_selection_step": None,
    })
    generation = _QA_STATE["repair_generation"]
    _qa_mark_failed_control(code_text, metadata)
    locator, value = _qa_extract_locator_and_value(code_text)
    if code_text.lstrip().startswith('CalendarAction('):
        locator = str((metadata or {}).get('primary_locator') or '')
        value = str((metadata or {}).get('expected_value') or '')
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
        "frames": context["frames"],
        **_qa_state_fields(),
    })
    # Loop reading commands for this SAME pause -- "verify_locator" is
    # a non-terminal, repeatable check (it does not count as a repair
    # attempt and does not resume the run): it reports back a
    # "locator_verified" event and waits for the NEXT command. Only a
    # terminal action (retry / retry_code / cancel) ever returns from
    # this function.
    while True:
        command = _qa_read_command()
        command_step = command.get("step")
        command_generation = command.get("repair_generation")
        if ((command_step is not None and int(command_step) != step_number) or
                (command_generation is not None and int(command_generation) != generation)):
            _qa_send_event({
                "event": "stale_decision_rejected", "step": step_number,
                "repair_generation": generation,
                "error": "Decision belongs to a different failed step or repair generation.",
            })
            continue
        action = command.get("action")
        if action == "select_element":
            _QA_STATE.update({"repair_state": "selecting", "pending_selection_step": step_number})
            _qa_send_event({"event": "selection_started", "step": step_number, "total_steps": _QA_TOTAL_STEPS, "repair_generation": generation})
            while True:
                _qa_send_event({"event": "selection_waiting", "step": step_number, "total_steps": _QA_TOTAL_STEPS, "timeout_seconds": int(_QA_SELECTION_TIMEOUT_MS / 1000)})
                selection = _qa_select_element(code_text, step_number, metadata)
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
                "repair_generation": generation,
                }
                _qa_send_event(selected_event)
                corrected_code = selection.get("corrected_code")
                if corrected_code:
                    _QA_SELECTIONS[step_number] = selection
                    _QA_STATE["repair_state"] = "verified"
                    return corrected_code
                rejection_reasons = [candidate.get("rejected_reason") for candidate in selection.get("candidates") or [] if candidate.get("rejected_reason")]
                rejection_reason = rejection_reasons[0] if rejection_reasons else "No unique, visible, actionable, semantically compatible locator was verified."
                _qa_send_event({"event": "selection_rejected", "step": step_number, "repair_generation": generation, "error": rejection_reason})
                _qa_send_event({"event": "selection_waiting", "step": step_number, "total_steps": _QA_TOTAL_STEPS, "repair_generation": generation, "timeout_seconds": int(_QA_SELECTION_TIMEOUT_MS / 1000), "message": "No safe locator was verified; selection remains active."})
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


def _qa_confirm_repair_persistence(step_number, repair_generation, code_text, corrected_code, selection=None):
    # By the time this is called, the browser action for this step has
    # ALREADY executed and ALREADY passed post-action verification. This
    # function's job is bookkeeping only: confirm (or not) that the parent
    # orchestrator's persistence ack matches this step/generation. It must
    # NEVER raise -- an unconfirmed ack is a distinct, non-fatal outcome
    # (REPAIR_PERSISTENCE_FAILED), not an ACTION_FAILED or
    # VERIFICATION_FAILED outcome, and must never cause the already-
    # succeeded action to be treated as a failure or retried/repeated.
    #
    # PERSISTENCE SAFETY (central guard, item 4): this is the one place
    # every call site in this file funnels through before anything is
    # actually requested to be written into durable recorded_script.
    # _qa_run_step() itself is already fixed to never offer an execution-
    # scoped runtime adaptation (e.g. the owner-scoped physical-trigger
    # click) here as `corrected_code` in the first place -- this is
    # deliberate defense-in-depth on top of that, not the only gate: a
    # future call site, or a mistake, must not be able to persist a non-
    # durable candidate just by calling this function directly.
    if not _qa_is_durable_persistable_code(corrected_code):
        _qa_send_event({
            "event": "runtime_adaptation_not_persisted",
            "step": step_number,
            "total_steps": _QA_TOTAL_STEPS,
            "repair_generation": repair_generation,
            "adaptation_type": (
                "owner_scoped_physical_trigger"
                if _qa_contains_non_replayable_runtime_marker(corrected_code)
                else "non_durable_candidate"
            ),
        })
        return False
    persistence = _qa_read_command()
    # NOTE: `repair_generation` legitimately starts at 0 (the very first
    # repair in a run, via the resolve-first path, is generation 0). The
    # previous `value or -1` pattern treated that real 0 as falsy and
    # substituted -1, so a perfectly matching ack for generation 0 could
    # never compare equal -- a second, independent root cause of the
    # reported "Verified repair could not be persisted" failures on a
    # step's first-ever repair. Use explicit `is not None` checks instead
    # so a genuine 0 is never mistaken for "missing".
    ack_step = persistence.get("step")
    ack_generation = persistence.get("repair_generation")
    confirmed = (
        persistence.get("action") == "repair_persisted"
        and ack_step is not None and int(ack_step) == step_number
        and ack_generation is not None and int(ack_generation) == repair_generation
    )
    repair_record = {
        "step": step_number,
        "repair_generation": repair_generation,
        "original": code_text,
        "corrected": corrected_code,
        "persistence_confirmed": confirmed,
    }
    if selection is not None:
        repair_record["selection"] = selection
    _QA_REPAIRS.append(repair_record)
    if not confirmed:
        _qa_send_event({
            "event": "repair_persistence_uncertain",
            "step": step_number,
            "total_steps": _QA_TOTAL_STEPS,
            "repair_generation": repair_generation,
            "detail": persistence,
        })
    return confirmed


def _qa_upload_search_dirs():
    """Folders searched (in order) for a recorded upload file NAME.

    A browser never exposes the real path of a file the operator picked, so
    the recorder stores the file name only. At replay the name is resolved
    against operator-configured folders: QA_UPLOAD_DIRS (os.pathsep list),
    then <repo>/Output/TestData, then the working directory.
    """
    import os as _os
    from pathlib import Path as _Path
    folders = [item for item in _os.environ.get("QA_UPLOAD_DIRS", "").split(_os.pathsep) if item.strip()]
    try:
        import Core.calendar_engine as _calendar_module
        folders.append(str(_Path(_calendar_module.__file__).resolve().parents[1] / "Output" / "TestData"))
    except Exception:
        pass
    folders.append(_os.getcwd())
    return folders


def _qa_prepare_exec_code(code):
    """Resolve recorded upload file NAMES to real files before the single exec().

    Only `set_input_files(<string literal(s)>)` is touched. A missing file
    fails the step with an explicit, actionable message -- an upload is never
    silently skipped, and the persisted script text is never rewritten.
    """
    if "set_input_files" not in code:
        return code
    import ast as _ast
    import os as _os
    try:
        tree = _ast.parse(code.strip())
    except SyntaxError:
        return code

    def resolve(name):
        if not isinstance(name, str) or not name:
            return name
        if _os.path.isabs(name) and _os.path.isfile(name):
            return name
        base = _os.path.basename(name.replace("\\", "/"))
        for folder in _qa_upload_search_dirs():
            for candidate in (_os.path.join(folder, name), _os.path.join(folder, base)):
                if _os.path.isfile(candidate):
                    return candidate
        raise RuntimeError(
            "Upload file '" + base + "' was not found. Place it in a folder listed in QA_UPLOAD_DIRS "
            "or in Output/TestData, or re-record the step with an absolute file path."
        )

    class _Resolver(_ast.NodeTransformer):
        def visit_Call(self, node):
            self.generic_visit(node)
            if isinstance(node.func, _ast.Attribute) and node.func.attr == "set_input_files" and node.args:
                first = node.args[0]
                if isinstance(first, _ast.Constant) and isinstance(first.value, str):
                    node.args[0] = _ast.copy_location(_ast.Constant(resolve(first.value)), first)
                elif isinstance(first, (_ast.List, _ast.Tuple)):
                    first.elts = [
                        _ast.copy_location(_ast.Constant(resolve(item.value)), item)
                        if isinstance(item, _ast.Constant) and isinstance(item.value, str) else item
                        for item in first.elts
                    ]
            return node

    return _ast.unparse(_ast.fix_missing_locations(_Resolver().visit(tree)))


def _qa_adapt_imported_change_step(code_text, metadata):
    """Type-aware handling of an IMPORTED Chrome Recorder ``change`` step.

    Chrome DevTools Recorder records a typed value AND a dropdown choice with the very same
    ``change`` step, so the importer can only translate it to ``fill``.  Playwright's fill() cannot
    drive a native <select>, and the semantic gate (correctly) refuses it.  For steps imported from a
    recording ONLY, when the single resolved element is a native <select> and the recorded value
    equals exactly one of its option values/labels, run it as ``select_option`` instead.  Anything
    else (no/many elements, not a <select>, value is not an option) is returned UNCHANGED and keeps
    failing closed.  The adaptation is reported through the normal repair/persistence protocol so the
    stored script is corrected after the operator confirms.  A previously persisted adaptation
    (code already select_option, metadata still saying fill) just gets its metadata normalised.
    """
    meta = dict(metadata or {})
    # An imported step keeps its adaptation after the operator/auto-repair persisted it: the repair
    # writer re-labels the step's SOURCE as "repaired/user-selected", so that label is accepted too.
    if str(meta.get("source") or "").lower() not in {"chrome-recorder-import", "repaired/user-selected"}:
        return code_text, metadata
    action = str(meta.get("action_type") or "").lower()
    if action not in {"fill", "select_option", "click"}:
        return code_text, metadata
    try:
        expr = _qa_locator_expression_from_code(code_text)
        if not expr.startswith("page."):
            return code_text, metadata
        locator = eval(expr, {**globals(), "page": _qa_active_page()})
        if not hasattr(locator, "count") or locator.count() != 1:
            # The saved primary no longer resolves (the page drifted): identify the control through
            # the step's own saved fallbacks instead, so a drifted dropdown / checkbox can still be
            # healed.  Only a candidate that resolves to exactly ONE element is accepted.
            locator = None
            for candidate_code in _qa_persisted_candidate_codes(code_text, meta):
                try:
                    candidate_expr = _qa_locator_expression_from_code(candidate_code)
                    if not candidate_expr.startswith("page."):
                        continue
                    candidate = eval(candidate_expr, {**globals(), "page": _qa_active_page()})
                    if hasattr(candidate, "count") and candidate.count() == 1:
                        locator = candidate
                        break
                except Exception:
                    continue
            if locator is None:
                return code_text, metadata
        if action == "click":
            # Chrome records a checkbox / radio toggle as a plain click. Keep the click (it toggles
            # exactly like the recording did) but give it checkbox/radio semantics so the gate accepts
            # the control and verification checks the state really flipped.
            kind = locator.first.evaluate(
                "el => el.tagName === 'INPUT' && ['checkbox','radio'].includes(el.type) ? "
                "[el.type, el.checked] : null"
            )
            if not kind:
                return code_text, metadata
            will_uncheck = kind[0] == "checkbox" and bool(kind[1])
            return code_text, dict(meta, field_type=kind[0], expected_post_state="unchecked" if will_uncheck else "checked")
        info = locator.first.evaluate(
            "el => el.tagName === 'SELECT' ? Array.from(el.options).map(o => [o.value, (o.label || o.text || '').trim()]) : null"
        )
        if not info:
            return code_text, metadata
        adapted = dict(meta, action_type="select_option", field_type="dropdown")
        if action == "select_option" or (".select_option(" in code_text and ".fill(" not in code_text):
            # Already adapted (and saved) earlier: only the step's metadata is stale.
            return code_text, adapted
        match = re.search(r"\.fill\((['\"])(.*)\1\)\s*$", code_text.strip(), re.S)
        if not match:
            return code_text, metadata
        wanted = match.group(2)
        hits = [o for o in info if wanted in (o[0], o[1])]
        if len(hits) != 1:
            return code_text, metadata
        new_code = code_text.strip()[: match.start()] + ".select_option(" + repr(wanted) + ")"
        _qa_send_event({
            "event": "action_adapted", "step": _QA_STATE.get("current_step"), "total_steps": _QA_TOTAL_STEPS,
            "from_action": "fill", "to_action": "select_option",
            "reason": "Imported 'change' step targets a native dropdown and the value is one of its options.",
        })
        return new_code, adapted
    except Exception:
        return code_text, metadata


# POPUP-SELECTION-CAPTURE --------------------------------------------
# A step performed INSIDE a popup / lookup window can return a value to the
# window that opened it (a selectable item that fills a parent form field and
# closes the popup). The recorder stores what the popup returned (POPUP_RETURN:
# the opener's changed control(s) and whether the step closed the popup).
# Replay must not report such a step as passed merely because the click ran:
# the popup must have closed when it did at record time and the opener's
# control must now hold the recorded value. A mismatch is a real failure and
# is never retried (the click already happened -- repeating it could select a
# different row or submit twice).
_QA_POPUP_CONTROL_VALUE_JS = r"""
el => {
    const tag = el.tagName.toLowerCase();
    const type = (el.getAttribute('type') || (tag === 'input' ? 'text' : tag)).toLowerCase();
    if (tag === 'select') { const o = el.selectedOptions && el.selectedOptions[0]; return o ? (o.text || o.value || '') : ''; }
    if (type === 'checkbox' || type === 'radio') return el.checked ? 'checked' : 'unchecked';
    return el.value == null ? '' : String(el.value);
}
"""


def _qa_popup_return_spec(metadata):
    raw = str((metadata or {}).get("popup_return") or "").strip()
    if not raw or raw == "-":
        return None
    try:
        spec = json.loads(raw)
    except Exception:
        return {"unreadable": True}
    return spec if isinstance(spec, dict) else {"unreadable": True}


def _qa_popup_control(opener, change):
    # Resolve the opener's control for one recorded change; never ambiguous.
    candidates = []
    display = str(change.get("locator") or "").strip()
    if display and display != "-":
        try:
            code = _qa_locator_code(display)
            candidates.append(eval(code, {"page": opener}))
        except Exception:
            pass
    xpath = str(change.get("xpath") or "").strip()
    if xpath:
        try:
            candidates.append(opener.locator("xpath=" + xpath))
        except Exception:
            pass
    for locator in candidates:
        try:
            if locator.count() == 1:
                return locator
        except Exception:
            continue
    return None


def _qa_normalise_popup_value(value):
    return re.sub(r"\s+", " ", str(value if value is not None else "")).strip()


def _qa_verify_popup_return(metadata, popup=None):
    # Returns None when the step carries no recorded popup return; otherwise a
    # dict {verified, checked, reason, ...} in the post-action verdict shape.
    spec = _qa_popup_return_spec(metadata)
    if spec is None:
        return None
    if spec.get("unreadable"):
        return {"verified": False, "checked": True, "reason": "The recorded popup return metadata could not be read; re-record this step."}
    popup = popup if popup is not None else globals().get("page")
    if popup is None:
        return {"verified": False, "checked": True, "reason": "No popup window is available to verify the selection."}
    timeout_ms = 6000
    if spec.get("popup_closed"):
        waited = 0
        while waited < timeout_ms:
            try:
                if popup.is_closed():
                    break
            except Exception:
                break
            time.sleep(0.1)
            waited += 100
        try:
            still_open = not popup.is_closed()
        except Exception:
            still_open = False
        if still_open:
            return {"verified": False, "checked": True, "popup_closed": False,
                    "reason": "The popup was recorded closing after this selection but it is still open: the selection did not take effect."}
    changes = [change for change in (spec.get("changes") or []) if isinstance(change, dict)]
    result = {"verified": True, "checked": True, "popup_closed": bool(spec.get("popup_closed")), "returned": [],
              "reason": "Popup closed as recorded." if spec.get("popup_closed") and not changes else "Popup step verified."}
    if not changes:
        return result
    opener = None
    try:
        opener = popup.opener()
    except Exception:
        opener = None
    if opener is None:
        try:
            opener = _qa_active_page()
        except Exception:
            opener = None
    if opener is None or opener is popup:
        return {"verified": False, "checked": True,
                "reason": "The page that opened the popup is no longer available, so the returned selection cannot be verified."}
    for change in changes:
        expected = _qa_normalise_popup_value(change.get("expected"))
        field = change.get("field") or change.get("locator") or "field"
        actual = None
        waited = 0
        control = None
        while waited <= timeout_ms:
            control = _qa_popup_control(opener, change)
            if control is not None:
                try:
                    actual = _qa_normalise_popup_value(control.evaluate(_QA_POPUP_CONTROL_VALUE_JS))
                except Exception:
                    actual = None
                if actual == expected:
                    break
            time.sleep(0.2)
            waited += 200
        if control is None:
            return {"verified": False, "checked": True,
                    "reason": "The form field '" + str(field) + "' that the popup selection should update was not found on the opening page."}
        if actual != expected:
            return {"verified": False, "checked": True, "expected": expected, "actual": actual,
                    "reason": "The popup selection did not update '" + str(field) + "': expected '" + expected + "' but the field holds '" + str(actual) + "'."}
        result["returned"].append({"field": field, "value": actual})
    result["reason"] = "Popup selection verified: " + ", ".join(
        "'" + str(item["field"]) + "' = '" + str(item["value"]) + "'" for item in result["returned"]
    ) + (" and the popup closed." if spec.get("popup_closed") else ".")
    return result


# Popups opened by a step that no later step ever switches to. A recorded
# script cannot select anything in a window it never touches, so this is
# reported (never silently) as soon as the script moves on without it.
_QA_POPUP_LEDGER = {}


def _qa_note_popup_activity(step_number, code_text):
    try:
        text = str(code_text or "")
        opened = re.search(r"expect_popup\(\)\s+as\s+(\w+)_info", text)
        if opened:
            _QA_POPUP_LEDGER[opened.group(1)] = {"opened_step": step_number, "used": False, "warned": False}
            return
        switch = re.match(r"\s*page\s*=\s*(\w+)_info\.value\s*$", text)
        if switch and switch.group(1) in _QA_POPUP_LEDGER:
            _QA_POPUP_LEDGER[switch.group(1)]["used"] = True
            return
        for name, entry in _QA_POPUP_LEDGER.items():
            if entry["used"] or entry["warned"] or step_number <= entry["opened_step"]:
                continue
            entry["warned"] = True
            _qa_send_event({
                "event": "popup_unattended", "step": step_number, "total_steps": _QA_TOTAL_STEPS,
                "popup": name, "opened_step": entry["opened_step"],
                "reason": "Step " + str(entry["opened_step"]) + " opened a popup window but no recorded step interacts with it. "
                          "If a selection is made in that window, it is missing from this script: re-record it.",
            })
    except Exception:
        pass


def _qa_run_step(step_number, code_text, metadata=None, next_metadata=None):
    global _QA_LAST_DYNAMIC_CONTROL
    if step_number <= _QA_STATE["last_passed_step"]:
        raise RuntimeError("Interactive replay attempted to rerun an already checkpointed step.")
    # LEGACY/CORRUPTED-SCRIPT FAIL-CLOSED PROTECTION (item 6): a step's
    # OWN recorded code_text -- what this run was handed before any
    # resolution ever ran -- must never itself be a runtime-only
    # internal placeholder. This can only happen if a prior (now fixed)
    # repair-persistence defect already wrote one into durable
    # recorded_script. Detected here, once, before the per-step retry
    # loop even starts: zero exec() calls, and no stale handle left over
    # from an earlier step in this same run is ever consulted or reused
    # for it.
    if _qa_contains_non_replayable_runtime_marker(code_text):
        _qa_send_event({
            "event": "step_failed_non_replayable_placeholder",
            "step": step_number, "total_steps": _QA_TOTAL_STEPS,
            "reason": "NON_REPLAYABLE_RUNTIME_PLACEHOLDER",
            "details": _qa_step_details(code_text, metadata),
        })
        raise _QaNonReplayableRuntimePlaceholderError(step_number, code_text)
    _QA_STATE.update({"current_step": step_number, "repair_state": "executing"})
    current_code = code_text
    current_code, metadata = _qa_adapt_imported_change_step(current_code, metadata)
    _qa_note_popup_activity(step_number, code_text)
    attempt = 0
    while True:
        _qa_send_event({"event": "step_started", "step": step_number, "total_steps": _QA_TOTAL_STEPS, "attempt": attempt + 1, "details": _qa_step_details(current_code, metadata)})
        pre_action_popup_ids = None
        owner_scoped_diagnostics = None
        resolved_candidate = None
        # PERSISTENCE SAFETY (item 2): tracks ONLY genuinely durable,
        # independently-replayable code -- defaults to whatever
        # current_code already is (code_text, unchanged, on the common
        # path). The owner-scoped physical-trigger substitution below
        # deliberately does NOT update this: that substitution is an
        # execution-scoped adaptation for THIS run only, never a locator
        # repair, so it must never make this step look durably repaired.
        durable_candidate_code = current_code
        # Independent safety requirement (item 5): never let a step
        # silently inherit a handle a DIFFERENT, earlier step resolved
        # and assigned. Only a successful owner-scoped resolution for
        # THIS step/attempt may populate it again below.
        _QA_OWNER_SCOPED_TRIGGER_HANDLE["handle"] = None
        try:
            action_type = str((metadata or {}).get("action_type") or "").lower()
            expression = _qa_locator_expression_from_code(current_code)
            locator_action = (
                expression.startswith("page.")
                and expression != current_code.strip()
                # Page-level input devices (page.keyboard / page.mouse /
                # page.touchscreen) act on the focused/pointer state, not on
                # a locator -- their string arguments are key names or text,
                # never selectors, so they must not enter locator resolution.
                and expression not in {"page.keyboard", "page.mouse", "page.touchscreen"}
                and action_type not in {"goto", "navigation", "execute", "assert"}
                and not current_code.lstrip().startswith(("assert ", "expect("))
            )
            if locator_action and attempt == 0:
                resolved_code, resolved_candidate, resolution_attempts = _qa_resolve_action(
                    current_code, metadata
                )
                _qa_send_event({
                    "event": "locator_resolution", "step": step_number,
                    "total_steps": _QA_TOTAL_STEPS,
                    "selected_source": (resolved_candidate or {}).get("source"),
                    "attempts": [{
                        "source": item.get("source"),
                        "identity": item.get("identity"),
                        "count": item.get("count", 0),
                        "visible": bool(item.get("visible")),
                        "actionable": bool(item.get("actionable")),
                        "semantic_compatible": bool(item.get("semantic_compatible")),
                        "semantic_reason": item.get("semantic_reason") or "",
                        "rejection_reason": (
                            "not found" if not item.get("count") else
                            "ambiguous" if item.get("count") != 1 else
                            "not visible" if not item.get("visible") else
                            "not actionable" if not item.get("actionable") else
                            "semantic mismatch" if not item.get("semantic_compatible") else ""
                        ),
                    } for item in resolution_attempts[:50]],
                })
                if not resolved_code:
                    raise _QaLocatorResolutionError(resolution_attempts)
                current_code = resolved_code
                # The generic cascade's own resolved_code is always a
                # genuinely durable, independently replayable statement
                # (primary/exact_semantic/fallback candidates are never
                # runtime-only references) -- safe to track as the
                # durable candidate unconditionally.
                durable_candidate_code = resolved_code
                if current_code != code_text:
                    _QA_SELECTIONS[step_number] = {
                        "repair_source": resolved_candidate.get("source"),
                        "selected_candidate": resolved_candidate,
                    }
                # ROUND-5-INTERACTION-TARGET: the logical/semantic control
                # is now resolved and fully validated above (identity,
                # visibility, actionability, semantic compatibility) --
                # that stays the owner for validation/ownership/next-step
                # purposes, unchanged. Separately, when the recorder
                # captured a distinct physical interaction surface for
                # this control (metadata["interaction_target"] -- e.g. a
                # composite widget's own trigger icon; present only when
                # the recorder observed the real click land somewhere
                # other than the logical control -- never a PSW- or
                # field-specific special case), resolve and validate THAT
                # too, generically, through the exact same verifier used
                # everywhere else in this harness (_qa_verify_code(), the
                # same one _qa_resolve_action() itself uses above). Absent
                # or unresolvable, this is a silent no-op: current_code
                # stays exactly what it already was. Target selection
                # happens exactly once, here, BEFORE any execution --
                # never as a second attempt after an uncertain click
                # (side-effect safety: one action, chosen up front).
                interaction_target_display = (metadata or {}).get("interaction_target")
                interaction_target_scope = str((metadata or {}).get("interaction_target_scope") or "").strip().lower()
                # ROUND-8-OWNER-SCOPED-INTERACTION-TARGET: a separate,
                # explicit metadata field -- never inferred from the
                # display string's own format, never a reinterpretation of
                # old scripts -- distinguishes the new owner-scoped path
                # from the existing global path below. Absent/"-"/
                # "global" (every script recorded before this round, and
                # every step whose physical trigger DOES have a safe
                # global locator) takes the completely unchanged Round 5/6
                # branch. Only "owner" takes this new branch, and it does
                # so INSTEAD of the global branch, never in addition to
                # it: interaction_target_display in the owner-scoped case
                # is a scope-RELATIVE locator, never safe to resolve
                # globally. If owner-scoped resolution fails for any
                # reason, this is a silent no-op -- current_code stays
                # exactly what it already was (the validated logical-
                # owner action), exactly like the existing "absent or
                # unresolvable" no-op convention below.
                if interaction_target_scope == "owner" and interaction_target_display:
                    owner_scoped_handle, owner_scoped_diagnostics = _qa_resolve_owner_scoped_trigger(
                        metadata, resolved_owner=resolved_candidate
                    )
                    if owner_scoped_handle is not None:
                        _QA_OWNER_SCOPED_TRIGGER_HANDLE["handle"] = owner_scoped_handle
                        _qa_send_event({
                            "event": "interaction_target_selected", "step": step_number,
                            "total_steps": _QA_TOTAL_STEPS,
                            "logical_locator": (metadata or {}).get("primary_locator"),
                            "interaction_target": interaction_target_display,
                            "interaction_target_scope": "owner",
                            "logical_owner_source": owner_scoped_diagnostics.get("logical_owner_source"),
                            "logical_owner_locator": owner_scoped_diagnostics.get("logical_owner_locator"),
                            "owner_scope_found": owner_scoped_diagnostics.get("owner_scope_found"),
                            "interaction_target_match_count": owner_scoped_diagnostics.get("interaction_target_match_count"),
                        })
                        current_code = "_QA_OWNER_SCOPED_TRIGGER_HANDLE['handle'].click()"
                    else:
                        # ROUND-8C fail-closed contract: a verified
                        # logical owner exists (or resolution was
                        # attempted against the cascade's own selected
                        # candidate) but the declared owner-scoped
                        # PHYSICAL target could not be safely, uniquely
                        # resolved. Never fall through to clicking the
                        # plain logical/fallback control as though it
                        # were equivalent to the declared physical
                        # trigger -- that exact silent fallthrough is
                        # what produced the live Step-14
                        # POST_ACTION_UNCERTAIN failure. Raise
                        # immediately, before exec() is ever reached, so
                        # no action -- physical or logical -- is taken
                        # for this step. Do not add a second click.
                        raise _QaOwnerScopedTargetResolutionError(owner_scoped_diagnostics)
                else:
                    interaction_target_code = (
                        _qa_locator_code(interaction_target_display) if interaction_target_display else ""
                    )
                    if interaction_target_code:
                        interaction_check = _qa_verify_code(interaction_target_code, None)
                        if (interaction_check.get("count") == 1 and interaction_check.get("visible")
                                and interaction_check.get("actionable")):
                            swapped_code = _qa_replace_locator_expression(current_code, interaction_target_code)
                            if swapped_code and swapped_code != current_code:
                                _qa_send_event({
                                    "event": "interaction_target_selected", "step": step_number,
                                    "total_steps": _QA_TOTAL_STEPS,
                                    "logical_locator": (metadata or {}).get("primary_locator"),
                                    "interaction_target": interaction_target_display,
                                })
                                current_code = swapped_code
                                # Unlike the owner-scoped physical-trigger
                                # substitution above, this global-path swap
                                # (Round 5/6, pre-existing) produces a real,
                                # standalone "page."-prefixed locator
                                # statement -- a genuinely durable
                                # improvement over the plain logical code,
                                # safe to persist exactly like any other
                                # durable repair.
                                durable_candidate_code = swapped_code
                # ROUND-5-POPUP-OWNERSHIP: snapshot which popup-like
                # surfaces are already visible BEFORE this action executes
                # (never after), so post-action verification's generic
                # popup-surface scan can require NEW evidence instead of
                # accepting an unrelated popup that was already open
                # elsewhere on the page.
                pre_action_popup_ids = _qa_popup_surface_snapshot()
            try:
                if current_code.lstrip().startswith('CalendarAction(page,'):
                    # Honor the same script-bound/closed-popup restoration
                    # rule as ordinary locator actions.
                    _qa_active_page()
                acting_page = globals().get("page")
                _qa_exec_action(current_code, metadata)
            except CalendarVerificationError as ex:
                raise _QaPostActionVerificationError('Calendar selection could not be verified: ' + str(ex)) from ex
            except CalendarError as ex:
                raise _QaCalendarConfirmationError('Calendar confirmation required: ' + str(ex)) from ex
            _qa_smart_wait(metadata)
            post_check = _qa_verify_post_action(
                current_code, metadata, next_metadata, pre_action_popup_ids, resolved_owner=resolved_candidate
            )
            _qa_send_event({"event": _qa_post_action_event_name(post_check), "step": step_number, "total_steps": _QA_TOTAL_STEPS, **post_check})
            if not post_check["verified"]:
                raise _QaPostActionVerificationError("Post-action verification failed: " + post_check["reason"])
            popup_check = _qa_verify_popup_return(metadata, popup=acting_page)
            if popup_check is not None:
                _qa_send_event({
                    "event": "popup_return_verified" if popup_check["verified"] else "post_action_failed",
                    "step": step_number, "total_steps": _QA_TOTAL_STEPS, **popup_check,
                })
                if not popup_check["verified"]:
                    raise _QaPostActionVerificationError("Popup selection verification failed: " + popup_check["reason"])
            if str((metadata or {}).get("field_type") or "").lower() in {"dropdown", "calendar", "autocomplete", "accordion"}:
                # Track the durable logical control here, not whatever
                # physical surface this particular run happened to
                # execute against -- a parent-reopen retry needs the
                # LOGICAL control's own name/locator to work from, and
                # the owner-scoped execution placeholder never carries
                # one anyway.
                _QA_LAST_DYNAMIC_CONTROL = durable_candidate_code
            # PERSISTENCE SAFETY (items 1/2): gate persistence on the
            # DURABLE candidate, never on whatever current_code happens
            # to equal after a possible execution-scoped substitution.
            # When the only thing that changed this run was the owner-
            # scoped physical-trigger swap, durable_candidate_code still
            # equals code_text here, so this block -- and the
            # "step_repaired"/"Repair saved" telemetry it implies -- is
            # correctly never entered at all for that case.
            if durable_candidate_code != code_text:
                selection = _QA_SELECTIONS.pop(step_number, {})
                _qa_send_event({
                    "event": "step_repaired",
                    "step": step_number,
                    "repair_generation": _QA_STATE["repair_generation"],
                    "original_code": code_text,
                    "corrected_code": durable_candidate_code,
                    "selection": selection,
                })
                _qa_confirm_repair_persistence(
                    step_number, _QA_STATE["repair_generation"], code_text, durable_candidate_code, selection,
                )
            _qa_clear_failed_control()
            _QA_STATE.update({
                "last_passed_step": step_number, "failed_step": None,
                "repair_state": "passed", "pending_selection_step": None,
            })
            step_passed_event = {"event": "step_passed", "step": step_number, "total_steps": _QA_TOTAL_STEPS, "attempt": attempt + 1, "details": _qa_step_details(current_code, metadata)}
            if owner_scoped_diagnostics is not None:
                # ROUND-8C telemetry: step_passed is a reliably-persisted
                # event (unlike the standalone interaction_target_selected
                # event, which was observed to never reach
                # runtime_activity_json even on a successful Round 8 run)
                # -- embed the owner-scoped diagnostics here so a passed
                # owner-scoped step still proves logical_owner_source,
                # owner_scope_found, interaction_target_match_count, etc.
                # without needing a second code path outside Core/.
                step_passed_event["owner_scoped_diagnostics"] = owner_scoped_diagnostics
            _qa_send_event(step_passed_event)
            # UNIVERSAL STEP COMPLETION GATE: Step N is already PASSed
            # and persisted above -- this pause is pure human-observation
            # pacing for the NEXT step's precondition gate, never a
            # substitute for the state-based verification that already
            # ran to get here.
            _qa_replay_speed_pause()
            return
        except Exception as ex:
            if isinstance(ex, _QaCalendarConfirmationError):
                # Safe deterministic calendar recovery was exhausted BEFORE
                # selection. Ask through the established same-session repair
                # protocol; never fall back to a generated day-ID click.
                attempt += 1
                if attempt > _QA_MAX_REPAIR_ROUNDS:
                    raise
                outcome = _qa_handle_step_failure(step_number, current_code, str(ex), attempt, metadata)
                if outcome is None:
                    _qa_clear_failed_control()
                    _qa_send_event({'event':'run_cancelled_by_operator','step':step_number})
                    sys.exit(2)
                current_code = outcome
                _QA_STATE['repair_state'] = 'retrying'
                _qa_send_event({'event':'step_retrying','step':step_number,'attempt':attempt+1})
                continue
            if isinstance(ex, _QaOwnerScopedTargetResolutionError):
                _QA_STATE.update({"failed_step": step_number, "repair_state": "owner_scoped_target_resolution_failed"})
                _qa_send_event({
                    "event": "owner_scoped_target_resolution_failed", "step": step_number,
                    "total_steps": _QA_TOTAL_STEPS, "error": str(ex),
                    "details": _qa_step_details(current_code, metadata),
                    "owner_scoped_diagnostics": ex.diagnostics,
                })
                # No physical or logical action was ever executed for
                # this step -- this failure occurs strictly before
                # exec(). Fail closed immediately: never enter the
                # auto-repair/fallback-candidate/parent-reopen retry
                # machinery below, and never fall through to clicking
                # the plain logical control as a substitute action.
                raise
            if isinstance(ex, _QaPostActionVerificationError):
                _QA_STATE.update({"failed_step": step_number, "repair_state": "post_action_uncertain"})
                _qa_send_event({
                    "event": "post_action_uncertain", "step": step_number,
                    "total_steps": _QA_TOTAL_STEPS, "error": str(ex),
                    "details": _qa_step_details(current_code, metadata),
                })
                # The action itself completed. Never repeat a potentially
                # destructive/navigation action as locator repair.
                raise
            if attempt == 0:
                if _QA_STATE["failed_step"] != step_number:
                    _QA_STATE["repair_generation"] += 1
                _QA_STATE.update({"failed_step": step_number, "repair_state": "failed", "pending_selection_step": None})
                _qa_mark_failed_control(current_code, metadata)
                _qa_send_event({"event": "step_failure_detected", "step": step_number, "total_steps": _QA_TOTAL_STEPS, "error": str(ex)})
                _qa_send_event({"event": "auto_repair_attempted", "step": step_number, "total_steps": _QA_TOTAL_STEPS})
                fallback_codes = (
                    [] if isinstance(ex, _QaLocatorResolutionError)
                    else _qa_persisted_candidate_codes(current_code, metadata)
                )
                for fallback_code in fallback_codes:
                    try:
                        _qa_exec_action(fallback_code, metadata)
                        _qa_smart_wait(metadata)
                        post_check = _qa_verify_post_action(
                            fallback_code, metadata, next_metadata,
                            resolved_owner={"code": fallback_code, "source": "auto_repair_fallback"},
                        )
                        if not post_check["verified"]:
                            raise _QaPostActionVerificationError("Post-action verification failed: " + post_check["reason"])
                        current_code = fallback_code
                        _qa_send_event({
                            "event": "fallback_succeeded", "step": step_number,
                            "total_steps": _QA_TOTAL_STEPS,
                            "details": _qa_step_details(current_code, metadata),
                        })
                        _qa_send_event({
                            "event": "step_repaired",
                            "step": step_number,
                            "repair_generation": _QA_STATE["repair_generation"],
                            "original_code": code_text,
                            "corrected_code": current_code,
                        })
                        _qa_confirm_repair_persistence(
                            step_number, _QA_STATE["repair_generation"], code_text, current_code,
                        )
                        _qa_clear_failed_control()
                        _QA_STATE.update({"last_passed_step": step_number, "failed_step": None, "repair_state": "passed", "pending_selection_step": None})
                        _qa_send_event({
                            "event": "step_passed", "step": step_number,
                            "total_steps": _QA_TOTAL_STEPS, "attempt": attempt + 1,
                            "details": _qa_step_details(current_code, metadata),
                        })
                        _qa_replay_speed_pause()
                        return
                    except _QaPostActionVerificationError as post_error:
                        _qa_send_event({
                            "event": "post_action_uncertain", "step": step_number,
                            "total_steps": _QA_TOTAL_STEPS, "error": str(post_error),
                            "details": _qa_step_details(fallback_code, metadata),
                        })
                        # This candidate action executed. Trying another
                        # locator could repeat a destructive side effect.
                        raise
                    except Exception:
                        continue
                if _qa_try_parent_reopen(metadata):
                    _qa_send_event({"event": "parent_reopen_succeeded", "step": step_number, "total_steps": _QA_TOTAL_STEPS})
                    try:
                        _qa_exec_action(current_code, metadata)
                        _qa_smart_wait(metadata)
                        post_check = _qa_verify_post_action(
                            current_code, metadata, next_metadata,
                            resolved_owner=resolved_candidate or {"code": current_code, "source": "parent_reopen_retry"},
                        )
                        if not post_check["verified"]:
                            raise _QaPostActionVerificationError("Post-action verification failed: " + post_check["reason"])
                        _qa_clear_failed_control()
                        _QA_STATE.update({"last_passed_step": step_number, "failed_step": None, "repair_state": "passed", "pending_selection_step": None})
                        _qa_send_event({"event": "step_passed", "step": step_number, "total_steps": _QA_TOTAL_STEPS, "attempt": attempt + 1, "details": _qa_step_details(current_code, metadata)})
                        _qa_replay_speed_pause()
                        return
                    except Exception as parent_retry_error:
                        if isinstance(parent_retry_error, _QaPostActionVerificationError):
                            _qa_send_event({
                                "event": "post_action_uncertain", "step": step_number,
                                "total_steps": _QA_TOTAL_STEPS, "error": str(parent_retry_error),
                                "details": _qa_step_details(current_code, metadata),
                            })
                            raise
                        _qa_send_event({
                            "event": "parent_retry_failed", "step": step_number,
                            "total_steps": _QA_TOTAL_STEPS, "error": str(parent_retry_error),
                        })
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
                _qa_clear_failed_control()
                _qa_send_event({
                    "event": "run_cancelled_by_operator",
                    "step": step_number,
                })
                sys.exit(2)
            _QA_STATE["repair_state"] = "retrying"
            _qa_send_event({"event": "step_retrying", "step": step_number, "attempt": attempt + 1})
            current_code = outcome
# --- end QA AI Studio interactive step runner ---

'''

# Computed once, at import time, from the exact template text above —
# this is the "harness_version"/"harness_template_sha256" identity
# proof described in the runtime-identity-proof comment inside the
# template itself. Every replay logs this value (see run_script_interactive()
# / _build_interactive_script() / _build_generic_interactive_script()
# below) and bakes it into the generated child script, so the operator
# can always confirm the PARENT process's in-memory template matches
# what actually ran — instead of only being able to inspect the .py
# file on disk, which proves nothing about what a long-running
# --no-reload server process still has loaded in memory.
_QA_HARNESS_TEMPLATE_SHA256 = hashlib.sha256(
    INTERACTIVE_HARNESS_TEMPLATE.encode("utf-8")
).hexdigest()[:16]


def _qa_static_dependency_gaps(source_text):
    """
    Best-effort, non-blocking static safety net for a MECHANICALLY
    ASSEMBLED harness (never arbitrary free-form user code): flags any
    `name.attr` / bare `name(...)` reference whose `name` is never
    bound ANYWHERE in the whole generated file (as an import,
    assignment, def, class, function/lambda parameter, for/with/except
    target, etc.). Such a name is a certain NameError at runtime.

    This is exactly the class of defect a prior "name 'time' is not
    defined" regression turned out to be reachable through (see the
    runtime-identity-proof comments above): compile() alone only
    catches SyntaxError, never a NameError that only manifests once a
    specific branch (e.g. the dropdown/combobox post-action check)
    actually executes on a real device, potentially minutes into a
    real replay. Running this check in the PARENT process, before a
    browser is ever launched, surfaces that same class of mistake
    immediately and for free — deliberately conservative (whole-file
    union of "bound anywhere" vs. "used anywhere", not scope-accurate)
    so it cannot false-positive on ordinary forward references or
    conditionally-executed imports; callers should therefore treat any
    finding as a strong warning to log and investigate, not as proof
    the script cannot possibly run.
    """
    try:
        tree = ast.parse(source_text)
    except SyntaxError:
        return []

    bound = set(dir(_qa_builtins_module))

    class _BindingCollector(ast.NodeVisitor):
        def visit_Import(self, node):
            for alias in node.names:
                bound.add((alias.asname or alias.name).split(".")[0])
            self.generic_visit(node)

        def visit_ImportFrom(self, node):
            for alias in node.names:
                if alias.name == "*":
                    continue
                bound.add(alias.asname or alias.name)
            self.generic_visit(node)

        def visit_FunctionDef(self, node):
            bound.add(node.name)
            self.generic_visit(node)

        def visit_AsyncFunctionDef(self, node):
            bound.add(node.name)
            self.generic_visit(node)

        def visit_ClassDef(self, node):
            bound.add(node.name)
            self.generic_visit(node)

        def visit_Name(self, node):
            if isinstance(node.ctx, (ast.Store, ast.Del)):
                bound.add(node.id)
            self.generic_visit(node)

        def visit_arg(self, node):
            bound.add(node.arg)
            self.generic_visit(node)

        def visit_Global(self, node):
            for name in node.names:
                bound.add(name)
            self.generic_visit(node)

        def visit_Nonlocal(self, node):
            for name in node.names:
                bound.add(name)
            self.generic_visit(node)

        def visit_ExceptHandler(self, node):
            if node.name:
                bound.add(node.name)
            self.generic_visit(node)

    _BindingCollector().visit(tree)

    used_roots = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
            used_roots.add(node.value.id)
        elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
            used_roots.add(node.id)

    missing = used_roots - bound
    # Names beginning with "_" or "self"/"cls" are overwhelmingly
    # either this template's own private helpers (always bound, so
    # never actually flagged) or conventions this conservative,
    # whole-file pass isn't a good fit for; excluding them keeps the
    # signal-to-noise ratio high for what this is actually meant to
    # catch — a genuinely missing top-level `import`.
    return sorted(
        name for name in missing
        if name and not name.startswith("_") and name not in {"self", "cls"}
    )


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

    @staticmethod
    def _subprocess_environment(extra=None):
        """Give generated scripts the same import root as the Web process."""
        environment = dict(os.environ)
        existing = environment.get("PYTHONPATH", "")
        roots = [str(APPLICATION_ROOT)]
        if existing:
            roots.append(existing)
        environment["PYTHONPATH"] = os.pathsep.join(roots)
        if extra:
            environment.update(extra)
        return environment

    @staticmethod
    def _safe_startup_diagnostic(stderr_text, script_path):
        """Return a concise redacted subprocess diagnostic for the UI."""
        text = (stderr_text or "").strip()
        redacted = re.sub(
            r"(?i)((?:password|passwd|token|authorization|secret|api[_-]?key)\s*[=:]\s*)([^\s,;]+)",
            r"\1******",
            text,
        )
        lines = [line.strip() for line in redacted.splitlines() if line.strip()]
        final_line = lines[-1] if lines else "The replay subprocess exited before Step 1."
        exception_line = next((
            line for line in reversed(lines)
            if re.match(r"[A-Za-z_][\w.]*(?:Error|Exception):", line)
        ), final_line)
        exception_type = "RuntimeError"
        exception_message = exception_line
        exception_match = re.match(r"([A-Za-z_][\w.]*(?:Error|Exception)):\s*(.*)", exception_line)
        if exception_match:
            exception_type = exception_match.group(1)
            exception_message = exception_match.group(2) or final_line
        line_number = None
        script_name = Path(script_path).name
        for line in reversed(lines):
            match = re.search(rf'File "[^"]*{re.escape(script_name)}", line (\d+)', line)
            if match:
                line_number = int(match.group(1))
                break
        message = f"{exception_type}: {exception_message}"
        if line_number:
            message += f" (generated script line {line_number})"
        return {
            "failure_stage": "Script Initialization",
            "exception_type": exception_type,
            "exception_message": exception_message,
            "script_line": line_number,
            "error": message,
            "final_message": f"FAILED — Script Initialization\n{message}",
        }

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
            env=self._subprocess_environment(),
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
                r"#\s*(DESCRIPTION|FIELD_TYPE|FIELD_NAME|SECTION|ACTION_TYPE|CONTROL_ROLE|CONTROL_CONTEXT|CONTROL_TRANSACTION|TRANSACTION_OPENER|TRANSACTION_POPUP|OWNERSHIP_EVIDENCE|OWNERSHIP_ACCEPTED|PARENT_LOCATOR|POPUP_LOCATOR|EXPECTED_VALUE|EXPECTED_POST_STATE|PRIMARY_LOCATOR|FALLBACK_LOCATORS|INTERACTION_TARGET_SCOPE|INTERACTION_TARGET|XPATH|IS_SENSITIVE|SENSITIVE|SOURCE|CALENDAR_METADATA|POPUP_RETURN):\s*(.*)",
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
            source = ast.get_source_segment(script_text, statement) or ""
            if has_structured_steps and metadata is None:
                # Task-1 Phase 3 (D8, no silent action/assertion loss): a
                # statement WITHOUT QA_STEP metadata inside a structured
                # script is either harness boilerplate (setup/teardown, bare
                # locator construction) -- safe to skip -- or real behaviour
                # the operator added by hand (an expect()/assert, an extra
                # click, a loop ...).  The latter used to be skipped
                # silently, so a script could PASS with its own added
                # assertion never executed.  Decline instead: the generic
                # extractor wraps and runs EVERY non-setup statement.
                if (
                    self._is_executable_recorded_statement(statement, source)
                    or isinstance(statement, ast.Assert)
                    or self._is_unrecognised_unstructured_action(statement, source)
                ):
                    return None
                continue
            if not self._is_executable_recorded_statement(statement, source):
                if (
                    metadata is not None
                    and not any(token in source for token in (
                        ".launch(", ".new_context(", ".new_page(", "context.close(", "browser.close(",
                    ))
                ):
                    # Task-1 (popup/tab support): the recorder tags a popup
                    # `with page.expect_popup() ...:` block and a tab switch
                    # `page = pageN_info.value` with QA_STEP metadata. A tagged
                    # statement is real recorded behaviour -- it runs as one
                    # opaque step instead of being dropped by the flat shape
                    # check below.
                    pass
                else:
                    if (
                        not has_structured_steps
                        and self._is_unrecognised_unstructured_action(statement, source)
                    ):
                        # Task-1 Phase 3 (no silent action loss): an UNSTRUCTURED
                        # (no QA_STEP metadata) recorded script contains a
                        # statement this flat extractor would drop (loop,
                        # with-block, assignment from a popup, if/try, ...).
                        # Decline, so the generic extractor -- which wraps
                        # every non-setup statement -- handles the whole
                        # script instead of silently running a subset.
                        return None
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
    def _is_bare_locator_construction(statement):
        """True ONLY for a statement that merely builds a locator and
        performs no action, e.g. ``page.get_by_role("button")``.

        Defect fixed (Task-1 audit D4): the previous regex
        ``page.(locator|get_by_*)(...)`` also matched complete ACTION
        statements such as ``page.get_by_role("button").click()``, so the
        generic extractor silently dropped real recorded actions -- a
        script could then "PASS" with its clicks never executed."""
        if not (isinstance(statement, ast.Expr) and isinstance(statement.value, ast.Call)):
            return False
        function = statement.value.func
        if not isinstance(function, ast.Attribute):
            return False
        locator_builders = ("locator", "frame_locator", "nth", "filter")
        if not (function.attr in locator_builders or function.attr.startswith("get_by_")):
            return False
        node = function.value
        while isinstance(node, (ast.Attribute, ast.Call)):
            node = node.func if isinstance(node, ast.Call) else node.value
        return isinstance(node, ast.Name)

    @staticmethod
    def _is_unrecognised_unstructured_action(statement, source):
        """True for a statement that is real script behaviour (not
        browser/context setup-teardown, not a bare locator construction)
        yet is not one of the flat extractor's recognised actions."""
        if any(token in source for token in (
            ".launch(", ".new_context(", ".new_page(",
            "context.close(", "browser.close(",
        )) and "\n" not in source.strip():
            return False
        if isinstance(statement, ast.Expr) and isinstance(statement.value, ast.Call):
            function = statement.value.func
            if (
                isinstance(function, ast.Attribute)
                and isinstance(function.value, ast.Name)
                and function.value.id == "page"
                and (function.attr == "locator" or function.attr.startswith("get_by_"))
            ):
                return False
        return True

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
        ).replace("__SELECTION_TIMEOUT_MS__", str(max(5000, min(timeout_ms * 2, 120000)))).replace(
            "__DISCOVERY_TIMEOUT_MS__", str(max(500, min(timeout_ms // 20, 2500)))
        ).replace(
            "__STATE_READINESS_TIMEOUT_MS__", str(max(5000, min(timeout_ms // 3, 15000)))
        ).replace("__TOTAL_STEPS__", str(len(lines))).replace(
            "__HARNESS_VERSION__", _QA_HARNESS_TEMPLATE_SHA256
        ).replace(
            # UNIVERSAL STEP COMPLETION GATE: Replay Speed reuses the
            # SAME configured slow_mo_ms value that already paces
            # Playwright's own slow_mo below -- one setting, two
            # connected effects (per-action slow-motion during a step,
            # plus a single clean pacing pause between a step's PASS and
            # the next step's precondition gate) -- rather than a second,
            # separately-configured value.
            "__REPLAY_SPEED_MS__", str(slow_mo_ms)
        )

        # Section 8 readiness signal: the step AFTER the one about to
        # run is strong evidence that step succeeded (e.g. "Commercial"
        # becoming resolvable is strong evidence the Consignment
        # Category dropdown opened) — see _qa_verify_post_action()'s
        # use of next_metadata inside INTERACTIVE_HARNESS_TEMPLATE.
        # Computed here, at BUILD time in the parent, because only the
        # parent has the full ordered step list; baked into each call
        # as a 4th literal argument so the child needs no extra lookup.
        steps_code = "\n".join(
            f"        _qa_run_step({item['step']}, {item['code']!r}, {item['metadata']!r}, "
            f"{(lines[index + 1]['metadata'] if index + 1 < len(lines) else None)!r})"
            for index, item in enumerate(lines)
        )

        flat_constants = self._extract_script_constants(script_text)

        return (
            harness
            + ((flat_constants + "\n\n") if flat_constants else "")
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

            if source and not self._is_bare_locator_construction(stmt):

                statements.append(source)

        return statements or None

    @staticmethod
    def _is_pure_constant_expression(node, known):
        """True for an expression built ONLY from literals, f-strings, string/number
        concatenation and previously declared constant names -- no calls, attribute
        access, comprehensions or lambdas, so carrying it into the harness can never run
        script logic or touch the environment."""
        if isinstance(node, ast.Constant):
            return True
        if isinstance(node, ast.Name):
            return isinstance(node.ctx, ast.Load) and node.id in known
        if isinstance(node, ast.JoinedStr):
            return all(
                isinstance(v, ast.Constant)
                or (isinstance(v, ast.FormattedValue) and PlaywrightRunner._is_pure_constant_expression(v.value, known))
                for v in node.values
            )
        if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Mod)):
            return (
                PlaywrightRunner._is_pure_constant_expression(node.left, known)
                and PlaywrightRunner._is_pure_constant_expression(node.right, known)
            )
        if isinstance(node, (ast.Tuple, ast.List, ast.Set)):
            return all(PlaywrightRunner._is_pure_constant_expression(e, known) for e in node.elts)
        if isinstance(node, ast.Dict):
            return all(
                k is not None and PlaywrightRunner._is_pure_constant_expression(k, known)
                and PlaywrightRunner._is_pure_constant_expression(v, known)
                for k, v in zip(node.keys, node.values)
            )
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
            return PlaywrightRunner._is_pure_constant_expression(node.operand, known)
        return False

    def _extract_script_constants(self, script_text):
        """Source of every module-level ``NAME = <pure constant expression>`` assignment.

        Imported / hand-written scripts routinely declare ``BASE = "https://..."`` (or a
        credentials-free selector/URL constant) at module level and use it inside run().
        The interactive harness only wraps run()'s body and previously dropped the module
        level entirely, so step 1 failed with ``name 'BASE' is not defined`` and no operator
        locator fix could ever help.  Constants are carried across verbatim, in order; an
        assignment that is anything other than a pure constant expression is NOT carried
        (it still surfaces as an honest NameError instead of being guessed)."""
        try:
            tree = ast.parse(script_text)
        except SyntaxError:
            return ""
        known = set()
        lines = []
        for node in tree.body:
            target = value = None
            if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
                target, value = node.targets[0].id, node.value
            elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.value is not None:
                target, value = node.target.id, node.value
            if target is None or not self._is_pure_constant_expression(value, known):
                continue
            source = ast.get_source_segment(script_text, node)
            if source:
                lines.append(source)
                known.add(target)
        return "\n".join(lines)

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
        ).replace("__SELECTION_TIMEOUT_MS__", str(max(5000, min(timeout_ms * 2, 120000)))).replace(
            "__DISCOVERY_TIMEOUT_MS__", str(max(500, min(timeout_ms // 20, 2500)))
        ).replace(
            "__STATE_READINESS_TIMEOUT_MS__", str(max(5000, min(timeout_ms // 3, 15000)))
        ).replace(
            "__HARNESS_VERSION__", _QA_HARNESS_TEMPLATE_SHA256
        ).replace(
            # Task-1 Phase 3 fix: this generic (Manually Recorded
            # fallback) builder never filled the Replay Speed
            # placeholder that _build_interactive_script() fills, so
            # the child crashed with NameError before Step 1 for any
            # recorded script containing loops / with-blocks. It is the
            # same single slow_mo value used by the speed shim below.
            "__REPLAY_SPEED_MS__", str(slow_mo_ms)
        )

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
        script_constants = self._extract_script_constants(script_text)
        if script_constants:
            imports_block += script_constants + "\n\n"

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
        script_source=None,
        script_id=None,
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

        if self.force_headless:
            # Unattended / server-side evidence run (see
            # TestExecutionManager.execute_playwright): same step-verifying harness,
            # but no window. The operator-driven web session never sets force_headless.
            interactive_script = HEADLESS_SHIM_TEMPLATE + interactive_script

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S%f")

        safe_name = "".join(
            c if c.isalnum() else "_" for c in tc_number
        )

        script_path = (
            OUTPUT_FOLDER / f"{safe_name}_{timestamp}_interactive.py"
        )

        generated_sha256 = hashlib.sha256(interactive_script.encode("utf-8")).hexdigest()

        # --------------------------------------------------------------
        # RUNTIME/HARNESS PRE-FLIGHT (2026-09-25)
        #
        # Root-cause context: a "name 'time' is not defined" failure
        # during a real replay's post-action verification traced back
        # to the PARENT process (started with --no-reload) still
        # holding an OLD in-memory copy of INTERACTIVE_HARNESS_TEMPLATE
        # from before a fix had been applied to this file on disk —
        # editing the .py file is not enough; the long-running server
        # process itself has to actually restart to pick it up, since
        # Python caches an already-imported module for the life of the
        # process. `python -m py_compile Core/playwright_runner.py`
        # only ever proves the file ON DISK is valid; it says nothing
        # about what the RUNNING process currently has loaded, and
        # nothing at all about the *generated* per-run child script,
        # which is built from whatever INTERACTIVE_HARNESS_TEMPLATE the
        # process holds right now. This block is what actually proves
        # (not assumes) that the text about to be written to
        # script_path is syntactically valid AND self-contained BEFORE
        # any browser is launched, and logs the exact identity of both
        # the parent module and the generated file so a stale-process
        # situation like the one above is immediately visible in the
        # server log instead of only surfacing as a deep runtime error
        # on a real device, steps into an unattended replay.
        try:
            compile(interactive_script, str(script_path), "exec")
        except SyntaxError as compile_error:
            self.logger.error(
                "PLAYWRIGHT_RUNTIME_DIAGNOSTIC harness_compile_failed "
                "runner_file=%s python=%s harness_template_sha256=%s "
                "generated_script_sha256=%s script_source=%s script_id=%s "
                "tc_number=%s error=%s",
                __file__, sys.executable, _QA_HARNESS_TEMPLATE_SHA256,
                generated_sha256, script_source, script_id, tc_number, compile_error,
            )
            return {
                "interactive_supported": True,
                "success": False,
                "error": (
                    "Internal error: the generated interactive harness failed to "
                    f"compile before it was run ({compile_error}). No browser was "
                    "launched and no action was performed against the application. "
                    "This is a bug in the harness generator, not your recorded "
                    "script — please report it."
                ),
            }

        dependency_gaps = _qa_static_dependency_gaps(interactive_script)
        if dependency_gaps:
            # Logged, not blocking (see _qa_static_dependency_gaps()'s
            # own docstring for why a hand-rolled static check is
            # deliberately not treated as proof a script CAN'T run) —
            # but this is exactly the log line that would have caught
            # a missing "import time" the moment it was (re)introduced,
            # rather than only on a real device minutes into a replay.
            self.logger.warning(
                "PLAYWRIGHT_RUNTIME_DIAGNOSTIC harness_dependency_gap "
                "runner_file=%s harness_template_sha256=%s generated_script_sha256=%s "
                "script_source=%s script_id=%s tc_number=%s possibly_undefined=%s",
                __file__, _QA_HARNESS_TEMPLATE_SHA256, generated_sha256,
                script_source, script_id, tc_number, dependency_gaps,
            )

        self.logger.info(
            "PLAYWRIGHT_RUNTIME_DIAGNOSTIC replay_starting runner_file=%s "
            "python=%s cwd=%s application_root=%s harness_template_sha256=%s "
            "generated_script=%s script_sha256=%s script_source=%s script_id=%s "
            "tc_number=%s",
            __file__, sys.executable, os.getcwd(), str(APPLICATION_ROOT),
            _QA_HARNESS_TEMPLATE_SHA256, str(script_path), generated_sha256,
            script_source, script_id, tc_number,
        )
        # --- end runtime/harness pre-flight ---

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
        saw_step_event = False
        required_step_ids = list(dict.fromkeys(int(match) for match in re.findall(
            r"_qa_run_step\(\s*(\d+)\s*,", interactive_script
        )))
        passed_step_ids = set()
        last_failure_event = None
        last_repair_event = None
        total_match = re.search(r"^_QA_TOTAL_STEPS\s*=\s*(\d+)", interactive_script, re.MULTILINE)
        total_steps = int(total_match.group(1)) if total_match else 0

        if on_runtime_event:
            on_runtime_event({
                "event": "run_initializing",
                "step": 0,
                "total_steps": total_steps,
                "failure_stage": "Script Initialization",
            })

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
            env=self._subprocess_environment({
                "QA_SELECTION_CANCEL_FILE": str(script_path) + ".selection.cancel"
            }),
            **popen_kwargs,
        )

        self._current_process = process

        try:

            while True:

                line = process.stdout.readline()

                if not line:

                    break

                line = line.rstrip("\n")

                if line.startswith("QA_CHILD_DIAGNOSTIC::"):

                    # Runtime identity proof from the child (see the
                    # comment in INTERACTIVE_HARNESS_TEMPLATE). Logged
                    # immediately, before any step runs, so a mismatch
                    # against the "replay_starting" line this same
                    # method already logged (below) is visible even if
                    # the run crashes before printing anything else.
                    try:
                        child_diag = json.loads(line[len("QA_CHILD_DIAGNOSTIC::"):])
                        self.logger.info(
                            "PLAYWRIGHT_RUNTIME_DIAGNOSTIC child_started harness_version=%s "
                            "python=%s cwd=%s harness_file=%s time_available=%s time_module=%s",
                            child_diag.get("harness_version"), child_diag.get("python"),
                            child_diag.get("cwd"), child_diag.get("harness_file"),
                            child_diag.get("time_available"), child_diag.get("time_module"),
                        )
                    except (TypeError, ValueError):
                        pass
                    stdout_lines.append(line)
                    continue

                if not line.startswith("QA_EVENT::"):

                    stdout_lines.append(line)

                    continue

                try:

                    event = json.loads(line[len("QA_EVENT::"):])

                except (TypeError, ValueError):

                    continue

                event_type = event.get("event")
                if event_type == "step_passed":
                    passed_step_ids.add(event.get("step"))
                if event.get("step") in required_step_ids:
                    event["progress_step"] = required_step_ids.index(event["step"]) + 1
                    event["completed_steps"] = len(passed_step_ids.intersection(required_step_ids))
                    event["total_steps"] = len(required_step_ids)
                if event_type in {"step_started", "step_passed", "step_failed"}:
                    saw_step_event = True
                if event_type in {
                    "step_failed", "step_failure_detected",
                    "post_action_failed", "post_action_uncertain",
                    "post_action_infrastructure_error",
                    "step_gave_up",
                }:
                    last_failure_event = dict(event)
                    if event_type == "post_action_infrastructure_error":
                        self.logger.error(
                            "PLAYWRIGHT_RUNTIME_DIAGNOSTIC post_action_infrastructure_error "
                            "step=%s reason=%s", event.get("step"), event.get("reason"),
                        )
                if event_type in {"element_selected", "fallback_succeeded", "step_repaired"}:
                    last_repair_event = dict(event)

                if event_type in {
                    "step_started", "step_passed", "step_retrying",
                    "step_failure_detected",
                    "locator_resolution", "date_entry_performed",
                    "popup_return_verified", "popup_unattended",
                    "post_action_verified", "post_action_failed",
                    "post_action_uncertain", "post_action_infrastructure_error",
                    "fallback_succeeded", "action_adapted",
                    "selection_started", "selection_waiting",
                    "selection_cancelled", "selection_failed",
                    "selection_rejected", "stale_decision_rejected",
                    "auto_repair_attempted", "parent_reopen_succeeded",
                    "parent_reopen_failed", "parent_retry_failed",
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
                    selection = event.get("selection") or {}
                    repair = {
                        "step": event.get("step"),
                        "repair_generation": event.get("repair_generation"),
                        "original": event.get("original_code"),
                        "corrected": event.get("corrected_code"),
                        "selection": selection or {
                            "selected_candidate": event.get("selected_candidate") or {},
                            "candidates": event.get("candidates") or [],
                            "xpath": event.get("xpath"),
                            "section": event.get("section"),
                            "accessible_name": event.get("field_name"),
                        },
                    }

                    repairs.append(repair)

                    if on_repaired:
                        persistence_result = on_repaired(repair)
                        persisted = persistence_result is not False
                    else:
                        persisted = True
                    process.stdin.write(json.dumps({
                        "action": "repair_persisted" if persisted else "repair_persistence_failed",
                        "step": event.get("step"),
                        "repair_generation": event.get("repair_generation"),
                    }) + "\n")
                    process.stdin.flush()

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

        success = ((return_code == 0) and not cancelled
                   and bool(required_step_ids)
                   and passed_step_ids.issuperset(required_step_ids))

        startup_diagnostic = None
        if not success and not cancelled and not saw_step_event:
            startup_diagnostic = self._safe_startup_diagnostic(
                stderr_text, script_path
            )
            self.logger.error(
                "Interactive replay failed before Step 1. Full subprocess stderr:\n%s",
                stderr_text or "(empty stderr)",
            )

        self.logger.info(
            f"Interactive Playwright run finished in {duration:.1f}s "
            f"— {'PASS' if success else ('CANCELLED' if cancelled else 'FAIL')} "
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
        if startup_diagnostic:
            result.update(startup_diagnostic)
        elif not success and not cancelled and last_failure_event:
            failed_step = int(last_failure_event.get("step") or 0)
            failure_event = str(last_failure_event.get("event") or "step_failure").upper()
            reason = str(
                last_failure_event.get("error")
                or last_failure_event.get("reason")
                or "Step execution failed."
            )
            details = last_failure_event.get("details") or {}
            field_name = str(details.get("field_name") or "").strip()
            original_locator = str(details.get("primary_locator") or details.get("locator") or "").strip()
            repair_locator = ""
            repair_source = ""
            if last_repair_event:
                selected = last_repair_event.get("selected_candidate") or {}
                repair_locator = str(
                    last_repair_event.get("locator")
                    or selected.get("expression")
                    or selected.get("locator")
                    or (last_repair_event.get("details") or {}).get("locator")
                    or ""
                ).strip()
                repair_source = (
                    "manual selection" if last_repair_event.get("event") == "element_selected"
                    else "saved fallback" if last_repair_event.get("event") == "fallback_succeeded"
                    else "automatic repair"
                )
            repair_summary = ""
            if repair_locator:
                repair_summary = (
                    f"\nOriginal locator: {original_locator or 'Unknown'}"
                    f"\nRepair locator: {repair_locator}"
                    f"\nRepair source: {repair_source}"
                )
            result.update({
                "failure_stage": "Step Execution",
                "failure_type": failure_event,
                "failure_reason": reason,
                "failed_step": failed_step,
                "total_steps": total_steps,
                "failure_details": details,
                "original_locator": original_locator,
                "repair_locator": repair_locator,
                "repair_source": repair_source,
                "final_message": (
                    f"Failed Step: {failed_step} / {total_steps}\n"
                    f"Field: {field_name or 'Unknown'}\n"
                    f"Failure Type: {failure_event}\n"
                    f"Reason: {reason}{repair_summary}"
                ),
            })
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
