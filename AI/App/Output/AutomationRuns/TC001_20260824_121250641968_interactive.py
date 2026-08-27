# --- QA AI Studio: speed/timeout safety shim (auto-inserted at run time, not saved) ---
# Slows every action down by 300ms and raises the default
# wait timeout to 45000ms so a real application that renders or
# responds slower than Playwright's default pace doesn't cause
# intermittent failures. Adjust in QA Automation -> Test Environment
# Settings.
from playwright.sync_api import BrowserType as _QA_BrowserType
from playwright.sync_api import BrowserContext as _QA_BrowserContext

_QA_ORIGINAL_LAUNCH = _QA_BrowserType.launch


def _qa_launch_with_speed_settings(self, **kwargs):
    kwargs.setdefault("slow_mo", 300)
    return _QA_ORIGINAL_LAUNCH(self, **kwargs)


_QA_BrowserType.launch = _qa_launch_with_speed_settings

_QA_ORIGINAL_NEW_PAGE = _QA_BrowserContext.new_page


def _qa_new_page_with_timeout(self, *args, **kwargs):
    page = _QA_ORIGINAL_NEW_PAGE(self, *args, **kwargs)
    page.set_default_timeout(45000)
    page.set_default_navigation_timeout(45000)
    return page


_QA_BrowserContext.new_page = _qa_new_page_with_timeout
# --- end QA AI Studio shim ---

# --- QA AI Studio: interactive step runner (auto-inserted at run time, not saved) ---
import json
import re
import sys

from playwright.sync_api import sync_playwright

_QA_MAX_REPAIR_ROUNDS = 3

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


def _qa_capture_context():
    context = {"url": "", "title": "", "accessibility": []}
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

with sync_playwright() as playwright:
    browser = playwright.chromium.launch(headless=False)
    context = browser.new_context()
    page = context.new_page()
    _qa_run_step(0, 'page.goto("https://qa.psw.gov.pk/app/")')
    _qa_run_step(1, 'page.get_by_role("button", name="Close").click()')
    _qa_run_step(2, 'page.get_by_role("textbox", name="username").click()')
    _qa_run_step(3, 'page.get_by_role("textbox", name="username").fill("UN-00-0656781")')
    _qa_run_step(4, 'page.get_by_role("textbox", name="username").press("Tab")')
    _qa_run_step(5, 'page.get_by_role("textbox", name="Min. 8 characters").click()')
    _qa_run_step(6, 'page.get_by_role("textbox", name="Min. 8 characters").fill("Test@1234")')
    _qa_run_step(7, 'page.get_by_role("button", name="Login").click()')
    _qa_run_step(8, 'page.get_by_role("link", name="Single Declarations").nth(1).click()')
    _qa_run_step(9, 'page.get_by_text("Create").nth(1).click()')
    _qa_run_step(10, 'page.locator(".k-widget.k-combobox > .k-dropdown-wrap > .k-select > .k-icon").first.click()')
    _qa_run_step(11, 'page.get_by_role("combobox", name="Consignment Category").click()')
    _qa_run_step(12, 'page.get_by_role("option", name="Commercial").click()')
    _qa_run_step(13, 'page.locator("div:nth-child(2) > .k-form-field > .d-flex.align-item-center > .k-widget > .k-dropdown-wrap > .k-select").click()')
    _qa_run_step(14, 'page.get_by_role("option", name="Export Commercial Transaction").click()')
    _qa_run_step(15, 'page.get_by_role("button", name="Confirm").click()')
    _qa_run_step(16, 'page.locator(".k-icon").first.click()')
    _qa_run_step(17, 'page.get_by_placeholder("Please select collectorate").fill("qas")')
    _qa_run_step(18, 'page.get_by_role("option", name="Port Qasim (exports), karachi").click()')
    _qa_run_step(19, 'page.get_by_role("textbox", name="Please enter consignee name").click()')
    _qa_run_step(20, 'page.get_by_role("textbox", name="Please enter consignee name").fill("Bukhari 01")')
    _qa_run_step(21, 'page.get_by_role("textbox", name="Please enter consignee name").press("Tab")')
    _qa_run_step(22, 'page.get_by_role("textbox", name="Please enter consignee address").fill("Bukhari Address")')
    _qa_run_step(23, 'page.get_by_role("textbox", name="Please enter consignee address").press("Tab")')
    _qa_run_step(24, 'page.get_by_role("textbox", name="Please enter BL number").fill("bl-1234")')
    _qa_run_step(25, 'page.get_by_role("button", name="Toggle calendar").click()')
    _qa_run_step(26, 'page.get_by_text("Jun").click()')
    _qa_run_step(27, 'page.get_by_text("6", exact=True).nth(1).click()')
    _qa_run_step(28, 'page.locator("div:nth-child(14) > .k-form-field > .d-flex.align-item-center > .k-widget > .k-dropdown-wrap > .k-select > .k-icon").click()')
    _qa_run_step(29, 'page.get_by_placeholder("Please select port of shipment").fill("qasim")')
    _qa_run_step(30, 'page.get_by_role("option", name="Port Qasim (exports), karachi").click()')
    _qa_run_step(31, 'page.locator("div:nth-child(15) > .k-form-field > .d-flex.align-item-center > .k-widget > .k-dropdown-wrap > .k-select > .k-icon").click()')
    _qa_run_step(32, 'page.get_by_placeholder("Please select destination").click()')
    _qa_run_step(33, 'page.get_by_placeholder("Please select destination").fill("united")')
    _qa_run_step(34, 'page.get_by_role("option", name="ARE - United Arab Emirates").click()')
    _qa_run_step(35, 'page.get_by_placeholder("Please select port of discharge").click()')
    _qa_run_step(36, 'page.locator(".k-dropdown-wrap.k-state-focused > .k-select > .k-icon").click()')
    _qa_run_step(37, 'page.get_by_placeholder("Please select port of discharge").fill("dubai")')
    _qa_run_step(38, 'page.get_by_role("option", name="DXB - Dubai").click()')
    _qa_run_step(39, 'page.get_by_role("textbox", name="Please enter place of delivery").click()')
    _qa_run_step(40, 'page.get_by_role("textbox", name="Please enter place of delivery").fill("Dubai")')
    _qa_run_step(41, 'page.get_by_role("textbox", name="Please enter place of delivery").press("Tab")')
    _qa_run_step(42, 'page.locator(".k-dropdown-wrap.k-state-focused > .k-select > .k-icon").click()')
    _qa_run_step(43, 'page.get_by_role("option", name="AAE - A.A.ENTERPRISES").click()')
    _qa_run_step(44, 'page.get_by_role("textbox", name="Please enter gross weight").click()')
    _qa_run_step(45, 'page.get_by_role("textbox", name="Please enter gross weight").click()')
    _qa_run_step(46, 'page.get_by_role("textbox", name="Please enter gross weight").click()')
    _qa_run_step(47, 'page.get_by_role("textbox", name="Please enter gross weight").press("ArrowLeft")')
    _qa_run_step(48, 'page.get_by_role("textbox", name="Please enter gross weight").press("ArrowLeft")')
    _qa_run_step(49, 'page.get_by_role("textbox", name="Please enter gross weight").press("ArrowLeft")')
    _qa_run_step(50, 'page.get_by_role("textbox", name="Please enter gross weight").press("ArrowLeft")')
    _qa_run_step(51, 'page.get_by_role("textbox", name="Please enter gross weight").press("Tab")')
    _qa_run_step(52, 'page.get_by_role("textbox", name="Please enter gross weight").click()')
    _qa_run_step(53, 'page.get_by_role("textbox", name="Please enter gross weight").click()')
    _qa_run_step(54, 'page.get_by_role("textbox", name="Please enter gross weight").click()')
    _qa_run_step(55, 'page.get_by_role("textbox", name="Please enter gross weight").click()')
    _qa_run_step(56, 'page.get_by_role("textbox", name="Please enter gross weight").press("ArrowLeft")')
    _qa_run_step(57, 'page.get_by_role("textbox", name="Please enter gross weight").press("ArrowLeft")')
    _qa_run_step(58, 'page.get_by_role("textbox", name="Please enter gross weight").press("ArrowLeft")')
    _qa_run_step(59, 'page.get_by_role("textbox", name="Please enter gross weight").fill("10.0")')
    _qa_run_step(60, 'page.get_by_role("textbox", name="Please enter net weight").click()')
    _qa_run_step(61, 'page.get_by_role("textbox", name="Please enter net weight").fill("09.0")')
    _qa_run_step(62, 'page.get_by_role("textbox", name="Please enter marks").click()')
    _qa_run_step(63, 'page.get_by_role("textbox", name="Please enter marks").fill("toys")')
    _qa_run_step(64, 'page.get_by_role("textbox", name="Please enter marks").press("Tab")')
    _qa_run_step(65, 'page.locator(".pb-3.col-xl-6 > div > span > .k-form-field > .d-flex.align-item-center > .k-widget > .k-dropdown-wrap > .k-select > .k-icon").click()')
    _qa_run_step(66, 'page.get_by_role("option", name="Qasim International Container").click()')
    _qa_run_step(67, 'page.locator("div:nth-child(23) > div > span > .k-form-field > .d-flex.align-item-center > .k-widget > .k-dropdown-wrap > .k-select").click()')
    _qa_run_step(68, 'page.get_by_role("option", name="Qasim International Container").click()')
    _qa_run_step(69, 'page.get_by_role("button", name="Save & Proceed").click()')
    _qa_run_step(70, 'page.get_by_role("button", name="Save & Proceed").click()')
    _qa_run_step(71, 'page.locator(".k-select").first.click()')
    _qa_run_step(72, 'page.get_by_placeholder("Please select currency").fill("us")')
    _qa_run_step(73, 'page.get_by_role("option", name="United States of America -").click()')
    _qa_run_step(74, 'page.locator("div:nth-child(3) > .k-form-field > .d-flex.align-item-center > .k-widget > .k-dropdown-wrap > .k-select > .k-icon").click()')
    _qa_run_step(75, 'page.get_by_role("option", name="Cost And Freight (CFR)").click()')
    _qa_run_step(76, 'page.get_by_placeholder("Please select bank name").click()')
    _qa_run_step(77, 'page.locator(".k-dropdown-wrap.k-state-focused > .k-select").click()')
    _qa_run_step(78, 'page.get_by_role("option", name="Al Baraka Bank (Pakistan) Ltd").click()')
    _qa_run_step(79, 'page.locator("div:nth-child(5) > .k-form-field > .d-flex.align-item-center > .k-widget > .k-dropdown-wrap > .k-select").click()')
    _qa_run_step(80, 'page.get_by_role("option", name="PK17AIIN0000100206680010").click()')
    _qa_run_step(81, 'page.locator("div:nth-child(6) > .k-form-field > .d-flex.align-item-center > .k-widget > .k-dropdown-wrap > .k-select").click()')
    _qa_run_step(82, 'page.get_by_role("option", name="Open Account").click()')
    _qa_run_step(83, 'page.get_by_role("textbox", name="Invoice Number").click()')
    _qa_run_step(84, 'page.get_by_role("textbox", name="Invoice Number").fill("1213213131")')
    _qa_run_step(85, 'page.get_by_role("button", name="Toggle calendar").first.click()')
    _qa_run_step(86, 'page.get_by_role("listitem").filter(has_text="Jun").click()')
    _qa_run_step(87, 'page.get_by_text("18").nth(1).click()')
    _qa_run_step(88, 'page.get_by_role("button", name="Add").click()')
    _qa_run_step(89, 'page.get_by_role("textbox", name="Freight").click()')
    _qa_run_step(90, 'page.get_by_role("textbox", name="Freight").fill("1")')
    _qa_run_step(91, 'page.get_by_role("button", name="Save & Proceed").click()')
    _qa_run_step(92, 'page.get_by_role("button", name="\ue11f Add Commodity").click()')
    _qa_run_step(93, 'page.get_by_placeholder("Please enter HS code").click()')
    _qa_run_step(94, 'page.get_by_placeholder("Please enter HS code").fill("9401.1000")')
    _qa_run_step(95, 'page.get_by_placeholder("Please enter HS code").press("Tab")')
    _qa_run_step(96, 'page.locator(".k-searchbar").first.click()')
    _qa_run_step(97, 'page.get_by_role("textbox", name="Please enter declared").click()')
    _qa_run_step(98, 'page.get_by_role("textbox", name="Please enter declared").fill("toysss")')
    _qa_run_step(99, 'page.locator("input[name=\\"unitValue\\"]").click()')
    _qa_run_step(100, 'page.locator("input[name=\\"unitValue\\"]").press("ArrowLeft")')
    _qa_run_step(101, 'page.locator("input[name=\\"unitValue\\"]").fill("100")')
    _qa_run_step(102, 'page.get_by_role("button", name="\ue11f Add Container").click()')
    _qa_run_step(103, 'page.get_by_role("textbox", name="Enter container number").click()')
    _qa_run_step(104, 'page.get_by_role("textbox", name="Enter container number").fill("QVLI1324244")')
    _qa_run_step(105, 'page.get_by_role("textbox", name="Enter Quantity").click()')
    _qa_run_step(106, 'page.get_by_role("textbox", name="Enter Quantity").fill("1000.0")')
    _qa_run_step(107, 'page.get_by_role("textbox", name="Enter Quantity").press("Tab")')
    _qa_run_step(108, 'page.get_by_role("textbox", name="Enter No of Package").fill("10.0")')
    _qa_run_step(109, 'page.locator(".k-select > .k-icon").click()')
    _qa_run_step(110, 'page.get_by_role("option", name="BAGS").click()')
    _qa_run_step(111, 'page.get_by_role("textbox", name="Enter No of Package").click()')
    _qa_run_step(112, 'page.get_by_role("textbox", name="Enter No of Package").fill("10.0")')
    _qa_run_step(113, 'page.get_by_role("button", name="Add").click()')
    _qa_run_step(114, 'page.get_by_role("button", name="Confirm").click()')
    _qa_run_step(115, 'page.locator("div:nth-child(2) > .card-header > .h-100").click()')
    _qa_run_step(116, 'page.locator("input[name=\\"quantityStatisticalPurpose\\"]").click()')
    _qa_run_step(117, 'page.locator("input[name=\\"quantityStatisticalPurpose\\"]").press("ArrowLeft")')
    _qa_run_step(118, 'page.locator("input[name=\\"quantityStatisticalPurpose\\"]").fill("1000")')
    _qa_run_step(119, 'page.get_by_role("button", name="Save").click()')
    _qa_run_step(120, 'page.get_by_role("button", name="No, Proceed Please").click()')
    _qa_run_step(121, 'page.get_by_role("button", name="Save & Proceed").click()')
    _qa_run_step(122, 'page.get_by_role("button", name="Upload").first.click()')
    _qa_run_step(123, 'page.get_by_role("textbox", name="Enter remarks (Optional)").click()')
    _qa_run_step(124, 'page.get_by_role("textbox", name="Enter remarks (Optional)").fill("sss")')
    _qa_run_step(125, 'page.get_by_text("Select files...Drop files").click()')
    _qa_run_step(126, 'page.get_by_role("button", name="Choose File").set_input_files("D:\\\\UploadPdf.pdf")')
    _qa_run_step(127, 'page.get_by_role("button", name="Upload").nth(1).click()')
    _qa_run_step(128, 'page.get_by_role("button", name="Choose File").set_input_files("D:\\\\UploadPdf.pdf")')
    _qa_run_step(129, 'page.get_by_role("button", name="Save & Proceed").click()')
    _qa_run_step(130, 'page.get_by_label("", exact=True).check()')
    _qa_run_step(131, 'page.get_by_role("button", name="Validate & Proceed").click()')
    _qa_run_step(132, 'page.get_by_role("button", name="Save and Submit").click()')
    _qa_run_step(133, 'page.get_by_role("tab", name="PD Account").click()')
    _qa_run_step(134, 'page.get_by_role("combobox", name="PD Account collectorate").click()')
    _qa_run_step(135, 'page.locator(".k-select").click()')
    _qa_run_step(136, 'page.get_by_role("combobox", name="PD Account collectorate").fill("Karach")')
    _qa_run_step(137, 'page.locator("div").filter(has_text="Karachi CustomKARACHI PORT").nth(1).click()')
    _qa_run_step(138, 'page.get_by_role("combobox", name="PD Account collectorate").click()')
    _qa_run_step(139, 'page.get_by_role("combobox", name="PD Account collectorate").click()')
    _qa_run_step(140, 'page.get_by_role("combobox", name="PD Account collectorate").fill("KARACH")')
    _qa_run_step(141, 'page.get_by_role("combobox", name="PD Account collectorate").click()')
    _qa_run_step(142, 'page.get_by_role("combobox", name="PD Account collectorate").fill("Karachi")')
    _qa_run_step(143, 'page.get_by_role("option", name="Karachi Custom").click()')
    _qa_run_step(144, 'page.get_by_role("button", name="Confirm Payment").click()')
    _qa_run_step(145, 'page.get_by_role("button", name="Confirm", exact=True).click()')
    _qa_run_step(146, 'page.get_by_role("button", name="OK").click()')
    _qa_run_step(147, 'page.get_by_role("button", name="ProfileTRUST SHOES").click()')
    _qa_run_step(148, 'page.get_by_role("button", name="Logout").click()')
    _qa_run_step(149, 'page.get_by_role("button", name="Close").click()')
    context.close()
    browser.close()

_qa_send_event({"event": "run_finished", "repairs": _QA_REPAIRS})
print('TEST PASSED')
