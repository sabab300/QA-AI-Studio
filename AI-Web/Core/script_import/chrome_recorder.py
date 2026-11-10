"""Chrome DevTools Recorder JSON -> QA AI Studio structured Python script.

Every recorded step is either converted to a structured (QA_STEP + metadata)
statement the replay engine can repair, or reported as unsupported -- never
silently dropped. A recording with any unsupported step is NEEDS REVIEW.
"""
from __future__ import annotations

import ast
import json
import re

from Core.script_import.models import (
    ExtractedTest, FORMAT_CHROME_RECORDER, RUNTIME_PYTHON, STATUS_CONVERSION_REQUIRED,
    STATUS_NEEDS_REVIEW, STATUS_INVALID, SEVERITY_ERROR, SEVERITY_WARNING, SEVERITY_INFO, find_test_id,
)

MODIFIERS = {"Control", "Shift", "Alt", "Meta"}
_DYNAMIC = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-|[0-9a-f]{12,}|\d{5,}|\b[a-z]+[-_]?\d{3,}[a-z0-9_-]*\b|:r\d+:|ember\d+|react-select|mui-\d+", re.I)
_SENSITIVE = re.compile(r"pass(word|wd)?|pwd|secret|token|otp|\bpin\b", re.I)
_ROLE_TYPE = {
    "button": "button", "link": "link", "textbox": "text", "searchbox": "text", "combobox": "dropdown",
    "listbox": "dropdown", "checkbox": "checkbox", "radio": "radio", "switch": "checkbox", "tab": "button",
    "menuitem": "button", "option": "button", "spinbutton": "text",
}


def _looks_dynamic(css: str) -> bool:
    return bool(_DYNAMIC.search(css or ""))


def _parse_selector(raw: str) -> dict | None:
    """One Chrome selector string -> a recorder-style locator dict (or None)."""
    if raw.startswith("aria/"):
        text = raw[5:]
        m = re.match(r'^(.*?)\[role="([^"]+)"\]$', text, re.S)
        if m and m.group(1).strip():
            return {"strategy": "role", "role": m.group(2), "name": m.group(1)}
        if text.strip():
            return {"strategy": "label", "value": text}
        return None
    if raw.startswith("xpath/"):
        value = raw[6:]
        return {"strategy": "xpath", "value": value.lstrip("/") and ("/" + value.lstrip("/") if value.startswith("/") and not value.startswith("//") else value) or value}
    if raw.startswith("text/"):
        return {"strategy": "text", "value": raw[5:]}
    if raw.startswith("pierce/"):
        css = raw[7:]
        return _css_locator(css)
    return _css_locator(raw)


def _css_locator(css: str) -> dict:
    m = re.fullmatch(r"#([A-Za-z][\w-]*)", css)
    if m:
        return {"strategy": "id", "value": m.group(1)}
    return {"strategy": "css", "value": css}


def _candidates(step: dict) -> list[dict]:
    out = []
    for chain in step.get("selectors") or []:
        if isinstance(chain, str):
            chain = [chain]
        if not chain:
            continue
        if len(chain) > 1:
            # chained shadow-root / nested selectors -> a single combined CSS/xpath chain
            parsed = [_parse_selector(c) for c in chain]
            if any(p is None for p in parsed):
                continue
            if all(p["strategy"] in ("css", "id") for p in parsed):
                css = " >> ".join(("#" + p["value"]) if p["strategy"] == "id" else p["value"] for p in parsed)
                out.append({"strategy": "css", "value": css})
            continue
        loc = _parse_selector(chain[0])
        if loc:
            out.append(loc)
    # de-duplicate
    seen, unique = set(), []
    for loc in out:
        key = json.dumps(loc, sort_keys=True)
        if key not in seen:
            seen.add(key)
            unique.append(loc)
    return unique


def _rank(loc: dict) -> int:
    s = loc["strategy"]
    if s == "role":
        return 0
    if s == "id" and not _looks_dynamic(loc["value"]):
        return 1
    if s == "label":
        return 2
    if s == "css" and not _looks_dynamic(loc["value"]) and "nth-" not in loc["value"]:
        return 3
    if s == "text":
        return 4
    if s == "xpath":
        return 5
    return 6


def _field_name(loc: dict, step: dict) -> str:
    for c in [loc] + _candidates(step):
        if c["strategy"] == "role":
            return c["name"]
        if c["strategy"] in ("label", "text"):
            return c["value"]
    return step.get("type", "Element")


def _locator_source(loc: dict) -> str:
    """Same Playwright source the Web recorder emits for each locator strategy
    (kept in lock-step with WebRecordingSession._locator_source by a parity test;
    duplicated here so the importer does not import the recorder's heavy chain)."""
    s = loc.get("strategy")
    if s == "testid":
        return f"page.get_by_test_id({loc['value']!r})"
    if s == "id":
        return f"page.locator({'#' + loc['value']!r})"
    if s == "role":
        return f"page.get_by_role({loc['role']!r}, name={loc['name']!r})"
    if s == "label":
        return f"page.get_by_label({loc['value']!r})"
    if s == "placeholder":
        return f"page.get_by_placeholder({loc['value']!r})"
    if s == "title":
        return f"page.get_by_title({loc['value']!r})"
    if s == "text":
        return f"page.get_by_text({loc['value']!r}, exact=True)"
    if s == "css":
        return f"page.locator({loc['value']!r})"
    if s == "xpath":
        return f"page.locator({'xpath=' + loc['value']!r})"
    raise ValueError("unsupported locator")


def locator_display(loc) -> str:
    """Same serialisation as WebRecordingSession._locator_display."""
    loc = loc or {}
    s = loc.get("strategy")
    if s == "role":
        return f"role={loc.get('role', '')}|name={loc.get('name', '')}"
    if s == "testid":
        return f"data-testid={loc.get('value', '')}"
    if s in {"label", "name", "placeholder", "title", "text"}:
        return f"{s}={loc.get('value', '')}"
    if s == "id":
        return "#" + str(loc.get("value") or "")
    if s == "xpath":
        return "xpath=" + str(loc.get("value") or "")
    return str(loc.get("value") or "")


def structured_step_lines(number, action, statement, field_type=None, field_name=None):
    """Structured QA_STEP block, identical in shape to the Web recorder's."""
    from Core.playwright_step_metadata import metadata_comment
    kind = action.get("kind") or "execute"
    field_type = field_type or action.get("field_type") or ("dropdown" if kind == "select_option" else "button" if kind == "click" else "text")
    field_name = field_name or action.get("field_name") or "Element"
    sensitive = field_type == "password" or any(tok in field_name.lower() for tok in ("password", "passwd", "secret", "token"))
    primary = (str(action.get("url") or "") if kind == "goto" else locator_display(action.get("locator"))) or "-"
    fallbacks = [locator_display(l) for l in action.get("fallback_locators") or []]
    fallbacks = [l for l in fallbacks if l and l != primary]
    description = ("Open application" if kind == "goto" else f"Enter {field_name}" if kind == "fill"
                   else f"Select {field_name}" if kind == "select_option" else f"Click {field_name}" if kind == "click"
                   else field_name)
    meta = [
        metadata_comment("QA_STEP", f"{number:03d}"),
        metadata_comment("DESCRIPTION", description),
        metadata_comment("FIELD_TYPE", field_type),
        metadata_comment("FIELD_NAME", field_name),
        metadata_comment("CALENDAR_METADATA", "{}"),
        metadata_comment("ACTION_TYPE", kind),
        metadata_comment("CONTROL_ROLE", action.get("control_role")),
        metadata_comment("EXPECTED_VALUE", action.get("expected_value") or action.get("value")),
        metadata_comment("PRIMARY_LOCATOR", primary),
        metadata_comment("FALLBACK_LOCATORS", "; ".join(fallbacks)),
        metadata_comment("XPATH", action.get("xpath") or "-"),
        metadata_comment("SENSITIVE", "true" if sensitive else "false"),
        metadata_comment("SOURCE", "chrome-recorder-import"),
        statement,
    ]
    return ["    " + line for line in meta]


def convert_chrome_recording(rec: dict, filename: str, index: int) -> ExtractedTest:
    title = str(rec.get("title") or filename or f"Recording {index + 1}")
    stem = re.sub(r"\.[^.]+$", "", filename or "recording")
    tcid = find_test_id(title, filename if index == 0 else "")
    t = ExtractedTest(test_id=tcid or (f"{stem}_{index + 1:02d}" if index else stem), name=title,
                      source_filename=filename, source_format=FORMAT_CHROME_RECORDER,
                      original_source=json.dumps(rec, indent=2, ensure_ascii=False), index_in_file=index,
                      id_generated=tcid is None, source_runtime="chrome-recorder")
    steps = rec.get("steps") or []

    lines: list[str] = []
    n = 0
    held: set[str] = set()
    pending_keyups: list[str] = []

    def add(action, statement):
        nonlocal n
        n += 1
        lines.extend(structured_step_lines(
            n, action, statement,
            field_type=action.get("field_type"), field_name=action.get("field_name")))
        t.steps.append({"step": n, "action": action.get("kind"), "target": action.get("field_name", ""),
                        "description": action.get("field_name", "")})

    unsupported = []
    for pos, step in enumerate(steps, start=1):
        kind = step.get("type")
        target = step.get("target", "main")
        if step.get("frame"):
            unsupported.append((pos, kind, "step targets an iframe (frame path); iframe context is not converted"))
            continue
        if target not in ("main", None):
            unsupported.append((pos, kind, f"step targets another tab/page ('{str(target)[:60]}'); multi-page ownership is not converted"))
            continue
        try:
            if kind == "setViewport":
                add({"kind": "execute", "field_type": "navigation", "field_name": "Viewport"},
                    f"page.set_viewport_size({{'width': {int(step['width'])}, 'height': {int(step['height'])}}})")
            elif kind == "navigate":
                add({"kind": "goto", "url": step["url"], "field_type": "navigation", "field_name": "Application URL"},
                    f"page.goto({step['url']!r})")
            elif kind in ("click", "doubleClick", "hover", "change"):
                cands = sorted(_candidates(step), key=_rank)
                if not cands:
                    unsupported.append((pos, kind, "no usable selector was recorded"))
                    continue
                primary, fallbacks = cands[0], cands[1:]
                src = _locator_source(primary)
                role = next((c["role"] for c in cands if c["strategy"] == "role"), None)
                fname = _field_name(primary, step)
                if kind == "change":
                    value = str(step.get("value", ""))
                    is_select = role in ("combobox", "listbox") or re.search(r"(^|[ >/])select(\b|\[)", json.dumps(step.get("selectors")), re.I) is not None
                    sensitive = bool(_SENSITIVE.search(fname) or _SENSITIVE.search(json.dumps(step.get("selectors"))))
                    if is_select:
                        statement, ak = f"{src}.select_option({value!r})", "select_option"
                        ftype = "dropdown"
                        t.add(SEVERITY_WARNING, "CHANGE_AS_SELECT",
                              f"Step {pos}: a 'change' on a combobox/select was converted to select_option(); confirm the option value.", pos)
                    else:
                        statement, ak = f"{src}.fill({value!r})", "fill"
                        ftype = "password" if sensitive else _ROLE_TYPE.get(role or "textbox", "text")
                    add({"kind": ak, "field_type": ftype, "field_name": fname, "locator": primary,
                         "fallback_locators": fallbacks, "xpath": next((c["value"] for c in cands if c["strategy"] == "xpath"), ""),
                         "value": value, "control_role": role, "tag": "select" if is_select else "input"}, statement)
                    if sensitive:
                        t.add(SEVERITY_WARNING, "LITERAL_SECRET",
                              f"Step {pos}: a credential-like field carries a literal value in the recording. It is masked in previews/logs; "
                              "use a runtime variable before sharing.", pos)
                else:
                    method = {"click": "click", "doubleClick": "dblclick", "hover": "hover"}[kind]
                    add({"kind": "click" if kind != "hover" else "execute", "field_type": _ROLE_TYPE.get(role or "button", "button"),
                         "field_name": fname, "locator": primary, "fallback_locators": fallbacks,
                         "xpath": next((c["value"] for c in cands if c["strategy"] == "xpath"), ""),
                         "control_role": role}, f"{src}.{method}()")
            elif kind == "keyDown":
                key = step.get("key", "")
                if key in MODIFIERS:
                    held.add(key)
                    add({"kind": "execute", "field_type": "text", "field_name": f"Hold {key}"}, f"page.keyboard.down({key!r})")
                else:
                    add({"kind": "execute", "field_type": "text", "field_name": f"Press {key}"}, f"page.keyboard.press({key!r})")
                    pending_keyups.append(key)
            elif kind == "keyUp":
                key = step.get("key", "")
                if key in MODIFIERS:
                    held.discard(key)
                    add({"kind": "execute", "field_type": "text", "field_name": f"Release {key}"}, f"page.keyboard.up({key!r})")
                elif key in pending_keyups:
                    pending_keyups.remove(key)   # paired with a keyboard.press() already emitted
                else:
                    t.add(SEVERITY_INFO, "UNPAIRED_KEYUP", f"Step {pos}: keyUp '{key}' had no preceding keyDown; ignored.", pos)
            elif kind == "scroll":
                cands = sorted(_candidates(step), key=_rank)
                x, y = int(step.get("x", 0) or 0), int(step.get("y", 0) or 0)
                if cands:
                    add({"kind": "execute", "field_type": "text", "field_name": "Scroll", "locator": cands[0]},
                        f"{_locator_source(cands[0])}.evaluate('el => el.scrollTo({x}, {y})')")
                else:
                    add({"kind": "execute", "field_type": "text", "field_name": "Scroll"}, f"page.evaluate('window.scrollTo({x}, {y})')")
            elif kind == "waitForElement":
                cands = sorted(_candidates(step), key=_rank)
                if not cands:
                    unsupported.append((pos, kind, "no usable selector was recorded"))
                    continue
                visible = step.get("visible", True)
                op = step.get("operator", ">=")
                count = step.get("count", 1)
                if op not in (">=", "==") or count not in (1, None) and op == ">=" and count != 1:
                    unsupported.append((pos, kind, f"count condition '{op} {count}' is not converted"))
                    continue
                src = _locator_source(cands[0])
                add({"kind": "execute", "field_type": "navigation", "field_name": f"Wait for {_field_name(cands[0], step)}",
                     "locator": cands[0], "fallback_locators": cands[1:]},
                    f"{src}.first.wait_for(state={'visible' if visible else 'hidden'!r})")
            elif kind == "waitForExpression":
                add({"kind": "execute", "field_type": "navigation", "field_name": "Wait for condition"},
                    f"page.wait_for_function({str(step.get('expression', ''))!r})")
            elif kind == "close":
                continue
            else:
                unsupported.append((pos, kind, f"step type '{kind}' is not supported"))
        except (KeyError, ValueError, TypeError) as err:
            unsupported.append((pos, kind, f"step could not be converted ({type(err).__name__}: {err})"))

    for pos, kind, reason in unsupported:
        t.add(SEVERITY_WARNING, "UNSUPPORTED_STEP", f"Step {pos} ({kind}) was not converted: {reason}", pos)
    if not lines:
        t.status = STATUS_INVALID
        t.add(SEVERITY_ERROR, "NO_STEPS", "The recording contains no convertible steps.")
        return t
    body = "\n".join(lines)
    script = (
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
    try:
        ast.parse(script)
    except SyntaxError as err:
        t.add(SEVERITY_ERROR, "CONVERSION_INVALID", f"Converted script did not parse: {err.msg}")
        t.status = STATUS_NEEDS_REVIEW
        return t
    t.assertion_count = 0
    t.add(SEVERITY_WARNING, "NO_ASSERTIONS",
          "Chrome Recorder recordings contain no assertions; add verification steps so a completed replay proves the scenario.")
    t.requires = ["python>=3.9", "playwright"]
    if unsupported:
        # lossless-or-review: an incomplete conversion is never offered as executable
        t.executable_source = None
        t.status = STATUS_NEEDS_REVIEW
        t.notes["partial_script_withheld"] = True
        return t
    t.executable_source = script
    t.executable_runtime = RUNTIME_PYTHON
    t.status = STATUS_NEEDS_REVIEW if t.id_generated else STATUS_CONVERSION_REQUIRED
    if t.id_generated:
        t.add(SEVERITY_WARNING, "ID_GENERATED",
              "No Test Case ID (e.g. TC_XXX_0001) found in the recording title or filename; map it to a test case before saving.")
    return t


def extract_chrome_recorder(data, filename: str) -> list[ExtractedTest]:
    recs = data if isinstance(data, list) else [data]
    return [convert_chrome_recording(r, filename, i) for i, r in enumerate(recs)]
