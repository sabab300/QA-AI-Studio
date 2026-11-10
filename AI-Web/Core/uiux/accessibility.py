"""Automated accessibility checks on the rendered DOM, kept separate from "needs a human" items.

Automated checks report only what can be measured from the page. Everything that needs judgement (is the alt text
meaningful? is the reading order sensible? does the screen reader announcement make sense?) is returned as a
human-review item and is never counted as a pass or a defect.
"""
from __future__ import annotations

from .util import composite, contrast_ratio, parse_color, to_hex

INTERACTIVE_ROLES = {"button", "link", "textbox", "combobox", "checkbox", "radio", "searchbox", "spinbutton", "slider", "tab", "menuitem"}


def _num(value):
    try:
        text = str(value)
        return float(text[:-2]) if text.endswith("px") else float(text)
    except (TypeError, ValueError):
        return None


def _is_large(e) -> bool:
    size = _num(e["style"].get("fontSize")) or 14
    weight = e["style"].get("fontWeight", "400")
    bold = (weight in ("bold", "bolder")) or (str(weight).isdigit() and int(weight) >= 700)
    return size >= 24 or (size >= 18.66 and bold)


def _check(key, prop, label, e, status, expected, actual, delta=None, reason="", level="definite", wcag="", extra=None):
    c = {"key": key, "category": "accessibility", "prop": prop, "label": label, "status": status,
         "component": (e.get("name") or e.get("text") or e["selector"])[:80] if e else "Page",
         "component_type": (e.get("role") or e.get("tag")) if e else "page",
         "expected": expected, "actual": actual, "delta": delta, "unit": "", "tolerance": None, "prov": wcag, "reason": reason,
         "match_confidence": 1.0, "match_status": "Measured", "design": None,
         "dom": ({"index": e["i"], "selector": e["selector"], "tag": e["tag"], "role": e.get("role", ""),
                  "box": {"x": round(e["x"], 1), "y": round(e["y"], 1), "w": round(e["w"], 1), "h": round(e["h"], 1)}} if e else None),
         "dom_measured": True, "a11y_level": level, "wcag": wcag}
    if extra:
        c.update(extra)
    return c


def audit(probe: dict, tab: dict | None = None, route: str = "/") -> dict:
    els = probe["elements"]
    by_i = {e["i"]: e for e in els}
    checks, human = [], []
    page_key = route or "/"

    # ---------------------------------------------------------------- document level
    if not probe.get("lang"):
        checks.append(_check(f"accessibility|lang|{page_key}", "lang", "Page language", None, "Fail", "html lang attribute set", "missing", wcag="WCAG 3.1.1 Language of Page"))
    if not (probe.get("title") or "").strip():
        checks.append(_check(f"accessibility|title|{page_key}", "title", "Page title", None, "Fail", "descriptive <title>", "empty", wcag="WCAG 2.4.2 Page Titled"))
    headings = [e for e in els if e.get("role") == "heading"]
    levels = []
    for h in headings:
        try:
            levels.append(int(h["tag"][1]))
        except (ValueError, IndexError):
            continue
    h1s = [h for h in headings if h["tag"] == "h1"]
    if headings and not h1s:
        checks.append(_check(f"accessibility|h1_missing|{page_key}", "h1_missing", "Main heading", headings[0], "Fail", "one h1 heading", "no h1", level="review", wcag="WCAG 1.3.1 / 2.4.6"))
    elif len(h1s) > 1:
        checks.append(_check(f"accessibility|h1_multiple|{page_key}", "h1_multiple", "Main heading", h1s[1], "Fail", "one h1 heading", f"{len(h1s)} h1 headings", level="review", wcag="WCAG 1.3.1"))
    for a, b in zip(levels, levels[1:]):
        if b - a > 1:
            h = next(x for x in headings if x["tag"] == f"h{b}")
            checks.append(_check(f"accessibility|heading_skip|{h['selector']}", "heading_skip", "Heading levels", h, "Fail", f"h{a + 1} after h{a}", f"h{b}", level="review", wcag="WCAG 1.3.1"))
            break

    # ---------------------------------------------------------------- ids and ARIA references
    seen = {}
    for e in els:
        i = (e.get("attrs") or {}).get("id")
        if i:
            seen.setdefault(i, []).append(e)
    for i, group in seen.items():
        if len(group) > 1:
            checks.append(_check(f"accessibility|duplicate_id|{i}", "duplicate_id", "Duplicate id", group[1], "Fail", "unique id", f"id '{i}' used {len(group)} times", wcag="WCAG 4.1.1"))
    for e in els:
        for ref in e.get("badRefs", []):
            checks.append(_check(f"accessibility|aria_ref|{e['selector']}|{ref}", "aria_ref", "ARIA reference", e, "Fail", "referenced element exists", ref, wcag="WCAG 4.1.2"))
        if e.get("aria", {}).get("aria-hidden") == "true" and e.get("focusable"):
            checks.append(_check(f"accessibility|hidden_focusable|{e['selector']}", "hidden_focusable", "Hidden but focusable", e, "Fail", "aria-hidden content is not focusable", "focusable", wcag="WCAG 4.1.2"))

    # ---------------------------------------------------------------- names and labels
    for e in els:
        role = e.get("role", "")
        if role in INTERACTIVE_ROLES and e["tag"] != "label":
            if not e.get("name"):
                if e["tag"] in ("input", "select", "textarea"):
                    checks.append(_check(f"accessibility|label|{e['selector']}", "label", "Form field label", e, "Fail", "an associated label or aria-label", "no accessible name", wcag="WCAG 1.3.1 / 3.3.2 / 4.1.2"))
                else:
                    checks.append(_check(f"accessibility|name|{e['selector']}", "name", "Accessible name", e, "Fail", "a name for this control", "no accessible name", wcag="WCAG 4.1.2 Name, Role, Value"))
            elif e.get("nameSource") == "placeholder":
                checks.append(_check(f"accessibility|placeholder_label|{e['selector']}", "placeholder_label", "Form field label", e, "Fail", "a visible label (placeholder is not a label)", "only a placeholder", level="review", wcag="WCAG 3.3.2 Labels or Instructions"))
        if e["tag"] == "img":
            if "alt" not in (e.get("attrs") or {}):
                checks.append(_check(f"accessibility|alt|{e['selector']}", "alt", "Image alternative text", e, "Fail", "alt attribute (empty if decorative)", "no alt attribute", wcag="WCAG 1.1.1 Non-text Content"))
            elif (e["attrs"].get("alt") or "") == "" and e["w"] > 48 and e["h"] > 48:
                human.append({"topic": "Image alternative text", "target": e["selector"], "question": "This large image has empty alt text. Confirm it is purely decorative."})
        if role == "img" and e["tag"] in ("svg", "span", "div") and not e.get("name") and e.get("aria", {}).get("aria-hidden") != "true":
            checks.append(_check(f"accessibility|icon_name|{e['selector']}", "icon_name", "Icon alternative text", e, "Fail", "a text alternative or aria-hidden", "no accessible name", level="review", wcag="WCAG 1.1.1"))
        if e["tag"] == "a" and (e.get("attrs") or {}).get("href") is not None and not e.get("name"):
            pass  # covered by 'name' above (link role)

    # ---------------------------------------------------------------- contrast
    skipped_images = 0
    for e in els:
        text = e.get("text")
        if not text or e.get("attrs", {}).get("disabled") or e["style"].get("visibility") == "hidden":
            continue
        fg = parse_color(e["style"].get("color"))
        bg = parse_color(e.get("bg"))
        if fg is None or bg is None:
            continue
        if e.get("bgImage"):
            skipped_images += 1
            continue
        opacity = _num(e["style"].get("opacity")) or 1.0
        fg_eff = composite((fg[0], fg[1], fg[2], fg[3] * opacity), bg)
        ratio = round(contrast_ratio(fg_eff, bg), 2)
        need = 3.0 if _is_large(e) else 4.5
        if ratio < need:
            checks.append(_check(f"accessibility|contrast|{e['selector']}", "contrast", "Text contrast", e, "Fail", f"at least {need}:1", f"{ratio}:1 ({to_hex(fg_eff)} on {to_hex(bg)})", round(ratio - need, 2),
                                 wcag="WCAG 1.4.3 Contrast (Minimum)", extra={"ratio": ratio, "required": need}))
    if skipped_images:
        human.append({"topic": "Text contrast over images or gradients", "target": f"{skipped_images} elements",
                      "question": "Text over background images or gradients cannot be measured automatically. Review its legibility."})

    # ---------------------------------------------------------------- keyboard
    if tab is not None:
        stops = tab.get("stops", [])
        if any(s.get("trap") for s in stops):
            s = next(s for s in stops if s.get("trap"))
            checks.append(_check(f"accessibility|keyboard_trap|{page_key}", "keyboard_trap", "Keyboard trap", by_i.get(s.get("i")), "Fail", "Tab moves focus on", f"focus stuck on {s['selector']}", wcag="WCAG 2.1.2 No Keyboard Trap"))
        reached = {s["selector"] for s in stops}
        for e in els:
            if e.get("focusable") and e["selector"] not in reached and e["w"] > 0 and not any(s.get("trap") for s in stops) and len(stops) < 60:
                checks.append(_check(f"accessibility|unreachable|{e['selector']}", "unreachable", "Keyboard reachability", e, "Fail", "reachable with the Tab key", "not reached", level="review", wcag="WCAG 2.1.1 Keyboard"))
        for s in stops:
            e = by_i.get(s.get("i"))
            if not e or s.get("trap"):
                continue
            before = e["style"]
            outline_visible = s["outlineStyle"] not in ("none", "hidden") and (_num(s["outlineWidth"]) or 0) > 0
            changed = (s["boxShadow"] not in ("none", "") and s["boxShadow"] != before.get("boxShadow")) or s["borderColor"] != before.get("borderTopColor") \
                or s["backgroundColor"] != before.get("backgroundColor") or (s["textDecoration"] != before.get("textDecorationLine"))
            if not outline_visible and not changed:
                checks.append(_check(f"accessibility|focus_visible|{e['selector']}", "focus_visible", "Visible focus indicator", e, "Fail", "a visible focus indicator", "no visible change on focus", wcag="WCAG 2.4.7 Focus Visible"))
    human += [
        {"topic": "Reading and focus order", "target": "Page", "question": "Confirm the Tab order follows the visual and logical order of the design."},
        {"topic": "Screen reader experience", "target": "Page", "question": "Automated checks cannot confirm what a screen reader announces. Test the key flows with a screen reader."},
        {"topic": "Colour is not the only cue", "target": "Page", "question": "Confirm errors, status and required fields are not conveyed by colour alone."},
        {"topic": "Placeholder contrast", "target": "Form fields", "question": "Placeholder text colour cannot be read from computed styles. Check it visually."},
    ]
    passed = len({"lang", "title"} - {c["prop"] for c in checks})
    return {"checks": checks, "human_review": human, "summary": {"failed": len(checks), "elements_examined": len(els), "keyboard_stops": len((tab or {}).get("stops", [])) if tab else None, "document_checks_passed": passed}}
