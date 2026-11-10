"""Normalise a Figma REST file (or a Figma JSON export) into frames and comparable design nodes.

Every extracted value keeps a provenance path (``prov``) pointing at the exact Figma property it came from.
A property that Figma did not provide is simply absent; it is never defaulted, so it can later be reported as
"Not Verifiable" instead of being compared against an invented value.
"""
from __future__ import annotations

import re

from .util import to_hex

MAX_NODES_PER_FRAME = 2500
VISUAL_TYPES = {"RECTANGLE", "ELLIPSE", "VECTOR", "LINE", "STAR", "POLYGON", "BOOLEAN_OPERATION", "TEXT"}
CONTAINER_TYPES = {"FRAME", "GROUP", "COMPONENT", "COMPONENT_SET", "INSTANCE", "SECTION"}

_ROLE_PATTERNS = [
    ("button", r"\b(button|btn|cta)\b|^button"),
    ("checkbox", r"\bcheck ?box\b"),
    ("radio", r"\bradio\b"),
    ("toggle", r"\b(toggle|switch)\b"),
    ("select", r"\b(select|dropdown|combo ?box|picker)\b"),
    ("textarea", r"\btext ?area\b"),
    ("input", r"\b(input|text ?field|textbox|text input|form field|field)\b"),
    ("table", r"\b(table|data ?grid|datagrid)\b"),
    ("pagination", r"\bpagination\b|\bpager\b"),
    ("tab", r"\btabs?\b"),
    ("breadcrumb", r"\bbreadcrumbs?\b"),
    ("nav", r"\b(nav|navbar|navigation|menu|sidebar|side bar)\b"),
    ("icon", r"\bicons?\b|^ic[_-]"),
    ("image", r"\b(image|img|photo|avatar|logo|illustration|picture)\b"),
    ("label", r"\blabel\b"),
    ("heading", r"\b(title|heading|headline|h[1-6])\b"),
    ("link", r"\blink\b"),
    ("badge", r"\b(badge|chip|tag|pill)\b"),
    ("alert", r"\b(alert|toast|snackbar|banner|error message|helper text)\b"),
]


def figma_color(c, opacity=1.0):
    if not isinstance(c, dict):
        return ""
    a = c.get("a", 1.0) * (opacity if opacity is not None else 1.0)
    rgba = (round(c.get("r", 0) * 255), round(c.get("g", 0) * 255), round(c.get("b", 0) * 255), round(a, 4))
    return to_hex(rgba)


def _solid_fill(node, key="fills"):
    for index, paint in enumerate(node.get(key) or []):
        if paint.get("visible", True) is False:
            continue
        if paint.get("type") == "SOLID":
            return figma_color(paint.get("color"), paint.get("opacity", 1.0)), f"{key}[{index}]"
        if paint.get("type") == "IMAGE":
            return "IMAGE", f"{key}[{index}]"
        if str(paint.get("type", "")).startswith("GRADIENT"):
            return "GRADIENT", f"{key}[{index}]"
    return "", ""


def infer_role(node: dict, parent_role: str = "") -> tuple[str, float, str]:
    """Return (role, confidence, basis). Roles are hints derived from naming/shape, not verified facts."""
    name = str(node.get("name", ""))
    ntype = node.get("type", "")
    low = name.lower()
    for role, pattern in _ROLE_PATTERNS:
        if re.search(pattern, low):
            if role == "icon" and ntype == "TEXT":
                continue
            return role, 0.8, f"layer name '{name}' matches '{role}'"
    if ntype == "TEXT":
        size = (node.get("style") or {}).get("fontSize", 0)
        if size >= 24:
            return "heading", 0.6, f"text size {size}px"
        return "text", 0.7, "text layer"
    if ntype == "VECTOR" or ntype == "BOOLEAN_OPERATION":
        return "icon", 0.5, "vector layer"
    return "", 0.0, ""


def _norm_node(node, origin, parent_path, depth, key, out, state):
    if state["count"] >= MAX_NODES_PER_FRAME:
        state["truncated"] = True
        return
    if node.get("visible", True) is False:
        return
    box = node.get("absoluteBoundingBox") or node.get("absoluteRenderBounds")
    ntype = node.get("type", "")
    path = f"{parent_path}/{node.get('name', '')}"
    entry = None
    if box and box.get("width") is not None and (ntype in VISUAL_TYPES or ntype in CONTAINER_TYPES):
        prov = {}
        entry = {
            "id": node.get("id"), "name": node.get("name", ""), "type": ntype, "path": path[-200:], "depth": depth,
            "x": round(box["x"] - origin[0], 2), "y": round(box["y"] - origin[1], 2),
            "w": round(box["width"], 2), "h": round(box["height"], 2),
        }
        prov.update({"x": "absoluteBoundingBox.x", "y": "absoluteBoundingBox.y", "w": "absoluteBoundingBox.width", "h": "absoluteBoundingBox.height"})
        if ntype == "TEXT":
            style = node.get("style") or {}
            color, p = _solid_fill(node)
            entry["text"] = {
                "chars": node.get("characters", ""),
                "fontFamily": style.get("fontFamily"), "fontSize": style.get("fontSize"), "fontWeight": style.get("fontWeight"),
                "lineHeightPx": style.get("lineHeightPx"), "letterSpacing": style.get("letterSpacing"),
                "align": (style.get("textAlignHorizontal") or "").lower() or None,
                "color": color if color not in ("IMAGE", "GRADIENT") else "",
            }
            for field, src in (("fontFamily", "style.fontFamily"), ("fontSize", "style.fontSize"), ("fontWeight", "style.fontWeight"),
                               ("lineHeightPx", "style.lineHeightPx"), ("letterSpacing", "style.letterSpacing"), ("align", "style.textAlignHorizontal")):
                if entry["text"].get(field) is not None:
                    prov[f"text.{field}"] = src
            if entry["text"]["color"]:
                prov["text.color"] = p
        else:
            color, p = _solid_fill(node)
            if color and color not in ("IMAGE", "GRADIENT"):
                entry["bg"] = color
                prov["bg"] = p
            elif color:
                entry["bg_kind"] = color.lower()
        stroke, sp = _solid_fill(node, "strokes")
        if stroke and stroke not in ("IMAGE", "GRADIENT") and node.get("strokeWeight"):
            entry["border"] = {"width": node.get("strokeWeight"), "color": stroke}
            prov["border"] = f"{sp}/strokeWeight"
        if node.get("cornerRadius") is not None:
            entry["radius"] = node["cornerRadius"]
            prov["radius"] = "cornerRadius"
        elif node.get("rectangleCornerRadii"):
            entry["radius"] = list(node["rectangleCornerRadii"])
            prov["radius"] = "rectangleCornerRadii"
        if node.get("opacity") is not None and node["opacity"] != 1:
            entry["opacity"] = node["opacity"]
            prov["opacity"] = "opacity"
        shadows = []
        for index, fx in enumerate(node.get("effects") or []):
            if fx.get("visible", True) and fx.get("type") in ("DROP_SHADOW", "INNER_SHADOW"):
                shadows.append({"type": fx["type"], "x": (fx.get("offset") or {}).get("x", 0), "y": (fx.get("offset") or {}).get("y", 0),
                                "blur": fx.get("radius", 0), "spread": fx.get("spread", 0), "color": figma_color(fx.get("color"))})
        if shadows:
            entry["shadow"] = shadows
            prov["shadow"] = "effects"
        if node.get("layoutMode") in ("HORIZONTAL", "VERTICAL"):
            entry["layout"] = {"mode": node["layoutMode"].lower(), "gap": node.get("itemSpacing"),
                               "padding": [node.get("paddingTop", 0), node.get("paddingRight", 0), node.get("paddingBottom", 0), node.get("paddingLeft", 0)]}
            prov["layout"] = "layoutMode/itemSpacing/padding*"
        bound = node.get("boundVariables")
        if bound:
            entry["variables"] = {k: [v.get("id") for v in (vs if isinstance(vs, list) else [vs]) if isinstance(v, dict)] for k, vs in bound.items()}
        role, conf, basis = infer_role(node)
        if role:
            entry["role"], entry["role_confidence"], entry["role_basis"] = role, conf, basis
        if node.get("characters") is None and ntype == "INSTANCE" and node.get("componentId"):
            entry["component_id"] = node["componentId"]
        entry["prov"] = prov
        out.append(entry)
        state["count"] += 1
    for child in node.get("children") or []:
        _norm_node(child, origin, path, depth + 1, key, out, state)


def classify_viewport(width: float) -> str:
    if width <= 480:
        return "mobile"
    if width <= 1024:
        return "tablet"
    return "desktop"


def extract_frames(document: dict, file_key: str = "", only_nodes: list | None = None) -> list:
    """Return normalised top-level frames: [{node_id,page,name,width,height,viewport_class,nodes,truncated}]."""
    frames = []
    pages = (document or {}).get("document", document or {}).get("children") or []
    for page in pages:
        if page.get("type") not in ("CANVAS", None):
            continue
        candidates = []
        for child in page.get("children") or []:
            if child.get("type") == "SECTION":
                candidates += [c for c in child.get("children") or [] if c.get("type") in ("FRAME", "COMPONENT", "COMPONENT_SET")]
            elif child.get("type") in ("FRAME", "COMPONENT", "COMPONENT_SET"):
                candidates.append(child)
        for frame in candidates:
            if only_nodes and frame.get("id") not in only_nodes:
                continue
            box = frame.get("absoluteBoundingBox")
            if not box:
                continue
            nodes, state = [], {"count": 0, "truncated": False}
            _norm_node(frame, (box["x"], box["y"]), "", 0, file_key, nodes, state)
            frames.append({"node_id": frame["id"], "page": page.get("name", ""), "name": frame.get("name", ""),
                           "width": round(box["width"], 2), "height": round(box["height"], 2),
                           "viewport_class": classify_viewport(box["width"]), "nodes": nodes, "truncated": state["truncated"]})
    return frames


def resolve_variable_names(frames: list, variables_meta: dict) -> None:
    """Replace variable ids on nodes with human-readable token names when the variables API provided them."""
    table = (variables_meta or {}).get("variables") or {}
    if not table:
        return
    for frame in frames:
        for node in frame["nodes"]:
            if node.get("variables"):
                node["variable_names"] = {k: [table.get(i, {}).get("name", i) for i in ids] for k, ids in node["variables"].items()}


def parse_frame_node(frame: dict, file_key: str = "", page: str = "") -> dict | None:
    """Normalise one frame node (as returned by GET /v1/files/:key/nodes)."""
    box = frame.get("absoluteBoundingBox")
    if not box or frame.get("visible", True) is False:
        return None
    nodes, state = [], {"count": 0, "truncated": False}
    _norm_node(frame, (box["x"], box["y"]), "", 0, file_key, nodes, state)
    return {"node_id": frame["id"], "page": page, "name": frame.get("name", ""), "width": round(box["width"], 2),
            "height": round(box["height"], 2), "viewport_class": classify_viewport(box["width"]), "nodes": nodes,
            "truncated": state["truncated"]}
