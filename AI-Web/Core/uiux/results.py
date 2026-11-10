"""Turns the measured checks of one screen into the rows people read: PASS / FAIL / Needs Review / Not Verifiable / Not Applicable.

Rules that keep the report honest
  * A PASS exists only for a check that was executed with both an expected and an actual value (or a rule measured on the page).
  * A FAIL needs a confirmed difference; anything the engine itself calls potential, unmatched or tolerance-borderline is Needs Review.
  * What the design file cannot provide is Not Verifiable, with the reason. A category with nothing to check is Not Applicable.
    A category that never ran (cancelled / blocked run) is Not Executed.
  * Related checks (same category, property, outcome and element type) become ONE row with the individual measurements kept
    in ``members`` - 150 mismatching paragraphs are one row, not 150.
  * A native .fig only carries a small preview thumbnail. Its pixels are never compared region-by-region with a full page; it
    yields a coarse, clearly labelled look-and-colour comparison that always needs review.
"""
from __future__ import annotations

import io
import re
from collections import OrderedDict

import numpy as np
from PIL import Image, ImageDraw

from . import categories as UC
from . import findings as F

PASS, FAIL, REVIEW, NOT_VERIFIABLE, NOT_APPLICABLE, NOT_EXECUTED = "PASS", "FAIL", "Needs Review", "Not Verifiable", "Not Applicable", "Not Executed"
STATUSES = (PASS, FAIL, REVIEW, NOT_VERIFIABLE, NOT_APPLICABLE, NOT_EXECUTED)
SORT_ORDER = {FAIL: 0, REVIEW: 1, NOT_VERIFIABLE: 2, PASS: 3, NOT_APPLICABLE: 4, NOT_EXECUTED: 5}
MAX_MEMBERS = 60
MAX_CROP_ROWS = 160            # rows that get their own cropped evidence per screen; the rest point at the full-page images
MAX_CROP_SIDE = 1600           # taller/wider regions are shown on the full page instead of as a crop
STRUCTURAL_CATEGORIES = {"layout", "spacing", "css", "colors", "typography", "forms", "buttons", "icons_images", "tables", "navigation", "states"}
PLURALS = {"": "elements", "button": "buttons", "input": "fields", "select": "selects", "text": "text elements", "image": "images", "link": "links", "heading": "headings",
           "container": "containers", "navigation": "navigation items", "table": "tables", "page": "page"}


def _plural(kind: str) -> str:
    return PLURALS.get(kind or "", (kind or "element") + "s")


def fmt(value, unit="") -> str:
    if value is None or value == "":
        return "—"
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, float):
        value = round(value, 2)
        text = f"{value:g}"
    else:
        text = str(value)
    if unit == "px" and isinstance(value, (int, float)):
        text += "px"
    elif unit == "%" and isinstance(value, (int, float)):
        text += "%"
    return text[:240]


# ----------------------------------------------------------------------------- evidence images
def _fit(tile: Image.Image, max_w=640) -> Image.Image:
    if tile.width > max_w:
        return tile.resize((max_w, max(1, int(tile.height * max_w / tile.width))), Image.LANCZOS)
    if tile.width < 200:
        k = min(4, max(1, 220 // max(1, tile.width)))
        return tile.resize((tile.width * k, tile.height * k), Image.NEAREST)
    return tile


def _png(im: Image.Image) -> bytes:
    out = io.BytesIO()
    im.save(out, "PNG", optimize=True)
    return out.getvalue()


def _tile(data: bytes | None, box: dict | None, outline=True, pad=20, label: str = ""):
    if data is None or not box:
        return None
    with Image.open(io.BytesIO(data)) as src:
        im = src.convert("RGB")
    x0, y0 = max(0, int(box["x"] - pad)), max(0, int(box["y"] - pad))
    x1, y1 = min(im.width, int(box["x"] + box["w"] + pad)), min(im.height, int(box["y"] + box["h"] + pad))
    if x1 - x0 < 2 or y1 - y0 < 2 or (x1 - x0) > MAX_CROP_SIDE or (y1 - y0) > MAX_CROP_SIDE:
        return None
    t = im.crop((x0, y0, x1, y1))
    if outline and box.get("w", 0) > 0 and box.get("h", 0) > 0:
        d = ImageDraw.Draw(t)
        d.rectangle([box["x"] - x0 - 1, box["y"] - y0 - 1, box["x"] - x0 + box["w"], box["y"] - y0 + box["h"]], outline=(220, 38, 38), width=2)
        if label:
            text = label[:36]
            tw = 6 * len(text) + 6
            ty = max(0, int(box["y"] - y0) - 13)
            d.rectangle([max(0, int(box["x"] - x0) - 1), ty, min(t.width, int(box["x"] - x0) + tw), ty + 12], fill=(220, 38, 38))
            d.text((max(0, int(box["x"] - x0)) + 2, ty + 1), text, fill=(255, 255, 255))
    return _fit(t)


def crop_panels(design_png, actual_png, diff_png, d_box, a_box, label: str = "") -> dict:
    """Expected / actual / difference crops around one element, with the compared box outlined and named. Missing inputs give None, never a fake."""
    union = F._box_union(d_box, a_box)
    return {"expected": _png(t) if (t := _tile(design_png, d_box, label=label)) else None,
            "actual": _png(t) if (t := _tile(actual_png, a_box, label=label)) else None,
            "diff": _png(t) if (t := _tile(diff_png, union, outline=False)) else None}


# ----------------------------------------------------------------------------- reference thumbnail comparison (coarse, review-only)
def _dominant(im: Image.Image, top=5, min_share=0.04, merge=34.0):
    arr = np.asarray(im.convert("RGB"))[::3, ::3].reshape(-1, 3).astype(np.int32)
    keys = (arr[:, 0] >> 4) * 256 + (arr[:, 1] >> 4) * 16 + (arr[:, 2] >> 4)
    counts = np.bincount(keys, minlength=4096)
    total = float(counts.sum()) or 1.0
    found = []
    for key in np.argsort(counts)[::-1][:40]:
        share = counts[key] / total
        if share < min_share:
            break
        mean = arr[keys == key].mean(axis=0)
        if any(np.linalg.norm(mean - np.array(c["rgb"])) < merge for c in found):
            for c in found:
                if np.linalg.norm(mean - np.array(c["rgb"])) < merge:
                    c["share"] += share
                    break
            continue
        found.append({"rgb": [int(v) for v in mean], "share": float(share)})
    found.sort(key=lambda c: -c["share"])
    return found[:top]


def _hex(rgb):
    return "#%02X%02X%02X" % tuple(rgb)


def palette_compare(ref_png: bytes, page_png: bytes, max_distance=42.0) -> dict:
    with Image.open(io.BytesIO(ref_png)) as r, Image.open(io.BytesIO(page_png)) as p:
        ref, page = r.convert("RGB"), p.convert("RGB")
        if page.height > 4000:
            page = page.crop((0, 0, page.width, 4000))
        expected = _dominant(ref, top=5, min_share=0.04)
        actual = _dominant(page, top=14, min_share=0.004)
    rows = []
    for c in expected:
        best = min(actual, key=lambda a: np.linalg.norm(np.array(a["rgb"]) - np.array(c["rgb"])), default=None)
        dist = float(np.linalg.norm(np.array(best["rgb"]) - np.array(c["rgb"]))) if best else None
        rows.append({"expected": _hex(c["rgb"]), "share": round(c["share"], 3), "closest": _hex(best["rgb"]) if best else "", "distance": round(dist, 1) if dist is not None else None,
                     "match": dist is not None and dist <= max_distance})
    swatch = Image.new("RGB", (max(1, len(rows)) * 70 + 10, 150), (255, 255, 255))
    d = ImageDraw.Draw(swatch)
    for i, row in enumerate(rows):
        x = 10 + i * 70
        d.rectangle([x, 8, x + 58, 60], fill=tuple(int(row["expected"][j:j + 2], 16) for j in (1, 3, 5)), outline=(100, 100, 100))
        d.text((x, 62), row["expected"], fill=(30, 30, 30))
        if row["closest"]:
            d.rectangle([x, 80, x + 58, 132], fill=tuple(int(row["closest"][j:j + 2], 16) for j in (1, 3, 5)), outline=(100, 100, 100) if row["match"] else (220, 38, 38), width=1 if row["match"] else 3)
            d.text((x, 134), row["closest"], fill=(30, 30, 30))
    return {"rows": rows, "all_match": bool(rows) and all(r["match"] for r in rows), "swatches": _png(swatch), "max_distance": max_distance}


def layout_similarity(ref_png: bytes, page_png: bytes) -> dict:
    """Coarse similarity of the top of the page (cut to the preview's proportions) with the preview, plus a heat map. Never a pixel verdict."""
    with Image.open(io.BytesIO(ref_png)) as r, Image.open(io.BytesIO(page_png)) as p:
        ref, page = r.convert("RGB"), p.convert("RGB")
    need = int(round(page.width * ref.height / ref.width))
    cut = min(page.height, need)
    page_top = page.crop((0, 0, page.width, cut))
    ref_top = ref.crop((0, 0, ref.width, max(1, int(round(ref.height * cut / need)))))
    w = 96
    h = max(8, int(round(w * ref_top.height / ref_top.width)))
    a = np.asarray(ref_top.resize((w, h), Image.LANCZOS).convert("L"), dtype=np.float32)
    b = np.asarray(page_top.resize((w, h), Image.LANCZOS).convert("L"), dtype=np.float32)
    a0, b0 = a - a.mean(), b - b.mean()
    denom = float(np.sqrt((a0 ** 2).sum() * (b0 ** 2).sum())) or 1.0
    ncc = float((a0 * b0).sum() / denom)
    heat = np.abs(a - b)
    heat_img = Image.fromarray(np.uint8(np.clip(heat * 2, 0, 255))).convert("RGB").resize((ref_top.width, ref_top.height), Image.NEAREST)
    return {"similarity": round(max(0.0, ncc), 2), "ncc": round(ncc, 3), "page_crop": _png(page_top.resize(ref_top.size, Image.LANCZOS)), "reference_crop": _png(ref_top),
            "heat": _png(heat_img), "page_height_used": cut, "complete": cut >= need * 0.98}


# ----------------------------------------------------------------------------- check -> result
def _result_of(check: dict, ctx: dict):
    """(result, confidence, reason)"""
    st = check["status"]
    if st == "Pass":
        return PASS, 0.95, ""
    if st == "Not Verifiable":
        return NOT_VERIFIABLE, 0.0, check.get("reason") or "This could not be verified."
    cls, conf, why = F.classify(check, ctx)
    if cls == "Confirmed Difference":
        return FAIL, conf, why
    if cls == "Not Verifiable":
        return NOT_VERIFIABLE, 0.0, why
    return REVIEW, conf, why


def _member(c: dict) -> dict:
    return {"component": str(c.get("component") or "")[:120], "type": c.get("component_type") or "", "expected": fmt(c.get("expected"), c.get("unit", "")), "actual": fmt(c.get("actual"), c.get("unit", "")),
            "delta": c.get("delta"), "unit": c.get("unit") or "", "tolerance": c.get("tolerance"), "selector": ((c.get("dom") or {}).get("selector") or "")[:160],
            "design_box": (c.get("design") or {}).get("box"), "page_box": (c.get("dom") or {}).get("box")}


def _summary(members: list, key: str, total: int) -> str:
    vals = [m[key] for m in members]
    if len(set(vals)) == 1:
        return vals[0]
    shown = "; ".join(f"{m['component'] or m['type'] or 'element'}: {m[key]}" for m in members[:3])
    return shown + (f" (+{total - 3} more)" if total > 3 else "")


def _worst(items: list):
    def mag(c):
        d = c.get("delta")
        return abs(d) if isinstance(d, (int, float)) else 0
    return max(items, key=mag)


def checks_to_rows(ctx: dict, sel: list, checks: list) -> list:
    groups: "OrderedDict[tuple, list]" = OrderedDict()
    for c in checks:
        if c["status"] in ("Explained", "Skipped"):
            continue
        res, conf, why = _result_of(c, ctx)
        owners = [k for k in UC.user_category_of(c) if k in sel] or UC.user_category_of(c)[:1]
        key = (owners[0] if owners else c["category"], c["prop"], res, "" if c["prop"] == "missing" else (c.get("component_type") or ""))
        groups.setdefault(key, []).append((c, conf, why))
    rows = []
    for (ucat, prop, res, ctype), items in groups.items():
        cs = [c for c, _, _ in items]
        n = len(cs)
        rep = _worst(cs) if res in (FAIL, REVIEW) else cs[0]
        members = [_member(c) for c in cs[:MAX_MEMBERS]]
        label = rep.get("label") or prop.replace("_", " ").capitalize()
        name = f"{label}: {rep.get('component')}" if n == 1 else f"{label} ({n} {_plural(ctype)})"
        website_only = all(c.get("dom_measured") for c in cs)
        if res == PASS:
            tol = rep.get("tolerance")
            reason = (f"All {n} measured element(s) are within the tolerance" if n > 1 else "Measured value is within the tolerance") + (f" (±{fmt(tol, rep.get('unit', ''))})." if tol not in (None, "") else ".")
            if website_only:
                reason = "The rule was checked on the page and no violation was found."
            sev = ""
        elif res == FAIL:
            title, description, difference = F.describe(rep)
            reason = (description or title) + (f" Similar differences on {n - 1} more element(s) are grouped here." if n > 1 else "")
            sev = max((F.severity_of(c, ctx)[0] for c in cs), key=lambda s: -F.SEV_RANK[s])
        elif res == REVIEW:
            reason = items[0][2] + (f" ({n} elements)" if n > 1 else "")
            sev = max((F.severity_of(c, ctx)[0] for c in cs), key=lambda s: -F.SEV_RANK[s])
        else:
            reason = items[0][2]
            sev = ""
        if website_only and res in (FAIL, REVIEW):
            expected, actual = _summary(members, "expected", n), _summary(members, "actual", n)
        else:
            expected, actual = _summary(members, "expected", n), _summary(members, "actual", n)
        rows.append({"user_category": ucat, "name": name[:200], "expected": expected, "actual": actual, "result": res, "reason": reason[:600], "severity": sev, "confidence": round(max(x[1] for x in items), 2),
                     "source": "website rule" if website_only else "design comparison", "member_count": n, "members": members, "finding_fingerprint": "",
                     "_rep": rep, "_prop": prop})
    return rows


def extra_rows(ctx: dict, sel: list, extra_nv: list) -> list:
    rows = []
    for item in extra_nv or []:
        res = NOT_VERIFIABLE if item["classification"] == "Not Verifiable" else REVIEW
        owners = [k for k in UC.user_category_of({"category": item["category"], "prop": item["prop"]}) if k in sel] or UC.user_category_of({"category": item["category"], "prop": item["prop"]})[:1]
        rows.append({"user_category": owners[0] if owners else item["category"], "name": item["component"][:200], "expected": item.get("expected") or "Information from the design",
                     "actual": item.get("actual") or "Not checked", "result": res, "reason": item["description"][:600], "severity": "", "confidence": item.get("confidence", 0.0),
                     "source": "coverage", "member_count": 1, "members": [], "finding_fingerprint": item.get("fingerprint", ""), "_rep": None, "_prop": item["prop"]})
    return rows


def visual_rows(ctx: dict, visual: dict | None, unexplained: list, explained: int) -> list:
    if not visual:
        return []
    size = visual["size"]
    cmp_note = f"{size['compared'][0]}×{size['compared'][1]}px compared"
    count = visual["region_count"]
    if count == 0 and visual["match"]:
        return [{"user_category": "visual", "name": "Page matches the design image", "expected": "Design image", "actual": f"{visual['diff_ratio'] * 100:.2f}% of pixels differ",
                 "result": PASS, "reason": f"No visible difference regions ({cmp_note}).", "severity": "", "confidence": 0.9, "source": "visual", "member_count": 1, "members": [],
                 "finding_fingerprint": "", "_rep": None, "_prop": "visual_pass", "_region": None}]
    members = [{"component": f"Region {i + 1}", "type": "region", "expected": "as designed", "actual": f"{int(r['ratio'] * 100)}% of pixels differ", "delta": r["ratio"], "unit": "ratio",
                "tolerance": 0, "selector": "", "design_box": {k: r[k] for k in ("x", "y", "w", "h")}, "page_box": {k: r[k] for k in ("x", "y", "w", "h")}} for i, r in enumerate(visual["regions"][:MAX_MEMBERS])]
    if explained >= count and count:
        res, why = FAIL, f"{count} region(s) differ from the design image; each coincides with a measured mismatch listed in this report ({cmp_note})."
    else:
        res, why = REVIEW, (f"{count} region(s) differ from the design image and {len(unexplained)} are not explained by a measured property. Pixel differences alone are reported for review, not as confirmed defects ({cmp_note}).")
    first = visual["regions"][0] if visual["regions"] else None
    return [{"user_category": "visual", "name": f"Visual comparison ({count} differing region{'s' if count != 1 else ''})", "expected": "Design image", "actual": f"{visual['diff_ratio'] * 100:.2f}% of pixels differ",
             "result": res, "reason": why, "severity": "Medium" if res == FAIL else "Low", "confidence": 0.8 if res == FAIL else 0.6, "source": "visual", "member_count": count, "members": members,
             "finding_fingerprint": "", "_rep": None, "_prop": "visual", "_region": ({k: first[k] for k in ("x", "y", "w", "h")} if first else None)}]


def reference_rows(sel: list, design_png: bytes, actual_png: bytes, dims: dict) -> tuple:
    """Rows for a design that is only an embedded preview thumbnail; returns (rows, extra evidence images keyed by row index)."""
    rows, images = [], {}
    note = f"The design is only the {dims['width']}×{dims['height']} preview embedded in the .fig file, not the frame at its design size"
    if "visual" in sel:
        s = layout_similarity(design_png, actual_png)
        rows.append({"user_category": "visual", "name": "Overall look compared with the reference preview", "expected": "Reference preview of the design",
                     "actual": f"{int(s['similarity'] * 100)}% coarse layout similarity" + ("" if s["complete"] else " (page shorter than the preview's proportions)"), "result": REVIEW,
                     "reason": f"{note}. Only a coarse comparison of the top of the page, scaled to the preview's proportions, is meaningful and it cannot confirm or refute individual differences. Review the images.",
                     "severity": "", "confidence": 0.3, "source": "reference preview", "member_count": 1, "members": [], "finding_fingerprint": "", "_rep": None, "_prop": "reference_look"})
        images[len(rows) - 1] = {"expected": s["reference_crop"], "actual": s["page_crop"], "diff": s["heat"]}
    if "colors" in sel:
        p = palette_compare(design_png, actual_png)
        missing = [r for r in p["rows"] if not r["match"]]
        listing = ", ".join(f"{r['expected']} ({int(r['share'] * 100)}%)" for r in p["rows"])
        if not p["rows"]:
            rows.append({"user_category": "colors", "name": "Dominant colours of the design", "expected": "Colours from the reference preview", "actual": "No dominant colour found", "result": NOT_VERIFIABLE,
                         "reason": "The preview has no colour covering at least 4% of its area.", "severity": "", "confidence": 0.0, "source": "reference preview", "member_count": 0, "members": [],
                         "finding_fingerprint": "", "_rep": None, "_prop": "palette"})
        else:
            res = PASS if p["all_match"] else REVIEW
            rows.append({"user_category": "colors", "name": "Dominant colours of the design appear on the page", "expected": listing,
                         "actual": ", ".join(f"{r['expected']}→{r['closest']}" for r in p["rows"]), "result": res,
                         "reason": (f"Every dominant colour of the preview has a page colour within {p['max_distance']:g} RGB distance. This is a colour-presence check, not a check of where the colours are used."
                                    if res == PASS else f"{len(missing)} dominant colour(s) of the preview were not found on the page within {p['max_distance']:g} RGB distance: " + ", ".join(r['expected'] for r in missing) + ". "
                                    "The preview is small and compressed, so review before treating this as a defect."),
                         "severity": "", "confidence": 0.7 if res == PASS else 0.4, "source": "reference preview", "member_count": len(p["rows"]),
                         "members": [{"component": r["expected"], "type": "colour", "expected": r["expected"], "actual": r["closest"] or "—", "delta": r["distance"], "unit": "rgb distance", "tolerance": p["max_distance"],
                                      "selector": "", "design_box": None, "page_box": None} for r in p["rows"]],
                         "finding_fingerprint": "", "_rep": None, "_prop": "palette"})
            images[len(rows) - 1] = {"expected": p["swatches"], "actual": None, "diff": None, "single_panel": True}
    return rows, images


COVERAGE_REASON = {
    "layout": "Element positions need the design's measured layout; this design source provides no element positions.",
    "spacing": "Sizes and gaps need the design's measured layout; this design source provides none.",
    "css": "Borders, radius, shadow and opacity need design style data; this design source provides none.",
    "colors": "Exact colours need design style data or a design image to sample.",
    "typography": "Font family, size, weight and line height need design text styles; this design source provides none.",
    "forms": "Fields and labels need the design's layers; this design source provides none.",
    "buttons": "Buttons need the design's layers; this design source provides none.",
    "icons_images": "Icons and images need the design's layers; this design source provides none.",
    "tables": "Table structure needs the design's layers; this design source provides none.",
    "navigation": "Menu items need the design's layers; this design source provides none.",
    "states": "No design frame is mapped for an interactive state, so states were not tested.",
}
NO_CHECK_REASON = "Nothing in the design or on the page matched this category, so no check could run. This is not a pass."


def coverage_rows(sel: list, rows: list, structural: bool, reference: bool, by_user: dict) -> list:
    """Every selected category must appear. One without a row is Not Verifiable (the design lacks the data) or Not Applicable (nothing to check)."""
    have = {r["user_category"] for r in rows}
    out = []
    for key in sel:
        if key in have:
            continue
        label = UC.LABELS.get(key, key)
        if key in STRUCTURAL_CATEGORIES and (not structural or reference):
            res, why = NOT_VERIFIABLE, COVERAGE_REASON.get(key, "The design source does not carry this information.")
        else:
            res, why = NOT_APPLICABLE, NO_CHECK_REASON
        out.append({"user_category": key, "name": f"{label}: " + ("not verifiable with this design" if res == NOT_VERIFIABLE else "no applicable check"), "expected": "Value from the design",
                    "actual": "Not compared" if res == NOT_VERIFIABLE else "Nothing to compare", "result": res, "reason": why, "severity": "", "confidence": 0.0, "source": "coverage",
                    "member_count": 0, "members": [], "finding_fingerprint": "", "_rep": None, "_prop": "coverage"})
    return out


def not_executed_rows(sel: list, have: set, why: str) -> list:
    return [{"user_category": k, "name": f"{UC.LABELS.get(k, k)}: not executed", "expected": "—", "actual": "—", "result": NOT_EXECUTED, "reason": why, "severity": "", "confidence": 0.0, "source": "coverage",
             "member_count": 0, "members": [], "finding_fingerprint": "", "_rep": None, "_prop": "not_executed"} for k in sel if k not in have]


# ----------------------------------------------------------------------------- evidence attachment
def attach_evidence(repo, run_id: str, rows: list, design_png, actual_png, diff_png, page_ev: dict, extra_images: dict | None = None, mapping_id: str = "") -> None:
    """Give each row its evidence references. Crops are stored once per distinct image; rows beyond the cap point at the full-page images."""
    cache: dict = {}
    extra_images = extra_images or {}

    def store(kind, data, label):
        if data is None:
            return None
        import hashlib
        h = hashlib.sha256(data).hexdigest()
        if h not in cache:
            with Image.open(io.BytesIO(data)) as im:
                w, hh = im.size
            cache[h] = repo.add_evidence(run_id, kind, "result", data, label[:150], meta={"mapping_id": mapping_id}, width=w, height=hh)
        return cache[h]

    order = sorted(range(len(rows)), key=lambda i: (SORT_ORDER[rows[i]["result"]], i))
    budget = MAX_CROP_ROWS
    for i in order:
        r = rows[i]
        ev = {"mode": "none", "design": None, "actual": None, "diff": None, "page_design": page_ev.get("design"), "page_actual": page_ev.get("screenshot"), "page_diff": page_ev.get("diff"),
              "region": None, "limitation": ""}
        if i in extra_images:
            imgs = extra_images[i]
            ev.update(mode="reference", design=store("result_expected", imgs.get("expected"), r["name"]), actual=store("result_actual", imgs.get("actual"), r["name"]), diff=store("result_diff", imgs.get("diff"), r["name"]))
            ev["limitation"] = "Reference preview only: the comparison is coarse and cannot confirm individual differences."
        else:
            rep = r.get("_rep")
            if rep is not None:
                d_box, a_box = (rep.get("design") or {}).get("box"), (rep.get("dom") or {}).get("box")
            else:
                d_box = a_box = r.get("_region")
            if budget > 0 and (d_box or a_box) and r["result"] in (FAIL, REVIEW, PASS):
                try:
                    label = ((rep or {}).get("component") or "") if rep is not None else ""
                    panels = crop_panels(design_png, actual_png, diff_png, d_box, a_box, label)
                except Exception:       # an image problem must never lose the result itself
                    panels = {"expected": None, "actual": None, "diff": None}
                if any(panels.values()):
                    ev.update(mode="crop", design=store("result_expected", panels["expected"], r["name"]), actual=store("result_actual", panels["actual"], r["name"]), diff=store("result_diff", panels["diff"], r["name"]))
                    ev["region"] = {"design": d_box, "page": a_box}
                    budget -= 1
            if ev["mode"] == "none" and (page_ev.get("screenshot") or page_ev.get("design")):
                ev["mode"] = "page"
                ev["region"] = {"design": d_box, "page": a_box} if (d_box or a_box) else None
            if ev["mode"] != "none" and design_png is None:
                ev["limitation"] = "The design has no image, so there is no expected picture; the expected value comes from the design's data."
            if ev["mode"] == "none":
                ev["limitation"] = "No screenshot is attached to this result."
        r["evidence"] = ev


# ----------------------------------------------------------------------------- developer-ready specification per row
def region_colour(png: bytes | None, box: dict | None):
    """Dominant colour of a region of a picture as '#RRGGBB' (None when it cannot be read)."""
    if png is None or not box:
        return None
    try:
        with Image.open(io.BytesIO(png)) as src:
            im = src.convert("RGB")
        x0, y0 = max(0, int(box["x"])), max(0, int(box["y"]))
        x1, y1 = min(im.width, int(box["x"] + box["w"])), min(im.height, int(box["y"] + box["h"]))
        if x1 - x0 < 2 or y1 - y0 < 2:
            return None
        dom = _dominant(im.crop((x0, y0, x1, y1)), top=1, min_share=0.2)
        return _hex(dom[0]["rgb"]) if dom else None
    except Exception:
        return None


def attach_specs(rows: list, ctx: dict, frame: dict, probe: dict, design_png, actual_png, nodes_have_data: bool) -> None:
    """Add ``evidence["spec"]`` to every row: element, exact location, locator, expected / actual field table, differences and an evidence-based suggested fix."""
    from . import spec as SP
    nodes = {n.get("id"): n for n in (frame.get("nodes") or [])}
    elements = probe.get("elements") or []
    by_i = {e["i"]: e for e in elements}
    page = probe.get("page") or {}
    ctx = {**ctx, "page_title": probe.get("title", ""), "page_w": page.get("scrollW") or 0, "page_h": page.get("scrollH") or 0}
    fr = {"width": frame.get("width"), "height": frame.get("height")}
    for r in rows:
        ev = r.setdefault("evidence", {})
        rep = r.get("_rep")
        node = nodes.get(((rep or {}).get("design") or {}).get("id")) if rep else None
        el = by_i.get(((rep or {}).get("dom") or {}).get("index")) if rep else None
        region = r.get("_region")
        sample = None
        if rep is None and region and design_png is not None and actual_png is not None:
            e, a = region_colour(design_png, region), region_colour(actual_png, region)
            if e and a and e != a:
                sample = {"expected": e, "actual": a}
        if rep is None and not region and not r.get("members"):
            ev["spec"] = {"element": r["name"], "screen": ctx.get("frame_name") or ctx.get("screen", ""), "page_url": ctx.get("page_url", ""), "location": "", "design_location": "", "website_location": "",
                          "position": "", "locator": "", "locator_kind": "", "fields": [], "differences": [], "unsupported": [], "inferred": False, "design_source": ctx.get("design_source", ""),
                          "suggested_fix": "", "note": "This result covers a whole category or the page, not one element."}
            continue
        try:
            spec = SP.build(ctx, rep, node, el, elements, fr, region, sample)
        except Exception as exc:     # a specification problem must never lose the result
            spec = {"element": r["name"], "screen": ctx.get("screen", ""), "page_url": ctx.get("page_url", ""), "location": "", "fields": [], "differences": [], "unsupported": [], "inferred": False,
                    "suggested_fix": "", "note": f"The specification could not be built ({type(exc).__name__})."}
        if r["result"] not in (FAIL,):
            spec["suggested_fix"] = ""
        if not spec.get("element"):                       # a grouped row without one representative element names its members
            names = list(dict.fromkeys(m.get("component") for m in r.get("members", []) if m.get("component")))
            if names:
                spec["element"] = ", ".join(names[:3]) + (f" and {len(names) - 3} more" if len(names) > 3 else "")
        if r.get("member_count", 1) > 1:
            also = [m["selector"] for m in r.get("members", [])[1:11] if m.get("selector")]
            spec["also_affected"] = also
            spec["also_affected_total"] = r["member_count"] - 1
        ev["spec"] = spec
