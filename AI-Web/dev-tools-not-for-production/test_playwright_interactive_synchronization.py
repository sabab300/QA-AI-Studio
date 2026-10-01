"""
Focused regression checks for the interactive-replay synchronization
work done on 2026-09-25 — the "name 'time' is not defined" root-cause
fix and the surrounding runtime/harness identity, dynamic-wait,
post-action-verification-error-separation, and side-effect-safety
hardening that came with it.

Matches the plain-script/run_checks() style of the existing
test_playwright_replay_startup_diagnostics.py / test_playwright_stable_assets.py
in this same folder, rather than introducing a new test framework
dependency.

Requires: pip install playwright && playwright install chromium
(same as the rest of dev-tools-not-for-production/).
"""

import json
import os
import sys
import threading
import time
from pathlib import Path

WEB_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = WEB_ROOT.parent
sys.path.insert(0, str(PROJECT_ROOT / "AI" / "venv" / "Lib" / "site-packages"))
sys.path.insert(0, str(WEB_ROOT))

from Core.playwright_runner import PlaywrightRunner, _qa_static_dependency_gaps


def _load_real_assess_playwright_script_quality():
    """
    Return the ACTUAL, current TestExecutionManager.assess_playwright_
    script_quality static method, extracted verbatim (via ast, not
    retyped) from the real Core/test_execution_manager.py on disk,
    executed against the real Core.playwright_semantics helper it
    calls — without going through `from Core.test_execution_manager
    import TestExecutionManager`.

    That plain import transitively pulls in the entire unrelated
    Knowledge Hub / RAG stack (Core.automation_generator ->
    Core.rag_engine -> ... -> Core.vector_store -> chromadb ->
    Core.embedding_engine -> sentence_transformers), none of which
    assess_playwright_script_quality (a @staticmethod using only `re`
    and Core.playwright_semantics.semantic_name_compatible) has any
    dependency on. Installing a multi-hundred-MB, out-of-scope ML/
    vector-DB stack purely to reach one unrelated static method would
    itself violate this task's own "don't touch unrelated modules"
    constraint far more than this extraction does. This still runs
    the real, current production source text for the method under
    test — it is not a reimplementation.
    """
    import ast
    import textwrap

    source_path = WEB_ROOT / "Core" / "test_execution_manager.py"
    source_text = source_path.read_text(encoding="utf-8")
    tree = ast.parse(source_text)
    method_node = None
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "assess_playwright_script_quality":
            method_node = node
            break
    assert method_node is not None, "assess_playwright_script_quality not found in test_execution_manager.py"
    lines = source_text.splitlines()[method_node.lineno - 1:method_node.end_lineno]
    method_source = textwrap.dedent("\n".join(lines))

    from Core.playwright_semantics import semantic_name_compatible

    namespace = {"re": __import__("re"), "semantic_name_compatible": semantic_name_compatible}
    exec(compile(method_source, str(source_path), "exec"), namespace)
    return namespace["assess_playwright_script_quality"]


AI_GENERATED_SHAPE_SCRIPT = '''
def run(playwright):
    browser = playwright.chromium.launch(headless=False)
    page = browser.new_page()

    # QA_STEP: 9 | Click Single Declarations
    # FIELD_TYPE: link
    # ACTION_TYPE: click
    # PRIMARY_LOCATOR: role=link|name=Single Declarations
    page.get_by_role("link", name="Single Declarations", exact=True).click()

    # QA_STEP: 10 | Open Consignment Category dropdown
    # FIELD_TYPE: dropdown
    # ACTION_TYPE: open_dropdown
    # CONTROL_ROLE: combobox
    # PRIMARY_LOCATOR: role=combobox|name=Consignment Category
    # POPUP_LOCATOR: role=listbox|name=Consignment Category options
    page.get_by_role("combobox", name="Consignment Category").click()

    # QA_STEP: 11 | Select Commercial
    # FIELD_TYPE: option
    # ACTION_TYPE: select_option
    # PRIMARY_LOCATOR: role=option|name=Commercial
    # PARENT_LOCATOR: role=combobox|name=Consignment Category
    page.get_by_role("option", name="Commercial", exact=True).click()

    context.close()
    browser.close()

with sync_playwright() as playwright:
    run(playwright)
'''


# --------------------------------------------------------------------
# TEST A: generated interactive harness has all runtime dependencies.
# --------------------------------------------------------------------
def test_a_harness_dependencies_complete(runner):
    harness = runner._build_interactive_script(
        AI_GENERATED_SHAPE_SCRIPT, slow_mo_ms=0, timeout_ms=5000, max_repair_rounds=1,
    )
    assert harness, "AI-generated-shape extractor did not match the fixture script"

    # compile() alone only proves syntax; the historical bug this test
    # suite exists for (a missing "import time") was syntactically
    # valid and passed py_compile every time — it only ever failed at
    # runtime, inside a specific branch. That is exactly why this test
    # ALSO runs _qa_static_dependency_gaps(), the new whole-file
    # binding-vs-usage safety net, and asserts it is empty.
    compile(harness, "test_a_generated_harness.py", "exec")
    gaps = _qa_static_dependency_gaps(harness)
    assert gaps == [], f"generated harness references undefined names: {gaps}"

    assert "import time" in harness
    assert harness.index("import time") < harness.index("time.monotonic(")
    assert "__HARNESS_VERSION__" not in harness, "build-time placeholder was not substituted"
    print("TEST A: PASS — generated harness compiles and has zero undefined top-level names")


# --------------------------------------------------------------------
# TEST B / C: dropdown opener -> popup/next-option readiness, and the
# post-action verification path itself never raises an unhandled
# NameError for a real dropdown/combobox check (live DOM + live
# timing, not a mock — proves the ACTUAL generated code, executed for
# real, does not reproduce the historical regression).
# --------------------------------------------------------------------
def test_b_c_dropdown_next_step_readiness(runner):
    from playwright.sync_api import sync_playwright

    harness = runner._build_interactive_script(
        AI_GENERATED_SHAPE_SCRIPT, slow_mo_ms=0, timeout_ms=5000, max_repair_rounds=1,
    )
    definitions = harness.split("with sync_playwright() as playwright:", 1)[0]
    namespace = {"__name__": "sync_fixture", "__file__": "test_b_c_fixture.py"}
    exec(compile(definitions, "test_b_c_fixture.py", "exec"), namespace)

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context()
        page = context.new_page()
        namespace.update({"browser": browser, "context": context, "page": page})

        # The owning combobox deliberately gives NO aria-expanded/
        # aria-owns evidence at all (as a real Kendo/React control
        # sometimes doesn't), and the injected element intentionally
        # uses role="menuitem" rather than role="option" — the latter
        # is ALSO one of the roles the pre-existing generic
        # popup-surface scan already looks for
        # ('[role="listbox"],[role="option"],[role="grid"],
        # [role="dialog"],...'), so it would satisfy that older check
        # first and never actually exercise the NEW code path this
        # test targets. role="menuitem" satisfies neither the
        # aria-expanded/aria-owns checks nor the generic popup-surface
        # selector, so the ONLY way this step can be verified is the
        # Section 8 next-step-readiness signal: the "Commercial"
        # control appearing in the DOM, injected here after a short
        # delay to imitate a real async render tick.
        page.set_content('''
            <div role="combobox" aria-label="Consignment Category">Choose</div>
        ''')
        page.evaluate('''() => {
            setTimeout(() => {
                const opt = document.createElement('div');
                opt.setAttribute('role', 'menuitem');
                opt.setAttribute('aria-label', 'Commercial');
                opt.textContent = 'Commercial';
                document.body.appendChild(opt);
            }, 400);
        }''')

        metadata = {
            "field_type": "dropdown", "action_type": "open_dropdown",
            "control_role": "combobox",
            "primary_locator": "role=combobox|name=Consignment Category",
            "popup_locator": "role=listbox|name=Consignment Category options",
        }
        next_metadata = {"step": 11, "primary_locator": "role=menuitem|name=Commercial"}
        code_text = 'page.get_by_role("combobox", name="Consignment Category").click()'

        started = time.perf_counter()
        result = namespace["_qa_verify_post_action"](code_text, metadata, next_metadata)
        elapsed = time.perf_counter() - started

        assert result["verified"] is True, result
        assert "next recorded step" in result["reason"]
        assert elapsed < 3.0, "should verify as soon as the next step's control appears, not wait out the full 3s"
        assert result.get("infrastructure_error") is not True

        event_name = namespace["_qa_post_action_event_name"](result)
        assert event_name == "post_action_verified"

        context.close()
        browser.close()
    print(f"TEST B/C: PASS — verified via next-step readiness in {elapsed:.2f}s, "
          "no NameError, correct event name")


# --------------------------------------------------------------------
# TEST D / E: a next-step locator that is initially absent and appears
# shortly afterward → replay waits and succeeds without repair.
# Section 8's core promise, isolated from Test B/C's dropdown-specific
# framing so it is exercised on its own bounded-wait terms.
# --------------------------------------------------------------------
def test_d_e_delayed_next_step_waits_and_succeeds(runner):
    from playwright.sync_api import sync_playwright

    harness = runner._build_interactive_script(
        AI_GENERATED_SHAPE_SCRIPT, slow_mo_ms=0, timeout_ms=5000, max_repair_rounds=1,
    )
    definitions = harness.split("with sync_playwright() as playwright:", 1)[0]
    namespace = {"__name__": "sync_fixture2", "__file__": "test_d_e_fixture.py"}
    exec(compile(definitions, "test_d_e_fixture.py", "exec"), namespace)

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context()
        page = context.new_page()
        namespace.update({"browser": browser, "context": context, "page": page})

        page.set_content('<div role="combobox" aria-label="Consignment Category">Choose</div>')
        # Appears LATE (1.2s in) but still inside the 3s bounded poll —
        # proves the wait is condition-based (it resolves the moment
        # the element appears) rather than a fixed short-circuit.
        page.evaluate('''() => {
            setTimeout(() => {
                const opt = document.createElement('div');
                opt.setAttribute('role', 'option');
                opt.setAttribute('aria-label', 'Commercial');
                document.body.appendChild(opt);
            }, 1200);
        }''')
        metadata = {"field_type": "dropdown", "action_type": "open_dropdown", "control_role": "combobox",
                     "primary_locator": "role=combobox|name=Consignment Category"}
        next_metadata = {"step": 11, "primary_locator": "role=option|name=Commercial"}
        started = time.perf_counter()
        result = namespace["_qa_verify_post_action"](
            'page.get_by_role("combobox", name="Consignment Category").click()',
            metadata, next_metadata,
        )
        elapsed = time.perf_counter() - started
        assert result["verified"] is True
        assert 1.0 < elapsed < 3.0, f"expected to wait roughly until the 1.2s mark, took {elapsed:.2f}s"
        context.close()
        browser.close()
    print(f"TEST D/E: PASS — waited {elapsed:.2f}s for a late-appearing next step, then succeeded, no repair")


# --------------------------------------------------------------------
# TEST F: next-step genuinely absent until timeout → bounded failure,
# not an infinite loop, and NOT misreported as an infrastructure error.
# --------------------------------------------------------------------
def test_f_absent_next_step_bounded_timeout(runner):
    from playwright.sync_api import sync_playwright

    harness = runner._build_interactive_script(
        AI_GENERATED_SHAPE_SCRIPT, slow_mo_ms=0, timeout_ms=5000, max_repair_rounds=1,
    )
    definitions = harness.split("with sync_playwright() as playwright:", 1)[0]
    namespace = {"__name__": "sync_fixture3", "__file__": "test_f_fixture.py"}
    exec(compile(definitions, "test_f_fixture.py", "exec"), namespace)

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context()
        page = context.new_page()
        namespace.update({"browser": browser, "context": context, "page": page})
        page.set_content('<div role="combobox" aria-label="Consignment Category">Choose</div>')
        # "Commercial" never appears at all.
        metadata = {"field_type": "dropdown", "action_type": "open_dropdown", "control_role": "combobox",
                     "primary_locator": "role=combobox|name=Consignment Category"}
        next_metadata = {"step": 11, "primary_locator": "role=option|name=Commercial"}
        started = time.perf_counter()
        result = namespace["_qa_verify_post_action"](
            'page.get_by_role("combobox", name="Consignment Category").click()',
            metadata, next_metadata,
        )
        elapsed = time.perf_counter() - started
        assert result["verified"] is False
        assert result.get("infrastructure_error") is not True
        assert 2.5 < elapsed < 6.0, f"expected a bounded ~3s wait, took {elapsed:.2f}s"
        context.close()
        browser.close()
    print(f"TEST F: PASS — bounded failure after {elapsed:.2f}s, correctly NOT tagged infrastructure_error")


# --------------------------------------------------------------------
# TEST C2 (infrastructure-error separation): re-using the mocked
# approach here is deliberate and necessary — a genuine
# NameError/AttributeError bug inside the verifier's OWN code cannot
# be reliably reproduced by manipulating a real DOM; it has to be
# injected directly.
# --------------------------------------------------------------------
def test_infrastructure_error_is_separated_from_app_failure(runner):
    harness = runner._build_interactive_script(
        AI_GENERATED_SHAPE_SCRIPT, slow_mo_ms=0, timeout_ms=5000, max_repair_rounds=1,
    )
    definitions = harness.split("with sync_playwright() as playwright:", 1)[0]
    namespace = {"__name__": "infra_fixture", "__file__": "test_infra_fixture.py"}
    exec(compile(definitions, "test_infra_fixture.py", "exec"), namespace)

    class ExplodingPage:
        def get_by_role(self, *a, **k):
            raise NameError("name 'time' is not defined")  # the exact historical failure

        def is_closed(self):
            # _qa_active_page() probes every globals() candidate named
            # "page"/"context"/"browser" and silently skips any whose
            # is_closed() call raises, in order to find the newest
            # still-open page. A mock lacking this method is excluded
            # before get_by_role() is ever reached, which would
            # misreport as "No active Playwright page is available"
            # (infrastructure_error=False) instead of exercising the
            # NameError this test exists to prove is tagged correctly.
            return False

    namespace["page"] = ExplodingPage()
    namespace["context"] = None
    namespace["browser"] = None
    metadata = {"field_type": "dropdown", "action_type": "open_dropdown", "control_role": "combobox",
                 "primary_locator": "role=combobox|name=Consignment Category"}
    result = namespace["_qa_verify_post_action"](
        'page.get_by_role("combobox", name="Consignment Category").click()', metadata, None,
    )
    assert result["verified"] is False
    assert result["infrastructure_error"] is True
    assert "bug in the verifier itself" in result["reason"]
    assert namespace["_qa_post_action_event_name"](result) == "post_action_infrastructure_error"
    print("TEST C2: PASS — a real NameError inside the verifier is tagged infrastructure_error, "
          "not conflated with an application-state mismatch")


# --------------------------------------------------------------------
# TEST G: an uncertain/failed post-action verification must NEVER
# cause the side-effecting action to be executed a second time.
# --------------------------------------------------------------------
def test_g_no_duplicate_side_effect_on_uncertain_verification(runner):
    from playwright.sync_api import sync_playwright

    script = '''
def run(playwright):
    browser = playwright.chromium.launch(headless=False)
    page = browser.new_page()
    # QA_STEP: 1 | Click the counter button
    # FIELD_TYPE: dropdown
    # ACTION_TYPE: open_dropdown
    # CONTROL_ROLE: combobox
    # PRIMARY_LOCATOR: role=button|name=Counter
    page.get_by_role("button", name="Counter").click()
    browser.close()

with sync_playwright() as playwright:
    run(playwright)
'''
    harness = runner._build_interactive_script(script, slow_mo_ms=0, timeout_ms=5000, max_repair_rounds=1)
    definitions = harness.split("with sync_playwright() as playwright:", 1)[0]
    namespace = {"__name__": "no_dup_fixture", "__file__": "test_g_fixture.py"}
    exec(compile(definitions, "test_g_fixture.py", "exec"), namespace)

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context()
        page = context.new_page()
        namespace.update({"browser": browser, "context": context, "page": page})

        # A button whose click increments a counter — verification is
        # deliberately impossible to satisfy (no matching popup/next
        # step ever appears), so _qa_run_step() must give up without
        # ever clicking a second time.
        page.set_content('''
            <button role="button" aria-label="Counter" onclick="window.__clicks=(window.__clicks||0)+1">Go</button>
        ''')

        events = []
        namespace["_qa_send_event"] = lambda event: events.append(event)

        try:
            namespace["_qa_run_step"](1, 'page.get_by_role("button", name="Counter").click()',
                                       {"field_type": "dropdown", "action_type": "open_dropdown",
                                        "control_role": "combobox",
                                        "primary_locator": "role=button|name=Counter"})
        except namespace["_QaPostActionVerificationError"]:
            pass  # expected — verification never resolves for this fixture

        click_count = page.evaluate("() => window.__clicks || 0")
        assert click_count == 1, f"the action must run exactly once; ran {click_count} times"
        event_types = [e.get("event") for e in events]
        assert "post_action_uncertain" in event_types
        assert event_types.count("step_started") == 1, "a side-effecting step must not be retried after an uncertain verification"

        context.close()
        browser.close()
    print("TEST G: PASS — action executed exactly once despite verification never resolving")


# --------------------------------------------------------------------
# TEST H: Stop Replay during a dynamic wait → clean cancellation.
# --------------------------------------------------------------------
def test_h_stop_replay_during_dynamic_wait():
    runner = PlaywrightRunner()

    # A script whose ONLY step targets a locator that will never
    # resolve, forcing _qa_resolve_action()'s bounded readiness poll
    # to run for a while — the dynamic wait this test cancels mid-way.
    script = '''
def run(playwright):
    browser = playwright.chromium.launch(headless=True)
    page = browser.new_page()
    page.goto("data:text/html,<button>Ready</button>")
    # QA_STEP: 1 | Click something that is never there
    page.locator("#does-not-exist-ever").click()
    browser.close()

with sync_playwright() as playwright:
    run(playwright)
'''
    started_event = threading.Event()

    def on_runtime_event(event):
        if event.get("event") == "step_started":
            started_event.set()

    result_holder = {}

    def run_it():
        result_holder["result"] = runner.run_script_interactive(
            script, tc_number="cancel_test",
            on_step_failed=lambda event: {"action": "cancel"},
            on_runtime_event=on_runtime_event,
            max_repair_rounds=1,
        )

    thread = threading.Thread(target=run_it, daemon=True)
    started = time.perf_counter()
    thread.start()

    assert started_event.wait(timeout=15), "step never started — cannot test mid-run cancellation"
    runner.cancel_current_run()
    thread.join(timeout=15)
    elapsed = time.perf_counter() - started

    assert not thread.is_alive(), "run_script_interactive() did not return after cancel_current_run()"
    result = result_holder.get("result") or {}
    assert elapsed < 15, f"cancellation took too long ({elapsed:.1f}s) — not clean"
    print(f"TEST H: PASS — cancelled mid-run and returned in {elapsed:.1f}s, "
          f"cancelled={result.get('cancelled')}, success={result.get('success')}")


# --------------------------------------------------------------------
# TEST J: a locator the save validator accepts (Stable/Acceptable, not
# Invalid) is genuinely executable by replay — closing the Recorder <->
# Replay locator contract loop for the semantic-role-based locator
# style the recorder is meant to prefer.
# --------------------------------------------------------------------
def test_j_recorder_accepted_locator_is_replay_executable():
    from playwright.sync_api import sync_playwright

    script_text = (
        AI_GENERATED_SHAPE_SCRIPT
        .replace("page.get_by_role(\"link\", name=\"Single Declarations\", exact=True).click()\n\n", "")
    )
    assess_playwright_script_quality = _load_real_assess_playwright_script_quality()
    quality = assess_playwright_script_quality(script_text)
    by_step = {item["step"]: item for item in quality}
    assert by_step[10]["classification"] != "Invalid", by_step[10]
    assert by_step[11]["classification"] != "Invalid", by_step[11]
    assert by_step[10]["semantic_valid"] is True
    assert by_step[11]["semantic_valid"] is True

    runner = PlaywrightRunner()
    harness = runner._build_interactive_script(script_text, slow_mo_ms=0, timeout_ms=5000, max_repair_rounds=1)
    definitions = harness.split("with sync_playwright() as playwright:", 1)[0]
    namespace = {"__name__": "recorder_contract_fixture", "__file__": "test_j_fixture.py"}
    exec(compile(definitions, "test_j_fixture.py", "exec"), namespace)

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context()
        page = context.new_page()
        namespace.update({"browser": browser, "context": context, "page": page})
        page.set_content('<div role="combobox" aria-label="Consignment Category">Choose</div>')
        expression = namespace["_qa_locator_expression_from_code"](
            'page.get_by_role("combobox", name="Consignment Category").click()'
        )
        located = eval(expression, {**namespace, "page": page})
        assert located.count() == 1
        context.close()
        browser.close()
    print("TEST J: PASS — the exact same locator the save validator accepted resolves for real under replay")


def run_checks():
    runner = PlaywrightRunner()
    test_a_harness_dependencies_complete(runner)
    test_b_c_dropdown_next_step_readiness(runner)
    test_d_e_delayed_next_step_waits_and_succeeds(runner)
    test_f_absent_next_step_bounded_timeout(runner)
    test_infrastructure_error_is_separated_from_app_failure(runner)
    test_g_no_duplicate_side_effect_on_uncertain_verification(runner)
    test_h_stop_replay_during_dynamic_wait()
    test_j_recorder_accepted_locator_is_replay_executable()


if __name__ == "__main__":
    run_checks()
    print("\nPlaywright interactive synchronization regression suite: PASS")
