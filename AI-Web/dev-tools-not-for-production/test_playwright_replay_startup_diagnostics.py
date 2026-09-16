"""Focused regression checks for interactive replay startup diagnostics."""

import os
import sys
import time
from pathlib import Path

WEB_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = WEB_ROOT.parent
sys.path.insert(0, str(PROJECT_ROOT / "AI" / "venv" / "Lib" / "site-packages"))
sys.path.insert(0, str(WEB_ROOT))

from Core.playwright_runner import PlaywrightRunner
from Core.test_execution_manager import TestExecutionManager


def build_harness(runner, timeout_ms=1000):
    script = '''from playwright.sync_api import sync_playwright

def run(playwright):
    browser = playwright.chromium.launch(headless=True)
    page = browser.new_page()
    # QA_STEP: 1 | Open fixture
    page.goto("data:text/html,<button>Ready</button>")
    browser.close()

with sync_playwright() as playwright:
    run(playwright)
'''
    return runner._build_generic_interactive_script(
        script, slow_mo_ms=0, timeout_ms=timeout_ms, max_repair_rounds=1
    )


def run_live_resolver_checks(harness):
    """Exercise resolver ordering and validation against a real browser DOM."""
    from playwright.sync_api import sync_playwright

    namespace = {"__name__": "resolver_fixture"}
    definitions = harness.split("with sync_playwright() as playwright:", 1)[0]
    exec(definitions, namespace)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context()
        page = context.new_page()
        namespace.update({"browser": browser, "context": context, "page": page})

        page.set_content('''
            <label for="category">Consignment Category</label>
            <button id="category" role="combobox" aria-label="Consignment Category"
                    onclick="this.setAttribute('aria-expanded', 'true')">Choose</button>
        ''')
        metadata = {
            "field_name": "Consignment Category",
            "field_type": "dropdown",
            "control_role": "combobox",
            "action_type": "open_dropdown",
            "primary_locator": "#consignmentCategory",
            "fallback_locators": (
                "role=combobox|name=Consignment Category || "
                "label=Consignment Category || placeholder=Consignment Category"
            ),
            "xpath": "//*[@id='consignmentCategory']",
        }
        original = "page.locator('#consignmentCategory').click()"
        started = time.perf_counter()
        resolved, selected, attempts = namespace["_qa_resolve_action"](original, metadata)
        assert time.perf_counter() - started < 5
        assert resolved == "page.get_by_role('combobox', name='Consignment Category', exact=True).click()"
        assert selected["source"] == "exact_semantic"
        assert [item["source"] for item in attempts] == ["primary", "exact_semantic"]
        candidates = namespace["_qa_resolution_candidates"](original, metadata)
        identities = [item["identity"] for item in candidates]
        assert identities.count("id:consignmentCategory") == 1

        page.set_content('''
            <label for="port">Port Qasim (exports), karachi</label>
            <input id="port" aria-label="Port Qasim (exports), karachi">
        ''')
        label_metadata = {
            "field_name": "Port Qasim (exports), karachi",
            "field_type": "autocomplete",
            "action_type": "click",
            "primary_locator": "#missing-port",
            "fallback_locators": (
                "role=option|name=Port Qasim (exports), karachi || "
                "label=Port Qasim (exports), karachi"
            ),
            "xpath": "//*[@id='also-missing']",
        }
        resolved, selected, attempts = namespace["_qa_resolve_action"](
            "page.locator('#missing-port').click()", label_metadata
        )
        assert selected["source"] == "fallback"
        assert "get_by_label" in resolved
        assert len(attempts) == 3

        page.set_content("<button>Unrelated</button>")
        started = time.perf_counter()
        resolved, selected, attempts = namespace["_qa_resolve_action"](original, metadata)
        assert time.perf_counter() - started < 5
        assert resolved is None and selected is None
        assert attempts and all(not item["semantic_compatible"] for item in attempts)
        context.close()
        browser.close()


def run_checks():
    runner = PlaywrightRunner()
    child_path = runner._subprocess_environment().get("PYTHONPATH", "").split(os.pathsep)
    assert str(WEB_ROOT) in child_path

    diagnostic = runner._safe_startup_diagnostic(
        'Traceback\n  File "sample_interactive.py", line 8, in <module>\n'
        'ModuleNotFoundError: No module named \'Core\'\n'
        'token=do-not-display',
        WEB_ROOT / "sample_interactive.py",
    )
    assert diagnostic["failure_stage"] == "Script Initialization"
    assert diagnostic["exception_type"] == "ModuleNotFoundError"
    assert diagnostic["script_line"] == 8
    assert "do-not-display" not in diagnostic["error"]

    manual = "print('manual')"
    automatic = "print('automatic')"
    assert TestExecutionManager.get_active_script({
        "active_script_source": "MANUAL",
        "recorded_script": manual,
        "automation_script": automatic,
    }) == manual

    harness = build_harness(runner)
    assert harness and "_QA_TOTAL_STEPS = 1" in harness
    compile(harness, "startup_diagnostic_fixture.py", "exec")
    assert "_QaPostActionVerificationError" in harness
    assert '"event": "post_action_uncertain"' in harness
    assert "Action completed; no deterministic post-condition was required." in harness
    assert "complete_semantic_name and normalized_actual == normalized_expected" in harness
    assert "Selected element is the preceding dynamic-control opener" in harness
    assert harness.index("primary = _qa_locator_code") < harness.index("raw_fallbacks =")
    assert harness.index("raw_fallbacks =") < harness.index('xpath = ((metadata or {}).get("xpath")')
    assert harness.index("for fallback_code in fallback_codes") < harness.index("if _qa_try_parent_reopen(metadata)")
    assert ", exact=True)" in harness
    assert "def _qa_resolution_candidates" in harness
    assert "def _qa_resolve_action" in harness
    assert "_QA_DISCOVERY_TIMEOUT_MS = 500" in harness
    assert harness.index('(\"primary\", code_text)') < harness.index('(\"exact_semantic\"')
    assert harness.index('(\"exact_semantic\"') < harness.index("persisted = _qa_persisted_candidate_codes")
    assert "raise _QaLocatorResolutionError(resolution_attempts)" in harness
    assert 'expression != current_code.strip()' in harness
    assert "last_failure_event" not in harness  # parent runner owns terminal diagnostics

    runner_source = (WEB_ROOT / "Core" / "playwright_runner.py").read_text(encoding="utf-8")
    assert '"post_action_uncertain"' in runner_source
    assert '"failure_type": failure_event' in runner_source
    assert '"original_locator": original_locator' in runner_source
    assert '"repair_source": repair_source' in runner_source
    run_live_resolver_checks(harness)


if __name__ == "__main__":
    run_checks()
    print("Playwright replay startup diagnostics: PASS")
