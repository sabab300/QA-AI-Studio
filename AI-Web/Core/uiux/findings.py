"""Turn raw checks into reviewable findings: classification, severity, confidence, fingerprints, grouping, regression.

Classification (the only values that may be stored):
  Confirmed Difference  - measured on BOTH sides (design property vs browser value) through a trusted mapping, or a
                          defect measured directly in the rendered page (overflow, broken image, missing accessible name...).
  Potential Difference  - evidence exists but is weaker: visual-only, an unconfirmed match, a borderline delta, an unapproved
                          mapping, or an accessibility item that needs judgement.
  Needs Investigation   - the evidence cannot decide: ambiguous match, possible intentional behaviour, fonts not installed.
  Not Verifiable        - the design source or the browser could not provide the property; never reported as a pass.

AI output is never evidence. ``source == "ai"`` findings are capped at Needs Investigation and carry no Confirmed status.
"""
from __future__ import annotations

import hashlib
import re
from collections import defaultdict

from .util import color_distance

CLASSIFICATIONS = ("Confirmed Difference", "Potential Difference", "Needs Investigation", "Not Verifiable")
SEVERITIES = ("Critical", "High", "Medium", "Low")
SEV_RANK = {"Critical": 0, "High": 1, "Medium": 2, "Low": 3}


def fingerprint(category: str, prop: str, identity: str, screen: str, viewport: str) -> str:
    base = "|".join(re.sub(r"\s+", " ", str(x or "").strip().lower()) for x in (category, prop, identity, screen, viewport))
    return hashlib.sha1(base.encode()).hexdigest()[:16]


def snapshot(finding: dict) -> str:
    return hashlib.sha1(f"{finding.get('expected')}|{finding.get('actual')}".encode()).hexdigest()[:12]


# ----------------------------------------------------------------------------- wording

def _dir(value, pos, neg):
    return pos if value > 0 else neg


def describe(c: dict) -> tuple[str, str, str]:
    comp, label, prop = c["component"], c["label"], c["prop"]
    exp, act, delta = c.get("expected"), c.get("actual"), c.get("delta")
    prov = f" (design property: {c['prov']})" if c.get("prov") else ""
    if prop == "position":
        dx, dy = c.get("dx", 0), c.get("dy", 0)
        parts = []
        if abs(dx) > 0.01:
            parts.append(f"{abs(dx):g}px {_dir(dx, 'to the right', 'to the left')}")
        if abs(dy) > 0.01:
            parts.append(f"{abs(dy):g}px {_dir(dy, 'lower', 'higher')}")
        move = " and ".join(parts)
        if c.get("aligned_with"):
            title = f"{comp} is misaligned with {', '.join(c['aligned_with'])} ({c.get('misaligned_edge', 'edge')})"
        else:
            title = f"{comp} is positioned differently from the design"
        return title, f"{comp} is {move} from where the design places it ({exp} expected, {act} actual).{prov}", f"{move}"
    if prop == "size":
        dw, dh = c.get("dw", 0), c.get("dh", 0)
        return (f"{comp} has a different size", f"Expected {exp}, actual {act} (width {dw:+g}px, height {dh:+g}px).{prov}", f"width {dw:+g}px, height {dh:+g}px")
    if prop in ("gap_v", "gap_h"):
        return (f"Spacing differs: {comp}", f"The distance is {act} but the design has {exp}.{prov}", f"{delta:+g}px")
    if prop == "missing":
        return (f"Missing from the page: {comp}", f"The design contains '{comp}' but no matching element was found on the page.", "element not found")
    if prop == "text_content":
        return (f"{comp}: text differs", f"The design text is “{exp}” but the page shows “{act}”.", f"“{exp}” → “{act}”")
    if c.get("dom_measured"):
        return (f"{label}: {comp}", f"Expected {exp}; measured {act}.", f"{act}")
    unit = c.get("unit") or ""
    diff = f"{delta:+g}{'px' if unit == 'px' else ''}" if isinstance(delta, (int, float)) else f"{exp} → {act}"
    if prop in ("background", "text_color", "border_color"):
        diff = f"{exp} → {act}"
    return (f"{comp}: {label.lower()} differs", f"Expected {exp}; actual {act}.{prov}", diff)


def suggest(c: dict) -> str:
    sel = (c.get("dom") or {}).get("selector", "")
    exp = c.get("expected")
    prop = c["prop"]
    css = {"background": f"background-color: {exp};", "text_color": f"color: {exp};", "border_color": f"border-color: {exp};", "border_width": f"border-width: {exp};",
           "border_radius": f"border-radius: {exp};", "font_size": f"font-size: {exp}px;", "font_weight": f"font-weight: {exp};", "line_height": f"line-height: {exp}px;",
           "letter_spacing": f"letter-spacing: {exp}px;", "font_family": f"font-family: {exp}, sans-serif;", "text_align": f"text-align: {exp};", "opacity": f"opacity: {exp};"}
    if prop in css and sel:
        return f"{sel} {{ {css[prop]} }}"
    if prop == "position":
        dx, dy = c.get("dx", 0), c.get("dy", 0)
        return f"Adjust the margin/padding or layout of {sel or c['component']} by {-dx:+g}px horizontally and {-dy:+g}px vertically (check the parent's flex/grid gap first)."
    if prop == "size":
        return f"Set the width/height of {sel or c['component']} to match {exp}, or check whether its content or padding changes the size."
    if prop in ("gap_v", "gap_h"):
        return f"Check margin/gap between these elements; the design spacing is {exp}."
    if prop == "missing":
        return "Add the element or confirm with design that it was removed intentionally (then update the design baseline)."
    if prop == "horizontal_overflow":
        return "Find the element that is wider than the viewport (listed in the evidence) and give it max-width: 100% or wrap it in an overflow-x: auto container."
    if prop == "contrast":
        return f"Increase the contrast of the text colour or background to reach {c.get('required', 4.5)}:1."
    if prop in ("label", "name", "icon_name"):
        return "Associate a visible <label for> or add an aria-label so the control has an accessible name."
    if prop == "alt":
        return "Add an alt attribute: descriptive text, or alt=\"\" if the image is decorative."
    if prop == "focus_visible":
        return "Restore a visible focus style, e.g. :focus-visible { outline: 2px solid <accent>; outline-offset: 2px; }."
    if prop == "text_content":
        return "Update the wording to match the design, or confirm the copy change with the content owner."
    return ""


# ----------------------------------------------------------------------------- severity

PRIMARY_NAMES = re.compile(r"\b(submit|save|login|log in|sign in|continue|next|confirm|pay|register|search)\b", re.I)


def severity_of(c: dict, ctx: dict) -> tuple[str, str]:
    prop, cat, ctype = c["prop"], c["category"], c.get("component_type", "")
    delta = abs(c["delta"]) if isinstance(c.get("delta"), (int, float)) else None
    name = c.get("component", "")
    required_field = False
    if prop == "missing":
        if ctype in ("button", "input", "select", "textarea") or PRIMARY_NAMES.search(name):
            return "Critical" if ctype in ("button", "input", "select", "textarea") else "High", "A control the user needs is missing from the page."
        return "High", "Content that exists in the design is missing from the page."
    if prop == "keyboard_trap":
        return "Critical", "Keyboard users cannot move past this element."
    if prop == "horizontal_overflow":
        return "High", "The page scrolls sideways at this viewport, hiding content."
    if prop == "overlap":
        return "High", "Interactive controls overlap, so one can be hard or impossible to use."
    if prop in ("label", "name"):
        return "High", "A control has no accessible name, so assistive technology cannot identify it."
    if prop == "contrast":
        ratio = c.get("ratio", 4.5)
        return ("High", f"Contrast {ratio}:1 is well below the minimum.") if ratio < 3 else ("Medium", f"Contrast {ratio}:1 is below the minimum.")
    if prop == "text_clipped":
        return "Medium", "Content is cut off."
    if prop in ("broken_image",):
        return "High", "An image failed to load."
    if prop in ("alt", "icon_name", "focus_visible", "lang", "title", "unreachable"):
        return "Medium", "Affects keyboard or screen-reader users."
    if prop in ("h1_missing", "h1_multiple", "heading_skip", "placeholder_label", "duplicate_id", "aria_ref", "hidden_focusable"):
        return "Low" if prop.startswith("h") or prop == "duplicate_id" else "Medium", "Structural accessibility issue."
    if prop == "text_content":
        return ("High", "Visible wording differs from the design.") if c.get("similarity", 1) < 0.5 else ("Medium", "Visible wording differs from the design.")
    if prop in ("background", "text_color", "border_color"):
        dist = delta or 0
        if ctype == "button" and dist > 25:
            return "Medium", "A button colour visibly differs from the design."
        return ("Medium", "A visible colour difference.") if dist >= 20 else ("Low", "A subtle colour difference.")
    if prop == "font_size":
        return ("High", "Text size differs by 4px or more.") if (delta or 0) >= 4 else ("Medium", "Text size differs.")
    if prop == "font_family":
        return "Medium", "A different typeface is used."
    if prop == "font_weight":
        return ("Medium", "Text weight differs noticeably.") if (delta or 0) >= 200 else ("Low", "Text weight differs slightly.")
    if prop in ("line_height", "letter_spacing", "text_align", "line_count"):
        return "Low", "A minor typographic difference."
    if prop == "border_radius":
        return ("Medium", "Corner rounding clearly differs.") if (delta or 0) >= 4 else ("Low", "A minor corner difference.")
    if prop in ("border_width", "shadow", "opacity"):
        return "Low", "A minor visual style difference."
    if prop == "position":
        d = delta or 0
        if d >= 24:
            return "High", "An element is far from its designed position."
        return ("Medium", "An element is noticeably off its designed position.") if d >= 8 else ("Low", "An element is slightly off its designed position.")
    if prop == "size":
        d = delta or 0
        return ("High", "An element is much larger/smaller than designed.") if d >= 24 else (("Medium", "An element's size noticeably differs.") if d >= 8 else ("Low", "An element's size slightly differs."))
    if prop in ("gap_v", "gap_h"):
        d = delta or 0
        return ("Medium", "Spacing noticeably differs.") if d >= 8 else ("Low", "Spacing slightly differs.")
    if prop in ("columns", "rows", "header_text", "order", "missing_item"):
        return "High", "The table or navigation structure differs from the design."
    if prop in ("column_width", "row_height"):
        return "Low", "A table dimension differs."
    if prop in ("placeholder", "label", "required"):
        return "Medium", "Form wording or required marker differs from the design."
    if prop == "appearance":
        return "Medium", "An icon or image looks different from the design."
    if prop == "visual_region":
        area = (c.get("region") or {}).get("w", 0) * (c.get("region") or {}).get("h", 0)
        return ("High", "A large screen area differs from the design.") if area > 40000 else (("Medium", "A visual difference with no structural explanation.") if area > 600 else ("Low", "A small visual difference."))
    return "Low", "A difference from the design."


# ----------------------------------------------------------------------------- classification and confidence

def classify(c: dict, ctx: dict, region: dict | None = None) -> tuple[str, float, str]:
    """Return (classification, confidence, reason)."""
    source = c.get("source", "rule")
    if c["status"] == "Not Verifiable":
        return "Not Verifiable", 0.0, c.get("reason") or "Not verifiable."
    match_score = c.get("match_confidence") or 0.0
    match_status = c.get("match_status", "")
    delta = abs(c["delta"]) if isinstance(c.get("delta"), (int, float)) else None
    tol = c.get("tolerance") or 0
    if source == "ai":
        return "Needs Investigation", min(0.5, match_score or 0.4), "Proposed by the AI assistant without deterministic evidence."
    if c.get("dom_measured"):
        if c.get("intentional_possible"):
            return "Needs Investigation", 0.6, "Truncation with an ellipsis can be intentional design."
        if c.get("a11y_level") == "review":
            return "Potential Difference", 0.6, "This accessibility item needs a human judgement."
        return "Confirmed Difference", 0.98, "Measured directly in the rendered page."
    if c.get("visual_only"):
        return "Potential Difference", 0.6, "Based on pixel comparison only; no structural property confirms it."
    if ctx.get("font_unavailable") and c["category"] == "typography" and c["prop"] in ("font_size", "line_height", "line_count", "letter_spacing"):
        return "Needs Investigation", 0.5, "The design font is not installed in the test browser, so text metrics may differ for that reason alone."
    if match_status == "Ambiguous":
        return "Needs Investigation", 0.45, "The page element could not be matched to the design layer unambiguously."
    if c["prop"] == "missing":
        if ctx.get("trusted"):
            return "Confirmed Difference", 0.85, "No element on the approved page matches this design layer."
        return "Potential Difference", 0.55, "The mapping is not approved yet, so the element may exist but not have been matched."
    if not ctx.get("trusted"):
        return "Potential Difference", min(0.7, 0.9 * (match_score or 0.5)), "The screen mapping is not approved, so differences are not confirmed."
    if match_status == "Needs confirmation":
        return "Potential Difference", min(0.7, 0.9 * match_score), "The element match is below the confirmation threshold; confirm the match to promote this."
    if delta is not None and tol and delta <= 1.5 * tol and c["prop"] not in ("columns", "rows", "text_content") and c.get("unit") in ("px", ""):
        return "Potential Difference", 0.6, "The difference is close to the tolerance and may be rounding."
    return "Confirmed Difference", round(min(0.97, 0.7 + 0.27 * (match_score or 0.9)), 2), "Design value and browser value were both measured and differ beyond the tolerance."


def _box_union(*boxes):
    boxes = [b for b in boxes if b]
    if not boxes:
        return None
    x0 = min(b["x"] for b in boxes)
    y0 = min(b["y"] for b in boxes)
    x1 = max(b["x"] + b["w"] for b in boxes)
    y1 = max(b["y"] + b["h"] for b in boxes)
    return {"x": x0, "y": y0, "w": x1 - x0, "h": y1 - y0}


def _intersects(a, b, pad=2):
    return a and b and not (a["x"] + a["w"] + pad < b["x"] or b["x"] + b["w"] + pad < a["x"] or a["y"] + a["h"] + pad < b["y"] or b["y"] + b["h"] + pad < a["y"])


# ----------------------------------------------------------------------------- grouping of missing elements

def _group_missing(checks: list) -> list:
    missing = [c for c in checks if c["prop"] == "missing" and c["status"] == "Fail" and c.get("design")]
    others = [c for c in checks if c not in missing]
    clusters = []
    for c in sorted(missing, key=lambda c: (c["design"]["box"]["y"], c["design"]["box"]["x"])):
        for cl in clusters:
            if any(abs(c["design"]["box"]["x"] - o["design"]["box"]["x"]) < 160 and -2 <= c["design"]["box"]["y"] - (o["design"]["box"]["y"] + o["design"]["box"]["h"]) <= 24 or
                   (_intersects(c["design"]["box"], o["design"]["box"], 8)) for o in cl):
                cl.append(c)
                break
        else:
            clusters.append([c])
    merged = []
    stop = {"label", "input", "helper", "text", "button", "icon", "field", "select", "textarea", "placeholder"}
    for cl in clusters:
        if len(cl) == 1:
            merged.append(cl[0])
            continue
        words = [[w for w in re.findall(r"[A-Za-z0-9]+", c["component"])] for c in cl]
        from collections import Counter
        counts = Counter(w for ws in words for w in set(ws) if w.lower() not in stop)
        common = [w for w in words[0] if counts[w] >= 2 and w.lower() not in stop] or [w for w in words[0] if w.lower() not in stop]
        base = " ".join(common) or cl[0]["component"]
        types = sorted({c.get("component_type") or "element" for c in cl})
        head = dict(cl[0])
        head["component"] = f"{base} ({len(cl)} elements)"
        head["component_type"] = "input" if any(t in ("input", "select", "textarea") for t in types) else types[0]
        head["expected"] = ", ".join(c["component"] for c in cl)
        head["design"] = {"id": cl[0]["design"]["id"], "name": head["component"], "box": _box_union(*[c["design"]["box"] for c in cl])}
        head["grouped"] = [c["component"] for c in cl]
        head["key"] = f"layout|missing|{base.lower()}"
        merged.append(head)
    return others + merged


# ----------------------------------------------------------------------------- build

def build_findings(ctx: dict, checks: list, visual: dict | None = None, extra_nv: list | None = None) -> dict:
    """ctx keys: run_id, screen, viewport, frame_id, mapping_id, trusted, design_version, build, document_version_id,
    structural_available, font_unavailable (list of families)."""
    out, nv_groups = [], defaultdict(list)
    checks = _group_missing([c for c in checks if c["status"] not in ("Pass", "Explained", "Skipped")])
    consumed_regions = set()
    regions = (visual or {}).get("regions", [])
    visual_ok = bool(visual) and not (visual.get("size", {}).get("width_mismatch") or visual.get("size", {}).get("height_mismatch"))
    for c in checks:
        if c["status"] == "Not Verifiable":
            nv_groups[(c["category"], c["prop"], c.get("reason", ""))].append(c)
            continue
        cls, conf, reason = classify(c, ctx)
        if cls == "Not Verifiable":
            continue
        sev, rationale = severity_of(c, ctx)
        title, description, difference = describe(c)
        box_d = (c.get("design") or {}).get("box")
        box_e = (c.get("dom") or {}).get("box")
        region = _box_union(box_d, box_e)
        evidence_region = None
        for n, r in enumerate(regions):
            if _intersects(r, box_d) or _intersects(r, box_e):
                consumed_regions.add(n)
                evidence_region = r if evidence_region is None else _box_union(evidence_region, r)
        screen = ctx["screen"]
        identity = (c.get("design") or {}).get("name") and ((c["design"].get("path") or c["design"]["name"])) or (c.get("dom") or {}).get("selector") or c["component"]
        fp = fingerprint(c["category"], c["prop"], identity if c["prop"] != "missing" else c["component"], screen, ctx["viewport"])
        f = {
            "fingerprint": fp, "category": c["category"], "prop": c["prop"], "component": c["component"], "component_type": c.get("component_type", ""),
            "title": title[:200], "description": description, "expected": str(c.get("expected") if c.get("expected") is not None else ""),
            "actual": str(c.get("actual") if c.get("actual") is not None else ""), "difference": difference,
            "classification": cls, "classification_reason": reason, "severity": sev, "severity_rationale": rationale, "confidence": round(conf, 2),
            "design_evidence": {"node": (c.get("design") or {}), "prov": c.get("prov"), "value": c.get("expected"), "source": "Figma structure" if ctx.get("structural_available") else "design image"},
            "browser_evidence": {"dom": c.get("dom"), "value": c.get("actual"), "delta": c.get("delta"), "unit": c.get("unit"), "tolerance": c.get("tolerance"),
                                 "match_confidence": c.get("match_confidence"), "match_status": c.get("match_status"), "grouped": c.get("grouped"), "offenders": c.get("offenders"),
                                 "wcag": c.get("wcag"), "ratio": c.get("ratio")},
            "suggestion": suggest(c), "suggestion_source": "rule" if suggest(c) else "", "verifiable": 1,
            "region": region, "visual_region": evidence_region, "screen": screen, "viewport": ctx["viewport"], "frame_id": ctx.get("frame_id", ""), "mapping_id": ctx.get("mapping_id", ""),
        }
        out.append(f)
    # pixel regions with no structural explanation
    if visual_ok:
        for n, r in enumerate(regions):
            if n in consumed_regions:
                continue
            c = {"category": "visual", "prop": "visual_region", "component": "Unmapped screen region", "component_type": "region", "status": "Fail", "visual_only": True,
                 "region": r, "expected": "as designed", "actual": f"{r['diff_pixels']} pixels differ ({int(r['ratio'] * 100)}% of the region)", "delta": r["ratio"],
                 "dom": None, "design": None, "match_confidence": None, "prov": "design image", "unit": "ratio", "tolerance": 0}
            cls, conf, reason = classify(c, ctx)
            sev, rationale = severity_of(c, ctx)
            fp = fingerprint("visual", "region", f"{r['x']//16},{r['y']//16}", ctx["screen"], ctx["viewport"])
            out.append({"fingerprint": fp, "category": "visual", "prop": "visual_region", "component": c["component"], "component_type": "region",
                        "title": f"Visual difference at ({r['x']}, {r['y']}) {r['w']}×{r['h']}px", "description": "The screenshot differs from the design image here and no structural property explains it.",
                        "expected": c["expected"], "actual": c["actual"], "difference": f"{int(r['ratio'] * 100)}% of pixels", "classification": cls, "classification_reason": reason, "severity": sev,
                        "severity_rationale": rationale, "confidence": round(conf, 2), "design_evidence": {"source": "design image", "value": "region of the design image"},
                        "browser_evidence": {"region": r, "expected_mean_rgb": r.get("expected_mean"), "actual_mean_rgb": r.get("actual_mean")},
                        "suggestion": "Compare the highlighted region with the design; check imagery, icons, background and effects.", "suggestion_source": "rule", "verifiable": 1,
                        "region": {k: r[k] for k in ("x", "y", "w", "h")}, "visual_region": r, "screen": ctx["screen"], "viewport": ctx["viewport"], "frame_id": ctx.get("frame_id", ""), "mapping_id": ctx.get("mapping_id", "")})
    # not verifiable, grouped by reason
    for (cat, prop, reason), group in nv_groups.items():
        sample = group[0]
        comps = sorted({g["component"] for g in group})
        out.append({"fingerprint": fingerprint(cat, prop, "nv:" + reason, ctx["screen"], ctx["viewport"]), "category": cat, "prop": prop,
                    "component": f"{len(comps)} element(s)" if len(comps) > 1 else comps[0], "component_type": sample.get("component_type", ""),
                    "title": f"Not verifiable: {sample['label']}" + (f" ({len(comps)} elements)" if len(comps) > 1 else ""), "description": reason or "This property could not be verified.",
                    "expected": "", "actual": "", "difference": "", "classification": "Not Verifiable", "classification_reason": reason, "severity": "Low", "severity_rationale": "Informational: this was not checked.",
                    "confidence": 0.0, "design_evidence": {}, "browser_evidence": {"components": comps[:20]}, "suggestion": "", "suggestion_source": "", "verifiable": 0,
                    "region": None, "visual_region": None, "screen": ctx["screen"], "viewport": ctx["viewport"], "frame_id": ctx.get("frame_id", ""), "mapping_id": ctx.get("mapping_id", "")})
    for item in extra_nv or []:
        out.append(item)
    out.sort(key=lambda f: (f["classification"] == "Not Verifiable", SEV_RANK[f["severity"]], f["category"], f["title"]))
    return {"findings": out, "regions_unexplained": [r for n, r in enumerate(regions) if n not in consumed_regions]}


def design_missing_viewport_finding(ctx: dict, reason: str) -> dict:
    return {"fingerprint": fingerprint("responsive", "no_design", reason, ctx["screen"], ctx["viewport"]), "category": "responsive", "prop": "no_design", "component": "Viewport",
            "component_type": "page", "title": f"Not verifiable: no design for the {ctx['viewport']} viewport", "description": reason, "expected": "", "actual": "", "difference": "",
            "classification": "Not Verifiable", "classification_reason": reason, "severity": "Low", "severity_rationale": "Informational.", "confidence": 0.0, "design_evidence": {}, "browser_evidence": {},
            "suggestion": "", "suggestion_source": "", "verifiable": 0, "region": None, "visual_region": None, "screen": ctx["screen"], "viewport": ctx["viewport"], "frame_id": "", "mapping_id": ctx.get("mapping_id", "")}


# ----------------------------------------------------------------------------- regression

def regression_diff(current: list, previous: list) -> dict:
    """Compare finding sets by fingerprint. Returns per-finding states and the list of fixed findings."""
    prev = {f["fingerprint"]: f for f in previous if f["classification"] != "Not Verifiable"}
    cur = {f["fingerprint"]: f for f in current if f["classification"] != "Not Verifiable"}
    states = {}
    for fp, f in cur.items():
        if fp not in prev:
            states[fp] = "New"
        else:
            p = prev[fp]
            same = (p.get("expected"), p.get("actual")) == (f.get("expected"), f.get("actual"))
            states[fp] = "Persisting" if same else "Changed"
    fixed = [p for fp, p in prev.items() if fp not in cur]
    return {"states": states, "fixed": fixed, "counts": {"new": sum(1 for s in states.values() if s == "New"), "persisting": sum(1 for s in states.values() if s == "Persisting"),
                                                       "changed": sum(1 for s in states.values() if s == "Changed"), "fixed": len(fixed)}}
