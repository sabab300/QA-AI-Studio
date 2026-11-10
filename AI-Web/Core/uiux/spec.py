"""Developer-ready specifications for one result: where the element is, what the design says, what the page has, what differs and what to change.

Rules
  * Expected values come only from the design's own data (Figma structure). Anything the design does not provide is
    "Not Verifiable" - never guessed. Values read off a picture (a sampled colour, a region's position) are labelled "inferred".
  * Actual values are what Chromium computed for the element (position, size, computed CSS, text) with a locator to find it again.
  * A suggested fix lists only properties that were measured and differ; it names the CSS property to change and the value the design
    requires. QA AI Studio never edits application code.
"""
from __future__ import annotations

import re

NV = "Not Verifiable"
MATCH, DIFFERS, INFO = "match", "differs", "info"


def _num(v):
    try:
        return float(str(v).replace("px", "").strip())
    except (TypeError, ValueError):
        return None


def _px(v):
    n = _num(v)
    return None if n is None else (f"{n:g}px")


def _rgba(s):
    m = re.match(r"rgba?\(([^)]+)\)", s or "")
    if not m:
        return None
    p = [float(x) for x in re.split(r"[ ,/]+", m.group(1).strip()) if x]
    return (int(round(p[0])), int(round(p[1])), int(round(p[2])), p[3] if len(p) > 3 else 1.0) if len(p) >= 3 else None


def hexof(s):
    """#RRGGBB for a CSS colour or an existing #hex; None when it is not a colour."""
    if not s:
        return None
    s = str(s).strip()
    if s.startswith("#"):
        h = s.upper()
        return "#" + h[1:7] if len(h) >= 7 else (("#" + "".join(c * 2 for c in h[1:4])) if len(h) == 4 else h)
    c = _rgba(s)
    return "#%02X%02X%02X" % c[:3] if c else None


def _dist(a, b):
    ra = tuple(int(a[i:i + 2], 16) for i in (1, 3, 5))
    rb = tuple(int(b[i:i + 2], 16) for i in (1, 3, 5))
    return sum((x - y) ** 2 for x, y in zip(ra, rb)) ** 0.5


COLOUR_NAMES = {"#FF0000": "red", "#0000FF": "blue", "#00FF00": "green", "#FFFFFF": "white", "#000000": "black", "#808080": "grey"}


def _fmt_colour(h):
    return h if h not in COLOUR_NAMES else f"{COLOUR_NAMES[h]} ({h})"


def band(x, y, w, h, width, height):
    """Coarse position, e.g. 'top-right', from an element's centre within a page or frame of the given size."""
    if not width or not height:
        return ""
    cx, cy = (x + w / 2) / width, (y + h / 2) / height
    v = "top" if cy < 0.2 else ("upper" if cy < 0.4 else ("middle" if cy < 0.6 else ("lower" if cy < 0.8 else "bottom")))
    hz = "left" if cx < 0.34 else ("centre" if cx < 0.66 else "right")
    return f"{v}-{hz}"


def design_path(node: dict, screen: str) -> str:
    parts = [p for p in (node.get("path") or "").split("/") if p]
    if parts and parts[0] == screen:
        parts = parts[1:]
    return " → ".join([screen] + parts) if screen else " → ".join(parts)


def dom_path(el: dict, elements: list, page_title: str, limit=6) -> str:
    chain, cur, seen = [], el, 0
    while cur is not None and seen < 12:
        label = (cur.get("name") or "").strip()[:40] or cur["attrs"].get("id") or (cur.get("classes") or "").split(" ")[0] or cur["tag"]
        label = f"{cur['tag']}" + (f" “{label}”" if label and label != cur["tag"] else "")
        chain.append(label)
        p = cur.get("p", -1)
        cur = elements[p] if isinstance(p, int) and 0 <= p < len(elements) and p != cur["i"] else None
        seen += 1
    chain = [c for c in reversed(chain) if not c.startswith("body")] or chain
    if len(chain) > limit:
        chain = chain[:1] + ["…"] + chain[-(limit - 2):]
    return " → ".join(([page_title] if page_title else []) + chain)


def locator_of(el: dict) -> tuple:
    sel = el.get("selector") or ""
    stable = sel.startswith("[data-testid") or (sel.startswith("#") and ">" not in sel)
    return sel, ("stable (data-testid / unique id)" if stable else "structural CSS path - may change when the page markup changes")


def _row(field, expected, actual, status, source="", note=""):
    return {"field": field, "expected": expected, "actual": actual, "status": status, "source": source, "note": note}


def _cmp_num(field, exp, act, tol=1.0, unit="px", source="Figma"):
    e, a = _num(exp), _num(act)
    if e is None:
        return _row(field, NV, (f"{a:g}{unit}" if a is not None else "—"), NV, source, "The design does not provide this value.")
    if a is None:
        return _row(field, f"{e:g}{unit}", "—", INFO, source, "The browser exposed no value.")
    return _row(field, f"{e:g}{unit}", f"{a:g}{unit}", MATCH if abs(e - a) <= tol else DIFFERS, source)


def _cmp_colour(field, exp, act, tol=14.0, source="Figma"):
    he, ha = hexof(exp), hexof(act)
    if not he:
        return _row(field, NV, _fmt_colour(ha) if ha else "—", NV, source, "The design does not provide a solid colour for this property.")
    if not ha:
        return _row(field, _fmt_colour(he), "—", INFO, source)
    return _row(field, _fmt_colour(he), _fmt_colour(ha), MATCH if _dist(he, ha) <= tol else DIFFERS, source)


def _radius(el):
    s = el["style"]
    vals = [_num(s.get(k)) for k in ("borderTopLeftRadius", "borderTopRightRadius", "borderBottomRightRadius", "borderBottomLeftRadius")]
    return vals if any(v is not None for v in vals) else None


def _border(el):
    s = el["style"]
    w = _num(s.get("borderTopWidth"))
    return w, hexof(s.get("borderTopColor")) if (w or 0) > 0 else None


def _pad(el):
    s = el["style"]
    return [_num(s.get(k)) for k in ("paddingTop", "paddingRight", "paddingBottom", "paddingLeft")]


def _box_text(x, y, w, h):
    return f"x {x:g}, y {y:g}, {w:g}×{h:g}px"


def build(ctx: dict, check: dict | None, node: dict | None, el: dict | None, elements: list, frame: dict, region: dict | None, color_sample: dict | None = None) -> dict:
    """The specification block for one result. ``node`` is the design layer, ``el`` the page element, either may be None."""
    screen = ctx.get("frame_name") or ctx.get("screen", "")
    page_title = ctx.get("page_title") or ""
    fields = []
    prov = (node or {}).get("prov") or {}
    inferred = False
    # ---- element and location
    name_exp = (node or {}).get("name") or ""
    name_act = ((el or {}).get("name") or (el or {}).get("text") or "").strip()[:80]
    tagrole = f"{el['tag']}" + (f" [{el['role']}]" if el and el.get("role") else "") if el else ""
    fields.append(_row("Element", name_exp or "—", (name_act + (f" ({tagrole})" if name_act else tagrole)) if el else ("not found on the page" if node else "—"), INFO if (node and el) else (DIFFERS if node and not el else INFO), "Figma layer name" if node else ""))
    fields.append(_row("Screen / page", screen or "—", ctx.get("page_url") or "—", INFO, "design frame / website URL"))
    loc_d = design_path(node, screen) if node else ""
    loc_a = dom_path(el, elements, page_title) if el else ""
    fields.append(_row("Location (hierarchy)", loc_d or NV, loc_a or "—", INFO if loc_d else NV, "Figma layer path" if loc_d else "", "" if loc_d else "The design has no layer path for this element."))
    pos_d = band(node["x"], node["y"], node["w"], node["h"], frame.get("width"), frame.get("height")) if node and frame.get("width") else ""
    pos_a = band(el["x"], el["y"], el["w"], el["h"], ctx.get("page_w"), ctx.get("page_h")) if el and ctx.get("page_w") else ""
    if pos_d or pos_a:
        fields.append(_row("Position in page", pos_d or NV, pos_a or "—", INFO, "relative to frame / page size", "Coarse band for orientation; the exact X/Y values below decide the result."))
    # ---- geometry
    if node:
        fields.append(_cmp_num("X position", node["x"], el["x"] if el else None, 1.5))
        fields.append(_cmp_num("Y position", node["y"], el["y"] if el else None, 1.5))
        fields.append(_cmp_num("Width", node["w"], el["w"] if el else None, 1.5))
        fields.append(_cmp_num("Height", node["h"], el["h"] if el else None, 1.5))
    elif el:
        fields.append(_row("Position / size", NV, _box_text(el["x"], el["y"], el["w"], el["h"]), NV, "", "The design source has no layer for this element."))
    if region and not node:
        inferred = True
        fields.append(_row("Region (inferred from the design image)", _box_text(region["x"], region["y"], region["w"], region["h"]), _box_text(region["x"], region["y"], region["w"], region["h"]), INFO, "inferred from pixels"))
    # ---- colours / border / radius / shadow / opacity
    if node and el:
        if (node.get("text") or {}).get("chars") is not None or node.get("type") == "TEXT":
            t = node.get("text") or {}
            fields.append(_cmp_colour("Text colour", t.get("color"), el["style"].get("color"), source="Figma text fill"))
        else:
            fields.append(_cmp_colour("Background colour", node.get("bg"), el.get("bg") or el["style"].get("backgroundColor"), source="Figma fill"))
        b = node.get("border")
        bw, bc = _border(el)
        if b:
            fields.append(_cmp_num("Border width", b.get("width"), bw, 0.5))
            fields.append(_cmp_colour("Border colour", b.get("color"), bc or ""))
        else:
            fields.append(_row("Border", "no stroke in the design", f"{bw:g}px" if bw else "none", MATCH if not bw else INFO, "Figma strokes", "" if not bw else "The page draws a border the design has no stroke for; confirm with the designer if it matters."))
        r_exp, r_act = node.get("radius"), _radius(el)
        if r_exp is not None:
            exp0 = r_exp if not isinstance(r_exp, list) else r_exp[0]
            fields.append(_cmp_num("Corner radius", exp0, (r_act or [None])[0], 0.5))
        else:
            fields.append(_row("Corner radius", NV, f"{(r_act or [0])[0]:g}px" if r_act else "—", NV, "", "The design has no corner radius for this layer."))
        if node.get("shadow"):
            sh = node["shadow"][0]
            fields.append(_row("Shadow", f"y {sh.get('y', 0):g}px, blur {sh.get('blur', 0):g}px {sh.get('color', '')}", el["style"].get("boxShadow") or "none", INFO, "Figma effects"))
        if node.get("opacity") is not None:
            fields.append(_cmp_num("Opacity", node["opacity"], el["style"].get("opacity"), 0.02, "", "Figma"))
        # ---- typography
        t = node.get("text") or {}
        if node.get("type") == "TEXT" and t:
            fam_act = (el["style"].get("fontFamily") or "").split(",")[0].strip().strip("\"'")
            fields.append(_row("Font family", t.get("fontFamily") or NV, fam_act or "—", (MATCH if (t.get("fontFamily") or "").lower() == fam_act.lower() else DIFFERS) if t.get("fontFamily") else NV, "Figma text style"))
            fields.append(_cmp_num("Font size", t.get("fontSize"), el["style"].get("fontSize"), 0.5))
            fields.append(_cmp_num("Font weight", t.get("fontWeight"), el["style"].get("fontWeight"), 0, ""))
            if t.get("lineHeightPx") is not None:
                fields.append(_cmp_num("Line height", t.get("lineHeightPx"), el["style"].get("lineHeight"), 1.0))
            if t.get("align"):
                act_align = (el["style"].get("textAlign") or "").lower().replace("start", "left").replace("end", "right")
                fields.append(_row("Text alignment", t["align"], act_align or "—", MATCH if t["align"] == act_align else DIFFERS, "Figma text style"))
            fields.append(_row("Text", t.get("chars") or "—", el.get("text") or el.get("fullText") or "—", MATCH if (t.get("chars") or "").strip() == (el.get("text") or el.get("fullText") or "").strip() else DIFFERS, "Figma text content"))
        # ---- spacing
        lay = node.get("layout")
        if lay:
            p_exp, p_act = lay.get("padding") or [], _pad(el)
            names = ("top", "right", "bottom", "left")
            ok = all(p_exp[i] is not None and p_act[i] is not None and abs(p_exp[i] - p_act[i]) <= 1 for i in range(4)) if len(p_exp) == 4 else None
            fields.append(_row("Padding (top/right/bottom/left)", " / ".join(f"{v:g}" for v in p_exp) + "px" if p_exp else NV, " / ".join(f"{v:g}" if v is not None else "?" for v in p_act) + "px", (MATCH if ok else DIFFERS) if ok is not None else NV, "Figma auto-layout"))
            if lay.get("gap") is not None:
                fields.append(_cmp_num("Gap between children", lay["gap"], _num(el["style"].get("gap")) if el["style"].get("gap") not in ("normal", None) else None, 1.0))
        else:
            fields.append(_row("Padding", NV, " / ".join(f"{v:g}" if v is not None else "?" for v in _pad(el)) + "px", NV, "", "The design layer has no auto-layout padding."))
        fields.append(_row("Margin", NV, " / ".join(f"{_num(el['style'].get(k)) or 0:g}" for k in ("marginTop", "marginRight", "marginBottom", "marginLeft")) + "px", NV, "", "Figma has no margin property; distances between layers are checked as positions and gaps."))
    elif el:
        s = el["style"]
        for label, val in (("Background colour", hexof(el.get("bg") or s.get("backgroundColor"))), ("Text colour", hexof(s.get("color"))), ("Font size", _px(s.get("fontSize"))), ("Font weight", s.get("fontWeight")),
                           ("Font family", (s.get("fontFamily") or "").split(",")[0].strip().strip("\"'"))):
            fields.append(_row(label, NV, val or "—", NV, "", "The design source provides no style data for this element."))
    # ---- extra measured facts that explain the check but are not design values
    if check and check.get("dom_measured"):
        fields.append(_row("Rule checked on the page", "no violation", check.get("actual") or check.get("label") or "violation", DIFFERS if check["status"] == "Fail" else MATCH, "website rule"))
    if color_sample:
        inferred = True
        fields.append(_row("Colour sampled from the design image", color_sample["expected"], color_sample["actual"], DIFFERS, "inferred from pixels", "Sampled from the picture, not read from design data."))
    selector, locator_kind = locator_of(el) if el else ("", "")
    focus = PROP_FIELDS.get((check or {}).get("prop", ""))
    for f in fields:
        f["focus"] = bool(focus and f["field"] in focus)
    differing = [f for f in fields if f["status"] == DIFFERS]
    also = []
    if any(f["focus"] for f in differing):       # the result is about these properties; other mismatches are listed separately
        also = [f for f in differing if not f["focus"]]
        differing = [f for f in differing if f["focus"]]
    spec = {"element": name_exp or name_act or (check or {}).get("component", ""), "screen": screen, "page_url": ctx.get("page_url", ""), "location": loc_a or loc_d, "design_location": loc_d,
            "website_location": loc_a, "position": pos_a or pos_d, "locator": selector, "locator_kind": locator_kind, "fields": fields, "differences": differing, "also_differs": also,
            "unsupported": [f["field"] for f in fields if f["status"] == NV], "inferred": inferred, "design_source": ctx.get("design_source", "")}
    spec["suggested_fix"] = suggest_fix(spec, check)
    return spec


PROP_FIELDS = {"position": ("X position", "Y position"), "size": ("Width", "Height"), "background": ("Background colour",), "border_width": ("Border width", "Border"), "border_color": ("Border colour",),
               "border_radius": ("Corner radius",), "shadow": ("Shadow",), "opacity": ("Opacity",), "font_family": ("Font family",), "font_size": ("Font size",), "font_weight": ("Font weight",),
               "line_height": ("Line height",), "text_color": ("Text colour",), "text_align": ("Text alignment",), "text_content": ("Text",), "placeholder": ("Text",), "missing": ("Element",),
               "missing_item": ("Element",), "gap_x": ("Gap between children",), "gap_y": ("Gap between children",)}
CSS_PROP = {"Background colour": "background-color", "Text colour": "color", "Font size": "font-size", "Font weight": "font-weight", "Font family": "font-family", "Line height": "line-height",
            "Text alignment": "text-align", "Corner radius": "border-radius", "Border width": "border-width", "Border colour": "border-color", "Opacity": "opacity", "Width": "width", "Height": "height"}


def suggest_fix(spec: dict, check: dict | None) -> str:
    """Only from measured differences. Empty when there is no confirmed difference to act on."""
    diffs = spec["differences"]
    focused = [d for d in diffs if d.get("focus")]
    if focused:
        diffs = focused
    sel = spec.get("locator") or spec.get("element") or "the element"
    if not diffs:
        return ""
    lines = []
    pos = [d for d in diffs if d["field"] in ("X position", "Y position")]
    if pos:
        lines.append(f"Align {sel} with the design: " + ", ".join(f"{d['field'].lower()} should be {d['expected']} (currently {d['actual']})" for d in pos)
                     + "; adjust the parent container's layout/margins rather than hard-coding coordinates.")
        diffs = [d for d in diffs if d not in pos]
    for d in diffs:
        prop = CSS_PROP.get(d["field"])
        if prop:
            lines.append(f"Set {prop} to {d['expected']} on {sel} (currently {d['actual']}).")
        elif d["field"] == "Text":
            lines.append(f"Change the text of {sel} to “{d['expected']}” (currently “{d['actual']}”).")
        elif d["field"].startswith("Padding"):
            lines.append(f"Set padding on {sel} to {d['expected']} (currently {d['actual']}).")
        elif d["field"] == "Element":
            lines.append(f"Add the element “{d['expected']}” described in the design; it was not found on the page.")
        elif d["field"] == "Rule checked on the page":
            lines.append(f"Fix the page so that {sel} satisfies the rule: {d['actual']}.")
        else:
            lines.append(f"Align {d['field'].lower()} of {sel} with the design: expected {d['expected']}, currently {d['actual']}.")
    return " ".join(lines[:6]) + " (Suggestion only, derived from the measured values; QA AI Studio does not change application code.)"
