"""User-facing test categories and how they map onto the measured checks of the engine.

The engine groups its checks by ``(category, prop)``.  People think in somewhat different groups ("Colors" cuts across CSS,
typography and icons; "Spacing and dimensions" is a slice of layout), so the one-click workflow selects **user categories**
and this module turns the selection into

  * the set of engine categories that must run, and
  * a predicate that keeps only the checks that belong to a selected user category.

Nothing here invents a check: a user category with no applicable check is reported as such ("no applicable check"), never as a pass.
Configurations saved before this module existed (no ``ui_categories`` flag) keep their original meaning: the engine categories as they were.
"""
from __future__ import annotations

# key -> label, hint, engine categories, optional {engine_category: set(props)} restriction (None = every prop of that engine category)
USER_CATEGORIES = [
    ("layout", "Layout and alignment", "Element positions, overlap, clipped content and missing layers.", {"layout": {"position", "missing", "overlap", "text_clipped"}}),
    ("spacing", "Spacing and dimensions", "Element sizes, gaps between elements, table row heights and column widths.", {"layout": {"size", "gap_x", "gap_y"}, "tables": {"row_height", "column_width"}}),
    ("css", "CSS and styling", "Borders, corner radius, shadow and opacity (colours are under Colors).", {"css": {"border_width", "border_radius", "shadow", "opacity"}}),
    ("colors", "Colors", "Background, border, text and icon colours.", {"css": {"background", "border_color"}, "typography": {"text_color"}, "icons_images": {"icon_color"}}),
    ("typography", "Fonts and typography", "Font family, size, weight, line height, letter spacing, alignment and wording.", {"typography": None}),
    ("forms", "Fields and forms", "Fields, labels, placeholders and required markers.", {"forms": None}),
    ("buttons", "Buttons", "Button presence, text and styling.", {"buttons": None}),
    ("icons_images", "Icons and images", "Icons, images and broken-image detection.", {"icons_images": None}),
    ("tables", "Tables", "Columns, rows and header text.", {"tables": None}),
    ("navigation", "Navigation", "Menu items and their order.", {"navigation": None}),
    ("responsive", "Responsive design", "Overflow and viewport match for the tested viewport only.", {"responsive": None}),
    ("accessibility", "Accessibility", "Automated accessibility checks; manual-review items are listed separately.", {"accessibility": None}),
    ("visual", "Visual comparison", "Pixel comparison of the page against the design image.", {"visual": None}),
    ("states", "Interactive states", "Hover, focus and other states, only where a design frame is mapped for the state.", {"states": None}),
]
KEYS = [c[0] for c in USER_CATEGORIES]
LABELS = {c[0]: c[1] for c in USER_CATEGORIES}
HINTS = {c[0]: c[2] for c in USER_CATEGORIES}
_SPEC = {c[0]: c[3] for c in USER_CATEGORIES}
DEFAULT_SELECTION = [k for k in KEYS if k != "states"]
# keys whose presence in a configuration means "this was saved by the category-aware workflow"
NEW_ONLY = {"spacing", "colors"}


def public() -> list:
    return [{"key": k, "label": LABELS[k], "hint": HINTS[k], "default": k in DEFAULT_SELECTION} for k in KEYS]


def engine_categories(selected) -> set:
    out = set()
    for key in selected:
        out.update(_SPEC.get(key, {}).keys())
    return out


def user_category_of(check: dict) -> list:
    """The user categories a check belongs to (usually one; ``typography/text_color`` is Colors, not Fonts)."""
    cat, prop = check.get("category"), check.get("prop")
    hits = []
    for key in KEYS:
        spec = _SPEC[key]
        if cat in spec and (spec[cat] is None or prop in spec[cat]):
            # an unrestricted entry loses to a more specific one elsewhere (typography/text_color belongs to colors only)
            if spec[cat] is None and any(cat in _SPEC[o] and _SPEC[o][cat] is not None and prop in _SPEC[o][cat] for o in KEYS if o != key):
                continue
            hits.append(key)
    return hits


def keep(selected, check: dict) -> bool:
    """True when the check belongs to at least one selected user category."""
    sel = set(selected)
    return any(k in sel for k in user_category_of(check))


def status_of(row: dict) -> str:
    return "Checked" if row.get("executed") else ("Not Verifiable" if row.get("not_verifiable") else "No applicable check")


def summarize(selected, checks: list) -> dict:
    """Per selected user category: executed / passed / failed / not verifiable / explained, and an honest status.

    status: 'Checked' (at least one measured check ran), 'Not Verifiable' (checks exist but none could be measured),
    'No applicable check' (nothing in the design or page matched, so nothing was verified - never a pass).
    """
    out = {}
    for key in selected:
        row = {"label": LABELS.get(key, key), "executed": 0, "passed": 0, "failed": 0, "not_verifiable": 0, "explained": 0}
        for c in checks:
            if key in user_category_of(c):
                st = c.get("status")
                if st == "Pass":
                    row["passed"] += 1
                elif st == "Fail":
                    row["failed"] += 1
                elif st == "Explained":
                    row["explained"] += 1
                else:
                    row["not_verifiable"] += 1
        row["executed"] = row["passed"] + row["failed"] + row["explained"]
        row["status"] = status_of(row)
        out[key] = row
    return out


def merge_summaries(parts: list) -> dict:
    """Combine the per-screen summaries of one run."""
    out = {}
    for part in parts:
        for key, row in (part or {}).items():
            agg = out.setdefault(key, {"label": row["label"], "executed": 0, "passed": 0, "failed": 0, "not_verifiable": 0, "explained": 0})
            for k in ("executed", "passed", "failed", "not_verifiable", "explained"):
                agg[k] += row.get(k, 0)
    for row in out.values():
        row["status"] = status_of(row)
    return out
