"""Regression fixture for recorder positional/layout and popup-ownership failures."""

import json
import sys
import time
from pathlib import Path
from urllib.parse import quote

WEB_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = WEB_ROOT.parent
sys.path.insert(0, str(PROJECT_ROOT / "AI" / "venv" / "Lib" / "site-packages"))
sys.path.insert(0, str(WEB_ROOT))

from playwright.sync_api import sync_playwright

from Core.test_execution_manager import TestExecutionManager
from Core.web_recorder import RECORDER_JS, WebRecordingSession


def run_fixture():
    frontend_source = (WEB_ROOT / "Frontend" / "index.html").read_text(encoding="utf-8")
    router_source = (WEB_ROOT / "Web" / "routers" / "recorder_router.py").read_text(encoding="utf-8")
    assert "socket._qaTerminalReceived = true" in frontend_source
    assert "socket._qaExpectedClose || socket._qaTerminalReceived" in frontend_source
    assert "Unexpected recorder connection failure." in frontend_source
    assert 'terminal_sent = await send_json({"type": "stopped"' in router_source
    assert "if not terminal_sent:" in router_source

    field_names = [
        "shipping", "shed", "currency", "delivery", "country",
        "port", "terminal", "bank", "iban", "payment",
    ]
    widgets = []
    for name in field_names:
        label = name.replace("calendarField", "Declaration Date").title()
        popup = name + "Popup"
        opener_tag = "input" if name == "shipping" else "button"
        opener_close = "/" if opener_tag == "input" else f">{label}</button"
        widgets.append(
            f'<{opener_tag} id="{name}" role="combobox" aria-label="Please select {label}" '
            f'aria-controls="{popup}" onclick="document.getElementById(\'{popup}\').hidden=false;'
            f'this.setAttribute(\'aria-expanded\',\'true\')"{opener_close}>'
            f'<div id="{popup}" role="listbox" hidden>'
            f'<input id="{name}Search" aria-label="Search {label}">'
            f'<div id="{name}Option" role="option" onclick="document.getElementById(\'{popup}\').hidden=true;'
            f'document.getElementById(\'{name}\').setAttribute(\'aria-expanded\',\'false\')">{label} Value</div></div>'
        )
    html = (
        '<section><div><div><a href="#single">Single Declarations</a></div></div></section>'
        '<div class="pb-3 col-xl-6"><button id="realAction" aria-label="Continue"><span>Continue</span></button></div>'
        '<div class="pb-3 col-xl-6">Empty layout</div><div class="k-header">Kendo header</div>'
        '<div class="p-0 px-3"><div class="p-0 mb-4"><div class="py-0 px-3"><form class="k-form p-0">'
        '<fieldset class="k-form-fieldset"><button type="button" class="btn btn-primary"></button></fieldset></form></div></div></div>'
        '<div id="notice" role="status" class="k-notification">Saved successfully '
        '<button id="noticeUndo" aria-label="Undo notification">Undo</button></div>'
        '<div class="k-datepicker"><input aria-label="Declaration Date"><button id="dateToggle" aria-controls="datePopup"><span class="k-icon">calendar</span></button></div>'
        '<div id="datePopup" class="k-calendar" role="grid"><button id="date2" role="gridcell">2</button></div>'
        + "".join(widgets)
    )
    actions = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context()
        context.expose_binding("__qa_record_action", lambda source, raw: actions.append(json.loads(raw)))
        context.add_init_script(RECORDER_JS.replace("__KNOWN_LOCATORS_JSON__", "[]"))
        page = context.new_page()
        page.set_default_timeout(1500)
        page.goto("data:text/html," + quote(html))
        page.get_by_text("Single Declarations").click()
        page.locator("#realAction span").click()
        page.locator(".pb-3").nth(1).click()
        page.locator(".k-header").click()
        page.locator("#notice").click(position={"x": 2, "y": 2})
        page.locator("#noticeUndo").click()
        page.locator("button.btn-primary").click()
        for index, name in enumerate(field_names[:10]):
            page.locator("#" + name).click()
            if index == 0:
                # A second event from the same opener must not create a new
                # transaction or duplicate open_dropdown step.
                page.locator("#" + name).click()
            time.sleep(0.04)
            if index == 0:
                page.locator("#shippingSearch").fill("ship")
            page.locator("#" + name + "Option").click()
            if index in {1, 2}:
                # Framework blur/change after selection is not user search.
                page.locator("#" + name).dispatch_event("change")
            time.sleep(0.04)
        page.locator("#dateToggle .k-icon").click()
        page.locator("#date2").click()
        browser.close()

    assert not any(
        (action.get("locator") or {}).get("strategy") == "css"
        and "nth-" in (action.get("locator") or {}).get("value", "")
        for action in actions
    )
    assert not any(action.get("field_name") in {"Empty layout", "Kendo header"} for action in actions)
    assert not any("Saved successfully" in action.get("field_name", "") for action in actions)
    assert any(action.get("field_name") == "Undo notification" for action in actions)
    assert not any(
        (action.get("locator") or {}).get("strategy") == "css"
        and "fieldset" in (action.get("locator") or {}).get("value", "")
        for action in actions
    )
    layout_button = next(
        action for action in actions
        if action.get("tag") == "button" and "btn-primary" in action.get("xpath", "")
    )
    assert layout_button["locator"]["strategy"] == "xpath"
    assert not any(locator.get("strategy") == "css" for locator in layout_button.get("fallback_locators", []))
    assert next(action for action in actions if action.get("field_name") == "Continue")["tag"] == "button"

    for name in field_names[:10]:
        expected = name.lower()
        opener = next(
            action for action in actions
            if action.get("kind") == "open_dropdown" and action.get("field_name", "").lower().endswith(expected)
        )
        option = next(
            action for action in actions
            if action.get("kind") == "select_option" and action.get("field_name", "").lower().startswith(expected)
        )
        assert option.get("parent_locator") == opener.get("locator")
        assert option.get("control_transaction") == opener.get("control_transaction")
        assert option.get("ownership_diagnostic", {}).get("accepted") is True

    shipping = [action for action in actions if "shipping" in action.get("field_name", "").lower()]
    assert len({action.get("control_transaction") for action in shipping}) == 1
    shipping_open = [action for action in shipping if action.get("kind") == "open_dropdown"]
    shipping_fill = [action for action in shipping if action.get("kind") == "fill"]
    shipping_select = [action for action in shipping if action.get("kind") == "select_option"]
    assert len(shipping_open) == len(shipping_fill) == len(shipping_select) == 1
    assert actions.index(shipping_open[0]) < actions.index(shipping_fill[0]) < actions.index(shipping_select[0])
    assert not [
        action for action in actions
        if action.get("kind") == "fill" and action.get("field_name", "").lower().endswith(("shed", "currency"))
    ]
    currency = next(action for action in actions if action.get("kind") == "select_option" and "currency" in action.get("field_name", "").lower())
    delivery = next(action for action in actions if action.get("kind") == "select_option" and "delivery" in action.get("field_name", "").lower())
    assert currency["parent_locator"] != delivery["parent_locator"]
    date_open = next(action for action in actions if action.get("kind") == "open_calendar")
    date_select = next(action for action in actions if action.get("kind") == "select_date")
    assert date_select.get("parent_locator") == date_open.get("locator")
    assert date_select.get("control_transaction") == date_open.get("control_transaction")

    session = WebRecordingSession(1, "https://example.invalid")
    session.actions = [{"kind": "goto", "url": "https://example.invalid"}] + actions
    script = session.build_script()
    compile(script, "recorded.py", "exec")
    assert "# CONTROL_TRANSACTION: popup-" in script
    assert "# TRANSACTION_OPENER:" in script
    assert "# TRANSACTION_POPUP:" in script
    assert "# OWNERSHIP_EVIDENCE:" in script
    assert "# OWNERSHIP_ACCEPTED: true" in script
    quality = TestExecutionManager.assess_playwright_script_quality(script)
    unsafe = [
        item for item in quality
        if item["classification"] == "Invalid"
        or not item["parent_valid"] or not item["action_valid"] or not item["semantic_valid"]
    ]
    assert not unsafe, unsafe

    # Real Stop lifecycle: every popup is already closed and no live opener
    # exists. Even if the opener event itself was unavailable, the option's
    # persisted proof must reconstruct its transaction deterministically.
    completed = [action for action in actions if action.get("kind") in {"select_option", "select_date"}]
    completed_session = WebRecordingSession(2, "https://example.invalid")
    completed_session.actions = [{"kind": "goto", "url": "https://example.invalid"}] + completed
    completed_script = completed_session.build_script()
    completed_quality = TestExecutionManager.assess_playwright_script_quality(completed_script)
    assert not [item for item in completed_quality if not item["parent_valid"]], completed_quality


if __name__ == "__main__":
    run_fixture()
    print("Playwright recorder popup transaction regression fixture: PASS")
