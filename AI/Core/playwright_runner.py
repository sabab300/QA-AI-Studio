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


# BUGFIX (ported from the Web port — see
# AI-Web/Core/playwright_runner.py's matching comment): anchored to
# this file's own location instead of the process's current working
# directory (see git_config_manager.py's matching comment in this
# same folder for why a bare relative path here is unsafe).
OUTPUT_FOLDER = Path(__file__).resolve().parent.parent / "Output" / "AutomationRuns"

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


# BUGFIX (ported from the Web port after being caught there in
# real runtime testing — see AI-Web/Core/playwright_runner.py's
# matching comment): every script this app generates or records
# calls browser.new_page() — a distinct method on Browser itself
# (it creates an implicit context AND the page in one call), NOT
# BrowserContext.new_page() (only used for a second+ page inside a
# context you created yourself). Patching only
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


def _qa_capture_failure_matches(raw_locator):
    if not raw_locator:
        return []
    try:
        loc = page.locator(raw_locator)
        return loc.evaluate_all("""els => els.slice(0, 12).map((el, index) => {
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
        "url": "", "title": "", "accessibility": [], "failure_matches": []
    }
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


def _qa_handle_step_failure(step_number, code_text, error_text, attempt):
    locator, value = _qa_extract_locator_and_value(code_text)
    context = _qa_capture_context(locator)
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
        "failure_matches": context["failure_matches"],
    })
    command = _qa_read_command()
    action = command.get("action")
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
                raise
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

    def __init__(self):

        self.logger = Logger.get_logger()

        self.environment_config = TestEnvironmentConfig()

        # Set only while an interactive run (see
        # run_script_interactive()) is in progress, so
        # cancel_current_run() — called from the UI thread when the
        # operator hits Cancel — has a live process to terminate.
        self._current_process = None

        OUTPUT_FOLDER.mkdir(parents=True, exist_ok=True)

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

    def run_script(self, script_text, tc_number="script", timeout_seconds=None):

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

        script_with_shim = SPEED_SHIM_TEMPLATE.format(
            slow_mo=slow_mo_ms, timeout=timeout_ms
        ) + script_text

        script_path.write_text(script_with_shim, encoding="utf-8")

        self.logger.info(
            f"Running Playwright script: {script_path} "
            f"(slow_mo={slow_mo_ms}ms, timeout={timeout_ms}ms)"
        )

        start = datetime.now()

        try:

            process = subprocess.run(
                [sys.executable, str(script_path)],
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
            )

            duration = (datetime.now() - start).total_seconds()

            passed = process.returncode == 0

            self.logger.info(
                f"Playwright script finished in {duration:.1f}s — "
                f"{'PASS' if passed else 'FAIL'} (exit code "
                f"{process.returncode})"
            )

            return {
                "success": passed,
                "stdout": process.stdout,
                "stderr": process.stderr,
                "return_code": process.returncode,
                "duration": duration,
                "script_path": str(script_path),
                "slow_mo_ms": slow_mo_ms,
                "timeout_ms": timeout_ms,
            }

        except subprocess.TimeoutExpired as ex:

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
                "stdout": ex.stdout or "",
                "stderr": ex.stderr or "",
                "duration": duration,
                "script_path": str(script_path),
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

        return (
            shim
            + harness
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
        max_repair_rounds=3,
    ):
        """
        Interactive counterpart to run_script() — for a single,
        operator-watched Execute only (never a multi-test batch run,
        where nobody would be there to answer a prompt; the caller
        is responsible for only using this when exactly one test
        case is being run).

        Whenever a step fails, calls on_step_failed(event_dict)
        SYNCHRONOUSLY and uses whatever decision dict it returns —
        the caller (a background QThread; see
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
        interactive run is in progress on the background thread —
        Popen.terminate() is safe to call from another thread.
        """

        process = self._current_process

        if process and process.poll() is None:

            try:

                process.terminate()

            except Exception:

                pass