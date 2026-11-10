"""Structural comparison: Figma properties against browser computed styles and geometry.

Every check records the expected value WITH its provenance (the Figma property path it came from) and the actual
value read from the browser. A property that the design source did not provide, or that the browser cannot expose
(e.g. ``::placeholder`` styling), is recorded as ``Not Verifiable`` with a reason - it is never compared against an
invented default.

A check is a dict:
    key, category, prop, label, status (Pass|Fail|Not Verifiable), component, component_type,
    expected, actual, delta, unit, tolerance, prov, reason, match_confidence, match_status,
    design {id,name,box}, dom {index,selector,tag,role,box}
"""
from __future__ import annotations

import re
import statistics
from dataclasses import dataclass, field

from .matching import CONTROL_ROLES, design_text, norm_text, text_similarity
from .util import color_distance, parse_color, to_hex

CATEGORIES = ["layout", "css", "typography", "forms", "buttons", "navigation", "tables", "icons_images", "responsive", "accessibility", "states", "visual"]


@dataclass
class Tolerances:
    position_px: float = 2.0
    size_px: float = 2.0
    gap_px: float = 2.0
    align_px: float = 1.5
    color_distance: float = 10.0
    font_size_px: float = 0.5
    line_height_px: float = 1.5
    letter_spacing_px: float = 0.3
    radius_px: float = 1.0
    border_px: float = 0.5
    opacity: float = 0.03
    shadow_px: float = 1.5
    icon_diff_ratio: float = 0.25
    viewport_px: float = 16.0       # how far the test viewport may differ from the design frame width before a mismatch is reported

    # Upper limits: an absurd tolerance would make every check pass and hide real defects, so values are clamped, never trusted.
    LIMITS = {"position_px": 100, "size_px": 100, "gap_px": 100, "align_px": 50, "color_distance": 100, "font_size_px": 10, "line_height_px": 50,
              "letter_spacing_px": 10, "radius_px": 50, "border_px": 10, "opacity": 0.5, "shadow_px": 50, "icon_diff_ratio": 0.9, "viewport_px": 400}

    @classmethod
    def problems(cls, data: dict | None) -> list:
        """Human-readable problems with a tolerance payload (unknown key, not a number, negative, above the allowed maximum).
        ``from_dict`` silently clamps; the one-click workflow uses this to refuse instead, so a typo cannot quietly loosen a check."""
        out = []
        for key, value in (data or {}).items():
            if key not in cls.LIMITS:
                out.append(f"'{key}' is not a tolerance this version supports.")
            elif isinstance(value, bool) or not isinstance(value, (int, float)) or value != value:
                out.append(f"{key.replace('_', ' ')} must be a number.")
            elif value < 0:
                out.append(f"{key.replace('_', ' ')} cannot be negative.")
            elif value > cls.LIMITS[key]:
                out.append(f"{key.replace('_', ' ')} cannot be more than {cls.LIMITS[key]:g}.")
        return out

    @classmethod
    def from_dict(cls, data: dict | None):
        base = cls()
        for key, value in (data or {}).items():
            if hasattr(base, key) and isinstance(value, (int, float)) and not isinstance(value, bool) and value == value and value >= 0:
                setattr(base, key, float(min(value, cls.LIMITS.get(key, 100))))
        return base


def px(value):
    try:
        text = str(value).strip()
        if text in ("", "normal", "auto", "none"):
            return None
        return float(text[:-2]) if text.endswith("px") else float(text)
    except (TypeError, ValueError):
        return None


def first_family(stack) -> str:
    first = str(stack or "").split(",")[0].strip().strip("\"'")
    return first.lower()


def weight_of(value):
    text = str(value or "").strip().lower()
    return {"normal": 400, "bold": 700}.get(text) or (int(float(text)) if re.match(r"^\d+(\.\d+)?$", text) else None)


def _align(value):
    v = str(value or "").lower()
    return {"start": "left", "left": "left", "end": "right", "right": "right", "center": "center", "justify": "justified", "justified": "justified"}.get(v)


def _box(o):
    return {"x": round(o["x"], 1), "y": round(o["y"], 1), "w": round(o["w"], 1), "h": round(o["h"], 1)}


def component_type(d: dict, e: dict | None) -> str:
    role = d.get("role", "")
    if role in ("button", "input", "select", "textarea", "checkbox", "radio", "toggle", "table", "icon", "image", "label", "link", "tab"):
        return role
    if role == "nav":
        return "navigation"
    if e:
        r = e.get("role", "")
        if r in ("button", "textbox", "combobox", "checkbox", "radio", "table", "img", "navigation", "link", "heading"):
            return {"textbox": "input", "combobox": "select", "img": "image", "navigation": "navigation"}.get(r, r)
    return role or ("text" if d.get("type") == "TEXT" else "container")


class Collector:
    def __init__(self, frame: dict, tol: Tolerances, enabled: set, dom_by_i: dict):
        self.frame, self.tol, self.enabled, self.dom_by_i = frame, tol, enabled, dom_by_i
        self.checks: list = []

    def on(self, category) -> bool:
        return category in self.enabled

    def add(self, category, prop, label, d, e, status, expected=None, actual=None, delta=None, unit="", tolerance=None, prov="", reason="",
            match=None, component=None, box_design=None, box_dom=None, extra=None):
        if not self.on(category):
            return None
        comp = component or (d.get("name") if d else (e.get("selector") if e else ""))
        ref = d["path"] if d else (e["selector"] if e else comp)
        check = {
            "key": f"{category}|{prop}|{ref}", "category": category, "prop": prop, "label": label, "status": status,
            "component": comp, "component_type": component_type(d, e) if d else (e.get("role") or e.get("tag") if e else ""),
            "expected": expected, "actual": actual, "delta": delta, "unit": unit, "tolerance": tolerance, "prov": prov, "reason": reason,
            "match_confidence": (match or {}).get("score"), "match_status": (match or {}).get("status", ""),
            "design": ({"id": d["id"], "name": d["name"], "path": d.get("path", ""), "box": box_design or _box(d)} if d else None),
            "dom": ({"index": e["i"], "selector": e["selector"], "tag": e["tag"], "role": e.get("role", ""), "box": box_dom or _box(e)} if e else None),
        }
        if extra:
            check.update(extra)
        self.checks.append(check)
        return check

    def numeric(self, category, prop, label, d, e, expected, actual, tol, unit="px", prov="", match=None, nv_reason="", **kw):
        if expected is None:
            return self.add(category, prop, label, d, e, "Not Verifiable", None, actual, unit=unit, prov=prov,
                            reason=nv_reason or "The design source did not provide this property.", match=match, **kw)
        if actual is None:
            return self.add(category, prop, label, d, e, "Not Verifiable", expected, None, unit=unit, prov=prov,
                            reason="The browser did not expose a measurable value for this property.", match=match, **kw)
        delta = round(actual - expected, 2)
        return self.add(category, prop, label, d, e, "Pass" if abs(delta) <= tol else "Fail", expected, actual, delta, unit, tol, prov, match=match, **kw)

    def color(self, category, prop, label, d, e, expected, actual, prov="", match=None, **kw):
        if not expected:
            return self.add(category, prop, label, d, e, "Not Verifiable", None, actual, prov=prov,
                            reason="The design source did not provide a solid colour for this property.", match=match, **kw)
        dist = color_distance(expected, actual)
        status = "Pass" if dist <= self.tol.color_distance else "Fail"
        return self.add(category, prop, label, d, e, status, to_hex(expected), to_hex(actual) or str(actual), round(dist, 1) if dist != float("inf") else None,
                        "colour distance", self.tol.color_distance, prov, match=match, **kw)


def estimate_offset(pairs, by_d, by_e, tol: Tolerances):
    """A uniform shift of the whole page (centred layout, scrollbar, body margin) is reported once, not per element."""
    dxs, dys = [], []
    for p in pairs:
        d, e = by_d[p["design_id"]], by_e[p["dom_index"]]
        if d.get("type") == "TEXT" or p["score"] < 0.8:
            continue
        dxs.append(e["x"] - d["x"])
        dys.append(e["y"] - d["y"])
    if len(dxs) < 6:
        return 0.0, 0.0, None
    mx, my = statistics.median(dxs), statistics.median(dys)
    agree = sum(1 for a, b in zip(dxs, dys) if abs(a - mx) <= tol.position_px and abs(b - my) <= tol.position_px) / len(dxs)
    if agree >= 0.6 and (abs(mx) > tol.position_px or abs(my) > tol.position_px):
        return mx, my, {"dx": round(mx, 1), "dy": round(my, 1), "agreeing_elements": round(agree, 2)}
    return 0.0, 0.0, None


def _parent_path(path: str) -> str:
    return path.rsplit("/", 1)[0]


def _shadow_of(style: str):
    """First outer shadow of a computed box-shadow string -> (x, y, blur, spread, rgba) or None."""
    if not style or style == "none":
        return None
    m = re.match(r"\s*(rgba?\([^)]*\)|#[0-9a-fA-F]+)\s+(-?[\d.]+)px\s+(-?[\d.]+)px\s+(-?[\d.]+)px(?:\s+(-?[\d.]+)px)?", style)
    if not m:
        return None
    return float(m.group(2)), float(m.group(3)), float(m.group(4)), float(m.group(5) or 0), m.group(1)


def compare(frame: dict, dom_elements: list, match: dict, enabled: set | None = None, tol: Tolerances | None = None,
            fonts_available: dict | None = None, icon_checker=None) -> dict:
    """Run the structural checks for one mapped screen. Returns {checks, offset, notes}."""
    tol = tol or Tolerances()
    enabled = set(enabled or CATEGORIES)
    nodes = frame["nodes"]
    by_d = {n["id"]: n for n in nodes}
    by_e = {e["i"]: e for e in dom_elements}
    C = Collector(frame, tol, enabled, by_e)
    pairs, aliases = match["pairs"], match["aliases"]
    pair_of = {p["design_id"]: p for p in pairs}
    ox, oy, offset = estimate_offset(pairs, by_d, by_e, tol)
    notes = []
    if offset:
        notes.append(f"The page content is offset by ({offset['dx']}px, {offset['dy']}px) relative to the design for {int(offset['agreeing_elements'] * 100)}% of elements; "
                     "the offset was removed before comparing positions and is reported once as a page-level difference.")

    def shifted(e):
        s = dict(e)
        s["x"], s["y"] = e["x"] - ox, e["y"] - oy
        return s

    # ---------------------------------------------------------------- per matched pair
    for p in pairs:
        d, e0 = by_d[p["design_id"]], by_e[p["dom_index"]]
        e = shifted(e0)
        style = e0["style"]
        is_text = d.get("type") == "TEXT"
        geo_ok = p["signals"].get("geometry", 1) >= 0.5 or p["status"] == "Approved"

        # ---- layout: position and size (one check each, reported with both axes)
        padded = is_text and (e["w"] > d["w"] + 2 * tol.size_px or e["h"] > d["h"] + 2 * tol.size_px)   # text layer sits inside a padded element
        if geo_ok and not padded:
            dx, dy = round(e["x"] - d["x"], 2), round(e["y"] - d["y"], 2)
            worst = max(abs(dx), abs(dy))
            C.add("layout", "position", "Position", d, e0, "Pass" if worst <= tol.position_px else "Fail", f"x {d['x']:g}, y {d['y']:g}", f"x {e['x']:g}, y {e['y']:g}",
                  worst, "px", tol.position_px, "absoluteBoundingBox", match=p, box_design=_box(d), box_dom=_box(e), extra={"dx": dx, "dy": dy})
            if not is_text or (d["w"] > 0 and abs(e["w"] - d["w"]) / max(d["w"], 1) <= 0.35):
                ew, eh, scroll_note = e["w"], e["h"], ""
                sc = e0.get("scroller")
                if sc and abs(sc["w"] - d["w"]) < abs(ew - d["w"]):
                    # the element scrolls horizontally inside a wrapper: the designed width is the visible (wrapper) width, not the scrolled content width
                    scroll_note = f" (visible width; content scrolls, {ew:g} wide)"
                    ew, eh = sc["w"], max(eh, sc["h"]) if abs(max(eh, sc["h"]) - d["h"]) < abs(eh - d["h"]) else eh
                dw, dh = round(ew - d["w"], 2), round(eh - d["h"], 2)
                tw = max(tol.size_px, d["w"] * 0.06) if is_text else tol.size_px
                C.add("layout", "size", "Size", d, e0, "Pass" if (abs(dw) <= tw and (is_text or abs(dh) <= tol.size_px)) else "Fail", f"{d['w']:g} × {d['h']:g}", f"{ew:g} × {eh:g}{scroll_note}",
                      max(abs(dw), abs(dh)), "px", tw, "absoluteBoundingBox", match=p, box_design=_box(d), box_dom=_box(e), extra={"dw": dw, "dh": dh})

        # ---- css: box styling (not for pure text layers, which carry typography)
        if not is_text and d.get("type") != "VECTOR":
            bg = d.get("bg")
            own = parse_color(style.get("backgroundColor"))
            actual_bg = style.get("backgroundColor") if own and own[3] > 0 else e0.get("bg")
            if d.get("bg_kind"):
                C.add("css", "background", "Background", d, e0, "Not Verifiable", reason=f"The design uses a {d['bg_kind']} fill, which is not compared as a flat colour.", match=p)
            elif bg:
                C.color("css", "background", "Background colour", d, e0, bg, actual_bg, prov=d["prov"].get("bg", ""), match=p)
            elif own and own[3] > 0.05 and p["score"] >= 0.8 and d.get("depth", 0) > 0 and d.get("role") not in ("text",):
                C.add("css", "background", "Background colour", d, e0, "Fail", "No fill", to_hex(own), None, prov="fills (empty)", match=p,
                      reason="The design layer has no fill but the element has a background colour.")
            border = d.get("border")
            bw = [px(style.get(f"border{s}Width")) or 0 for s in ("Top", "Right", "Bottom", "Left")]
            bstyle = style.get("borderTopStyle", "none")
            actual_bw = max(bw) if bstyle not in ("none", "hidden") else 0
            if border:
                C.numeric("css", "border_width", "Border width", d, e0, float(border["width"]), actual_bw, tol.border_px, prov=d["prov"].get("border", ""), match=p)
                if actual_bw > 0:
                    C.color("css", "border_color", "Border colour", d, e0, border["color"], style.get("borderTopColor"), prov=d["prov"].get("border", ""), match=p)
            elif actual_bw >= 1 and p["score"] >= 0.8 and d.get("type") != "TEXT" and not e0.get("role") in ("button",) :
                C.add("css", "border_width", "Border width", d, e0, "Fail", "No border", f"{actual_bw:g}px", actual_bw, "px", tol.border_px, "strokes (empty)", match=p,
                      reason="The design layer has no stroke but the element draws a border.")
            radius = d.get("radius")
            actual_r = [px(style.get(k)) for k in ("borderTopLeftRadius", "borderTopRightRadius", "borderBottomRightRadius", "borderBottomLeftRadius")]
            if any(v is None for v in actual_r):
                pass
            else:
                if radius is None and (d["w"] > 0 and (d.get("bg") or d.get("border"))) and p["score"] >= 0.8:
                    exp_r = [0.0] * 4
                    prov = "cornerRadius absent (Figma omits zero radius)"
                elif radius is None:
                    exp_r = None
                    prov = ""
                else:
                    exp_r = [float(v) for v in (radius if isinstance(radius, list) else [radius] * 4)]
                    prov = d["prov"].get("radius", "")
                if exp_r is not None:
                    worst = max(abs(a - b) for a, b in zip(exp_r, actual_r))
                    capped = min(d["w"], d["h"]) / 2
                    if all(a >= capped - 0.5 for a in actual_r) and all(b >= capped - 0.5 for b in exp_r):
                        worst = 0.0
                    C.add("css", "border_radius", "Border radius", d, e0, "Pass" if worst <= tol.radius_px else "Fail",
                          f"{exp_r[0]:g}px" if len(set(exp_r)) == 1 else "/".join(f"{v:g}" for v in exp_r),
                          f"{actual_r[0]:g}px" if len(set(actual_r)) == 1 else "/".join(f"{v:g}" for v in actual_r),
                          round(max(actual_r) - max(exp_r), 2), "px", tol.radius_px, prov, match=p)
            shadows = d.get("shadow")
            actual_shadow = _shadow_of(style.get("boxShadow"))
            if shadows:
                outer = [s for s in shadows if s["type"] == "DROP_SHADOW"]
                if outer and actual_shadow is None:
                    C.add("css", "shadow", "Shadow", d, e0, "Fail", f"drop shadow blur {outer[0]['blur']:g}px", "none", prov=d["prov"].get("shadow", ""), match=p,
                          reason="The design has a drop shadow but the element has none.")
                elif outer and actual_shadow:
                    dy, blur = actual_shadow[1] - outer[0]["y"], actual_shadow[2] - outer[0]["blur"]
                    ok = abs(dy) <= tol.shadow_px and abs(blur) <= tol.shadow_px * 2
                    C.add("css", "shadow", "Shadow", d, e0, "Pass" if ok else "Fail", f"y {outer[0]['y']:g}px, blur {outer[0]['blur']:g}px",
                          f"y {actual_shadow[1]:g}px, blur {actual_shadow[2]:g}px", round(max(abs(dy), abs(blur)), 2), "px", tol.shadow_px, d["prov"].get("shadow", ""), match=p)
            if d.get("opacity") is not None:
                actual_o = px(style.get("opacity"))
                C.numeric("css", "opacity", "Opacity", d, e0, float(d["opacity"]), actual_o, tol.opacity, unit="", prov=d["prov"].get("opacity", ""), match=p)

    # ---------------------------------------------------------------- typography (matched TEXT layers + aliased TEXT layers)
    typed = [(by_d[p["design_id"]], by_e[p["dom_index"]], p, None) for p in pairs if by_d[p["design_id"]].get("type") == "TEXT"]
    typed += [(by_d[a["design_id"]], by_e[a["dom_index"]], a, a) for a in aliases if a["kind"] in ("content",)]
    placeholders = [(by_d[a["design_id"]], by_e[a["dom_index"]], a) for a in aliases if a["kind"] in ("placeholder", "selected-text")]
    for d, e0, p, alias in typed:
        t = d.get("text") or {}
        style = e0["style"]
        prov = d["prov"]
        fam = first_family(style.get("fontFamily"))
        if t.get("fontFamily"):
            ok = first_family(t["fontFamily"]) == fam
            C.add("typography", "font_family", "Font family", d, e0, "Pass" if ok else "Fail", t["fontFamily"], style.get("fontFamily", "").split(",")[0].strip("\"' "),
                  prov=prov.get("text.fontFamily", ""), match=p)
            if ok and fonts_available is not None and not fonts_available.get(style.get("fontFamily"), True):
                C.add("typography", "font_installed", "Font installed in test browser", d, e0, "Not Verifiable", t["fontFamily"], "fallback font rendered",
                      reason=f"'{t['fontFamily']}' is declared but not installed in the test browser, so rendered glyphs and text widths come from a fallback font.", match=p)
        else:
            C.add("typography", "font_family", "Font family", d, e0, "Not Verifiable", None, style.get("fontFamily"), reason="The design source did not provide a font family.", match=p)
        C.numeric("typography", "font_size", "Font size", d, e0, t.get("fontSize"), px(style.get("fontSize")), tol.font_size_px, prov=prov.get("text.fontSize", ""), match=p)
        exp_w = weight_of(t.get("fontWeight"))
        C.numeric("typography", "font_weight", "Font weight", d, e0, exp_w, weight_of(style.get("fontWeight")), 0, unit="", prov=prov.get("text.fontWeight", ""), match=p)
        lh = px(style.get("lineHeight"))
        boxed = alias is not None or e0["h"] > d["h"] + 2 * tol.size_px   # text inside a padded/fixed-height element: the element's line-height is an implementation detail
        if not boxed:
            C.numeric("typography", "line_height", "Line height", d, e0, t.get("lineHeightPx"), lh, tol.line_height_px, prov=prov.get("text.lineHeightPx", ""), match=p,
                      nv_reason="The design source did not provide a line height.")
        ls = px(style.get("letterSpacing")) or 0.0
        if t.get("letterSpacing") is not None:
            C.numeric("typography", "letter_spacing", "Letter spacing", d, e0, float(t["letterSpacing"]), ls, tol.letter_spacing_px, prov=prov.get("text.letterSpacing", ""), match=p)
        C.color("typography", "text_color", "Text colour", d, e0, t.get("color"), style.get("color"), prov=prov.get("text.color", ""), match=p)
        if t.get("align"):
            actual_align = _align(style.get("textAlign"))
            C.add("typography", "text_align", "Text alignment", d, e0, "Pass" if actual_align == t["align"] else "Fail", t["align"], actual_align, prov=prov.get("text.align", ""), match=p)
        # wording
        chars = t.get("chars", "")
        actual_texts = [x for x in (e0.get("text"), e0.get("fullText"), e0.get("name")) if x]
        if chars and actual_texts:
            sim = max(text_similarity(chars, x) for x in actual_texts)
            ctype = component_type(d, e0)
            cat = "buttons" if ctype == "button" and alias else ("navigation" if ctype == "navigation" else "typography")
            C.add(cat, "text_content", "Text content", d, e0, "Pass" if sim >= 0.99 else "Fail", chars, actual_texts[0], prov="characters", match=p,
                  extra={"similarity": sim})
        if lh and t.get("lineHeightPx") and e0.get("text") and not boxed and d["h"] > 0:
            exp_lines, act_lines = max(1, round(d["h"] / t["lineHeightPx"])), e0.get("lines", 0)
            if act_lines and exp_lines != act_lines:
                C.add("typography", "line_count", "Number of text lines", d, e0, "Fail", exp_lines, act_lines, act_lines - exp_lines, "lines", prov="absoluteBoundingBox.height / lineHeightPx", match=p)
    for d, e0, p in placeholders:
        chars = (d.get("text") or {}).get("chars", "")
        actual = (e0.get("attrs") or {}).get("placeholder") or e0.get("selectedText") or ""
        C.add("forms", "placeholder", "Placeholder / default text", d, e0, "Pass" if norm_text(chars) == norm_text(actual) else "Fail", chars, actual, prov="characters", match=p)
        C.add("typography", "placeholder_style", "Placeholder styling", d, e0, "Not Verifiable", reason="Placeholder styling (::placeholder) cannot be read from computed styles of the field.", match=p,
              component=d["name"])

    # ---------------------------------------------------------------- relationships: spacing (and alignment wording)
    live = []
    for p in pairs:
        d, e0 = by_d[p["design_id"]], by_e[p["dom_index"]]
        e = shifted(e0)
        if p["status"] == "Ambiguous" or p["signals"].get("geometry", 1) < 0.5:
            continue
        if d.get("type") == "TEXT" and (e["w"] > d["w"] + 2 * tol.size_px or e["h"] > d["h"] + 2 * tol.size_px):
            continue     # text inside a padded cell: spacing is a property of the cell, covered by table checks
        live.append((d, e, e0, p))
    groups: dict = {}
    for item in live:
        groups.setdefault(_parent_path(item[0]["path"]), []).append(item)
    own = {}      # design id -> (dx, dy) of the element's own position difference
    for c in C.checks:
        if c["prop"] == "position" and c["design"]:
            own[c["design"]["id"]] = (c.get("dx", 0.0), c.get("dy", 0.0))
    if C.on("layout"):
        for parent, items in groups.items():
            ordered = sorted(items, key=lambda t: (t[0]["y"], t[0]["x"]))
            for axis in ("v", "h"):
                for a in ordered:
                    best = None
                    for b in ordered:
                        if b is a:
                            continue
                        if axis == "v":
                            overlap = min(a[0]["x"] + a[0]["w"], b[0]["x"] + b[0]["w"]) - max(a[0]["x"], b[0]["x"])
                            gap = b[0]["y"] - (a[0]["y"] + a[0]["h"])
                            ok = overlap >= 0.5 * min(a[0]["w"], b[0]["w"]) and gap >= -0.5
                        else:
                            overlap = min(a[0]["y"] + a[0]["h"], b[0]["y"] + b[0]["h"]) - max(a[0]["y"], b[0]["y"])
                            gap = b[0]["x"] - (a[0]["x"] + a[0]["w"])
                            ok = overlap >= 0.5 * min(a[0]["h"], b[0]["h"]) and gap >= -0.5
                        if ok and gap <= 120 and (best is None or gap < best[0]):
                            best = (gap, b)
                    if not best:
                        continue
                    gap, b = best
                    actual_gap = (b[1]["y"] - (a[1]["y"] + a[1]["h"])) if axis == "v" else (b[1]["x"] - (a[1]["x"] + a[1]["w"]))
                    delta = round(actual_gap - gap, 2)
                    ca = C.add("layout", f"gap_{axis}", "Spacing between elements", a[0], a[2], "Pass" if abs(delta) <= tol.gap_px else "Fail",
                               f"{gap:g}px {'below' if axis == 'v' else 'after'} {a[0]['name']}", f"{actual_gap:g}px", delta, "px", tol.gap_px,
                               "absoluteBoundingBox (distance between layers)", match=b[3], component=f"{a[0]['name']} → {b[0]['name']}",
                               box_design=_box(b[0]), box_dom=_box(b[1]))
                    if ca and ca["status"] == "Fail":
                        idx = 1 if axis == "v" else 0
                        da, db = own.get(a[0]["id"], (0.0, 0.0))[idx], own.get(b[0]["id"], (0.0, 0.0))[idx]
                        cause = None
                        if abs(abs(db - da) - abs(delta)) <= 2.0:
                            cause = b[0] if abs(db) >= abs(da) else a[0]
                        if cause is not None:
                            ca["status"] = "Explained"
                            ca["reason"] = f"A consequence of '{cause['name']}' being positioned differently from the design."
                            ca["explained_by"] = cause["id"]
        # position differences that break an alignment shared by siblings in the design read as "misaligned"
        for parent, items in groups.items():
            for axis, key, edge in (("y", "dy", "top edge"), ("x", "dx", "left edge")):
                for d, e, e0, p in items:
                    if d.get("type") == "TEXT":
                        continue
                    mates = [m for m in items if m[0] is not d and abs(m[0][axis] - d[axis]) <= 1.0 and m[0].get("type") != "TEXT"]
                    chk = next((c for c in C.checks if c["prop"] == "position" and c["design"] and c["design"]["id"] == d["id"]), None)
                    if mates and chk and chk["status"] == "Fail" and abs(chk.get(key, 0)) > tol.align_px:
                        mate_ok = [m for m in mates if abs(own.get(m[0]["id"], (0, 0))[0 if axis == "x" else 1]) <= tol.position_px]
                        if mate_ok:
                            chk.setdefault("aligned_with", [])
                            chk["aligned_with"] = sorted(set(chk["aligned_with"] + [m[0]["name"] for m in mate_ok][:3]))
                            chk["misaligned_edge"] = edge

    # ---------------------------------------------------------------- forms: label association and required marker
    if C.on("forms"):
        labels = [(by_d[p["design_id"]], by_e[p["dom_index"]], p) for p in pairs if by_d[p["design_id"]].get("role") == "label" or by_d[p["design_id"]]["name"].lower().startswith("label")]
        for p in pairs:
            d, e0 = by_d[p["design_id"]], by_e[p["dom_index"]]
            if d.get("role") not in ("input", "select", "textarea", "checkbox", "radio"):
                continue
            near = [l for l in labels if abs(l[0]["x"] - d["x"]) <= 2 and 0 <= d["y"] - (l[0]["y"] + l[0]["h"]) <= 40]
            if not near:
                continue
            ld, le, lp = min(near, key=lambda l: d["y"] - (l[0]["y"] + l[0]["h"]))
            expected_label = (ld.get("text") or {}).get("chars", "")
            if expected_label:
                actual_name = e0.get("name", "")
                if not actual_name:
                    C.add("forms", "label", "Field label", d, e0, "Fail", expected_label, "(no accessible label)", prov="label layer above field", match=p,
                          reason="The design shows a label but the field has no accessible label.")
                else:
                    ok = norm_text(expected_label) == norm_text(actual_name)
                    C.add("forms", "label", "Field label", d, e0, "Pass" if ok else "Fail", expected_label, actual_name, prov="label layer above field", match=p)
                wants_required = expected_label.strip().endswith("*")
                has_required = bool((e0.get("attrs") or {}).get("required")) or e0.get("aria", {}).get("aria-required") == "true"
                if wants_required or has_required:
                    C.add("forms", "required", "Required marker", d, e0, "Pass" if wants_required == has_required else "Fail",
                          "required" if wants_required else "optional", "required" if has_required else "optional", prov="label text ends with *", match=p)

    # ---------------------------------------------------------------- navigation: items and order
    if C.on("navigation"):
        nav = [(by_d[p["design_id"]], by_e[p["dom_index"]], p) for p in pairs if by_d[p["design_id"]].get("role") == "nav" and by_d[p["design_id"]].get("type") == "TEXT"]
        if len(nav) >= 2:
            design_order = [d["name"] for d, _, _ in sorted(nav, key=lambda t: (round(t[0]["y"] / 10), t[0]["x"]))]
            dom_order = [d["name"] for d, e, _ in sorted(nav, key=lambda t: (round(t[1]["y"] / 10), t[1]["x"]))]
            C.add("navigation", "order", "Navigation item order", nav[0][0], nav[0][1], "Pass" if design_order == dom_order else "Fail",
                  " → ".join(design_order), " → ".join(dom_order), prov="layer positions", match=nav[0][2], component="Navigation")
        expected_nav = [by_d[i] for i in match["unmatched_design"] if by_d[i].get("role") == "nav" and by_d[i].get("type") == "TEXT"]
        for d in expected_nav:
            C.add("navigation", "missing_item", "Navigation item present", d, None, "Fail", (d.get("text") or {}).get("chars", d["name"]), "not found", prov="characters",
                  reason="No matching navigation item was found on the page.")

    # ---------------------------------------------------------------- tables
    if C.on("tables"):
        for p in pairs:
            d, e0 = by_d[p["design_id"]], by_e[p["dom_index"]]
            if not (d.get("role") == "table" and d.get("type") != "TEXT" and e0.get("tag") == "table"):
                continue
            rows = [n for n in nodes if n["path"].startswith(d["path"] + "/") and n["depth"] == d["depth"] + 1 and n.get("type") not in ("TEXT",)]
            if not rows:
                continue
            rows.sort(key=lambda n: n["y"])
            header = rows[0]
            hcells = sorted([n for n in nodes if n["path"].startswith(header["path"] + "/") and n.get("type") == "TEXT"], key=lambda n: n["x"])
            dom_th = sorted([x for x in dom_elements if x["tag"] == "th" and x["selector"].startswith(e0["selector"])], key=lambda x: x["x"])
            dom_rows = [x for x in dom_elements if x["tag"] == "tr" and x["selector"].startswith(e0["selector"]) and "tbody" in x["selector"]]
            body_rows = rows[1:]
            C.numeric("tables", "columns", "Number of columns", d, e0, len(hcells), len(dom_th) or None, 0, unit="", prov="header row text layers", match=p)
            C.numeric("tables", "rows", "Number of rows", d, e0, len(body_rows), len(dom_rows), 0, unit="", prov="row layers", match=p)
            for cell_d, th in zip(hcells, dom_th):
                a = (cell_d.get("text") or {}).get("chars", "")
                b = th.get("text") or th.get("name") or ""
                C.add("tables", "header_text", "Column header", cell_d, th, "Pass" if norm_text(a) == norm_text(b) else "Fail", a, b, prov="characters", match=p)
            scrolls = bool(e0.get("scroller"))
            if scrolls:
                notes.append(f"{d['name']} scrolls horizontally inside a wrapper; column widths were not compared because the designed widths describe the visible area, not the scrolled content.")
            for i in range(len(hcells) - 1):
                if i < len(dom_th) and not scrolls:
                    exp_w = hcells[i + 1]["x"] - hcells[i]["x"]
                    C.numeric("tables", "column_width", f"Width of column {i + 1}", hcells[i], dom_th[i], exp_w, dom_th[i]["w"], tol.size_px + 2, prov="distance between header text layers", match=p)
            for rd, rr in zip(body_rows, dom_rows):
                C.numeric("tables", "row_height", "Row height", rd, rr, rd["h"], rr["h"], tol.size_px, prov="absoluteBoundingBox.height", match=p)

    # ---------------------------------------------------------------- consequences: children of a layer that itself differs
    def _within(child, parent, keys, tolerance):
        return all(abs(child.get(k, 0)) <= tolerance or abs(child.get(k, 0) - parent.get(k, 0)) <= 2.0 for k in keys)
    parents = {c["design"]["path"]: c for c in C.checks if c["prop"] in ("size", "position") and c["status"] == "Fail" and c.get("design")}
    for c in C.checks:
        if c["status"] != "Fail" or not c.get("design") or c["prop"] not in ("size", "position", "column_width", "row_height"):
            continue
        path = c["design"]["path"]
        for ppath, pc in parents.items():
            if ppath == path or not path.startswith(ppath + "/"):
                continue
            if c["prop"] in ("column_width", "row_height") and pc["prop"] == "size":
                ok = True
            elif c["prop"] == pc["prop"] == "size":
                ok = _within(c, pc, ("dw", "dh"), tol.size_px)
            elif c["prop"] == pc["prop"] == "position":
                ok = _within(c, pc, ("dx", "dy"), tol.position_px)
            else:
                ok = False
            if ok:
                c["status"] = "Explained"
                c["reason"] = f"A consequence of '{pc['component']}' differing from the design."
                break

    # ---------------------------------------------------------------- icons and images
    if C.on("icons_images") and icon_checker:
        for p in pairs:
            d, e0 = by_d[p["design_id"]], by_e[p["dom_index"]]
            if d.get("role") not in ("icon", "image") and d.get("type") != "VECTOR":
                continue
            e = shifted(e0)
            result = icon_checker(_box(d), _box(e))
            if result is None:
                C.add("icons_images", "appearance", "Icon / image appearance", d, e0, "Not Verifiable", reason="No design image was available to compare this region.", match=p)
                continue
            ratio = result
            C.add("icons_images", "appearance", "Icon / image appearance", d, e0, "Pass" if ratio <= tol.icon_diff_ratio else "Fail",
                  "as designed", f"{ratio * 100:.0f}% of pixels differ", round(ratio, 3), "ratio", tol.icon_diff_ratio, "design image region", match=p,
                  extra={"visual_only": True})
            if d.get("fill") is None and (d.get("prov") or {}).get("bg") is None:
                C.add("icons_images", "icon_color", "Icon colour", d, e0, "Not Verifiable", reason="Vector fill colour is not exposed by computed styles of an SVG.", match=p)

    # ---------------------------------------------------------------- design nodes without a counterpart
    missing = []
    for did in match["unmatched_design"]:
        d = by_d[did]
        if d.get("type") == "TEXT" and any(a["design_id"] == did for a in aliases):
            continue
        if d.get("type") != "TEXT" and not (d.get("role") or d.get("bg")):
            continue
        missing.append(d)
    # children of a missing container are one defect with the container
    names = {d["path"] for d in missing}
    for d in missing:
        if any(d["path"].startswith(other + "/") for other in names if other != d["path"]):
            continue
        C.add("layout", "missing", "Element present", d, None, "Fail", d["name"], "not found on the page", prov="layer present in design",
              reason="No element matching this design layer was found on the page.", extra={"missing": True,
                    "text": design_text(d, nodes), "candidates": 0})
    return {"checks": C.checks, "offset": offset, "notes": notes}


# ----------------------------------------------------------------------------- DOM-only rules (no design needed)

def dom_rules(probe: dict, viewport: tuple, enabled: set | None = None, frame_width: float | None = None) -> list:
    """Measured facts about the rendered page that are defects regardless of the design."""
    enabled = set(enabled or CATEGORIES)
    out = []
    els = probe["elements"]
    vw = viewport[0]
    if "responsive" in enabled and probe["page"]["hOverflow"]:
        offenders = [els[i] for i in probe.get("overflowIdx", [])[:6]]
        wide = max((e["x"] + e["w"] for e in els), default=vw)
        out.append({"key": f"responsive|h_overflow|{vw}", "category": "responsive", "prop": "horizontal_overflow", "label": "Horizontal overflow", "status": "Fail",
                    "component": "Page", "component_type": "page", "expected": f"content fits within {vw}px", "actual": f"content is {round(probe['page']['scrollW'] - vw)}px wider than the viewport",
                    "delta": probe["page"]["scrollW"] - vw, "unit": "px", "tolerance": 1, "prov": "", "reason": "", "match_confidence": 1.0, "match_status": "Measured",
                    "design": None, "dom": ({"index": offenders[0]["i"], "selector": offenders[0]["selector"], "tag": offenders[0]["tag"], "role": offenders[0].get("role", ""), "box": _box(offenders[0])} if offenders else None),
                    "offenders": [{"selector": o["selector"], "right_edge": round(o["x"] + o["w"])} for o in offenders], "dom_measured": True})
    if "layout" in enabled:
        for e in els:
            if e.get("clipped") or e.get("truncated"):
                if e["tag"] in ("html", "body"):
                    continue
                out.append({"key": f"layout|text_clipped|{e['selector']}", "category": "layout", "prop": "text_clipped", "label": "Content clipped or truncated",
                            "status": "Fail", "component": e.get("name") or e["selector"], "component_type": e.get("role") or e["tag"],
                            "expected": "all content visible", "actual": "content is truncated" if e.get("truncated") else "content overflows its box and is clipped",
                            "delta": None, "unit": "", "tolerance": None, "prov": "", "reason": "", "match_confidence": 1.0, "match_status": "Measured", "design": None,
                            "dom": {"index": e["i"], "selector": e["selector"], "tag": e["tag"], "role": e.get("role", ""), "box": _box(e)}, "dom_measured": True,
                            "intentional_possible": bool(e.get("truncated"))})
        controls = [e for e in els if e.get("role") in ("button", "textbox", "combobox", "checkbox", "radio", "link") and e["w"] > 0 and e["h"] > 0]
        reported = set()
        for i, a in enumerate(controls):
            for b in controls[i + 1:]:
                x0, y0 = max(a["x"], b["x"]), max(a["y"], b["y"])
                x1, y1 = min(a["x"] + a["w"], b["x"] + b["w"]), min(a["y"] + a["h"], b["y"] + b["h"])
                inter = max(0, x1 - x0) * max(0, y1 - y0)
                if inter > 0.25 * min(a["w"] * a["h"], b["w"] * b["h"]) and (a["i"], b["i"]) not in reported:
                    reported.add((a["i"], b["i"]))
                    out.append({"key": f"layout|overlap|{a['selector']}|{b['selector']}", "category": "layout", "prop": "overlap", "label": "Controls overlap",
                                "status": "Fail", "component": f"{a.get('name') or a['tag']} / {b.get('name') or b['tag']}", "component_type": "controls",
                                "expected": "controls do not overlap", "actual": f"{round(inter)}px² overlap", "delta": round(inter), "unit": "px²", "tolerance": 0,
                                "prov": "", "reason": "", "match_confidence": 1.0, "match_status": "Measured", "design": None,
                                "dom": {"index": a["i"], "selector": a["selector"], "tag": a["tag"], "role": a.get("role", ""), "box": _box(a)}, "dom_measured": True})
    if "icons_images" in enabled:
        for e in els:
            if e.get("img", {}).get("broken"):
                out.append({"key": f"icons_images|broken_image|{e['selector']}", "category": "icons_images", "prop": "broken_image", "label": "Image failed to load", "status": "Fail",
                            "component": e["attrs"].get("src", e["selector"]), "component_type": "image", "expected": "image loads", "actual": "image could not be loaded",
                            "delta": None, "unit": "", "tolerance": None, "prov": "", "reason": "", "match_confidence": 1.0, "match_status": "Measured", "design": None,
                            "dom": {"index": e["i"], "selector": e["selector"], "tag": e["tag"], "role": e.get("role", ""), "box": _box(e)}, "dom_measured": True})
    if frame_width and "responsive" in enabled and abs(frame_width - vw) > max(8, 0.02 * frame_width):
        out.append({"key": f"responsive|viewport_mismatch|{vw}", "category": "responsive", "prop": "viewport_mismatch", "label": "Viewport differs from the design frame", "status": "Not Verifiable",
                    "component": "Page", "component_type": "page", "expected": f"{round(frame_width)}px wide frame", "actual": f"{vw}px viewport", "delta": vw - frame_width, "unit": "px",
                    "tolerance": None, "prov": "", "reason": "The viewport is not the design frame's width, so layout comparison against this frame may not be meaningful.",
                    "match_confidence": 1.0, "match_status": "Measured", "design": None, "dom": None, "dom_measured": True})
    return out
